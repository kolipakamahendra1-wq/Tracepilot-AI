"""pgvector-backed incident/runbook index. Same search() interface as retrieval.index.Index.

Embeddings: sentence-transformers (all-MiniLM-L6-v2, 384-d) when EMBEDDINGS=sentence-transformers and installed,
otherwise a dependency-free 384-d hashed n-gram embedding.
"""
import os

import numpy as np
import psycopg
from pgvector.psycopg import register_vector
from sklearn.feature_extraction.text import HashingVectorizer

DIM = 384


def make_embedder():
    if os.getenv("EMBEDDINGS") == "sentence-transformers":
        from sentence_transformers import SentenceTransformer

        model = SentenceTransformer("sentence-transformers/all-MiniLM-L6-v2")
        return lambda text: model.encode(text, normalize_embeddings=True)
    hv = HashingVectorizer(n_features=DIM, ngram_range=(1, 2), stop_words="english", alternate_sign=False, norm="l2")
    return lambda text: hv.transform([text]).toarray()[0]


class PgVectorIndex:
    def __init__(self, url: str, docs: list[dict], embed=None):
        self.url = url.replace("postgresql+psycopg://", "postgresql://")
        self.embed = embed or make_embedder()
        with psycopg.connect(self.url, autocommit=True) as c:
            c.execute("CREATE EXTENSION IF NOT EXISTS vector")
        with psycopg.connect(self.url) as c:
            register_vector(c)
            c.execute(f"CREATE TABLE IF NOT EXISTS kb_docs (id text PRIMARY KEY, cause text, service text, text text, embedding vector({DIM}))")
            for d in docs:
                c.execute("INSERT INTO kb_docs VALUES (%s,%s,%s,%s,%s) ON CONFLICT (id) DO UPDATE SET text=EXCLUDED.text, embedding=EXCLUDED.embedding",
                          (d["id"], d.get("cause"), d.get("service"), d["text"], np.asarray(self.embed(d["text"]), dtype=np.float32)))

    def search(self, query: str, k: int = 3) -> list[dict]:
        q = np.asarray(self.embed(query), dtype=np.float32)
        with psycopg.connect(self.url) as c:
            register_vector(c)
            rows = c.execute("SELECT id, cause, service, text, 1 - (embedding <=> %s) AS sim FROM kb_docs ORDER BY embedding <=> %s LIMIT %s",
                             (q, q, k)).fetchall()
        return [{"id": r[0], "cause": r[1], "service": r[2], "text": r[3], "score": round(float(r[4]), 4)} for r in rows if r[4] > 0]
