# deploy

Everything the running stack needs from outside the application code: the reverse proxy
configuration, the template for the root `.env` used by the Compose stack, and the one-time script
that prepared the server's Docker installation. `docker-compose.yml` at the repository root mounts
`nginx.conf`; the other two files are used by hand.

| File | What it is |
| --- | --- |
| `nginx.conf` | Configuration of the `proxy` service (nginx 1.30), mounted read-only at `/etc/nginx/conf.d/default.conf`. Listens on port 80 (published as 8080) and routes `/api/` to `api:8000`, `/docs/` to `docs:80` and everything else to `web:3000`. Download URLs get their own locations: unbuffered, with a one-line download log that records only the first 8 characters of the token; their status polls are not logged. Upstreams resolve per request through Docker's DNS, so recreating a container never leaves the proxy pointing at an old address. |
| `env.docker.example` | Template for the root `.env` when running the Compose stack: the two required secrets, `POSTGRES_PASSWORD`, `ENVIRONMENT` and the four Vertex settings. Every other setting takes its default from `docker-compose.yml`. Copy it with `cp deploy/env.docker.example .env` and fill it in; never commit the result. |
| `server-bootstrap.sh` | One-time setup for a server whose Docker came from snap. It backs up the database, the uploads volume, `.env` and `secrets/`, stops if either data backup is empty, then removes snap Docker, installs Docker Engine and the Compose plugin from Docker's apt repository, and adds the deploy account to the `docker` group. It is run once as root (`ssh -t <SERVER_USER>@<SERVER_HOST> "sudo bash -s" < deploy/server-bootstrap.sh`), is not part of a normal deploy, and does not restore anything: restoring and rebuilding happen afterwards over SSH. The account is the one that ran `sudo` (or `APP_USER` if set), and the checkout must be at `/home/<account>/mrr`. |

## How it is used

- Locally and on the server, Compose mounts `nginx.conf` into the `proxy` container. A change to
  it needs no image build, only `docker compose up -d --force-recreate proxy`.
- `env.docker.example` is read by nobody at run time; it is copied once to `.env`. A key added to
  `.env` reaches the backend containers only if `docker-compose.yml` names it in its
  `x-backend-env` block.
- Nothing here has automated tests. After changing `nginx.conf`, recreate the proxy and check the
  routes through port 8080: `/login` returns 200, `/api/users/me` returns 401 without a session,
  `/docs/` returns 200.

## Documentation

- [How to run the app locally](../docs/how-to/run-the-app-locally.md)
- [How to deploy to the server](../docs/how-to/deploy-to-the-server.md)
- [How to back up and restore](../docs/how-to/back-up-and-restore.md)
- [Compose services reference](../docs/reference/compose-services.md), including every proxy route

<!-- reviewed: 2026-09-30 -->
