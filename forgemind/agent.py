"""The agent loop: plan -> retrieve -> grade -> retry -> answer -> verify citations."""
import re

from .llm import NO_ANSWER

MIN_COVERAGE = 0.5
MIN_SIMILARITY = 0.35

SYNONYMS = {
    "vacation": "leave holiday", "pto": "paid leave", "salary": "compensation pay",
    "laptop": "device equipment", "wfh": "remote work home", "fired": "termination",
    "quit": "resignation notice", "bill": "invoice", "login": "authentication password",
    "reimbursement": "expense claim", "outage": "incident",
    "minimum": "least", "maximum": "up to", "length": "characters",
}


def relevant(hit, semantic: bool, strict: bool) -> bool:
    """Gate evidence before it reaches the answer step.

    strict: the answerer cannot judge relevance itself (extractive mode).
    semantic: similarity reflects meaning, so no shared word is needed.
    """
    if semantic:
        return hit.similarity >= MIN_SIMILARITY or hit.coverage >= MIN_COVERAGE
    if hit.coverage == 0:  # look-alike spelling alone never counts
        return False
    return not strict or hit.coverage >= MIN_COVERAGE or hit.similarity >= MIN_SIMILARITY


def reformulate(query: str) -> str:
    extra = [SYNONYMS[w] for w in re.findall(r"[a-z]+", query.lower()) if w in SYNONYMS]
    return f"{query} {' '.join(extra)}" if extra else ""


class Agent:
    def __init__(self, retriever, llm):
        self.retriever, self.llm = retriever, llm

    def ask(self, principal, question, top_k=6):
        queries = self.llm.plan(question)
        trace = [{"step": "plan", "queries": queries}]
        evidence, answered = {}, []

        for query in queries:
            found = self._retrieve(principal, query, top_k, trace)
            if not found:
                retry = reformulate(query)
                if retry:
                    trace.append({"step": "reformulate", "query": retry})
                    # Grade against the expanded wording.
                    found = self._retrieve(principal, retry, top_k, trace, min_coverage=0.3)
                    query = retry
            if found:
                answered.append(query)
                for hit in found[:3]:
                    evidence[hit.chunk_id] = hit

        hits = list(evidence.values())[:top_k]
        if not hits:
            trace.append({"step": "abstain", "reason": "no relevant evidence"})
            return self._result(NO_ANSWER, [], False, trace)

        answer = self.llm.answer(question, hits, answered)
        cited = [int(n) for n in re.findall(r"\[(\d+)\]", answer)]
        valid = sorted({n for n in cited if 1 <= n <= len(hits)})
        invalid = sorted(set(cited) - set(valid))
        trace.append({"step": "verify", "cited": valid, "invalid": invalid})
        if answer.strip() == NO_ANSWER:
            return self._result(NO_ANSWER, [], False, trace)
        if not valid or invalid:
            # An answer that cannot be traced to a source is not returned.
            trace.append({"step": "abstain", "reason": "answer failed citation check"})
            return self._result(NO_ANSWER, [], False, trace)

        sources = [{"n": n, "doc_id": h.doc_id, "title": h.title, "chunk": h.position,
                    "snippet": h.text[:400]}
                   for n, h in enumerate(hits, 1) if n in valid]
        return self._result(answer, sources, True, trace)

    def _retrieve(self, principal, query, top_k, trace, min_coverage=None):
        hits = self.retriever.search(principal, query, top_k)
        semantic = not self.retriever.embedder.name.startswith("hashing")
        strict = self.llm.name == "extractive"
        if min_coverage is None:
            good = [h for h in hits if relevant(h, semantic, strict)]
        else:
            good = [h for h in hits if h.coverage >= min_coverage]
        trace.append({"step": "retrieve", "query": query, "found": len(hits), "relevant": len(good)})
        return good

    @staticmethod
    def _result(answer, sources, grounded, trace):
        return {"answer": answer, "sources": sources, "grounded": grounded, "trace": trace}
