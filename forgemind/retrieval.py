"""Hybrid retrieval: BM25 + vector similarity, merged with reciprocal rank fusion."""
import math
import re
from collections import Counter
from dataclasses import dataclass

import numpy as np

STOPWORDS = frozenset(
    "a an and are as at be by can do does for from how i if in is it its many much of on or our "
    "that the their there this to was we what when where which who why will with you your".split())


def stem(word: str) -> str:
    for suffix in ("ing", "ed", "es", "s"):
        if word.endswith(suffix) and len(word) - len(suffix) >= 3 and not word.endswith("ss"):
            word = word[:-len(suffix)]
            break
    return word[:-1] if word.endswith("e") and len(word) > 3 else word


def tokenize(text: str):
    return [stem(w) for w in re.findall(r"[a-z0-9]+", text.lower()) if w not in STOPWORDS]


@dataclass
class Hit:
    chunk_id: int
    doc_id: str
    title: str
    position: int
    text: str
    score: float
    coverage: float  # share of query terms present in the chunk
    similarity: float


def bm25(query_terms, docs, k1=1.5, b=0.75):
    n = len(docs)
    avg = sum(map(len, docs)) / n or 1
    df = Counter(t for d in docs for t in set(d))
    scores = []
    for doc in docs:
        tf, score = Counter(doc), 0.0
        for t in set(query_terms):
            if tf[t]:
                idf = math.log(1 + (n - df[t] + 0.5) / (df[t] + 0.5))
                score += idf * tf[t] * (k1 + 1) / (tf[t] + k1 * (1 - b + b * len(doc) / avg))
        scores.append(score)
    return scores


class Retriever:
    def __init__(self, store, embedder):
        self.store, self.embedder = store, embedder

    def search(self, principal, query, top_k=5):
        rows = self.store.visible_chunks(principal, self.embedder.name)
        terms = set(tokenize(query))
        if not rows or not terms:
            return []
        # Title words count as part of each chunk.
        docs = [tokenize(r["title"] + " " + r["text"]) for r in rows]
        lexical = bm25(list(terms), docs)
        matrix = np.stack([np.frombuffer(r["vector"], dtype=np.float32) for r in rows])
        similarity = matrix @ self.embedder.embed([query])[0]

        fused = [0.0] * len(rows)
        for scores in (lexical, similarity):
            order = sorted(range(len(rows)), key=lambda i: -scores[i])
            for rank, i in enumerate(order):
                if scores[i] > 0:
                    fused[i] += 1 / (60 + rank + 1)

        best = sorted(range(len(rows)), key=lambda i: (-fused[i], -lexical[i]))[:top_k]
        return [Hit(rows[i]["id"], rows[i]["doc_id"], rows[i]["title"], rows[i]["position"],
                    rows[i]["text"], fused[i], len(terms & set(docs[i])) / len(terms),
                    float(similarity[i]))
                for i in best if fused[i] > 0]
