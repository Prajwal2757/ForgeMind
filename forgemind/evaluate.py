"""Small regression eval: right source retrieved, expected fact present, abstains when it should."""
import json
from pathlib import Path

from .auth import Principal
from .config import Settings
from .service import ForgeMind

ROOT = Path(__file__).resolve().parent.parent


def run(root=ROOT):
    core = ForgeMind(Settings(db_path=":memory:"))
    admin = Principal("eval", "eval", "admin", ())
    for path in sorted((root / "examples" / "docs").glob("*.md")):
        core.ingest(admin, path.name, path.read_bytes())
    cases = json.loads((root / "evals" / "cases.json").read_text())
    failures = []
    for case in cases:
        out = core.ask(admin, case["question"])
        titles = {s["title"] for s in out["sources"]}
        if case.get("abstain"):
            ok = not out["grounded"]
        else:
            ok = (set(case["sources"]) <= titles
                  and all(x.lower() in out["answer"].lower() for x in case["contains"]))
        if not ok:
            failures.append({"question": case["question"], "answer": out["answer"],
                             "sources": sorted(titles)})
    core.store.close()
    return {"total": len(cases), "passed": len(cases) - len(failures), "failures": failures}
