import os
import time

from fastapi import Depends, FastAPI, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy import JSON, Float, ForeignKey, Integer, String, create_engine, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column
from sqlalchemy.pool import StaticPool

from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from backend.agents.graph import run_investigation
from backend.api.auth import Principal, load_keys, require
from backend.api.ratelimit import RateLimiter
from backend.data.generator import make_dataset, make_history
from backend.observability import INVESTIGATION_SECONDS, INVESTIGATIONS, RATE_LIMITED, setup_tracing
from backend.retrieval.index import Index
from backend.tools.toolbox import ToolBox


class Base(DeclarativeBase):
    pass


class IncidentRow(Base):
    __tablename__ = "incidents"
    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String)
    scenario_id: Mapped[str] = mapped_column(String)


class InvestigationRow(Base):
    __tablename__ = "investigations"
    id: Mapped[int] = mapped_column(primary_key=True)
    incident_id: Mapped[int] = mapped_column(ForeignKey("incidents.id"))
    status: Mapped[str] = mapped_column(String)
    duration_s: Mapped[float] = mapped_column(Float)
    report: Mapped[dict] = mapped_column(JSON)
    evidence: Mapped[list] = mapped_column(JSON)
    audit: Mapped[list] = mapped_column(JSON)
    approval: Mapped[str] = mapped_column(String, default="pending")


class IncidentIn(BaseModel):
    title: str
    scenario_id: str


class InvestigationIn(BaseModel):
    incident_id: int


class ApprovalIn(BaseModel):
    approved: bool
    comment: str = ""


def _default_llm():
    from backend.agents.llm import make_llm

    return make_llm()


def create_app(db_url: str | None = None, llm=None, history=None, api_keys: str | None = None, rate_limit: int | None = None) -> FastAPI:
    """llm/history/api_keys/rate_limit default to environment configuration."""
    db_url = db_url or os.getenv("TRACEPILOT_DB", "sqlite:///tracepilot.db")
    llm = llm if llm is not None else _default_llm()
    keys = load_keys(api_keys if api_keys is not None else os.getenv("TRACEPILOT_API_KEYS"))
    limiter = RateLimiter(rate_limit or int(os.getenv("RATE_LIMIT_PER_MIN", "30")), os.getenv("REDIS_URL"))
    viewer, investigator, approver = (require(r, keys) for r in ("viewer", "investigator", "approver"))
    kw = {"connect_args": {"check_same_thread": False}, "poolclass": StaticPool} if ":memory:" in db_url else {}
    engine = create_engine(db_url, **kw)
    Base.metadata.create_all(engine)
    scenarios = {s.id: s for s in make_dataset()}
    if history is None:
        docs = make_history()
        if db_url.startswith("postgresql"):
            from backend.retrieval.pgvector_index import PgVectorIndex

            history = PgVectorIndex(db_url, docs)
        else:
            history = Index(docs)
    app = FastAPI(title="TracePilot AI", description="Read-only AI incident investigation and root-cause analysis")

    setup_tracing(app)
    app.add_middleware(CORSMiddleware, allow_origins=os.getenv("CORS_ORIGINS", "http://localhost:5173").split(","), allow_methods=["*"], allow_headers=["*"])

    @app.get("/scenarios")
    def list_scenarios(_p: Principal = Depends(viewer)):
        return [{"id": s.id, "service": s.service, "alert": s.alert} for s in scenarios.values()]

    @app.get("/investigations")
    def list_investigations(_p: Principal = Depends(viewer)):
        with Session(engine) as db:
            return [{"id": r.id, "incident_id": r.incident_id, "status": r.status, "approval": r.approval,
                     "top_cause": r.report["hypotheses"][0]["cause"] if r.report["hypotheses"] else None}
                    for r in db.scalars(select(InvestigationRow).order_by(InvestigationRow.id.desc())).all()]

    @app.post("/incidents", status_code=201)
    def create_incident(body: IncidentIn, _p: Principal = Depends(investigator)):
        if body.scenario_id not in scenarios:
            raise HTTPException(404, "unknown scenario_id")
        with Session(engine) as db:
            row = IncidentRow(title=body.title, scenario_id=body.scenario_id)
            db.add(row)
            db.commit()
            return {"id": row.id, "title": row.title, "scenario_id": row.scenario_id}

    @app.get("/incidents/{incident_id}")
    def get_incident(incident_id: int, _p: Principal = Depends(viewer)):
        with Session(engine) as db:
            row = db.get(IncidentRow, incident_id)
            if not row:
                raise HTTPException(404, "incident not found")
            return {"id": row.id, "title": row.title, "scenario_id": row.scenario_id}

    @app.post("/investigations", status_code=201)
    def start_investigation(body: InvestigationIn, p: Principal = Depends(investigator)):
        if not limiter.allow(p.name):
            RATE_LIMITED.inc()
            raise HTTPException(429, "rate limit exceeded")
        with Session(engine) as db:
            inc = db.get(IncidentRow, body.incident_id)
            if not inc:
                raise HTTPException(404, "incident not found")
            scn = scenarios[inc.scenario_id]
            audit: list = []
            t0 = time.perf_counter()
            report, evidence = run_investigation(ToolBox(scn, history, audit), f"{inc.title} {scn.alert}", scn.exemplar_trace, llm)
            audit.append({"tool": "investigation", "args": {"requested_by": p.name}, "status": "ok"})
            row = InvestigationRow(incident_id=inc.id, status="awaiting_approval" if report.hypotheses else "needs_human",
                                   duration_s=time.perf_counter() - t0, report=report.model_dump(),
                                   evidence=[e.model_dump() for e in evidence], audit=audit)
            INVESTIGATIONS.labels(row.status).inc()
            INVESTIGATION_SECONDS.observe(row.duration_s)
            db.add(row)
            db.commit()
            return {"id": row.id, "status": row.status}

    def _inv(db, inv_id):
        row = db.get(InvestigationRow, inv_id)
        if not row:
            raise HTTPException(404, "investigation not found")
        return row

    @app.get("/investigations/{inv_id}")
    def get_investigation(inv_id: int, _p: Principal = Depends(viewer)):
        with Session(engine) as db:
            r = _inv(db, inv_id)
            return {"id": r.id, "incident_id": r.incident_id, "status": r.status, "approval": r.approval, "report": r.report}

    @app.get("/investigations/{inv_id}/evidence")
    def get_evidence(inv_id: int, _p: Principal = Depends(viewer)):
        with Session(engine) as db:
            return _inv(db, inv_id).evidence

    @app.post("/investigations/{inv_id}/approve")
    def approve(inv_id: int, body: ApprovalIn, p: Principal = Depends(approver)):
        """Records the human decision only. TracePilot never executes remediation itself."""
        with Session(engine) as db:
            r = _inv(db, inv_id)
            r.approval = f"{'approved' if body.approved else 'rejected'} by {p.name}"
            r.audit = [*r.audit, {"tool": "approval", "args": {"approver": p.name, "comment": body.comment}, "status": r.approval}]
            db.commit()
            return {"approval": r.approval, "executed": False}

    @app.get("/metrics")
    def metrics(_p: Principal = Depends(viewer)):
        with Session(engine) as db:
            rows = db.scalars(select(InvestigationRow)).all()
            n = len(rows)
            return {"investigations": n,
                    "avg_duration_s": sum(r.duration_s for r in rows) / n if n else 0.0,
                    "agent_failures": sum(bool(r.report["failures"]) for r in rows),
                    "human_escalations": sum(r.status == "needs_human" for r in rows),
                    "awaiting_approval": sum(r.status == "awaiting_approval" for r in rows)}

    @app.get("/prom", include_in_schema=False)
    def prometheus():
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    @app.get("/traces/{trace_id}")
    def trace(trace_id: str, _p: Principal = Depends(viewer)):
        for s in scenarios.values():
            if trace_id.startswith(f"tr-{s.id}-"):
                return ToolBox(s, history).call("get_trace", trace_id=trace_id)
        raise HTTPException(404, "trace not found")

    return app


app = create_app()
