# Security

## Controls
- Passwords: scrypt with per-user salt, 12-128 characters.
- Sessions: signed, expiring tokens. Role, groups and status are read from the database on
  every request.
- Login throttling per address, per user and address, and per account.
- Tenant and group filtering happens before search scoring and before any model call.
- Uploads: size, queue and storage limits; request body deadlines; parsing in a separate
  process with CPU, memory and time limits and without the app's secrets in its environment.
- Retrieved text is marked as untrusted in prompts; answers without valid citations are dropped.
- Web: strict Content Security Policy, no inline script, text-only DOM insertion.
- Container: non-root, read-only filesystem, no Linux capabilities, TLS through Caddy.

## Residual risks
- The parser process is not isolated from the filesystem or network. A parser exploit would
  run with the app's access. Only let trusted people upload.
- Prompt injection from document text can be reduced but not ruled out.
- Someone who knows a user name can lock that account for 15 minutes by guessing.
- The database is not encrypted by the app. Use disk encryption.
- Admins see every document in their tenant, and the audit log stores questions.
- Anyone with shell access to the server has full access.
- No independent penetration test has been done.

Rotating `FORGEMIND_SECRET` signs everyone out.
