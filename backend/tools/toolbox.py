"""Read-only diagnostic tools behind an allowlist, with PII masking and an audit log."""
import re

from backend.data.generator import RUNBOOKS, Scenario
from backend.observability import TOOL_CALLS
from backend.retrieval.index import Index

ALLOWED_TOOLS = {"search_logs", "query_metrics", "get_trace", "list_deployments", "search_incidents", "get_runbook", "compare_service_versions"}
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
_IP = re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b")


def mask_pii(value):
    if isinstance(value, str):
        return _IP.sub("[IP]", _EMAIL.sub("[EMAIL]", value))
    if isinstance(value, (list, tuple)):
        return [mask_pii(v) for v in value]
    if isinstance(value, dict):
        return {k: mask_pii(v) for k, v in value.items()}
    return value


class ToolNotAllowed(Exception):
    pass


class ToolBox:
    def __init__(self, scenario: Scenario, history: Index, audit: list | None = None):
        self.s = scenario
        self.history = history
        self.audit = audit if audit is not None else []

    def call(self, name: str, **args):
        if name not in ALLOWED_TOOLS:
            self.audit.append({"tool": name, "args": args, "status": "denied"})
            TOOL_CALLS.labels(name, "denied").inc()
            raise ToolNotAllowed(name)
        try:
            result = mask_pii(getattr(self, name)(**args))
        except Exception as e:
            self.audit.append({"tool": name, "args": args, "status": f"error: {e}"})
            TOOL_CALLS.labels(name, "error").inc()
            raise
        self.audit.append({"tool": name, "args": args, "status": "ok"})
        TOOL_CALLS.labels(name, "ok").inc()
        return result

    # --- tools (all read-only) ---
    def search_logs(self, query: str = "", level: str | None = None, limit: int = 50):
        words = query.lower().split()
        out = [l for l in self.s.logs if (not level or l["level"] == level) and all(w in l["msg"].lower() for w in words)]
        return out[:limit]

    def query_metrics(self, metric: str, start: int = 0, end: int = 10**6):
        if metric not in self.s.metrics:
            raise KeyError(f"unknown metric {metric}")
        return [(t, v) for t, v in self.s.metrics[metric] if start <= t <= end]

    def get_trace(self, trace_id: str):
        spans = [sp for sp in self.s.spans if sp["trace_id"] == trace_id]
        if not spans:
            raise KeyError(f"unknown trace {trace_id}")
        return spans

    def list_deployments(self, service: str | None = None):
        return [d for d in self.s.deployments if not service or d["service"] == service]

    def search_incidents(self, query: str, k: int = 3):
        return self.history.search(query, k)

    def get_runbook(self, cause: str):
        return {"cause": cause, "text": RUNBOOKS[cause]}

    def compare_service_versions(self, service: str, version_a: str, version_b: str):
        deps = {d["version"]: d for d in self.s.deployments if d["service"] == service}
        newer = deps.get(version_b) or deps.get(version_a)
        if newer is None:
            raise KeyError("versions not found")
        lat = self.s.metrics["latency_p99_ms"]
        before = [v for t, v in lat if t < newer["ts"]]
        after = [v for t, v in lat if t >= newer["ts"]]
        return {"change": newer["change"], "kind": newer["kind"], "deployed_at": newer["ts"],
                "p99_before": round(sum(before) / max(len(before), 1), 1), "p99_after": round(sum(after) / max(len(after), 1), 1)}
