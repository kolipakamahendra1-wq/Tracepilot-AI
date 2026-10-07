import pytest
from fastapi.testclient import TestClient

from backend.agents.investigator import Investigator
from backend.api.main import create_app
from backend.data.generator import make_dataset, make_history, make_scenario
from backend.evaluation.run_eval import evaluate
from backend.retrieval.index import Index
from backend.tools.toolbox import ALLOWED_TOOLS, ToolBox, ToolNotAllowed, mask_pii


@pytest.fixture(scope="module")
def idx():
    return Index(make_history())


def test_generator_deterministic_and_sized():
    assert make_scenario(3).logs == make_scenario(3).logs
    assert len(make_dataset()) == 60
    assert len({s.cause for s in make_dataset()}) == 6


def test_allowlist_blocks_writes_and_audits(idx):
    tb = ToolBox(make_scenario(0), idx)
    with pytest.raises(ToolNotAllowed):
        tb.call("restart_service", target="x")
    assert tb.audit[-1]["status"] == "denied"
    assert all(not t.startswith(("restart", "rollback", "delete")) for t in ALLOWED_TOOLS)


def test_pii_masking():
    assert mask_pii("login a@b.com from 10.0.0.1") == "login [EMAIL] from [IP]"
    s = make_scenario(0)
    logs = ToolBox(s, Index(make_history())).call("search_logs", query="login")
    assert all("@" not in l["msg"] for l in logs)


def test_report_cites_only_real_evidence_and_never_executes(idx):
    s = make_scenario(1)
    r, ev = Investigator(ToolBox(s, idx)).investigate(s.alert, s.exemplar_trace)
    ids = {e.id for e in ev}
    assert r.hypotheses and all(set(h.evidence_ids) <= ids for h in r.hypotheses)
    assert r.hypotheses[0].cause == s.cause
    assert r.remediation["requires_approval"] and r.remediation["status"] == "pending_approval"


def test_tool_failure_recovery(idx):
    s = make_scenario(2)
    s.metrics.pop("memory_mb")
    r, _ = Investigator(ToolBox(s, idx)).investigate(s.alert, s.exemplar_trace)
    assert any("memory_mb" in f for f in r.failures) and r.hypotheses


def test_api_flow():
    c = TestClient(create_app("sqlite:///:memory:"))
    inc = c.post("/incidents", json={"title": "Checkout latency up", "scenario_id": "scn-000"}).json()
    inv = c.post("/investigations", json={"incident_id": inc["id"]}).json()
    assert inv["status"] == "awaiting_approval"
    assert c.get(f"/investigations/{inv['id']}").json()["report"]["hypotheses"]
    assert c.get(f"/investigations/{inv['id']}/evidence").json()
    assert c.post(f"/investigations/{inv['id']}/approve", json={"approved": True, "comment": "ok"}).json()["executed"] is False
    assert c.get("/metrics").json()["investigations"] == 1
    assert c.get("/traces/tr-scn-000-0").status_code == 200
    assert c.get("/incidents/999").status_code == 404


def test_eval_metrics_sane():
    m = evaluate()
    assert m["task_completion_rate"] == 1.0 and m["top3_recall"] >= 0.9 and m["evidence_citation_accuracy"] == 1.0


def test_dashboard_endpoints():
    c = TestClient(create_app("sqlite:///:memory:"))
    assert len(c.get("/scenarios").json()) == 60
    assert c.get("/investigations").json() == []


# --- LangGraph agent with a fake LLM ---
import json

from backend.agents.graph import run_investigation
from backend.agents.llm import LLMResult


class FakeLLM:
    model = "fake"

    def __init__(self, payload=None, boom=False):
        self.payload, self.boom = payload, boom

    def complete(self, system, user):
        if self.boom:
            raise RuntimeError("api down")
        return LLMResult(self.payload if isinstance(self.payload, str) else json.dumps(self.payload), 100, 50, 0.001)


def test_llm_grounded_ranking_is_used(idx):
    s = make_scenario(1)
    base, _ = run_investigation(ToolBox(s, idx), s.alert, s.exemplar_trace)
    top = base.hypotheses[0]
    llm = FakeLLM({"summary": "x", "ranking": [{"cause": top.cause, "evidence_ids": top.evidence_ids[:1], "explanation": "because"}]})
    r, _ = run_investigation(ToolBox(s, idx), s.alert, s.exemplar_trace, llm)
    assert r.llm["used"] and r.llm["cost_usd"] == 0.001
    assert r.hypotheses[0].explanation == "because" and r.hypotheses[0].evidence_ids == top.evidence_ids[:1]
    assert len(r.hypotheses) == len(base.hypotheses)


def test_llm_hallucinated_citations_rejected(idx):
    s = make_scenario(1)
    base, _ = run_investigation(ToolBox(s, idx), s.alert, s.exemplar_trace)
    bad = {"summary": "x", "ranking": [{"cause": "memory_leak", "evidence_ids": ["E999"], "explanation": "made up"}, {"cause": "not_a_cause", "evidence_ids": ["E1"], "explanation": ""}]}
    r, _ = run_investigation(ToolBox(s, idx), s.alert, s.exemplar_trace, FakeLLM(bad))
    assert [h.cause for h in r.hypotheses] == [h.cause for h in base.hypotheses]
    assert any("ungrounded" in f for f in r.failures)


def test_llm_failure_falls_back(idx):
    s = make_scenario(1)
    r, _ = run_investigation(ToolBox(s, idx), s.alert, s.exemplar_trace, FakeLLM(boom=True))
    assert r.hypotheses[0].cause == s.cause and any("llm failed" in f for f in r.failures)
    r2, _ = run_investigation(ToolBox(s, idx), s.alert, s.exemplar_trace, FakeLLM("not json"))
    assert r2.hypotheses[0].cause == s.cause


# --- RBAC, rate limit, prometheus ---
KEYS = "v:viewer:vera,i:investigator:ivan,a:approver:ada"


def test_rbac():
    c = TestClient(create_app("sqlite:///:memory:", api_keys=KEYS))
    assert c.get("/scenarios").status_code == 401
    assert c.get("/scenarios", headers={"x-api-key": "bad"}).status_code == 401
    assert c.get("/scenarios", headers={"x-api-key": "v"}).status_code == 200
    body = {"title": "t", "scenario_id": "scn-000"}
    assert c.post("/incidents", json=body, headers={"x-api-key": "v"}).status_code == 403
    inc = c.post("/incidents", json=body, headers={"x-api-key": "i"}).json()
    inv = c.post("/investigations", json={"incident_id": inc["id"]}, headers={"x-api-key": "i"}).json()
    assert c.post(f"/investigations/{inv['id']}/approve", json={"approved": True}, headers={"x-api-key": "i"}).status_code == 403
    r = c.post(f"/investigations/{inv['id']}/approve", json={"approved": True}, headers={"x-api-key": "a"})
    assert r.json()["approval"] == "approved by ada"


def test_rate_limit_and_prometheus():
    c = TestClient(create_app("sqlite:///:memory:", rate_limit=2))
    inc = c.post("/incidents", json={"title": "t", "scenario_id": "scn-000"}).json()
    codes = [c.post("/investigations", json={"incident_id": inc["id"]}).status_code for _ in range(3)]
    assert codes == [201, 201, 429]
    text = c.get("/prom").text
    assert "tracepilot_investigations_total" in text and "tracepilot_tool_calls_total" in text
