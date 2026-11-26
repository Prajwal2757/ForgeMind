"""CLI: python -m forgemind {serve,token,ingest,ask,eval}"""
import argparse
import json
import sys
from pathlib import Path

from .auth import ROLES, Principal, issue_token
from .config import Settings
from .service import ForgeMind


def main(argv=None):
    parser = argparse.ArgumentParser(prog="forgemind")
    sub = parser.add_subparsers(dest="cmd", required=True)
    serve = sub.add_parser("serve", help="run the web server")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    token = sub.add_parser("token", help="issue an access token")
    token.add_argument("--tenant", required=True)
    token.add_argument("--user", required=True)
    token.add_argument("--role", choices=ROLES, default="viewer")
    token.add_argument("--groups", default="")
    token.add_argument("--hours", type=int, default=8)
    ingest = sub.add_parser("ingest", help="load files (local operator, admin rights)")
    ingest.add_argument("--tenant", required=True)
    ingest.add_argument("--groups", default="")
    ingest.add_argument("files", nargs="+")
    ask = sub.add_parser("ask", help="ask a question (local operator, admin rights)")
    ask.add_argument("--tenant", required=True)
    ask.add_argument("question")
    sub.add_parser("eval", help="run the bundled evaluation set")
    args = parser.parse_args(argv)
    split = lambda s: [g.strip() for g in s.split(",") if g.strip()]

    if args.cmd == "serve":
        import uvicorn
        from .api import create_app
        uvicorn.run(create_app(), host=args.host, port=args.port)
    elif args.cmd == "token":
        print(issue_token(Settings().require_secret(), args.tenant, args.user, args.role,
                          split(args.groups), args.hours * 3600))
    elif args.cmd == "eval":
        from .evaluate import run
        report = run()
        print(json.dumps(report, indent=2))
        return 0 if report["passed"] == report["total"] else 1
    else:
        core = ForgeMind()
        operator = Principal(args.tenant, "cli", "admin", ())
        if args.cmd == "ingest":
            for name in args.files:
                path = Path(name)
                print(core.ingest(operator, path.name, path.read_bytes(), split(args.groups)))
        else:
            print(json.dumps(core.ask(operator, args.question), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
