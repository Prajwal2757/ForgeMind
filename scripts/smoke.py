"""End-to-end check of a running deployment.

Usage: FORGEMIND_PASSWORD=... python scripts/smoke.py https://host tenant user
The user needs the editor or admin role. The test document is deleted afterwards.
"""
import json
import os
import sys
import time
import urllib.request


def main(base, tenant, user):
    token = None

    def call(method, path, body=None):
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        if isinstance(body, dict):
            body = json.dumps(body).encode()
        req = urllib.request.Request(base + path, body, headers, method=method)
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.load(resp)

    for attempt in range(60):
        try:
            call("GET", "/ready")
            break
        except OSError:
            if attempt == 59:
                raise
            time.sleep(1)
    token = call("POST", "/api/login", {"tenant": tenant, "user": user,
                                        "password": os.environ["FORGEMIND_PASSWORD"]})["token"]
    job_id = call("POST", "/api/documents?filename=smoke-test.md",
                  b"The zebrafish protocol requires 17 calibration cycles.")["job_id"]
    for _ in range(60):
        job = call("GET", f"/api/jobs/{job_id}")
        if job["status"] in ("done", "failed"):
            break
        time.sleep(1)
    assert job["status"] == "done", job
    try:
        out = call("POST", "/api/ask",
                   {"question": "How many calibration cycles does the zebrafish protocol require?"})
        assert out["grounded"] and "17" in out["answer"], out
    finally:
        call("DELETE", f"/api/documents/{job['doc_id']}")
    print("smoke test passed")


if __name__ == "__main__":
    main(*sys.argv[1:4])
