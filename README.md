# ForgeMind: Enterprise Agentic RAG Platform

Ask questions over company documents and get answers with citations, limited to what the
person asking is allowed to see. Built to run on a single server.

## Features
- PDF, DOCX, Markdown and text, processed by a persistent background queue
- Hybrid retrieval (BM25 + vectors, reciprocal rank fusion)
- Agent loop: plan, retrieve, grade, reformulate, answer, check citations, or abstain
- Organisations (tenants), viewer / editor / admin roles, per-document groups
- User accounts with hashed passwords, immediate session revocation, login throttling
- Audit log, rate limits, quotas, JSON logs, Prometheus metrics, readiness checks
- HTTPS deployment with Docker Compose and Caddy, backups, schema migrations

## Try it locally (Python 3.13)
```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
export FORGEMIND_SECRET=$(python -c "import secrets;print(secrets.token_hex(32))")
python -m forgemind user-add --tenant acme --user admin --role admin   # asks for a password
python -m forgemind ingest --tenant acme examples/docs/*.md
python -m forgemind serve        # http://127.0.0.1:8000, organisation "acme"
```
Deploying for real: see [docs/OPERATIONS.md](docs/OPERATIONS.md).
Security design: see [docs/SECURITY.md](docs/SECURITY.md).

## Answer quality
The default `extractive` mode needs no API key and only quotes matching sentences. It can refuse
a reworded question ("daily meal limit when travelling") and can answer a near miss with the
wrong fact ("parental leave" returns the annual leave line). **Do not rely on it for real use.**

Set `FORGEMIND_LLM=anthropic` with `ANTHROPIC_API_KEY`, or `FORGEMIND_LLM=openai` with
`OPENAI_API_KEY` and `FORGEMIND_LLM_MODEL`. The default embeddings are character hashes, not
semantic; set `FORGEMIND_EMBEDDINGS=openai` for semantic search, then run
`python -m forgemind reindex`. With any remote provider, document text and questions are sent
to that provider.

The bundled eval is 9 questions written alongside the code, with a small synonym list in
`agent.py`. It catches regressions and says nothing about accuracy on your documents.

## API
All `/api` routes except login need `Authorization: Bearer <token>`.

| Method and path | Who | Notes |
|---|---|---|
| POST `/api/login` | anyone | `{"tenant","user","password"}` |
| POST `/api/logout`, `/api/password`; GET `/api/me` | signed in | logout ends all your sessions |
| POST `/api/documents?filename=x.pdf&groups=hr` | editor, admin | raw body; returns a job id |
| GET `/api/jobs/{id}` | uploader, admin | `queued`, `running`, `done`, `failed` |
| GET `/api/documents?limit=&offset=` | signed in | only what you can see |
| DELETE `/api/documents/{id}` | owner, admin | |
| POST `/api/ask` | signed in | `{"question"}` |
| GET, POST `/api/users`; PATCH `/api/users/{name}` | admin | own tenant only |
| GET `/api/audit?limit=&before=&action=` | admin | own tenant only |
| GET `/health`, `/ready` | anyone | |
| GET `/metrics` | metrics token | off unless configured |

## Tests
```bash
pip install -r requirements-dev.txt
python -m pytest -q
python -m forgemind eval
```

## Known limits
- One server, SQLite. No high availability or horizontal scaling.
- No single sign-on (OIDC/SAML) and no multi-factor authentication.
- No OCR for scanned documents; no connectors to Drive, SharePoint and similar.
- A tenant's search index is rebuilt in memory after each upload or delete.
- The citation check confirms citations point to retrieved text, not that each claim is true.
- The Anthropic and OpenAI-compatible providers are tested against a local stand-in only,
  never the live services.
