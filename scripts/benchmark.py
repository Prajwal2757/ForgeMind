"""Time index build and queries on synthetic text. Usage: python -m scripts.benchmark [chunks]"""
import random
import statistics
import sys
import tempfile
import time
from pathlib import Path

from forgemind.auth import Principal
from forgemind.config import Settings
from forgemind.service import ForgeMind


def main(total=20000):
    rng = random.Random(0)
    words = [f"term{i}" for i in range(5000)]
    admin = Principal("bench", "bench", "admin", ())
    with tempfile.TemporaryDirectory() as tmp:
        core = ForgeMind(Settings(db_path=str(Path(tmp) / "b.db"), llm_provider="extractive",
                                  embed_provider="hashing"))
        for start in range(0, total, 500):
            chunks = [" ".join(rng.choices(words, k=130)) for _ in range(min(500, total - start))]
            core.store.add_document(admin, f"doc{start}.txt", [], chunks,
                                    core.embedder.embed(chunks), core.embedder.name)
        started = time.perf_counter()
        core.agent.retriever.index("bench")
        print(f"chunks: {total}\nindex build: {time.perf_counter() - started:.1f} s")
        times = []
        for _ in range(50):
            query = " ".join(rng.choices(words, k=5))
            started = time.perf_counter()
            core.agent.retriever.search(admin, query)
            times.append((time.perf_counter() - started) * 1000)
        print(f"query median: {statistics.median(times):.0f} ms, max: {max(times):.0f} ms")
    try:
        import resource
        print(f"peak memory: {resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024:.0f} MB")
    except ImportError:
        pass


if __name__ == "__main__":
    main(*map(int, sys.argv[1:2]))
