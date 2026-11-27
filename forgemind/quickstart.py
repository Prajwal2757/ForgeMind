"""First-run setup and local server. Started by start.py."""
import argparse
import getpass
import os
import secrets
import socket
import sys
import threading
import time
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TENANT, ADMIN = "acme", "admin"


def load_env_file(path):
    """KEY=VALUE lines. Variables already set in the shell win."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        key, sep, value = line.strip().partition("=")
        if sep and not key.startswith("#") and value.strip():
            os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


def ensure_secret(path):
    if os.getenv("FORGEMIND_SECRET"):
        return
    if not path.exists():
        with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as f:
            f.write(secrets.token_hex(32))
    os.environ["FORGEMIND_SECRET"] = path.read_text().strip()


def ask_password():
    if os.getenv("FORGEMIND_PASSWORD"):  # for automation
        return os.environ["FORGEMIND_PASSWORD"]
    print(f"\nChoose a password for the '{ADMIN}' account (12+ characters).")
    print("Nothing appears while you type. That is normal.")
    while True:
        first = getpass.getpass("Password: ")
        if not 12 <= len(first) <= 128:
            print("Use 12 to 128 characters.")
        elif first != getpass.getpass("Repeat password: "):
            print("The passwords do not match.")
        else:
            return first


def first_run(core):
    if core.users.tenant_exists(TENANT):
        return
    core.users.create(TENANT, ADMIN, ask_password(), "admin")
    from .auth import Principal
    for path in sorted((ROOT / "examples" / "docs").glob("*.md")):
        core.jobs.submit(Principal(TENANT, ADMIN, "admin", ()), path.name, path.read_bytes())
    while core.jobs.run_once():
        pass
    print("Account created and three sample documents added.")


def open_when_up(port, url):
    for _ in range(100):
        try:
            socket.create_connection(("127.0.0.1", port), timeout=1).close()
            webbrowser.open(url)
            return
        except OSError:
            time.sleep(0.2)


def main(argv=None):
    parser = argparse.ArgumentParser(prog="start.py")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args(argv)

    load_env_file(ROOT / ".env")
    os.environ.setdefault("FORGEMIND_DB", str(ROOT / "forgemind.db"))
    ensure_secret(ROOT / ".forgemind-secret")

    with socket.socket() as probe:
        try:
            probe.connect(("127.0.0.1", args.port))
            print(f"Port {args.port} is already in use. Try: python start.py --port 8080")
            return 1
        except OSError:
            pass

    from .service import ForgeMind
    try:
        first_run(ForgeMind())
    except (KeyboardInterrupt, EOFError):
        print("\nCancelled.")
        return 1

    url = f"http://127.0.0.1:{args.port}"
    print(f"\nForgeMind is starting at {url}")
    print(f"Sign in with organisation: {TENANT}   user: {ADMIN}   password: the one you chose")
    print("Keep this window open. Press Ctrl+C to stop.\n")
    if not args.no_browser:
        threading.Thread(target=open_when_up, args=(args.port, url), daemon=True).start()

    from .__main__ import main as cli
    return cli(["serve", "--port", str(args.port)])


if __name__ == "__main__":
    sys.exit(main())
