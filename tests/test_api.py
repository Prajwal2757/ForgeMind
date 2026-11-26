"""Runs a real server on a free port and calls it over HTTP."""
import json
import socket
import threading
import time
import urllib.error
import urllib.request

import pytest
import uvicorn

from forgemind.api import create_app
from forgemind.auth import issue_token
from forgemind.config import Settings

SECRET = "s" * 40


@pytest.fixture(scope="module")
def base():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    app = create_app(Settings(db_path=":memory:", secret=SECRET, max_upload_bytes=2000,
                              rate_limit_per_minute=50))
    server = uvicorn.Server(uvicorn.Config(app, port=port, log_level="error"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 10
    while not server.started:
        assert time.time() < deadline, "server did not start"
        time.sleep(0.02)
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(5)


def call(base, method, path, role=None, body=None, user=None):
    headers = {}
    if role:
        headers["Authorization"] = "Bearer " + issue_token(SECRET, "acme", user or role, role)
    req = urllib.request.Request(base + path, body, headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            raw = r.read()
            return r.status, json.loads(raw) if "json" in r.headers["content-type"] else raw
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def test_full_flow(base):
    assert call(base, "GET", "/health")[0] == 200
    assert b"ForgeMind" in call(base, "GET", "/")[1]
    assert call(base, "GET", "/api/documents")[0] == 401
    up = "/api/documents?filename=leave.md"
    text = b"Employees receive 24 days of annual leave."
    assert call(base, "POST", up, "viewer", text)[0] == 403
    status, doc = call(base, "POST", up, "editor", text)
    assert status == 201
    status, out = call(base, "POST", "/api/ask", "viewer",
                       json.dumps({"question": "How many days of annual leave?"}).encode())
    assert status == 200 and out["grounded"] and "24 days" in out["answer"]
    assert call(base, "GET", "/api/audit", "viewer")[0] == 403
    assert [e["action"] for e in call(base, "GET", "/api/audit", "admin")[1]["events"]] == ["ask", "ingest"]
    assert call(base, "DELETE", "/api/documents/" + doc["id"], "admin")[0] == 200


def test_bad_input(base):
    for body in (b"nope", b"[]", b'{"question": ""}', b'{"question": 5}'):
        assert call(base, "POST", "/api/ask", "viewer", body, user="bad")[0] == 400
    assert call(base, "POST", "/api/documents?filename=a.txt", "editor", b"a" * 3000)[0] == 413
    assert call(base, "POST", "/api/documents?filename=a.exe", "editor", b"a")[0] == 400


def test_rate_limit(base):
    codes = [call(base, "GET", "/api/documents", "viewer", user="spam")[0] for _ in range(51)]
    assert codes[-1] == 429 and codes[0] == 200
