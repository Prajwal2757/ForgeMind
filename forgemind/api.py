"""HTTP API (Starlette)."""
import json
import logging
import time
from collections import defaultdict, deque
from pathlib import Path

import jwt
from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse
from starlette.routing import Route

from .auth import verify_token
from .config import Settings
from .service import ForgeMind

STATIC = Path(__file__).parent / "static"
log = logging.getLogger("forgemind")


def create_app(settings=None):
    settings = settings or Settings()
    secret = settings.require_secret()
    core = ForgeMind(settings)
    calls = defaultdict(deque)

    def principal(request: Request):
        scheme, _, token = request.headers.get("authorization", "").partition(" ")
        try:
            if scheme.lower() != "bearer":
                raise jwt.InvalidTokenError()
            p = verify_token(secret, token)
        except jwt.InvalidTokenError:
            raise HTTPException(401, "invalid or missing token")
        # In-process limiter: per worker, resets on restart.
        window, now = calls[(p.tenant, p.user)], time.monotonic()
        while window and now - window[0] > 60:
            window.popleft()
        if len(window) >= settings.rate_limit_per_minute:
            raise HTTPException(429, "rate limit exceeded")
        window.append(now)
        return p

    async def read_limited(request):
        body = bytearray()
        async for part in request.stream():
            body += part
            if len(body) > settings.max_upload_bytes:
                raise HTTPException(413, "request too large")
        return bytes(body)

    async def health(request):
        return JSONResponse({"status": "ok", "llm": core.agent.llm.name,
                             "embeddings": core.embedder.name})

    async def index(request):
        return FileResponse(STATIC / "index.html", headers={
            "Content-Security-Policy": "default-src 'self'; script-src 'unsafe-inline'; "
                                       "style-src 'unsafe-inline'; connect-src 'self'"})

    async def upload(request):
        p = principal(request)
        if not p.can_write():
            raise HTTPException(403, "viewers cannot add documents")
        # Raw body upload: ?filename=policy.pdf&groups=hr,finance
        filename = Path(request.query_params.get("filename", "")).name
        if not filename:
            raise HTTPException(400, "filename query parameter is required")
        groups = [g.strip() for g in request.query_params.get("groups", "").split(",") if g.strip()]
        data = await read_limited(request)
        try:
            result = await run_in_threadpool(core.ingest, p, filename, data, groups)
        except PermissionError as exc:
            raise HTTPException(403, str(exc))
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        return JSONResponse(result, status_code=201)

    async def documents(request):
        return JSONResponse({"documents": core.store.list_documents(principal(request))})

    async def delete(request):
        p = principal(request)
        doc_id = request.path_params["doc_id"]
        outcome = core.store.delete_document(p, doc_id)
        if outcome == "missing":
            raise HTTPException(404, "not found")
        if outcome == "forbidden":
            raise HTTPException(403, "only the owner or an admin can delete")
        core.store.log(p, "delete", {"doc_id": doc_id})
        return JSONResponse({"deleted": doc_id})

    async def ask(request):
        p = principal(request)
        try:
            question = json.loads(await read_limited(request))["question"].strip()
        except (ValueError, KeyError, TypeError, AttributeError):
            raise HTTPException(400, 'send JSON: {"question": "..."}')
        if not 0 < len(question) <= 2000:
            raise HTTPException(400, "question must be 1-2000 characters")
        try:
            return JSONResponse(await run_in_threadpool(core.ask, p, question))
        except OSError:
            log.exception("model provider call failed")
            raise HTTPException(502, "model provider unavailable")

    async def audit(request):
        p = principal(request)
        if not p.is_admin():
            raise HTTPException(403, "admin only")
        return JSONResponse({"events": core.store.audit_log(p.tenant)})

    async def on_error(request, exc):
        return JSONResponse({"error": exc.detail}, status_code=exc.status_code)

    app = Starlette(routes=[
        Route("/", index),
        Route("/health", health),
        Route("/api/documents", documents, methods=["GET"]),
        Route("/api/documents", upload, methods=["POST"]),
        Route("/api/documents/{doc_id}", delete, methods=["DELETE"]),
        Route("/api/ask", ask, methods=["POST"]),
        Route("/api/audit", audit),
    ], exception_handlers={HTTPException: on_error})
    app.state.core = core
    return app
