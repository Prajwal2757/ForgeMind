"""Application service shared by the API, CLI and evals."""
from .agent import Agent
from .config import Settings
from .embeddings import make_embedder
from .ingest import chunk_text, extract_text
from .llm import make_llm
from .retrieval import Retriever
from .store import Store


class ForgeMind:
    def __init__(self, settings=None):
        self.settings = settings or Settings()
        self.store = Store(self.settings.db_path)
        self.embedder = make_embedder(self.settings)
        self.agent = Agent(Retriever(self.store, self.embedder), make_llm(self.settings))

    def ingest(self, principal, filename, data, groups=()):
        if not principal.can_write():
            raise PermissionError("viewers cannot add documents")
        if not principal.is_admin() and not set(groups) <= set(principal.groups):
            raise PermissionError("you can only share with groups you belong to")
        chunks = chunk_text(extract_text(filename, data))
        vectors = self.embedder.embed([f"{filename}\n{c}" for c in chunks])
        doc_id = self.store.add_document(
            principal, filename, groups, chunks, vectors, self.embedder.name)
        self.store.log(principal, "ingest", {"doc_id": doc_id, "title": filename,
                                             "chunks": len(chunks), "groups": list(groups)})
        return {"id": doc_id, "title": filename, "chunks": len(chunks)}

    def ask(self, principal, question):
        result = self.agent.ask(principal, question)
        self.store.log(principal, "ask", {"question": question, "grounded": result["grounded"],
                                          "documents": [s["doc_id"] for s in result["sources"]]})
        return result
