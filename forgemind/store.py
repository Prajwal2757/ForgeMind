"""Documents, audit log and rate limits. Every document read is scoped to a tenant."""
import json
import time
import uuid

import numpy as np


def visible(principal, groups) -> bool:
    # No groups means visible to the whole tenant.
    return principal.is_admin() or not groups or bool(set(groups) & set(principal.groups))


def _bump(c, tenant):
    c.execute("INSERT INTO revisions VALUES(?,1) ON CONFLICT(tenant) DO UPDATE SET "
              "revision=revision+1", (tenant,))


class Store:
    def __init__(self, db, max_chunks=50000):
        self.db, self.max_chunks = db, max_chunks

    def add_document(self, principal, title, groups, chunks, vectors, embedder, conn=None) -> str:
        doc_id = uuid.uuid4().hex

        def write(c):
            used = c.execute("SELECT COUNT(*) FROM chunks c JOIN documents d ON d.id=c.doc_id "
                             "WHERE d.tenant=?", (principal.tenant,)).fetchone()[0]
            if used + len(chunks) > self.max_chunks:
                raise ValueError("document storage limit reached for this organisation")
            c.execute("INSERT INTO documents VALUES(?,?,?,?,?,?,?)",
                      (doc_id, principal.tenant, title, principal.user,
                       json.dumps(sorted(set(groups))), embedder, time.time()))
            c.executemany("INSERT INTO chunks(doc_id,position,text,vector) VALUES(?,?,?,?)",
                          [(doc_id, i, t, np.asarray(v, np.float32).tobytes())
                           for i, (t, v) in enumerate(zip(chunks, vectors))])
            _bump(c, principal.tenant)

        if conn is not None:
            write(conn)
        else:
            with self.db.tx(write=True) as c:
                write(c)
        return doc_id

    def list_documents(self, principal, limit=100, offset=0):
        with self.db.tx() as c:
            rows = c.execute("SELECT * FROM documents WHERE tenant=? ORDER BY created DESC",
                             (principal.tenant,)).fetchall()
        docs = [{"id": r["id"], "title": r["title"], "owner": r["owner"],
                 "groups": json.loads(r["groups"]), "created": r["created"]} for r in rows]
        return [d for d in docs if visible(principal, d["groups"])][offset:offset + limit]

    def delete_document(self, principal, doc_id) -> str:
        """Returns 'deleted', 'forbidden' or 'missing' (also used for hidden documents)."""
        with self.db.tx(write=True) as c:
            row = c.execute("SELECT * FROM documents WHERE id=? AND tenant=?",
                            (doc_id, principal.tenant)).fetchone()
            if row is None or not visible(principal, json.loads(row["groups"])):
                return "missing"
            if not (principal.is_admin() or (principal.can_write() and row["owner"] == principal.user)):
                return "forbidden"
            c.execute("DELETE FROM documents WHERE id=?", (doc_id,))
            _bump(c, principal.tenant)
        return "deleted"

    def revision(self, tenant) -> int:
        with self.db.tx() as c:
            row = c.execute("SELECT revision FROM revisions WHERE tenant=?", (tenant,)).fetchone()
        return row["revision"] if row else 0

    def snapshot(self, tenant, embedder):
        """(revision, rows) read in one transaction, for building a search index."""
        with self.db.tx() as c:
            rev = c.execute("SELECT revision FROM revisions WHERE tenant=?", (tenant,)).fetchone()
            rows = c.execute(
                "SELECT c.id, c.doc_id, c.position, c.text, c.vector, d.title, d.groups "
                "FROM chunks c JOIN documents d ON d.id=c.doc_id "
                "WHERE d.tenant=? AND d.embedder=? ORDER BY c.id", (tenant, embedder)).fetchall()
        return (rev["revision"] if rev else 0), rows

    def reindex(self, embedder):
        """Re-embed documents made with a different provider. Returns how many changed."""
        with self.db.tx() as c:
            docs = c.execute("SELECT id,tenant,title FROM documents WHERE embedder!=?",
                             (embedder.name,)).fetchall()
        for doc in docs:
            with self.db.tx() as c:
                chunks = c.execute("SELECT id,text FROM chunks WHERE doc_id=? ORDER BY position",
                                   (doc["id"],)).fetchall()
            vectors = embedder.embed([f"{doc['title']}\n{r['text']}" for r in chunks])
            with self.db.tx(write=True) as c:
                c.executemany("UPDATE chunks SET vector=? WHERE id=?",
                              [(np.asarray(v, np.float32).tobytes(), r["id"])
                               for r, v in zip(chunks, vectors)])
                c.execute("UPDATE documents SET embedder=? WHERE id=?", (embedder.name, doc["id"]))
                _bump(c, doc["tenant"])
        return len(docs)

    def log(self, tenant, user, action, detail):
        with self.db.tx(write=True) as c:
            c.execute("INSERT INTO audit(ts,tenant,user,action,detail) VALUES(?,?,?,?,?)",
                      (time.time(), tenant, user, action, json.dumps(detail)))

    def audit_log(self, tenant, limit=100, before=None, action=None):
        sql, args = "SELECT id,ts,user,action,detail FROM audit WHERE tenant=?", [tenant]
        if before is not None:
            sql, args = sql + " AND id<?", args + [before]
        if action:
            sql, args = sql + " AND action=?", args + [action]
        with self.db.tx() as c:
            rows = c.execute(sql + " ORDER BY id DESC LIMIT ?", args + [limit]).fetchall()
        return [{**dict(r), "detail": json.loads(r["detail"])} for r in rows]

    def purge(self, audit_days):
        cutoff = time.time() - audit_days * 86400
        with self.db.tx(write=True) as c:
            audit = c.execute("DELETE FROM audit WHERE ts<?", (cutoff,)).rowcount
            jobs = c.execute("DELETE FROM jobs WHERE status IN ('done','failed') AND created<?",
                             (cutoff,)).rowcount
            c.execute("DELETE FROM rate WHERE window_start<?", (time.time() - 86400,))
        return {"audit": audit, "jobs": jobs}

    def hit(self, key, limit, window) -> bool:
        """Count one event. Shared by all processes using this database."""
        now = time.time()
        with self.db.tx(write=True) as c:
            row = c.execute("SELECT window_start,count FROM rate WHERE key=?", (key,)).fetchone()
            if row is None or now - row["window_start"] >= window:
                c.execute("INSERT OR REPLACE INTO rate VALUES(?,?,1)", (key, now))
                return True
            if row["count"] >= limit:
                return False
            c.execute("UPDATE rate SET count=count+1 WHERE key=?", (key,))
        return True

    def reset(self, key):
        with self.db.tx(write=True) as c:
            c.execute("DELETE FROM rate WHERE key=?", (key,))
