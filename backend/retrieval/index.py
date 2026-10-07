from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity


class Index:
    """TF-IDF index over documents ({'id','text',...}). Swappable for pgvector later."""

    def __init__(self, docs: list[dict]):
        self.docs = docs
        self.vec = TfidfVectorizer(ngram_range=(1, 2), stop_words="english")
        self.matrix = self.vec.fit_transform([d["text"] for d in docs])

    def search(self, query: str, k: int = 3) -> list[dict]:
        sims = cosine_similarity(self.vec.transform([query]), self.matrix)[0]
        order = sims.argsort()[::-1][:k]
        return [{**self.docs[i], "score": round(float(sims[i]), 4)} for i in order if sims[i] > 0]
