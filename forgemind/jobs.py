"""Persistent ingestion queue in the database, with leases, retries and crash recovery."""
import json
import logging
import threading
import time
import uuid

from .ingest import chunk_text, extract_text

log = logging.getLogger("forgemind.jobs")
MAX_ATTEMPTS = 3
LEASE = 600


class QueueFull(Exception):
    pass


class Jobs:
    def __init__(self, db, store, users, embedder, settings):
        self.db, self.store, self.users = db, store, users
        self.embedder, self.settings = embedder, settings
        self.heartbeat = 0.0
        self.busy = False
        self._last_tenant = ""

    def submit(self, principal, filename, data, groups=()) -> str:
        if not principal.can_write():
            raise PermissionError("viewers cannot add documents")
        if not principal.is_admin() and not set(groups) <= set(principal.groups):
            raise PermissionError("you can only share with groups you belong to")
        job_id, now = uuid.uuid4().hex, time.time()
        with self.db.tx(write=True) as c:
            pending = c.execute("SELECT COUNT(*) FROM jobs WHERE tenant=? AND status IN "
                                "('queued','running')", (principal.tenant,)).fetchone()[0]
            if pending >= self.settings.max_pending_jobs:
                raise QueueFull("too many documents waiting; try again shortly")
            c.execute("INSERT INTO jobs VALUES(?,?,?,?,?,?,'queued',NULL,NULL,0,0,0,?)",
                      (job_id, principal.tenant, principal.user, filename,
                       json.dumps(list(groups)), data, now))
        return job_id

    def get(self, principal, job_id):
        with self.db.tx() as c:
            row = c.execute("SELECT id,user,filename,status,error,doc_id FROM jobs "
                            "WHERE id=? AND tenant=?", (job_id, principal.tenant)).fetchone()
        if row is None or not (principal.is_admin() or row["user"] == principal.user):
            return None
        return {k: row[k] for k in ("id", "filename", "status", "error", "doc_id")}

    def _claim(self):
        now = time.time()
        with self.db.tx(write=True) as c:
            # A job that keeps killing its worker must not loop forever.
            c.execute("UPDATE jobs SET status='failed', error='processing did not finish', "
                      "data=NULL WHERE status='running' AND lease_until<? AND attempts>=?",
                      (now, MAX_ATTEMPTS))
            row = c.execute(
                "SELECT * FROM jobs WHERE (status='queued' AND run_after<=?) OR "
                "(status='running' AND lease_until<?) "
                # Take turns between tenants, oldest first within each.
                "ORDER BY tenant<=?, tenant, created LIMIT 1",
                (now, now, self._last_tenant)).fetchone()
            if row is None:
                return None
            c.execute("UPDATE jobs SET status='running', attempts=attempts+1, lease_until=? "
                      "WHERE id=?", (now + LEASE, row["id"]))
            self._last_tenant = row["tenant"]
            return dict(row, attempts=row["attempts"] + 1)

    def _finish(self, c, job, status, error=None, doc_id=None, run_after=0):
        """Returns False if another worker has taken the job over."""
        keep_data = status == "queued"
        return c.execute(
            "UPDATE jobs SET status=?, error=?, doc_id=?, run_after=?, "
            "data=CASE WHEN ? THEN data ELSE NULL END "
            "WHERE id=? AND status='running' AND attempts=?",
            (status, error, doc_id, run_after, keep_data, job["id"], job["attempts"])).rowcount == 1

    def run_once(self) -> bool:
        job = self._claim()
        if job is None:
            return False
        groups = json.loads(job["groups"])
        try:
            # Permissions may have changed since the upload was accepted.
            principal = self.users.get(job["tenant"], job["user"])
            if principal is None or not principal.can_write() or not (
                    principal.is_admin() or set(groups) <= set(principal.groups)):
                raise PermissionError("you no longer have permission to add this document")
            text = extract_text(job["filename"], job["data"], self.settings.parse_timeout)
            chunks = chunk_text(text)
            vectors = self.embedder.embed([f"{job['filename']}\n{c}" for c in chunks])
            with self.db.tx(write=True) as c:  # document and job status commit together
                owned = c.execute("SELECT 1 FROM jobs WHERE id=? AND status='running' AND "
                                  "attempts=?", (job["id"], job["attempts"])).fetchone()
                if not owned:
                    return True
                doc_id = self.store.add_document(principal, job["filename"], groups, chunks,
                                                 vectors, self.embedder.name, conn=c)
                self._finish(c, job, "done", doc_id=doc_id)
            self.store.log(job["tenant"], job["user"], "ingest",
                           {"doc_id": doc_id, "title": job["filename"], "chunks": len(chunks),
                            "groups": groups})
        except (ValueError, PermissionError) as exc:  # will not succeed on retry
            with self.db.tx(write=True) as c:
                self._finish(c, job, "failed", str(exc))
        except Exception:
            log.exception("ingestion job %s failed", job["id"])
            retry = job["attempts"] < MAX_ATTEMPTS
            with self.db.tx(write=True) as c:
                self._finish(c, job, "queued" if retry else "failed",
                             None if retry else "temporary error; please upload again",
                             run_after=time.time() + 10 * job["attempts"])
        return True

    def run_forever(self, stop: threading.Event):
        while not stop.is_set():
            self.heartbeat = time.time()
            try:
                self.busy = True
                worked = self.run_once()
            except Exception:
                log.exception("worker loop error")
                worked = False
            finally:
                self.busy = False
            if not worked:
                stop.wait(0.5)
