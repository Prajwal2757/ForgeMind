"""CI check: a fresh `python start.py` installs, starts and passes the smoke test."""
import os
import signal
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main():
    env = {**os.environ, "FORGEMIND_PASSWORD": "ci-only-password-123"}
    group = ({"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt"
             else {"start_new_session": True})
    server = subprocess.Popen([sys.executable, "start.py", "--no-browser"], cwd=ROOT, env=env,
                              **group)
    try:
        for _ in range(600):  # first run installs dependencies
            try:
                import urllib.request
                urllib.request.urlopen("http://127.0.0.1:8000/ready", timeout=2)
                break
            except OSError:
                if server.poll() is not None:
                    sys.exit("start.py exited early")
                import time
                time.sleep(1)
        subprocess.run([sys.executable, "scripts/smoke.py", "http://127.0.0.1:8000", "acme", "admin"],
                       cwd=ROOT, env=env, check=True)
    finally:
        if os.name == "nt":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(server.pid)])
        else:
            os.killpg(server.pid, signal.SIGTERM)
        server.wait(30)


if __name__ == "__main__":
    main()
