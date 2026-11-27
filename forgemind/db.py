"""SQLite access and schema migrations. One short-lived connection per operation."""
import sqlite3
from contextlib import contextmanager

# Append only. Never edit a migration that has shipped.
MIGRATIONS = [
    """
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
      action TEXT NOT NULL, detail TEXT NOT NULL)
    """,
    """
    CREATE TABLE users(
      tenant TEXT NOT NULL, name TEXT NOT NULL, password TEXT NOT NULL, role TEXT NOT NULL,
      groups TEXT NOT NULL, active INTEGER NOT NULL, token_version INTEGER NOT NULL,
      created REAL NOT NULL, PRIMARY KEY(tenant, name));
    CREATE TABLE jobs(
      id TEXT PRIMARY KEY, tenant TEXT NOT NULL, user TEXT NOT NULL, filename TEXT NOT NULL,
      groups TEXT NOT NULL, data BLOB, status TEXT NOT NULL, error TEXT, doc_id TEXT,
      attempts INTEGER NOT NULL, run_after REAL NOT NULL, lease_until REAL NOT NULL,
      created REAL NOT NULL);
    CREATE INDEX jobs_status ON jobs(status, run_after);
    CREATE TABLE revisions(tenant TEXT PRIMARY KEY, revision INTEGER NOT NULL);
    CREATE TABLE rate(key TEXT PRIMARY KEY, window_start REAL NOT NULL, count INTEGER NOT NULL);
    CREATE INDEX audit_tenant ON audit(tenant, id)
    """,
]


class Database:
    def __init__(self, path):
        if path == ":memory:":
            raise ValueError("an on-disk database path is required")
        self.path = path
        conn = self._connect()
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            self._migrate(conn)
        finally:
            conn.close()

    def _connect(self):
        conn = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    @staticmethod
    def _migrate(conn):
        conn.execute("BEGIN IMMEDIATE")  # only one process migrates at a time
        try:
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            if version > len(MIGRATIONS):
                raise RuntimeError("database was created by a newer ForgeMind version")
            for number, script in enumerate(MIGRATIONS[version:], version + 1):
                for statement in script.split(";"):
                    if statement.strip():
                        conn.execute(statement)
                conn.execute(f"PRAGMA user_version={number}")
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise

    @contextmanager
    def tx(self, write=False):
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            try:
                yield conn
            except BaseException:
                conn.execute("ROLLBACK")
                raise
            conn.execute("COMMIT")
        finally:
            conn.close()

    def backup(self, destination):
        source, target = self._connect(), sqlite3.connect(destination)
        try:
            source.backup(target)
        finally:
            source.close()
            target.close()
