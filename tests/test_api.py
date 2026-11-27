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
from forgemind.config import Settings

from .conftest import PASSWORD, SECRET


@pytest.fixture
def server(tmp_path):
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    app = create_app(Settings(db_path=str(tmp_path / "api.db"), secret=SECRET, max_upload_bytes=2000,
                              rate_limit_per_minute=40, login_attempts=3, metrics_token="m" * 20,
                              allowed_hosts="127.0.0.1", body_timeout=1, llm_provider="extractive",
                              embed_provider="hashing"))
    for name, role in (("root", "admin"), ("ed", "editor"), ("vi", "viewer")):
        app.state.core.users.create("acme", name, PASSWORD, role)
    srv = uvicorn.Server(uvicorn.Config(app, port=port, log_level="error"))
    thread = threading.Thread(target=srv.run, daemon=True)
    thread.start()
    deadline = time.time() + 10
    while not srv.started:
        assert time.time() < deadline, "server did not start"
        time.sleep(0.02)
    yield Client(f"http://127.0.0.1:{port}", app)
    srv.should_exit = True
    thread.join(15)


class Client:
    def __init__(self, base, app):
        self.base, self.app, self.headers = base, app, {}

    def call(self, method, path, token=None, body=None, headers=None):
        headers = dict(headers or {})
        if token:
            headers["Authorization"] = f"Bearer {token}"
        if isinstance(body, dict):
            body = json.dumps(body).encode()
        req = urllib.request.Request(self.base + path, body, headers, method=method)
        try:
            resp = urllib.request.urlopen(req, timeout=10)
        except urllib.error.HTTPError as e:
            resp = e
        with resp:
            self.headers, raw = resp.headers, resp.read()
            is_json = "json" in resp.headers.get("content-type", "")
            return resp.status if hasattr(resp, "status") else resp.code, json.loads(raw) if is_json else raw

    def login(self, user, password=PASSWORD):
        return self.call("POST", "/api/login", body={"tenant": "acme", "user": user,
                                                     "password": password})

    def token(self, user):
        return self.login(user)[1]["token"]


def test_full_flow(server):
    c = server
    assert c.call("GET", "/health")[0] == 200
    assert c.call("GET", "/ready")[0] == 200
    assert b"ForgeMind" in c.call("GET", "/")[1]
    assert "unsafe-inline" not in c.headers["content-security-policy"]
    assert c.call("GET", "/app.js")[0] == 200 and c.call("GET", "/style.css")[0] == 200
    assert c.call("GET", "/api/documents")[0] == 401
    editor, viewer, root = c.token("ed"), c.token("vi"), c.token("root")
    up = "/api/documents?filename=leave.md"
    text = b"Employees receive 24 days of annual leave."
    assert c.call("POST", up, viewer, text)[0] == 403
    status, accepted = c.call("POST", up, editor, text)
    assert status == 202
    for _ in range(100):
        job = c.call("GET", "/api/jobs/" + accepted["job_id"], editor)[1]
        if job["status"] == "done":
            break
        time.sleep(0.1)
    assert job["status"] == "done"
    assert c.call("GET", "/api/jobs/" + accepted["job_id"], viewer)[0] == 404
    status, out = c.call("POST", "/api/ask", viewer, {"question": "How many days of annual leave?"})
    assert status == 200 and out["grounded"] and "24 days" in out["answer"]
    assert c.headers["cache-control"] == "no-store" and c.headers["x-request-id"]
    assert c.call("GET", "/api/audit", viewer)[0] == 403
    actions = {e["action"] for e in c.call("GET", "/api/audit", root)[1]["events"]}
    assert {"login", "ingest", "ask"} <= actions
    assert c.call("DELETE", "/api/documents/" + job["doc_id"], root)[0] == 200


def test_user_administration_and_session_revocation(server):
    c = server
    root, viewer = c.token("root"), c.token("vi")
    new = {"name": "nina", "password": PASSWORD, "role": "editor", "groups": ["hr"]}
    assert c.call("POST", "/api/users", viewer, new)[0] == 403
    assert c.call("POST", "/api/users", root, new)[0] == 201
    assert c.call("POST", "/api/users", root, new)[0] == 400
    assert c.call("PATCH", "/api/users/vi", root, {"tenant": "x"})[0] == 400
    assert c.call("PATCH", "/api/users/vi", root, {"active": "no"})[0] == 400
    assert c.call("PATCH", "/api/users/vi", root, {"active": False})[0] == 200
    assert c.call("GET", "/api/me", viewer)[0] == 401
    assert c.login("vi")[0] == 401
    assert c.call("POST", "/api/logout", root)[0] == 200
    assert c.call("GET", "/api/me", root)[0] == 401


def test_password_change(server):
    c = server
    token = c.token("ed")
    assert c.call("POST", "/api/password", token, {"current": "nope", "new": "x" * 14})[0] == 403
    assert c.call("POST", "/api/password", token, {"current": PASSWORD, "new": "x" * 14})[0] == 200
    assert c.call("GET", "/api/me", token)[0] == 401
    assert c.login("ed", "x" * 14)[0] == 200


def test_login_lockout(server):
    codes = [server.login("ed", "wrong password!")[0] for _ in range(4)]
    assert codes == [401, 401, 401, 429]
    assert server.login("ed")[0] == 429


def test_bad_input_and_host_check(server):
    c = server
    token = c.token("ed")
    for body in (b"nope", b"[]", b"[" * 5000, b'{"question": ""}', b'{"question": 5}'):
        assert c.call("POST", "/api/ask", token, body)[0] == 400
    assert c.call("POST", "/api/documents?filename=a.txt", token, b"a" * 3000)[0] == 413
    assert c.call("GET", "/api/documents?limit=abc", token)[0] == 400
    assert c.call("POST", "/api/login", body={"tenant": 1})[0] == 400
    assert c.call("GET", "/health", headers={"Host": "evil.example"})[0] == 400


def test_internal_errors_are_not_leaked(server):
    token = server.token("ed")

    def boom(*a):
        raise RuntimeError("secret detail")

    server.app.state.core.agent.ask = boom
    status, body = server.call("POST", "/api/ask", token, {"question": "hi there"})
    assert status == 500 and body["error"] == "internal error" and "secret" not in json.dumps(body)


def test_metrics_and_rate_limit(server):
    c = server
    assert c.call("GET", "/metrics")[0] == 404
    status, text = c.call("GET", "/metrics", "m" * 20)
    assert status == 200 and b"forgemind_requests_total" in text
    token = c.token("vi")
    codes = [c.call("GET", "/api/me", token)[0] for _ in range(41)]
    assert codes[0] == 200 and codes[-1] == 429


def test_stalled_body_is_cut_off(server):
    port = int(server.base.rsplit(":", 1)[1])
    with socket.create_connection(("127.0.0.1", port), timeout=10) as s:
        s.sendall(b"POST /api/login HTTP/1.1\r\nHost: 127.0.0.1\r\nContent-Length: 100\r\n\r\n{")
        assert b" 408 " in s.recv(200)


def test_reset_during_login_leaves_no_valid_session(server):
    users = server.app.state.core.users
    real = users.verify_password

    def verify_then_reset(*args):
        version = real(*args)
        users.update("acme", "ed", password="a brand new password")
        return version

    users.verify_password = verify_then_reset
    status, body = server.login("ed")
    assert status == 200 and server.call("GET", "/api/me", body["token"])[0] == 401


def test_null_fields_and_surrogates_are_rejected(server):
    root = server.token("root")
    base = {"name": "zed", "password": PASSWORD}
    for extra in ({"role": None}, {"groups": None}):
        assert server.call("POST", "/api/users", root, {**base, **extra})[0] == 400
    assert server.call("POST", "/api/login",
                       body=b'{"tenant":"acme","user":"ed","password":"\\ud800"}')[0] == 400


def test_audit_paging_and_unknown_tenants(server):
    root = server.token("root")
    server.call("POST", "/api/login", body={"tenant": "ghost", "user": "x", "password": "y"})
    assert server.app.state.core.store.audit_log("ghost") == []
    server.login("vi", "wrong password!")
    only = server.call("GET", "/api/audit?action=login", root)[1]["events"]
    assert {e["action"] for e in only} == {"login"}
    older = server.call("GET", f"/api/audit?before={only[0]['id']}", root)[1]["events"]
    assert all(e["id"] < only[0]["id"] for e in older)
    assert server.call("GET", "/api/audit?before=x", root)[0] == 400


def test_distributed_guessing_is_capped_per_account(server):
    store = server.app.state.core.store
    for _ in range(30):  # login_attempts * 10, as if from other addresses
        store.hit("login-account:acme:ed", 30, 900)
    assert server.login("ed")[0] == 429
