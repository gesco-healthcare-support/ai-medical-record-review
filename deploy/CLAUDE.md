# deploy/ - agent instructions

Proxy config, the Compose `.env` template, and a one-time server bootstrap script. Full docs:
`docs/how-to/deploy-to-the-server.md`, `docs/how-to/back-up-and-restore.md`,
`docs/reference/compose-services.md`.

## Rules

- The repository is PUBLIC. Never write a real hostname, IP address, account name, email address or
  credential into any file here. Use `<SERVER_HOST>`, `<SERVER_USER>` and `CHANGE_ME_...` values.
- `env.docker.example` holds placeholders only. Never paste a real `.env` value into it.
- ASCII only.

## nginx.conf invariants

- Resolve every upstream through a variable (`set $upstream_x host:port; proxy_pass http://$upstream_x;`)
  with the existing `resolver 127.0.0.11`. A literal `proxy_pass http://api:8000` caches the
  container IP at startup and 502s after that container is recreated.
- Keep the download location regex anchored at the token:
  `^/api/documents/[^/]+/downloads/[^/]+$`. Unanchored, it also matches `.../status` and logs every
  status poll as a download.
- Keep `proxy_buffering off` on the download location. The file is patient data; buffering copies it
  into nginx temp files. The API also sends `X-Accel-Buffering: no`.
- Never let a download URL reach the standard access log: the download location logs in the
  `mrr_download` format (first 8 characters of the token only), the status location has
  `access_log off`. Any new location matching `/api/documents/*/downloads/*` must do the same.
- Keep `client_max_body_size 500m` or higher: large records are uploaded through this proxy.
- `/api/` keeps `proxy_read_timeout 300s`; synchronous export routes run long.
- The API's `/health` is NOT routed by the proxy (`/health` goes to Next.js). Probe it inside the
  `api` container.
- A new upstream service needs a `location`, the variable pattern above, and an entry in
  `depends_on` of `proxy` in `docker-compose.yml`.

## env.docker.example invariants

- A key only reaches a container if `docker-compose.yml` names it in `x-backend-env`. Adding a key
  here without the compose line does nothing.
- `backend/tests/test_pool_wiring.py` checks the ROOT `.env.example` against compose, not this file.
  Nothing tests this file; keep it consistent by hand.
- `SECRET_KEY` and `SECURITY_PASSWORD_SALT` must stay present (compose refuses to start without
  them). `ENVIRONMENT=dev` stays the default: `prod` marks the cookie Secure and breaks login over
  plain HTTP.

## server-bootstrap.sh

- One-time, run as root, for a server whose Docker came from snap. Not part of any deploy.
- It must stay backup-first: nothing destructive runs before the `test -s` gate on both backups.
- Do not extend it into a general deploy script; the deploy procedure lives in
  `docs/how-to/deploy-to-the-server.md`.

## Checks after an edit

No automated tests cover this folder. After changing `nginx.conf`:

```bash
docker compose up -d --force-recreate proxy
docker compose logs proxy --since 1m
curl -s -o /dev/null -w '%{http_code}\n' http://localhost:8080/login
curl -s -o /dev/null -w '%{http_code}\n' http://localhost:8080/api/users/me
curl -s -o /dev/null -w '%{http_code}\n' http://localhost:8080/docs/
```

Expect 200, 401, 200, and no `emerg` line in the proxy log. Syntax-check the loaded file with:

```bash
docker compose exec proxy nginx -t
```

Recreate the proxy after every edit, not just restart-and-hope: `nginx.conf` is a single-file bind
mount, and an editor that saves by replacing the file leaves a running container reading the old
copy.
