"""Embedding providers. All return L2-normalised float32 rows."""
import json
import urllib.request

import numpy as np
from sklearn.feature_extraction.text import HashingVectorizer


class HashingEmbedder:
    """Offline default. Character n-grams: tolerant of word forms, NOT semantic."""
    name = "hashing-char-1024"

    def __init__(self):
        self._v = HashingVectorizer(analyzer="char_wb", ngram_range=(3, 5),
                                    n_features=1024, alternate_sign=True, norm="l2")

    def embed(self, texts):
        return self._v.transform(texts).toarray().astype(np.float32)


class OpenAIEmbedder:
    """Any OpenAI-compatible /embeddings endpoint."""

    def __init__(self, base_url, api_key, model):
        self.url, self.key, self.model = base_url.rstrip("/") + "/embeddings", api_key, model
        self.name = f"openai:{model}"

    def embed(self, texts):
        rows = []
        for i in range(0, len(texts), 64):
            req = urllib.request.Request(
                self.url, json.dumps({"model": self.model, "input": texts[i:i + 64]}).encode(),
                {"Authorization": f"Bearer {self.key}", "Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=60) as resp:
                data = sorted(json.load(resp)["data"], key=lambda d: d["index"])
            rows += [d["embedding"] for d in data]
        m = np.array(rows, dtype=np.float32)
        return m / np.clip(np.linalg.norm(m, axis=1, keepdims=True), 1e-12, None)


def make_embedder(settings):
    if settings.embed_provider == "hashing":
        return HashingEmbedder()
    if settings.embed_provider == "openai":
        return OpenAIEmbedder(settings.openai_base_url, settings.openai_api_key, settings.embed_model)
    raise ValueError(f"unknown embedding provider: {settings.embed_provider}")
