#!/usr/bin/env bash
# One-time server bootstrap: back up the existing data, then replace snap Docker with the official
# Docker Engine (apt) and put adityag in the docker group. Run ONCE as root on the Sarhad box:
#
#   ssh -t adityag@192.168.100.58 "sudo bash -s" < deploy/server-bootstrap.sh
#
# It is backup-first: if the DB or uploads dump is missing/empty, it ABORTS before anything
# destructive (nothing is removed until the backups are verified on disk). After it prints
# BOOTSTRAP_OK, the rest of the deploy (git clone + restore + rebuild) runs over SSH with no sudo,
# because adityag is then in the docker group.
set -euo pipefail

REPO=/home/adityag/mrr
STAMP="$(date +%Y%m%d-%H%M%S)"
BK="/home/adityag/mrr-backup-${STAMP}"

echo "== [1/6] Back up current data (from snap Docker) -> ${BK} =="
mkdir -p "${BK}"
cd "${REPO}"
# Ensure the DB + api are up so we can dump them (idempotent if already running).
docker compose up -d postgres api
for i in $(seq 1 60); do
  docker compose exec -T postgres pg_isready -U mrr -d mrr >/dev/null 2>&1 && break
  sleep 1
done
docker compose exec -T postgres pg_dump -U mrr -Fc mrr > "${BK}/mrr.dump"
docker compose exec -T api tar czf - -C /app/uploads . > "${BK}/uploads.tgz"
cp "${REPO}/.env" "${BK}/env.bak"
cp -a "${REPO}/secrets" "${BK}/secrets.bak"

echo "== [2/6] Verify backups (GATE: abort if empty) =="
test -s "${BK}/mrr.dump"    || { echo "FATAL: DB dump is empty; aborting, nothing removed."; exit 1; }
test -s "${BK}/uploads.tgz" || { echo "FATAL: uploads archive is empty; aborting, nothing removed."; exit 1; }
ls -la "${BK}"
echo "backup sizes: db=$(du -h "${BK}/mrr.dump" | cut -f1) uploads=$(du -h "${BK}/uploads.tgz" | cut -f1)"

echo "== [3/6] Stop the snap stack + remove snap Docker (data already backed up) =="
docker compose down
snap remove docker

echo "== [4/6] Install the official Docker Engine (apt) =="
apt-get update
apt-get install -y ca-certificates curl
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
chmod a+r /etc/apt/keyrings/docker.asc
CODENAME="$(. /etc/os-release && echo "${VERSION_CODENAME}")"
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu ${CODENAME} stable" \
  > /etc/apt/sources.list.d/docker.list
apt-get update
apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

echo "== [5/6] Enable Docker + add adityag to the docker group =="
systemctl enable --now docker
usermod -aG docker adityag

echo "== [6/6] Done =="
docker --version
docker compose version
echo "BACKUP_DIR=${BK}"
echo "BOOTSTRAP_OK"
