# ForgeMind: Enterprise Agentic RAG Platform

Ask questions over company documents and get answers with citations, limited to what the
asker is allowed to see.

## Features
- Ingests PDF, DOCX, Markdown and text
- Hybrid retrieval: BM25 + vector similarity, merged by reciprocal rank fusion
- Agent loop: plan, retrieve, grade, reformulate, answer, verify citations, or abstain
- Multi-tenant, with roles (viewer / editor / admin) and per-document groups
- Permission filtering before scoring, so hidden text never reaches the model
- Audit log, upload size limit, per-user rate limit
- Web UI, HTTP API, CLI, Docker, CI, regression evals

## Architecture
```
upload -> extract -> chunk -> embed -> SQLite
question -> auth -> plan -> [permission filter -> BM25 + vectors] -> grade -> (reformulate)
                                      -> answer -> citation check -> response + audit
```

## Quick start
```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
export FORGEMIND_SECRET=$(python -c "import secrets;print(secrets.token_hex(32))")
python -m forgemind ingest --tenant acme examples/docs/*.md
python -m forgemind token --tenant acme --user you --role admin
python -m forgemind serve      # open http://127.0.0.1:8000 and paste the token
```
Docker: `cp .env.example .env`, set `FORGEMIND_SECRET`, then `docker compose up --build`.

## Using a real model
The default `extractive` mode quotes matching sentences. It needs no API key and is meant for
demos and tests, not for reasoning over documents. Observed limits: it can refuse a reworded
question ("daily meal limit when travelling"), and it can answer a near-miss with the wrong fact
("parental leave" returns the annual leave line). Use a real model for anything that matters. For generated answers:
```bash
export FORGEMIND_LLM=anthropic ANTHROPIC_API_KEY=...
```
or `FORGEMIND_LLM=openai` with `OPENAI_API_KEY` and `FORGEMIND_LLM_MODEL` (any compatible
endpoint via `OPENAI_BASE_URL`). In these modes the question and retrieved text are sent to
that provider.

The default embeddings are character n-gram hashes, which are not semantic. Set
`FORGEMIND_EMBEDDINGS=openai` for semantic search. Documents must be re-ingested after
changing embedding provider.

## API
All `/api` routes need `Authorization: Bearer <token>`.

| Method | Path | Notes |
|---|---|---|
| POST | `/api/documents?filename=x.pdf&groups=hr` | raw file body; editor or admin |
| GET | `/api/documents` | only documents you can see |
| DELETE | `/api/documents/{id}` | owner or admin |
| POST | `/api/ask` | `{"question": "..."}` |
| GET | `/api/audit` | admin, own tenant |
| GET | `/health` | no auth |

## Tests
```bash
pip install -r requirements-dev.txt
python -m pytest -q
python -m forgemind eval
```

The bundled eval is a 9-question regression check written alongside the code, including a small
synonym list in `agent.py`. It is not a measure of accuracy on your documents.

## Not production-ready yet
- Tokens are issued by CLI; there is no SSO/OIDC and no token revocation
- Retrieval scans all of a tenant's chunks in memory; use a vector database beyond small corpora
- Ingestion runs inside the request; no job queue, OCR or connectors
- Rate limiting is per process
- The Anthropic and OpenAI-compatible providers have not been run against the live services
- PDF/DOCX parsing is not sandboxed; only the compressed upload size is limited
- Serve behind a TLS-terminating proxy
- The citation check confirms citations point to retrieved sources, not that each claim is true
- Group names and permissions come from the token; there is no user directory
