# Deployment guide (Google Cloud Run)

Status: the workflow and steps below are written but **have not been run** (no cloud account was available while building).
Everything else (Compose stack with Postgres/pgvector, Redis, Prometheus, Grafana, Jaeger) was run and checked locally.

## Local stack
    TRACEPILOT_API_KEYS="viewkey:viewer:vera,invkey:investigator:ivan,appkey:approver:ada" docker compose up --build
App http://localhost:8080 | Grafana http://localhost:3000 (admin/admin) | Prometheus :9090 | Jaeger :16686.
Enter an API key in the dashboard's key box. With `TRACEPILOT_API_KEYS` unset, auth is off (dev only).
Set `ANTHROPIC_API_KEY` to enable the Claude re-ranking step; set `LANGFUSE_PUBLIC_KEY`/`LANGFUSE_SECRET_KEY` for LLM traces.

## Cloud Run
1. Create a GCP project, enable Cloud Run, Artifact Registry, Secret Manager, Cloud SQL.
2. Artifact Registry repo `tracepilot`; Cloud SQL Postgres 16 with `CREATE EXTENSION vector` allowed; Memorystore/Upstash Redis.
3. Secret Manager secrets: `tracepilot-db-url` (`postgresql+psycopg://...`), `tracepilot-api-keys`, `tracepilot-redis-url`, `anthropic-api-key`.
4. Workload Identity Federation for GitHub; grant the service account Cloud Run Admin, Artifact Registry Writer, Secret Accessor.
5. GitHub repo variables `GCP_PROJECT`, `GCP_REGION`, `FRONTEND_ORIGIN`; secrets `GCP_WORKLOAD_IDENTITY_PROVIDER`, `GCP_SERVICE_ACCOUNT`.
6. Run the "Deploy to Cloud Run" workflow. Host `frontend/` (static build) on any static host and proxy `/api` to the Cloud Run URL.
7. Cloud Run is publicly reachable (`--allow-unauthenticated`); API-key RBAC is what protects it, so always set `tracepilot-api-keys`.
   `/prom` is unauthenticated; restrict it at the network layer or scrape it from inside the VPC.
