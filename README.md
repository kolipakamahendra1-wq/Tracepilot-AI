# TracePilot AI

AI incident investigation and root-cause analysis system. Given an alert, it queries read-only diagnostic tools
(logs, metrics, traces, deployments, incident history, runbooks), records numbered evidence, ranks root-cause
hypotheses that cite that evidence, and proposes remediation that **requires human approval**. It never changes production.

## Run
    pip install -r requirements.txt
    python -m pytest -q
    python -m backend.evaluation.run_eval
    uvicorn backend.api.main:app --reload     # docs at /docs

## Docker (dashboard at http://localhost:8080)
    docker compose up --build

## Frontend dev
    cd frontend && npm install && npm run dev    # http://localhost:5173, proxies /api to :8000

API: POST /incidents, GET /incidents/{id}, POST /investigations, GET /investigations/{id},
GET /investigations/{id}/evidence, POST /investigations/{id}/approve, GET /metrics, GET /traces/{id}.

## Architecture
React dashboard -> FastAPI (API-key RBAC, Redis rate limit) -> LangGraph agent (gather -> Claude reason -> grounding validate)
-> read-only allowlisted tools -> logs/metrics/traces/deployments + pgvector incident KB. Postgres stores investigations;
Prometheus/Grafana/OpenTelemetry(Jaeger)/Langfuse provide observability. Roles: viewer < investigator < approver < admin (`X-API-Key`).

## Status
Verified locally (tests + full Docker Compose run): synthetic generator, 7 read-only tools with PII masking and audit,
LangGraph agent with fake-LLM tests (grounded, hallucinated-citation and failure paths), Postgres + pgvector retrieval,
Redis rate limiting, RBAC, Prometheus + Grafana dashboard, OpenTelemetry traces in Jaeger, React dashboard, CI.

Written but NOT exercised: the real Claude call (needs `ANTHROPIC_API_KEY`), Langfuse export (needs keys), sentence-transformers
embeddings (`EMBEDDINGS=sentence-transformers`, extra install), and the Cloud Run deploy workflow (see docs/DEPLOY.md).
Eval numbers are for the deterministic path on clean synthetic data (token cost 0), so they show wiring, not real-world accuracy.
