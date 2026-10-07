"""Deterministic evidence-grounded investigator.

plan -> call read-only tools -> record evidence -> score hypotheses -> structured report.
Every claim cites evidence ids. Remediation is only ever a proposal pending human approval.
"""
from statistics import mean

from pydantic import BaseModel

from backend.data.generator import CAUSES, SERVICES
from backend.tools.toolbox import ToolBox

KEYWORDS = {
    "bad_deploy": ["nullpointer", "new code path", "regression"],
    "db_pool_exhaustion": ["pool", "no available connections"],
    "cache_miss_storm": ["cache miss", "cache invalidation", "stampede"],
    "dependency_timeout": ["timed out", "circuit breaker", "read timeout"],
    "memory_leak": ["gc ", "heap", "outofmemory"],
    "config_change": ["thread pool size", "config reload", "queue full"],
}
SLOW_OP_CAUSE = {"db.pool.acquire": "db_pool_exhaustion", "cache.get": "cache_miss_storm", "http.client": "dependency_timeout"}
METRICS = ["latency_p99_ms", "error_rate", "db_pool_in_use_pct", "cache_hit_ratio", "dependency_latency_ms", "memory_mb"]


class Evidence(BaseModel):
    id: str
    tool: str
    summary: str
    supports: list[str] = []
    weight: float = 0.0
    data: dict = {}


class Hypothesis(BaseModel):
    cause: str
    title: str
    score: float
    confidence: float
    evidence_ids: list[str]
    explanation: str = ""


class Report(BaseModel):
    service: str | None
    onset: int | None
    summary: str
    hypotheses: list[Hypothesis]
    next_steps: list[str]
    remediation: dict
    tools_used: list[str]
    failures: list[str]
    llm: dict = {}


class Investigator:
    def __init__(self, toolbox: ToolBox):
        self.tb = toolbox
        self.evidence: list[Evidence] = []
        self.failures: list[str] = []
        self.tools_used: list[str] = []

    def _call(self, name, **args):
        """Failure recovery: record and continue instead of aborting the investigation."""
        self.tools_used.append(name)
        try:
            return self.tb.call(name, **args)
        except Exception as e:  # noqa: BLE001
            self.failures.append(f"{name} failed: {e}")
            return None

    def _ev(self, tool, summary, supports=(), weight=0.0, **data):
        e = Evidence(id=f"E{len(self.evidence) + 1}", tool=tool, summary=summary, supports=list(supports), weight=weight, data=data)
        self.evidence.append(e)
        return e

    def investigate(self, alert: str, exemplar_trace: str | None = None) -> tuple[Report, list[Evidence]]:
        service = next((s for s in SERVICES if s in alert), None)
        onset = self._metrics()
        corr_deploy = self._deployments(service, onset)
        top_log = self._logs()
        self._trace(exemplar_trace)
        self._history(alert, top_log)
        if corr_deploy:
            cmp_ = self._call("compare_service_versions", service=service, version_a=corr_deploy["prev_version"], version_b=corr_deploy["version"])
            if cmp_:
                self._ev("compare_service_versions", f"p99 {cmp_['p99_before']}ms -> {cmp_['p99_after']}ms across change '{cmp_['change']}'",
                         [("bad_deploy" if corr_deploy["kind"] == "deploy" else "config_change")], 1.0, **cmp_)
        return self._report(service, onset)

    # --- steps ---
    def _metrics(self):
        series = {m: self._call("query_metrics", metric=m) for m in METRICS}
        lat = series["latency_p99_ms"]
        onset = None
        if lat:
            base = mean(v for t, v in lat if t < 60)
            onset = next((t for t, v in lat if v > 1.5 * base), None)
        rules = {
            "db_pool_in_use_pct": ("db_pool_exhaustion", lambda b, a: a > 90, "connection pool saturated"),
            "cache_hit_ratio": ("cache_miss_storm", lambda b, a: a < 0.6 * b, "cache hit ratio collapsed"),
            "dependency_latency_ms": ("dependency_timeout", lambda b, a: a > 4 * b, "dependency latency spiked"),
            "memory_mb": ("memory_leak", lambda b, a: a > 1.5 * b, "memory grew steadily"),
        }
        for m, s in series.items():
            if not s:
                continue
            b = mean(v for t, v in s if t < 60)
            a = mean(v for t, v in s[-4:])
            if m in rules and rules[m][1](b, a):
                cause, _, text = rules[m]
                self._ev("query_metrics", f"{m}: {text} ({b:.2f} -> {a:.2f})", [cause], 3.0, metric=m, baseline=b, current=a)
            elif m in ("latency_p99_ms", "error_rate") and a > 1.5 * b:
                self._ev("query_metrics", f"{m} rose {a / b:.1f}x over baseline ({b:.3f} -> {a:.3f})", [], 0.0, metric=m, baseline=b, current=a)
        return onset

    def _deployments(self, service, onset):
        deps = self._call("list_deployments", service=service) or []
        hit = None
        for d in deps:
            if onset is not None and 0 <= onset - d["ts"] <= 20:
                cause = "bad_deploy" if d["kind"] == "deploy" else "config_change"
                self._ev("list_deployments", f"{d['kind']} {d['version']} ('{d['change']}') landed {onset - d['ts']}min before onset", [cause], 3.0, **d)
                hit = d
        if not hit:
            self._ev("list_deployments", "no deployment or config change within 20min before onset", [], 0.0)
        return hit

    def _logs(self):
        logs = (self._call("search_logs", level="ERROR") or []) + (self._call("search_logs", level="WARN") or [])
        counts = {c: 0 for c in KEYWORDS}
        for l in logs:
            for c, kws in KEYWORDS.items():
                if any(k in l["msg"].lower() for k in kws):
                    counts[c] += 1
        for c, n in counts.items():
            if n:
                ids = [l["id"] for l in logs if any(k in l["msg"].lower() for k in KEYWORDS[c])][:3]
                self._ev("search_logs", f"{n} log lines match {c} signatures", [c], min(n, 5) * 0.6, count=n, log_ids=ids)
        return logs[0]["msg"] if logs else ""

    def _trace(self, trace_id):
        if not trace_id:
            return
        spans = self._call("get_trace", trace_id=trace_id)
        if spans:
            slow = max(spans, key=lambda s: s["duration_ms"])
            cause = next((c for op, c in SLOW_OP_CAUSE.items() if slow["op"].startswith(op)), None)
            self._ev("get_trace", f"slowest span {slow['op']} took {slow['duration_ms']}ms", [cause] if cause else [], 2.0 if cause else 0.0, span=slow)

    def _history(self, alert, top_log):
        hits = self._call("search_incidents", query=f"{alert} {top_log}", k=3) or []
        for h in hits:
            self._ev("search_incidents", f"similar past incident {h['id']} (sim {h['score']}) had cause {h['cause']}", [h["cause"]], 2.0 * h["score"], incident=h["id"])

    def _report(self, service, onset):
        scores = {c: 0.0 for c in CAUSES}
        cites = {c: [] for c in CAUSES}
        for e in self.evidence:
            for c in e.supports:
                scores[c] += e.weight
                cites[c].append(e.id)
        total = sum(scores.values()) or 1.0
        ranked = sorted((c for c in CAUSES if scores[c] > 0), key=lambda c: -scores[c])
        hyps = [Hypothesis(cause=c, title=CAUSES[c], score=round(scores[c], 3), confidence=round(scores[c] / total, 3), evidence_ids=cites[c]) for c in ranked]
        next_steps, remediation = ["Gather more telemetry; no strong hypothesis found."], {"action": "none", "requires_approval": True, "status": "not_proposed"}
        if hyps:
            rb = self._call("get_runbook", cause=hyps[0].cause)
            if rb:
                next_steps = [rb["text"]]
                self._ev("get_runbook", f"runbook for {hyps[0].cause}", [], 0.0)
            remediation = {"action": f"Follow runbook for {hyps[0].cause}", "requires_approval": True, "status": "pending_approval"}
        top = hyps[0] if hyps else None
        summary = (f"Most likely root cause: {top.title} (confidence {top.confidence:.0%}), citing {', '.join(top.evidence_ids)}." if top else "No root cause identified.")
        if self.failures:
            summary += f" Note: {len(self.failures)} tool call(s) failed; conclusions may be incomplete."
        return Report(service=service, onset=onset, summary=summary, hypotheses=hyps, next_steps=next_steps, remediation=remediation,
                      tools_used=sorted(set(self.tools_used)), failures=self.failures), self.evidence
