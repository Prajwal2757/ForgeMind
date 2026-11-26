"""Hybrid retrieval (BM25 + vectors, reciprocal rank fusion) over a cached per-tenant index.

Permissions are applied before scoring, and BM25 statistics are computed over visible chunks
only, so hidden documents do not influence results.
"""
import json
import math
import re
import threading
from collections import Counter, OrderedDict
from dataclasses import dataclass

import numpy as np

from .store import visible

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


class Index:
    def __init__(self, rows):
        self.meta = [(r["id"], r["doc_id"], r["title"], r["position"], r["text"]) for r in rows]
        self.matrix = (np.frombuffer(b"".join(r["vector"] for r in rows), dtype=np.float32)
                       .reshape(len(rows), -1) if rows else None)
        # Chunks sharing an access list are checked once per query.
        keys = {}
        self.acl = np.array([keys.setdefault(r["groups"], len(keys)) for r in rows], dtype=np.int32)
        self.acl_groups = [json.loads(k) for k in keys]
        self.lengths = np.zeros(len(rows), dtype=np.float32)
        postings = {}
        for i, r in enumerate(rows):
            tokens = tokenize(r["title"] + " " + r["text"])
            self.lengths[i] = len(tokens)
            for term, count in Counter(tokens).items():
                ids, counts = postings.setdefault(term, ([], []))
                ids.append(i)
                counts.append(count)
        self.postings = {t: (np.array(i, np.int32), np.array(c, np.float32))
                         for t, (i, c) in postings.items()}

    def mask(self, principal):
        allowed = np.array([visible(principal, g) for g in self.acl_groups], dtype=bool)
        return allowed[self.acl]

    def score(self, terms, mask, k1=1.5, b=0.75):
        scores, matched = np.zeros(len(self.meta)), np.zeros(len(self.meta))
        n = int(mask.sum())
        avg = float(self.lengths[mask].mean()) or 1.0
        for term in terms:
            if term not in self.postings:
                continue
            ids, tf = self.postings[term]
            keep = mask[ids]
            ids, tf = ids[keep], tf[keep]
            if not len(ids):
                continue
            idf = math.log(1 + (n - len(ids) + 0.5) / (len(ids) + 0.5))
            scores[ids] += idf * tf * (k1 + 1) / (tf + k1 * (1 - b + b * self.lengths[ids] / avg))
            matched[ids] += 1
        return scores, matched / len(terms)


class Retriever:
    def __init__(self, store, embedder, max_tenants=16, max_chunks=100000):
        self.store, self.embedder = store, embedder
        self.max_tenants, self.max_chunks = max_tenants, max_chunks
        self._cache = OrderedDict()
        self._lock = threading.Lock()  # guards the two dicts only
        self._building = {}

    def index(self, tenant) -> Index:
        revision = self.store.revision(tenant)
        with self._lock:
            cached = self._cache.get(tenant)
            if cached and cached[0] >= revision:
                self._cache.move_to_end(tenant)
                return cached[1]
            build_lock = self._building.setdefault(tenant, threading.Lock())
        with build_lock:  # one build per tenant; other tenants are not blocked
            with self._lock:
                cached = self._cache.get(tenant)
            if cached and cached[0] >= revision:
                return cached[1]
            revision, rows = self.store.snapshot(tenant, self.embedder.name)
            index = Index(rows)
            with self._lock:
                self._cache[tenant] = (revision, index)
                self._cache.move_to_end(tenant)
                while len(self._cache) > 1 and (
                        len(self._cache) > self.max_tenants
                        or sum(len(i.meta) for _, i in self._cache.values()) > self.max_chunks):
                    self._cache.popitem(last=False)
        return index

    def search(self, principal, query, top_k=5):
        terms = set(tokenize(query))
        index = self.index(principal.tenant)
        if not index.meta or not terms:
            return []
        mask = index.mask(principal)
        if not mask.any():
            return []
        lexical, coverage = index.score(terms, mask)
        similarity = index.matrix @ self.embedder.embed([query])[0]
        similarity = np.where(mask, similarity, 0.0)

        fused = np.zeros(len(index.meta))
        for scores in (lexical, similarity):
            positive = np.flatnonzero(scores > 0)
            order = positive[np.argsort(-scores[positive], kind="stable")][:200]
            fused[order] += 1.0 / (61 + np.arange(len(order)))

        candidates = np.flatnonzero(fused > 0)
        best = candidates[np.lexsort((candidates, -lexical[candidates], -fused[candidates]))][:top_k]
        hits = []
        for i in best:
            chunk_id, doc_id, title, position, text = index.meta[i]
            hits.append(Hit(chunk_id, doc_id, title, position, text, float(fused[i]),
                            float(coverage[i]), float(similarity[i])))
        return hits
