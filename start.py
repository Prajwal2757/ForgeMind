"""One-command local start: python start.py

Creates a private virtual environment, installs dependencies, then runs forgemind.quickstart.
For local testing. For a real deployment see docs/OPERATIONS.md.
"""
import os
import subprocess
import sys
import venv
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def main():
    if sys.version_info < (3, 13):
        sys.exit(f"ForgeMind needs Python 3.13 or newer; this is {sys.version.split()[0]}.\n"
                 "Install it from https://www.python.org/downloads/ and run this again.")
    env_dir = ROOT / ".venv"
    python = env_dir / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    marker = env_dir / "forgemind-requirements.txt"
    wanted = (ROOT / "requirements.txt").read_text()
    if not python.exists():
        print("Creating a virtual environment...")
        venv.create(env_dir, with_pip=True)
    if not marker.exists() or marker.read_text() != wanted:
        print("Installing dependencies (first run takes a few minutes)...")
        if subprocess.call([str(python), "-m", "pip", "install", "-q", "-r",
                            str(ROOT / "requirements.txt")]):
            sys.exit("Installing dependencies failed. Check your internet connection.")
        marker.write_text(wanted)
    try:
        sys.exit(subprocess.call([str(python), "-m", "forgemind.quickstart", *sys.argv[1:]],
                                 cwd=ROOT))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
