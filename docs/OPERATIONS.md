# Operations

## Deploy
Needs a server with Docker Compose, ports 80 and 443 open, and a DNS name pointing to it.

```bash
cp .env.example .env     # set FORGEMIND_SECRET, FORGEMIND_DOMAIN and a model provider
chmod 600 .env
docker compose up -d --build --wait
docker compose exec forgemind python -m forgemind user-add --tenant acme --user admin --role admin
```
Check the deployment (creates and then deletes one test document):
```bash
FORGEMIND_PASSWORD='...' python3 scripts/smoke.py https://your.domain acme admin
```

## Users
`user-add`, and `user-update --role/--groups/--password/--disable/--enable`. Admins can also
use `/api/users`. Any change signs that user out.

## Backup and restore
```bash
docker compose exec forgemind python -m forgemind backup /data/backup-$(date +%F).db
docker compose cp forgemind:/data/backup-$(date +%F).db .
```
Copy backups off the server; they contain all documents and password hashes. Delete old copies
in `/data`. To restore:
```bash
docker compose stop forgemind
docker compose run --rm --no-deps forgemind sh -c "rm -f /data/forgemind.db-wal /data/forgemind.db-shm"
docker compose cp ./backup.db forgemind:/data/forgemind.db
docker compose run --rm --no-deps --user root --cap-add CHOWN forgemind chown app /data/forgemind.db
docker compose start forgemind
```
Rehearse a restore before you depend on it.

## Upgrade
Back up, `git pull`, `docker compose up -d --build --wait`. Schema migrations run at start.
Going back to an older version means restoring the backup.

Upgrading from 0.1: old CLI-issued tokens stop working. Create accounts with `user-add`.

## Monitoring
- `/ready` fails if the database or ingestion worker is unavailable.
- Set `FORGEMIND_METRICS_TOKEN` to scrape `/metrics`. Counters reset on restart.
- Logs are JSON on stdout with a request id, also returned as `X-Request-ID`. Configure
  Docker log rotation on the host.
- Schedule `python -m forgemind purge --days 365` to match your retention policy.

## Capacity
Synthetic benchmark (`python -m scripts.benchmark 20000`) on the development machine, with
offline embeddings: 20,000 chunks gave a 2.2 s index build, 6 ms median search and 413 MB peak
memory. It excludes model and embedding API time and concurrent load. Semantic embeddings use
more memory (1536 dimensions against 1024). Load-test with your own documents.

## Separate worker (optional)
Set `FORGEMIND_EMBEDDED_WORKER=0` and run `python -m forgemind worker` against the same
database on the same machine. SQLite must not be shared over a network filesystem.
