"""SQLite persistence. Every read is scoped to a tenant and the caller's groups."""
import json
import sqlite3
import threading
import time
import uuid

import numpy as np

SCHEMA = """
CREATE TABLE IF NOT EXISTS documents(
  id TEXT PRIMARY KEY, tenant TEXT NOT NULL, title TEXT NOT NULL, owner TEXT NOT NULL,
  groups TEXT NOT NULL, embedder TEXT NOT NULL, created REAL NOT NULL);
CREATE INDEX IF NOT EXISTS documents_tenant ON documents(tenant);
CREATE TABLE IF NOT EXISTS chunks(
  id INTEGER PRIMARY KEY, doc_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  position INTEGER NOT NULL, text TEXT NOT NULL, vector BLOB NOT NULL);
CREATE INDEX IF NOT EXISTS chunks_doc ON chunks(doc_id);
CREATE TABLE IF NOT EXISTS audit(
  id INTEGER PRIMARY KEY, ts REAL NOT NULL, tenant TEXT NOT NULL, user TEXT NOT NULL,
  action TEXT NOT NULL, detail TEXT NOT NULL);
"""


class Store:
    def __init__(self, path):
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        self._db.execute("PRAGMA foreign_keys=ON")
        self._db.executescript(SCHEMA)

    def close(self):
        self._db.close()

    @staticmethod
    def _visible(principal, groups_json) -> bool:
        groups = json.loads(groups_json)
        # No groups means visible to the whole tenant.
        return principal.is_admin() or not groups or bool(set(groups) & set(principal.groups))

    def add_document(self, principal, title, groups, chunks, vectors, embedder) -> str:
        doc_id = uuid.uuid4().hex
        with self._lock, self._db:
            self._db.execute("INSERT INTO documents VALUES(?,?,?,?,?,?,?)",
                             (doc_id, principal.tenant, title, principal.user,
                              json.dumps(sorted(set(groups))), embedder, time.time()))
            self._db.executemany(
                "INSERT INTO chunks(doc_id,position,text,vector) VALUES(?,?,?,?)",
                [(doc_id, i, c, np.asarray(v, np.float32).tobytes())
                 for i, (c, v) in enumerate(zip(chunks, vectors))])
        return doc_id

    def list_documents(self, principal):
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM documents WHERE tenant=? ORDER BY created DESC",
                (principal.tenant,)).fetchall()
        return [{"id": r["id"], "title": r["title"], "owner": r["owner"],
                 "groups": json.loads(r["groups"]), "created": r["created"]}
                for r in rows if self._visible(principal, r["groups"])]

    def delete_document(self, principal, doc_id) -> str:
        """Returns 'deleted', 'forbidden' or 'missing' (also used for hidden documents)."""
        with self._lock, self._db:
            row = self._db.execute("SELECT * FROM documents WHERE id=? AND tenant=?",
                                   (doc_id, principal.tenant)).fetchone()
            if row is None or not self._visible(principal, row["groups"]):
                return "missing"
            if not (principal.is_admin() or row["owner"] == principal.user):
                return "forbidden"
            self._db.execute("DELETE FROM documents WHERE id=?", (doc_id,))
        return "deleted"

    def visible_chunks(self, principal, embedder):
        """Permission filtering happens here, before anything is scored or sent to a model."""
        with self._lock:
            rows = self._db.execute(
                "SELECT c.id, c.doc_id, c.position, c.text, c.vector, d.title, d.groups "
                "FROM chunks c JOIN documents d ON d.id=c.doc_id "
                "WHERE d.tenant=? AND d.embedder=? ORDER BY c.id",
                (principal.tenant, embedder)).fetchall()
        return [r for r in rows if self._visible(principal, r["groups"])]

    def log(self, principal, action, detail):
        with self._lock, self._db:
            self._db.execute("INSERT INTO audit(ts,tenant,user,action,detail) VALUES(?,?,?,?,?)",
                             (time.time(), principal.tenant, principal.user, action,
                              json.dumps(detail)))

    def audit_log(self, tenant, limit=100):
        with self._lock:
            rows = self._db.execute(
                "SELECT ts,user,action,detail FROM audit WHERE tenant=? ORDER BY id DESC LIMIT ?",
                (tenant, limit)).fetchall()
        return [{**dict(r), "detail": json.loads(r["detail"])} for r in rows]
