"""LangGraph investigation agent: gather (read-only tools) -> reason (Claude) -> validate (grounding check).

Claude only re-ranks and explains hypotheses. Anything it says that is not grounded in recorded
evidence is discarded, and without an LLM the deterministic ranking is returned unchanged.
"""
import json
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from backend.agents.investigator import CAUSES, Hypothesis, Investigator
from backend.tools.toolbox import ToolBox

SYSTEM = (
    "You are an incident root-cause analyst. You may ONLY use the evidence provided. "
    "Rank the candidate root causes and explain each in one sentence, citing evidence ids. "
    "Never propose executing changes. Reply with JSON only: "
    '{"summary": str, "ranking": [{"cause": str, "evidence_ids": [str], "explanation": str}]}'
)


class State(TypedDict, total=False):
    alert: str
    trace: str | None
    report: Any
    evidence: list
    proposal: dict | None
    llm: dict


def _prompt(state: State) -> str:
    ev = "\n".join(f"{e.id} [{e.tool}] {e.summary}" for e in state["evidence"])
    cands = ", ".join(h.cause for h in state["report"].hypotheses)
    return f"Alert: {state['alert']}\nValid causes: {', '.join(CAUSES)}\nDeterministic candidates: {cands}\nEvidence:\n{ev}"


def build_graph(toolbox: ToolBox, llm=None):
    def gather(state: State) -> State:
        report, evidence = Investigator(toolbox).investigate(state["alert"], state.get("trace"))
        return {"report": report, "evidence": evidence, "proposal": None, "llm": {"used": False}}

    def reason(state: State) -> State:
        if llm is None or not state["report"].hypotheses:
            return {}
        try:
            res = llm.complete(SYSTEM, _prompt(state))
            raw = res.text[res.text.index("{"): res.text.rindex("}") + 1]
            return {"proposal": json.loads(raw), "llm": {"used": True, "model": llm.model, "input_tokens": res.input_tokens,
                                                          "output_tokens": res.output_tokens, "cost_usd": round(res.cost_usd, 6)}}
        except Exception as e:  # noqa: BLE001 - LLM failure falls back to deterministic ranking
            state["report"].failures.append(f"llm failed: {e}")
            return {}

    def validate(state: State) -> State:
        report, prop = state["report"], state.get("proposal")
        if not prop:
            return {}
        known = {e.id for e in state["evidence"]}
        base = {h.cause: h for h in report.hypotheses}
        ranked: list[Hypothesis] = []
        for item in prop.get("ranking", []):
            cause, cited = item.get("cause"), [i for i in item.get("evidence_ids", []) if i in known]
            if cause in base and cited and all(h.cause != cause for h in ranked):
                ranked.append(base[cause].model_copy(update={"evidence_ids": cited, "explanation": str(item.get("explanation", ""))}))
        if not ranked:  # nothing grounded: keep deterministic result
            report.failures.append("llm output ungrounded; kept deterministic ranking")
            return {}
        ranked += [h for c, h in base.items() if all(r.cause != c for r in ranked)]
        report.hypotheses = ranked
        top = ranked[0]
        report.summary = f"Most likely root cause: {top.title}, citing {', '.join(top.evidence_ids)}. {top.explanation}".strip()
        return {}

    def finish(state: State) -> State:
        state["report"].llm = state.get("llm", {"used": False})
        return {}

    g = StateGraph(State)
    for name, fn in [("gather", gather), ("reason", reason), ("validate", validate), ("finish", finish)]:
        g.add_node(name, fn)
    g.add_edge(START, "gather")
    g.add_edge("gather", "reason")
    g.add_edge("reason", "validate")
    g.add_edge("validate", "finish")
    g.add_edge("finish", END)
    return g.compile()


def run_investigation(toolbox: ToolBox, alert: str, trace: str | None = None, llm=None):
    out = build_graph(toolbox, llm).invoke({"alert": alert, "trace": trace})
    return out["report"], out["evidence"]
