"""Run the investigator over all synthetic incidents and print metrics."""
import time

from backend.agents.graph import run_investigation
from backend.data.generator import make_dataset, make_history
from backend.retrieval.index import Index
from backend.tools.toolbox import ToolBox

CORE = {"query_metrics", "search_logs", "list_deployments", "search_incidents", "get_trace", "get_runbook"}
EXPECTED_EXTRA = {"bad_deploy": {"compare_service_versions"}, "config_change": {"compare_service_versions"}}


def evaluate(n: int = 60, llm=None) -> dict:
    idx, rows = Index(make_history()), []
    for s in make_dataset(n):
        t0 = time.perf_counter()
        try:
            r, ev = run_investigation(ToolBox(s, idx), s.alert, s.exemplar_trace, llm)
            done = True
        except Exception:  # noqa: BLE001
            rows.append({"done": False})
            continue
        causes = [h.cause for h in r.hypotheses]
        ids = {e.id for e in ev}
        cited = [i for h in r.hypotheses for i in h.evidence_ids]
        expected = CORE | EXPECTED_EXTRA.get(s.cause, set())
        used = set(r.tools_used)
        rows.append({"done": done, "top1": causes[:1] == [s.cause], "top3": s.cause in causes[:3],
                     "cite": sum(i in ids for i in cited) / max(len(cited), 1),
                     "tools": len(used & expected) / len(used | expected),
                     "halluc": sum(not h.evidence_ids for h in r.hypotheses) / max(len(r.hypotheses), 1),
                     "lat": time.perf_counter() - t0, "cost": r.llm.get("cost_usd", 0.0)})
    ok = [r for r in rows if r["done"]]
    avg = lambda k: round(sum(r[k] for r in ok) / max(len(ok), 1), 4)
    return {"incidents": n, "task_completion_rate": round(len(ok) / n, 4), "top1_accuracy": avg("top1"), "top3_recall": avg("top3"),
            "evidence_citation_accuracy": avg("cite"), "tool_selection_accuracy": avg("tools"), "hallucination_rate": avg("halluc"),
            "avg_latency_s": avg("lat"), "token_cost_usd": round(sum(r.get("cost", 0) for r in ok), 6)}


if __name__ == "__main__":
    for k, v in evaluate().items():
        print(f"{k:28s} {v}")
