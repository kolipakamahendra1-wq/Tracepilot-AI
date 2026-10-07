"""Integration test, runs only when TEST_POSTGRES_URL is set (e.g. postgresql+psycopg://user:pw@localhost:5432/db)."""
import os

import pytest

from backend.data.generator import make_history

URL = os.getenv("TEST_POSTGRES_URL")
pytestmark = pytest.mark.skipif(not URL, reason="TEST_POSTGRES_URL not set")


def test_pgvector_retrieves_matching_cause():
    from backend.retrieval.pgvector_index import PgVectorIndex

    idx = PgVectorIndex(URL, make_history())
    hits = idx.search("timeout waiting for connection from pool (pool exhausted)", k=3)
    assert hits and hits[0]["cause"] == "db_pool_exhaustion"


def test_api_runs_on_postgres():
    from fastapi.testclient import TestClient

    from backend.api.main import create_app

    c = TestClient(create_app(URL))
    inc = c.post("/incidents", json={"title": "t", "scenario_id": "scn-001"}).json()
    inv = c.post("/investigations", json={"incident_id": inc["id"]}).json()
    assert c.get(f"/investigations/{inv['id']}").json()["report"]["hypotheses"]
