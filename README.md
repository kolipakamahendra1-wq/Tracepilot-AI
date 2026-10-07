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

## Free LLM (no paid API)
Any OpenAI-compatible endpoint works; if `LLM_BASE_URL` is set it is used instead of Claude.

    # Ollama (local, free, no key)
    LLM_BASE_URL=http://localhost:11434/v1 LLM_MODEL=llama3.2:3b uvicorn backend.api.main:app
    # Docker Compose reaching Ollama on the host
    LLM_BASE_URL=http://host.docker.internal:11434/v1 LLM_MODEL=llama3.2:3b docker compose up --build
    # Groq / OpenRouter (":free" models) / Gemini free tier: set LLM_BASE_URL, LLM_MODEL, LLM_API_KEY

With no LLM configured the deterministic ranking is used. Claude: set `ANTHROPIC_API_KEY` instead.
Tested for real with Ollama `llama3.2:3b` on 4 incidents: all 4 top causes correct, grounded explanations, but 20-70 s per
investigation on CPU (set `LLM_TIMEOUT_S` if your model is slower).

## Architecture
React dashboard -> FastAPI (API-key RBAC, Redis rate limit) -> LangGraph agent (gather -> Claude reason -> grounding validate)
-> read-only allowlisted tools -> logs/metrics/traces/deployments + pgvector incident KB. Postgres stores investigations;
Prometheus/Grafana/OpenTelemetry(Jaeger)/Langfuse provide observability. Roles: viewer < investigator < approver < admin (`X-API-Key`).

## Status
Verified locally (tests + full Docker Compose run): synthetic generator, 7 read-only tools with PII masking and audit,
LangGraph agent with fake-LLM tests (grounded, hallucinated-citation and failure paths), Postgres + pgvector retrieval,
Redis rate limiting, RBAC, Prometheus + Grafana dashboard, OpenTelemetry traces in Jaeger, React dashboard, CI.

Written but NOT exercised: the real Claude call (needs `ANTHROPIC_API_KEY`; the free-model path was run against Ollama), Langfuse export (needs keys), sentence-transformers
embeddings (`EMBEDDINGS=sentence-transformers`, extra install), and the Cloud Run deploy workflow (see docs/DEPLOY.md).
Eval numbers are for the deterministic path on clean synthetic data (token cost 0), so they show wiring, not real-world accuracy.
