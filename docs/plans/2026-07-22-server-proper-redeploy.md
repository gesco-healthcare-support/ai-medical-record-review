---
feature: Rebuild the Sarhad server deploy the standard way (Docker Engine + git clone + data migration)
date: 2026-07-22
status: draft
base-branch: main
related-issues: []
---

## Goal

Replace the non-standard server deploy (snap Docker + file-transferred code, no git) with the
standard setup: **apt Docker Engine + a `git clone` of `main` + the existing data migrated in**,
with zero data loss. End state: `/home/adityag/mrr` is a real git checkout on `main` (`c6f03ea`,
the hang fix), Docker runs via the official Engine with `adityag` in the `docker` group (no sudo
for docker), and the app serves the same data on `http://192.168.100.58:8080`.

## Context & decisions

Why now: Adrian's standard is Docker-Engine + git-clone deploys; the box currently runs the **snap**
Docker build (root-only socket, no `docker` group -> forced the sudo friction) and its code was put
there by **file transfer** (no `.git`). Adrian approved rebuilding it correctly, executing now,
accepting brief downtime (business hours, ~9 AM PDT 2026-07-22).

Server facts (verified read-only this session): Ubuntu 24.04.1 (noble), amd64; git 2.43.0 present;
snap Docker only (compose v2 plugin v5.1.1 present); GitHub reachable (200); disk 59G free of 97G;
repo at `/home/adityag/mrr` (owned by `adityag`, 22M); `.env` = `ENVIRONMENT=dev`, Vertex on,
project `gen-lang-client-0785241985`, `GOOGLE_CLOUD_LOCATION=global`, `GOOGLE_APPLICATION_CREDENTIALS`
-> `/secrets/adc.json` (today's ADC already placed, sha `823c62ed…`). Compose project name `mrr`,
services `postgres redis api web proxy segment-worker summarize-worker`.

Resolved decisions:
- Decision: **logical** data migration (`pg_dump` + uploads `tar`), not volume copy, because snap
  Docker volumes (`/var/snap/docker/...`) and Engine volumes (`/var/lib/docker/...`) are separate
  stores; a dump/restore is portable and survives the snap removal.
- Decision: **plain anonymous HTTPS `git clone`** -- the repo is PUBLIC (verified: `private:false`,
  `visibility:public`; anonymous `git ls-remote` returns the `main` tip `c6f03ea`). No deploy key,
  token, or auth needed on the box. (I wrongly assumed private in the first draft; corrected after
  checking.) Future updates are `git pull` in the checkout.
- Decision: **remove snap Docker** after Engine is in and data is restored (Adrian: "remove the
  hosted app if necessary"), keeping the backups as the rollback.
- Decision: keep `ENVIRONMENT=dev` + HTTP today (prod + TLS + durable SA key are a separate pass).

## Prerequisite (Adrian does this; I cannot -- it needs the sudo password)

- **Root bootstrap (the ONLY thing you run).** `adityag` is an admin (sudo) user, so this is one
  command: `ssh -t adityag@192.168.100.58 "sudo bash -s" < deploy/server-bootstrap.sh` (password
  once). The script backs up (from snap) + verifies + stops the snap stack + installs Docker Engine
  + removes snap Docker + adds `adityag` to `docker`. Reviewable; `set -euo pipefail` with a
  backup-size gate BEFORE any destructive step. No GitHub auth needed (public repo).

## Tasks (implementation blueprint)

### T1 -- Backup + Docker Engine bootstrap (Adrian runs; script is mine)  [approach: code]
- what: CREATE `deploy/server-bootstrap.sh` (committed). Steps, `set -euo pipefail`:
  1. `cd /home/adityag/mrr`; `BK=/home/adityag/mrr-backup-<ts>`; `mkdir`.
  2. `docker compose exec -T postgres pg_dump -U mrr -Fc mrr > $BK/mrr.dump`
  3. `docker compose exec -T api tar czf - -C /app/uploads . > $BK/uploads.tgz`
  4. `cp .env $BK/; cp -a secrets $BK/`; `test -s $BK/mrr.dump && test -s $BK/uploads.tgz` (GATE).
  5. `docker compose down` (keeps snap volumes until step 7).
  6. install Engine: docker apt repo for `noble`/`amd64` + `apt-get install docker-ce docker-ce-cli
     containerd.io docker-buildx-plugin docker-compose-plugin`.
  7. `snap remove docker`; `usermod -aG docker adityag`; `systemctl enable --now docker`.
  8. print `$BK` path + `echo BOOTSTRAP_OK`.
- acceptance (EARS): WHEN the script finishes, THE SYSTEM SHALL have `$BK/mrr.dump` (>0) +
  `$BK/uploads.tgz` (>0) on disk, Docker Engine active (`docker` in a fresh session, no sudo), and
  snap Docker removed. IF the backup gate fails, THE SYSTEM SHALL abort before removing anything.

### T2b -- git clone + restore config (mine, over key)  [approach: code]
- what: `mv /home/adityag/mrr /home/adityag/mrr.filecopy.bak`;
  `git clone https://github.com/gesco-healthcare-support/ai-medical-record-review.git /home/adityag/mrr`
  (anonymous, public repo); `git -C /home/adityag/mrr checkout main`; restore `cp mrr.filecopy.bak/.env mrr/.env` and
  `cp mrr.filecopy.bak/secrets/adc.json mrr/secrets/adc.json` (chmod 600).
- acceptance (EARS): WHEN the clone completes, THE SYSTEM SHALL show `git rev-parse HEAD` == the
  `main` tip and `.env` + `secrets/adc.json` restored identical to the pre-migration copies.

### T4 -- Restore data + bring up (mine, over key)  [approach: code]
- what: `docker compose up -d postgres redis`; wait healthy; recreate the DB clean then
  `docker compose exec -T postgres pg_restore -U mrr -d mrr --clean --if-exists < $BK/mrr.dump`
  (or create db + restore); restore uploads:
  `docker compose exec -T api sh -c 'mkdir -p /app/uploads && tar xzf - -C /app/uploads' < $BK/uploads.tgz`;
  `docker compose run --rm api alembic upgrade head`; `docker compose build`; `docker compose up -d`.
- acceptance (EARS): WHEN the stack is up, THE SYSTEM SHALL report the SAME document + summary counts
  as the pre-migration DB, all 7 containers healthy.

### T5 -- Verify (mine, over key)  [approach: code]
- what: container health; live Vertex call from a container (the wired ADC); `curl :8080` 200;
  `/api/users/me` 401; row/summary counts match backup; worker logs show the new stage/id lines.
- acceptance (EARS): WHEN verification runs, THE SYSTEM SHALL pass all of: proxy 200, Vertex OK,
  counts match, containers healthy.

## Validation loop
- `git ls-remote ...` (T1), `echo BOOTSTRAP_OK` + backup files present (T2), `git rev-parse HEAD` (T3),
  doc/summary count parity pre/post (T4), proxy 200 + Vertex OK + counts (T5).

## Risk / rollback
Blast radius: the whole server app; **irreversible if backups are bad** -> the backup-size GATE in
T2 (step 4) must pass before `snap remove` (step 7). Downtime ~15-30 min (Engine install + rebuild).
Rollback: the app is down only between T2 and T5. If T3-T5 fail, restore from `$BK` into a fresh
compose up; worst case `snap install docker` + restore `$BK` to return to the prior state. Backups
are kept until Adrian confirms the new stack is good.
Data at risk: 13 docs + ~690 summaries + uploaded PDFs -- all dumped in T2 before anything destructive.
