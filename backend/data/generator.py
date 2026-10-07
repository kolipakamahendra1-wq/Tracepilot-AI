"""Seeded synthetic incidents: telemetry, deployments, history, runbooks.

Time is minutes from the start of a 180-minute window; onset is at ONSET.
"""
import random
from dataclasses import dataclass, field

ONSET = 120
WINDOW = 180
STEP = 5
SERVICES = ["checkout-api", "payment-service", "inventory-service", "search-api", "user-service", "orders-api"]

CAUSES = {
    "bad_deploy": "Faulty code release",
    "db_pool_exhaustion": "Database connection pool exhaustion",
    "cache_miss_storm": "Cache miss storm / cache invalidation",
    "dependency_timeout": "Downstream dependency timeout",
    "memory_leak": "Memory leak / GC pressure",
    "config_change": "Bad configuration change",
}

LOG_SIGNATURES = {
    "bad_deploy": ["NullPointerException in handler after upgrade to {ver}", "unexpected response schema from new code path", "regression detected in {svc} handler: unhandled exception"],
    "db_pool_exhaustion": ["timeout waiting for connection from pool (pool exhausted)", "HikariPool: no available connections, 0 idle", "db connection pool saturated, requests queued"],
    "cache_miss_storm": ["cache miss rate high, falling back to database", "redis keys evicted, cold cache stampede", "cache invalidation flushed all keys"],
    "dependency_timeout": ["upstream payment-gateway timed out after 5000ms", "circuit breaker open for downstream dependency", "read timeout calling external dependency"],
    "memory_leak": ["GC overhead limit warning, heap usage 92%", "OutOfMemoryWarning: heap nearly exhausted", "long GC pause 1800ms"],
    "config_change": ["worker thread pool size reduced, request queue growing", "config reload applied: max_workers changed", "rejecting requests: queue full after config update"],
}
NOISE_LOGS = ["request completed in {n}ms", "health check ok", "user login user{n}@example.com", "cache lookup key=item:{n}", "slow request {n}ms", "retrying request", "GET /api/v1/items 200"]
CONFIG_CHANGES = ["thread_pool_size 200->10", "max_workers 64->8", "request_queue_limit 1000->50"]
CODE_CHANGES = ["refactor pricing logic", "new serialization library", "rewrite order validation"]

SLOW_OP = {"db_pool_exhaustion": "db.pool.acquire", "cache_miss_storm": "cache.get", "dependency_timeout": "http.client payment-gateway"}

RUNBOOKS = {
    "bad_deploy": "Runbook: faulty release. Compare versions, review diff, roll back the latest deployment after approval, verify error rate drops.",
    "db_pool_exhaustion": "Runbook: connection pool exhaustion. Check db_pool_in_use, find leaking connections, raise pool size or fix long transactions.",
    "cache_miss_storm": "Runbook: cache miss storm. Check cache hit ratio, warm the cache, rate limit stampede, review recent invalidation.",
    "dependency_timeout": "Runbook: downstream dependency timeout. Check dependency latency, enable circuit breaker, add timeouts and fallbacks, contact owning team.",
    "memory_leak": "Runbook: memory leak. Inspect heap growth and GC pauses, capture heap dump, restart pods gradually, find leaking objects.",
    "config_change": "Runbook: bad configuration change. Diff the last config change, revert config value after approval, confirm queue depth recovers.",
}


@dataclass
class Scenario:
    id: str
    service: str
    cause: str
    alert: str
    logs: list = field(default_factory=list)
    metrics: dict = field(default_factory=dict)
    spans: list = field(default_factory=list)
    deployments: list = field(default_factory=list)

    @property
    def exemplar_trace(self) -> str:
        return f"tr-{self.id}-0"


def _series(rng, base, noise, f):
    return [(t, round(max(0.0, f(t, base) + rng.gauss(0, noise)), 3)) for t in range(0, WINDOW, STEP)]


def _metrics(rng, cause):
    post = lambda t: t >= ONSET
    ramp = lambda t: max(0.0, (t - ONSET + STEP) / (WINDOW - ONSET))

    def latency(t, b):
        if cause in ("bad_deploy", "config_change", "db_pool_exhaustion"):
            return b * (3 if post(t) else 1)
        if cause in ("cache_miss_storm", "dependency_timeout"):
            return b * (2.5 if post(t) else 1)
        return b * (1 + 0.8 * ramp(t))

    return {
        "latency_p99_ms": _series(rng, 200, 10, latency),
        "error_rate": _series(rng, 0.01, 0.003, lambda t, b: b * (8 if post(t) and cause != "memory_leak" else 1)),
        "db_pool_in_use_pct": _series(rng, 40, 4, lambda t, b: 98 if post(t) and cause == "db_pool_exhaustion" else b),
        "cache_hit_ratio": _series(rng, 0.95, 0.01, lambda t, b: 0.3 if post(t) and cause == "cache_miss_storm" else b),
        "dependency_latency_ms": _series(rng, 80, 8, lambda t, b: b * 12 if post(t) and cause == "dependency_timeout" else b),
        "memory_mb": _series(rng, 800, 15, lambda t, b: b * (1 + 1.2 * ramp(t)) if cause == "memory_leak" else b),
        "cpu_pct": _series(rng, 35, 4, lambda t, b: b),
    }


def make_scenario(i: int) -> Scenario:
    rng = random.Random(1000 + i)
    cause = list(CAUSES)[i % len(CAUSES)]
    svc = rng.choice(SERVICES)
    ver = f"v{rng.randint(2, 9)}.{rng.randint(0, 9)}.{rng.randint(0, 9)}"
    prev = f"v1.{rng.randint(1, 9)}.0"
    sid = f"scn-{i:03d}"
    mentions_deploy = cause in ("bad_deploy", "config_change") or rng.random() < 0.3
    alert = f"{svc} latency increased and errors rising" + (" after today's deployment" if mentions_deploy else "")
    s = Scenario(id=sid, service=svc, cause=cause, alert=alert)
    s.metrics = _metrics(rng, cause)

    n = 0

    def add_log(ts, level, msg):
        nonlocal n
        n += 1
        s.logs.append({"id": f"{sid}-log{n}", "ts": ts, "service": svc, "level": level, "msg": msg})

    for t in range(WINDOW):
        if rng.random() < 0.35:
            add_log(t, "INFO", rng.choice(NOISE_LOGS).format(n=rng.randint(1, 900)))
    for _ in range(rng.randint(8, 14)):
        add_log(rng.randint(ONSET, WINDOW - 1), "ERROR", rng.choice(LOG_SIGNATURES[cause]).format(ver=ver, svc=svc))
    if rng.random() < 0.25:  # distractor: a few lines from an unrelated cause
        other = rng.choice([c for c in CAUSES if c != cause])
        for _ in range(2):
            add_log(rng.randint(ONSET, WINDOW - 1), "WARN", rng.choice(LOG_SIGNATURES[other]).format(ver=ver, svc=svc))
    s.logs.sort(key=lambda x: x["ts"])

    if cause in ("bad_deploy", "config_change"):
        kind, change = ("deploy", rng.choice(CODE_CHANGES)) if cause == "bad_deploy" else ("config", rng.choice(CONFIG_CHANGES))
        s.deployments.append({"id": f"{sid}-dep2", "ts": ONSET - rng.randint(1, 6), "service": svc, "kind": kind, "version": ver, "prev_version": prev, "change": change})
    elif rng.random() < 0.5:  # distractor: unrelated deploy well before onset
        s.deployments.append({"id": f"{sid}-dep2", "ts": ONSET - 90, "service": svc, "kind": "deploy", "version": ver, "prev_version": prev, "change": "minor copy fix"})
    s.deployments.append({"id": f"{sid}-dep1", "ts": 5, "service": svc, "kind": "deploy", "version": prev, "prev_version": "v1.0.0", "change": "routine dependency bump"})
    s.deployments.sort(key=lambda d: d["ts"])

    slow = SLOW_OP.get(cause)
    for k in range(6):
        tr = f"tr-{sid}-{k}"
        ops = [("gateway.handle", 20), ("business.logic", 40), (slow or "db.query", 1500 if (k == 0 and slow) else 60)]
        for j, (op, dur) in enumerate(ops):
            s.spans.append({"trace_id": tr, "span_id": f"{tr}-s{j}", "service": svc, "op": op, "duration_ms": dur + rng.randint(0, 10), "error": k == 0 and j == 2 and slow is not None})
    return s


def make_history(n_per_cause: int = 4) -> list[dict]:
    """Past incident reports, independent of eval scenarios (separate seed)."""
    rng = random.Random(7)
    docs = []
    for cause, title in CAUSES.items():
        for k in range(n_per_cause):
            svc = rng.choice(SERVICES)
            sig = rng.choice(LOG_SIGNATURES[cause]).format(ver="v0", svc=svc)
            docs.append({"id": f"INC-{cause[:4]}-{k}", "cause": cause, "service": svc,
                         "text": f"{svc} latency and errors increased. Observed: {sig}. Root cause: {title}. Resolved after following the {cause} runbook."})
    return docs


def make_dataset(n: int = 60) -> list[Scenario]:
    return [make_scenario(i) for i in range(n)]
