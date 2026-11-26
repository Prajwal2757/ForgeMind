"""Wires the components together; shared by the API, CLI and evals."""
from .agent import Agent
from .auth import Users
from .config import Settings
from .db import Database
from .embeddings import make_embedder
from .jobs import Jobs
from .llm import make_llm
from .retrieval import Retriever
from .store import Store


class ForgeMind:
    def __init__(self, settings=None):
        self.settings = settings or Settings()
        self.db = Database(self.settings.db_path)
        self.users = Users(self.db)
        self.store = Store(self.db, self.settings.max_chunks_per_tenant)
        self.embedder = make_embedder(self.settings)
        self.jobs = Jobs(self.db, self.store, self.users, self.embedder, self.settings)
        self.agent = Agent(Retriever(self.store, self.embedder,
                                     max_chunks=self.settings.max_cached_chunks), make_llm(self.settings))

    def ask(self, principal, question):
        result = self.agent.ask(principal, question)
        self.store.log(principal.tenant, principal.user, "ask",
                       {"question": question, "grounded": result["grounded"],
                        "documents": [s["doc_id"] for s in result["sources"]]})
        return result
