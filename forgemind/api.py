"""HTTP API (Starlette)."""
import asyncio
import contextlib
import hmac
import json
import logging
import threading
import time
import uuid
from pathlib import Path

import jwt
from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException
from starlette.middleware import Middleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, PlainTextResponse
from starlette.routing import Route

from .auth import MAX_PASSWORD, NAME
from .config import Settings
from .jobs import QueueFull
from .net import ProviderError
from .observability import Metrics, configure_logging
from .service import ForgeMind

STATIC = Path(__file__).parent / "static"
log = logging.getLogger("forgemind.http")
SECURITY_HEADERS = [
    (b"content-security-policy", b"default-src 'self'; frame-ancestors 'none'; base-uri 'none'; "
                                 b"form-action 'self'"),
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"DENY"),
    (b"referrer-policy", b"no-referrer"),
]


class Observe:
    """Request id, security headers, access log, metrics and a last-resort error response."""

    def __init__(self, app, metrics):
        self.app, self.metrics = app, metrics

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        request_id, started, status = uuid.uuid4().hex[:16], time.perf_counter(), [500]
        sent = [False]

        async def wrapped(message):
            if message["type"] == "http.response.start":
                sent[0], status[0] = True, message["status"]
                headers = list(message.get("headers", []))
                headers += SECURITY_HEADERS + [(b"x-request-id", request_id.encode())]
                if scope["path"].startswith("/api/"):
                    headers.append((b"cache-control", b"no-store"))
                message = {**message, "headers": headers}
            await send(message)

        try:
            await self.app(scope, receive, wrapped)
        except Exception:
            log.exception("unhandled error", extra={"request_id": request_id})
            if not sent[0]:
                await JSONResponse({"error": "internal error", "request_id": request_id},
                                   status_code=500)(scope, receive, wrapped)
        finally:
            endpoint = scope.get("endpoint")
            route = getattr(endpoint, "__name__", "unmatched")  # bounded label set
            seconds = time.perf_counter() - started
            method = scope["method"] if scope["method"] in (
                "GET", "POST", "PATCH", "DELETE", "HEAD", "OPTIONS") else "OTHER"
            self.metrics.record(method, route, status[0], seconds)
            log.info("request", extra={"request_id": request_id, "method": method,
                                       "route": route, "status": status[0],
                                       "ms": round(seconds * 1000, 1)})


def create_app(settings=None):
    settings = settings or Settings()
    secret = settings.require_secret()
    configure_logging()
    core = ForgeMind(settings)
    metrics = Metrics()
    stop = threading.Event()

    def client_ip(request):
        return request.client.host if request.client else "unknown"

    async def principal(request: Request):
        scheme, _, token = request.headers.get("authorization", "").partition(" ")
        try:
            if scheme.lower() != "bearer":
                raise jwt.InvalidTokenError()
            p = await run_in_threadpool(core.users.authenticate, secret, token)
        except jwt.InvalidTokenError:
            raise HTTPException(401, "invalid or expired session")
        key = f"api:{p.tenant}:{p.user}"
        if not await run_in_threadpool(core.store.hit, key, settings.rate_limit_per_minute, 60):
            raise HTTPException(429, "rate limit exceeded")
        return p

    async def admin(request):
        p = await principal(request)
        if not p.is_admin():
            raise HTTPException(403, "admin only")
        return p

    async def read_limited(request, limit, deadline):
        """Bounded in size and time, so stalled senders cannot hold connections open."""
        async def read():
            body = bytearray()
            async for part in request.stream():
                body += part
                if len(body) > limit:
                    raise HTTPException(413, "request too large")
            return bytes(body)

        try:
            return await asyncio.wait_for(read(), deadline)
        except TimeoutError:
            raise HTTPException(408, "request body took too long")

    async def read_json(request):
        try:
            data = json.loads(await read_limited(request, 16 * 1024, settings.body_timeout))
            json.dumps(data, ensure_ascii=False).encode()  # rejects unpaired surrogates
        except (ValueError, RecursionError):
            raise HTTPException(400, "send a JSON object")
        if not isinstance(data, dict):
            raise HTTPException(400, "send a JSON object")
        return data

    def audit_event(p, action, detail):
        return run_in_threadpool(core.store.log, p.tenant, p.user, action, detail)

    # --- public ---
    async def health(request):
        return JSONResponse({"status": "ok"})

    async def ready(request):
        try:
            await run_in_threadpool(core.store.revision, "_ready")
        except Exception:
            log.exception("database not ready")
            return JSONResponse({"status": "database unavailable"}, status_code=503)
        jobs = core.jobs
        if settings.embedded_worker and not jobs.busy and time.time() - jobs.heartbeat > 30:
            return JSONResponse({"status": "worker not running"}, status_code=503)
        return JSONResponse({"status": "ready"})

    async def metrics_endpoint(request):
        expected = f"Bearer {settings.metrics_token}".encode()
        given = request.headers.get("authorization", "").encode()
        if not settings.metrics_token or not hmac.compare_digest(given, expected):
            raise HTTPException(404, "not found")
        return PlainTextResponse(metrics.render())

    async def static(request):
        name = {"/": "index.html", "/app.js": "app.js", "/style.css": "style.css"}[request.url.path]
        return FileResponse(STATIC / name)

    async def login(request):
        data = await read_json(request)
        tenant, user, password = (data.get(k) for k in ("tenant", "user", "password"))
        if not (isinstance(tenant, str) and NAME.fullmatch(tenant)
                and isinstance(user, str) and NAME.fullmatch(user)
                and isinstance(password, str) and 0 < len(password) <= MAX_PASSWORD):
            raise HTTPException(400, "tenant, user and password are required")
        ip = client_ip(request)
        specific = f"login:{tenant}:{user}:{ip}"
        for key, limit in ((f"login-ip:{ip}", settings.login_attempts * 10),
                           (specific, settings.login_attempts),
                           # All addresses together, against distributed guessing.
                           (f"login-account:{tenant}:{user}", settings.login_attempts * 10)):
            if not await run_in_threadpool(core.store.hit, key, limit, 900):
                raise HTTPException(429, "too many attempts; try again later")
        version = await run_in_threadpool(core.users.verify_password, tenant, user, password)
        if version is None:
            if await run_in_threadpool(core.users.tenant_exists, tenant):
                await run_in_threadpool(core.store.log, tenant, user, "login_failed", {"ip": ip})
            raise HTTPException(401, "wrong tenant, user or password")
        await run_in_threadpool(core.store.reset, specific)
        try:  # a reset or disable since the check leaves this token already revoked
            token = await run_in_threadpool(core.users.issue_token, secret, tenant, user,
                                            settings.token_ttl, version)
        except KeyError:
            raise HTTPException(401, "wrong tenant, user or password")
        await run_in_threadpool(core.store.log, tenant, user, "login", {"ip": ip})
        return JSONResponse({"token": token, "expires_in": settings.token_ttl})

    # --- session ---
    async def me(request):
        p = await principal(request)
        return JSONResponse({"tenant": p.tenant, "user": p.user, "role": p.role,
                             "groups": list(p.groups)})

    async def logout(request):
        p = await principal(request)
        await run_in_threadpool(core.users.revoke, p.tenant, p.user)
        await audit_event(p, "logout", {})
        return JSONResponse({"signed_out": True})

    async def change_password(request):
        p = await principal(request)
        data = await read_json(request)
        current, new = data.get("current"), data.get("new")
        if not (isinstance(current, str) and 0 < len(current) <= MAX_PASSWORD
                and isinstance(new, str)):
            raise HTTPException(400, "current and new passwords are required")
        key = f"password:{p.tenant}:{p.user}"
        if not await run_in_threadpool(core.store.hit, key, settings.login_attempts, 900):
            raise HTTPException(429, "too many attempts; try again later")
        version = await run_in_threadpool(core.users.verify_password, p.tenant, p.user, current)
        if version is None:
            raise HTTPException(403, "current password is wrong")
        try:
            await run_in_threadpool(lambda: core.users.update(p.tenant, p.user, password=new,
                                                        expect_version=version))
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        await audit_event(p, "password_changed", {})
        return JSONResponse({"changed": True})

    # --- documents ---
    async def upload(request):
        p = await principal(request)
        if not p.can_write():
            raise HTTPException(403, "viewers cannot add documents")
        # Raw body upload: ?filename=policy.pdf&groups=hr,finance
        filename = Path(request.query_params.get("filename", "")).name
        if not filename or len(filename) > 200 or not filename.isprintable():
            raise HTTPException(400, "a valid filename query parameter is required")
        groups = [g.strip() for g in request.query_params.get("groups", "").split(",") if g.strip()]
        if len(groups) > 20 or not all(NAME.fullmatch(g) for g in groups):
            raise HTTPException(400, "invalid groups")
        data = await read_limited(request, settings.max_upload_bytes,
                                  settings.upload_timeout)
        try:
            job_id = await run_in_threadpool(core.jobs.submit, p, filename, data, groups)
        except PermissionError as exc:
            raise HTTPException(403, str(exc))
        except QueueFull as exc:
            raise HTTPException(429, str(exc))
        return JSONResponse({"job_id": job_id, "status": "queued"}, status_code=202)

    async def job(request):
        p = await principal(request)
        found = await run_in_threadpool(core.jobs.get, p, request.path_params["job_id"])
        if found is None:
            raise HTTPException(404, "not found")
        return JSONResponse(found)

    async def documents(request):
        p = await principal(request)
        try:
            limit = min(max(int(request.query_params.get("limit", 100)), 1), 500)
            offset = min(max(int(request.query_params.get("offset", 0)), 0), 10 ** 9)
        except ValueError:
            raise HTTPException(400, "limit and offset must be integers")
        found = await run_in_threadpool(core.store.list_documents, p, limit, offset)
        return JSONResponse({"documents": found})

    async def delete_document(request):
        p = await principal(request)
        doc_id = request.path_params["doc_id"]
        outcome = await run_in_threadpool(core.store.delete_document, p, doc_id)
        if outcome == "missing":
            raise HTTPException(404, "not found")
        if outcome == "forbidden":
            raise HTTPException(403, "only the owner or an admin can delete")
        await audit_event(p, "delete", {"doc_id": doc_id})
        return JSONResponse({"deleted": doc_id})

    async def ask(request):
        p = await principal(request)
        question = (await read_json(request)).get("question")
        if not isinstance(question, str) or not 0 < len(question.strip()) <= 2000:
            raise HTTPException(400, "question must be 1-2000 characters")
        try:
            return JSONResponse(await run_in_threadpool(core.ask, p, question.strip()))
        except ProviderError:
            raise HTTPException(502, "model provider unavailable")

    # --- administration (always limited to the admin's own tenant) ---
    async def list_users(request):
        p = await admin(request)
        return JSONResponse({"users": await run_in_threadpool(core.users.list, p.tenant)})

    async def create_user(request):
        p = await admin(request)
        data = await read_json(request)
        try:
            await run_in_threadpool(core.users.create, p.tenant, data.get("name"),
                                    data.get("password"), data.get("role", "viewer"),
                                    data.get("groups", []))
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        await audit_event(p, "user_created", {"name": data["name"]})
        return JSONResponse({"created": data["name"]}, status_code=201)

    async def update_user(request):
        p = await admin(request)
        name = request.path_params["name"]
        data = await read_json(request)
        if not data or set(data) - {"role", "groups", "password", "active"}:
            raise HTTPException(400, "allowed fields: role, groups, password, active")
        try:
            await run_in_threadpool(lambda: core.users.update(p.tenant, name, **data))
        except KeyError:
            raise HTTPException(404, "not found")
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        await audit_event(p, "user_updated", {"name": name, "fields": sorted(data)})
        return JSONResponse({"updated": name})

    async def audit(request):
        p = await admin(request)
        query = request.query_params
        try:
            limit = min(max(int(query.get("limit", 100)), 1), 500)
            before = int(query["before"]) if "before" in query else None
            if before is not None and not 0 <= before < 2 ** 62:
                raise ValueError
        except ValueError:
            raise HTTPException(400, "limit and before must be integers")
        events = await run_in_threadpool(core.store.audit_log, p.tenant, limit, before,
                                         query.get("action"))
        return JSONResponse({"events": events})

    async def on_error(request, exc):
        return JSONResponse({"error": exc.detail}, status_code=exc.status_code)

    @contextlib.asynccontextmanager
    async def lifespan(app):
        stop.clear()
        thread = None
        if settings.embedded_worker:
            core.jobs.heartbeat = time.time()
            thread = threading.Thread(target=core.jobs.run_forever, args=(stop,), daemon=True)
            thread.start()
        yield
        stop.set()
        if thread:
            thread.join(10)

    hosts = [h.strip() for h in settings.allowed_hosts.split(",") if h.strip()]
    app = Starlette(
        lifespan=lifespan,
        middleware=[Middleware(Observe, metrics=metrics),
                    Middleware(TrustedHostMiddleware, allowed_hosts=hosts)],
        exception_handlers={HTTPException: on_error},
        routes=[
            Route("/", static), Route("/app.js", static), Route("/style.css", static),
            Route("/health", health), Route("/ready", ready),
            Route("/metrics", metrics_endpoint),
            Route("/api/login", login, methods=["POST"]),
            Route("/api/logout", logout, methods=["POST"]),
            Route("/api/me", me),
            Route("/api/password", change_password, methods=["POST"]),
            Route("/api/documents", documents, methods=["GET"]),
            Route("/api/documents", upload, methods=["POST"]),
            Route("/api/documents/{doc_id}", delete_document, methods=["DELETE"]),
            Route("/api/jobs/{job_id}", job),
            Route("/api/ask", ask, methods=["POST"]),
            Route("/api/users", list_users, methods=["GET"]),
            Route("/api/users", create_user, methods=["POST"]),
            Route("/api/users/{name}", update_user, methods=["PATCH"]),
            Route("/api/audit", audit),
        ])
    app.state.core = core
    return app
