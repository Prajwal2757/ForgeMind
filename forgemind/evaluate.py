"""Small regression eval: right source retrieved, expected fact present, abstains when it should."""
import json
import tempfile
from pathlib import Path

from .auth import Principal
from .config import Settings
from .ingest import chunk_text, extract_text
from .service import ForgeMind

ROOT = Path(__file__).resolve().parent.parent


def run(root=ROOT):
    paths = sorted((root / "examples" / "docs").glob("*.md"))
    cases = json.loads((root / "evals" / "cases.json").read_text())
    failures = []
    with tempfile.TemporaryDirectory() as tmp:
        core = ForgeMind(Settings(db_path=str(Path(tmp) / "eval.db")))
        admin = Principal("eval", "eval", "admin", ())
        for path in paths:
            chunks = chunk_text(extract_text(path.name, path.read_bytes()))
            vectors = core.embedder.embed([f"{path.name}\n{c}" for c in chunks])
            core.store.add_document(admin, path.name, [], chunks, vectors, core.embedder.name)
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
    return {"total": len(cases), "passed": len(cases) - len(failures), "failures": failures}
