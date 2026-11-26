"""CLI for operators. Run: python -m forgemind --help"""
import argparse
import getpass
import json
import os
import signal
import sys
import threading
from pathlib import Path

from .auth import ROLES, Principal
from .config import Settings
from .ingest import chunk_text, extract_text


def _split(value):
    return [g.strip() for g in value.split(",") if g.strip()]


def _password():
    value = os.getenv("FORGEMIND_PASSWORD")  # for automation
    if value:
        return value
    first = getpass.getpass("Password (12+ characters): ")
    if first != getpass.getpass("Repeat: "):
        raise ValueError("passwords do not match")
    return first


def build_parser():
    parser = argparse.ArgumentParser(prog="forgemind")
    sub = parser.add_subparsers(dest="cmd", required=True)
    serve = sub.add_parser("serve", help="run the web server")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    sub.add_parser("worker", help="run a standalone ingestion worker")

    for name, text in (("user-add", "create a user"), ("user-update", "change a user")):
        p = sub.add_parser(name, help=text)
        p.add_argument("--tenant", required=True)
        p.add_argument("--user", required=True)
        p.add_argument("--role", choices=ROLES, default="viewer" if name == "user-add" else None)
        p.add_argument("--groups", default="" if name == "user-add" else None)
        if name == "user-update":
            p.add_argument("--password", action="store_true", help="set a new password")
            state = p.add_mutually_exclusive_group()
            state.add_argument("--disable", dest="active", action="store_false", default=None)
            state.add_argument("--enable", dest="active", action="store_true")
    token = sub.add_parser("token", help="issue a session token for an existing user")
    token.add_argument("--tenant", required=True)
    token.add_argument("--user", required=True)

    ingest = sub.add_parser("ingest", help="load files directly (local operator)")
    ingest.add_argument("--tenant", required=True)
    ingest.add_argument("--groups", default="")
    ingest.add_argument("files", nargs="+")
    ask = sub.add_parser("ask", help="ask a question (local operator)")
    ask.add_argument("--tenant", required=True)
    ask.add_argument("question")
    backup = sub.add_parser("backup", help="write a consistent copy of the database")
    backup.add_argument("destination")
    purge = sub.add_parser("purge", help="delete old audit events and finished jobs")
    purge.add_argument("--days", type=int, required=True)
    sub.add_parser("reindex", help="re-embed after changing the embedding provider")
    sub.add_parser("eval", help="run the bundled evaluation set")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        return run(args)
    except (ValueError, KeyError, RuntimeError, FileNotFoundError) as exc:
        message = f"unknown user {exc}" if isinstance(exc, KeyError) else str(exc)
        print(f"error: {message}", file=sys.stderr)
        return 1


def run(args):
    if args.cmd == "serve":
        import uvicorn
        from .api import create_app
        # Requests are logged by the app (without query strings), so uvicorn's access log is off.
        uvicorn.run(create_app(), host=args.host, port=args.port, access_log=False,
                    server_header=False, limit_concurrency=100, timeout_keep_alive=5)
        return 0
    if args.cmd == "eval":
        from .evaluate import run as evaluate
        report = evaluate()
        print(json.dumps(report, indent=2))
        return 0 if report["passed"] == report["total"] else 1

    from .service import ForgeMind
    settings = Settings()
    core = ForgeMind(settings)
    if args.cmd == "worker":
        from .observability import configure_logging
        configure_logging()
        stop = threading.Event()
        for sig in (signal.SIGINT, signal.SIGTERM):
            signal.signal(sig, lambda *_: stop.set())
        core.jobs.run_forever(stop)
    elif args.cmd == "user-add":
        core.users.create(args.tenant, args.user, _password(), args.role, _split(args.groups))
        core.store.log(args.tenant, "cli", "user_created", {"name": args.user})
        print("created")
    elif args.cmd == "user-update":
        core.users.update(args.tenant, args.user, role=args.role,
                          groups=None if args.groups is None else _split(args.groups),
                          password=_password() if args.password else None, active=args.active)
        core.store.log(args.tenant, "cli", "user_updated", {"name": args.user})
        print("updated; existing sessions are signed out")
    elif args.cmd == "token":
        print(core.users.issue_token(settings.require_secret(), args.tenant, args.user,
                                     settings.token_ttl))
    elif args.cmd in ("ingest", "ask"):
        operator = Principal(args.tenant, "cli", "admin", ())
        if args.cmd == "ask":
            print(json.dumps(core.ask(operator, args.question), indent=2))
        else:
            for name in args.files:
                path = Path(name)
                chunks = chunk_text(extract_text(path.name, path.read_bytes(),
                                                 settings.parse_timeout))
                vectors = core.embedder.embed([f"{path.name}\n{c}" for c in chunks])
                doc_id = core.store.add_document(operator, path.name, _split(args.groups), chunks,
                                                 vectors, core.embedder.name)
                core.store.log(args.tenant, "cli", "ingest", {"doc_id": doc_id,
                                                              "title": path.name})
                print(f"{path.name}: {len(chunks)} chunks ({doc_id})")
    elif args.cmd == "backup":
        if Path(args.destination).exists():
            raise ValueError("destination already exists")
        core.db.backup(args.destination)
        print(f"backup written to {args.destination}")
    elif args.cmd == "purge":
        if args.days < 1:
            raise ValueError("--days must be at least 1")
        print(core.store.purge(args.days))
    elif args.cmd == "reindex":
        print(f"re-embedded {core.store.reindex(core.embedder)} documents")
    return 0


if __name__ == "__main__":
    sys.exit(main())
