#!/usr/bin/env bash
# The staging acceptance stage: tests a commit as it would run in production, before a promotion to production.
# Run by .github/workflows/staging-acceptance.yml from the root of STAGING's code, with PRODUCTION's code checked
# out in ./production-code. Documented in docs/reference/ci-and-merge-gates.md.
#
#   1. Build the images once (api, web, docs) and scan them: Grype fails on a critical vulnerability with a fix.
#   2. Upgrade test: production's code migrates an empty database and the seed script creates synthetic data through
#      its API; then staging's migrations upgrade that database, `alembic check` must be clean, and the seed script
#      reads the data back through staging's API.
#   3. The production-mode stack (ENVIRONMENT=prod) must come up healthy.
#   4. ZAP baseline scan of the running app: fails on any High-risk alert; every alert is listed in the job summary.
#
# Nothing real is used: placeholder secrets, no model calls, a synthetic account and the repository's synthetic PDF.
set -euo pipefail

readonly PROJECT=mrrci
readonly BASE=http://localhost:8080
readonly STATE="${RUNNER_TEMP:-/tmp}/acceptance-state.json"
readonly PROD_OVERRIDE=.github/scripts/compose.production-code.yml
readonly GRYPE_VERSION=0.119.0
readonly GRYPE_SHA256=3fa2dc4b924621ab65404cf08d0b8438d896d80ab949c9d5a4ca283c36004c9b
readonly ZAP_IMAGE=ghcr.io/zaproxy/zaproxy:2.17.0@sha256:781a2bdaea47324e7bab583e2263f21d257b0aee61ed51521a5be45f5f5081ef

# Production mode. docker-compose.yml refuses to start without the two secrets; the workflow sets placeholders for
# them in its YAML `env:`, in one place, where the line gitleaks' generic-api-key rule flags carries its allow marker.
# ENVIRONMENT=prod requires GOOGLE_GENAI_USE_VERTEXAI=true. No step calls a model.
: "${SECRET_KEY:?set by the workflow: a placeholder, nothing real}"
: "${SECURITY_PASSWORD_SALT:?set by the workflow: a placeholder, nothing real}"
export SECRET_KEY SECURITY_PASSWORD_SALT
export ENVIRONMENT=prod
export GOOGLE_GENAI_USE_VERTEXAI=true
export GOOGLE_CLOUD_PROJECT="${GOOGLE_CLOUD_PROJECT:-acceptance-no-model-calls}"
export GIT_SHA="${GITHUB_SHA:-local}"

compose() { docker compose -p "$PROJECT" "$@"; }
production_compose() { docker compose -p "$PROJECT" -f docker-compose.yml -f "$PROD_OVERRIDE" "$@"; }
summary() { if [ -n "${GITHUB_STEP_SUMMARY:-}" ]; then printf '%s\n' "$*" >> "$GITHUB_STEP_SUMMARY"; fi; }
cleanup() { compose down -v --remove-orphans > /dev/null 2>&1 || true; }
trap cleanup EXIT

# The same readiness test as the e2e job: nginx serves /login AND uvicorn answers /health inside the api container.
wait_ready() {
  local label=$1 web
  for _ in $(seq 1 60); do
    web=$(curl -fsS -o /dev/null -w '%{http_code}' "$BASE/login" 2> /dev/null || true)
    if [ "$web" = "200" ] && compose exec -T api python -c \
      "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/health', timeout=2).status == 200 else 1)" \
      2> /dev/null; then
      echo "$label: web + api ready"
      return 0
    fi
    sleep 2
  done
  echo "$label: the app did not become ready on :8080"
  compose logs --no-color proxy web api
  return 1
}

echo "::group::1. Build the images once, and pull the ones the stack uses as published"
compose build api web docs
compose pull proxy postgres redis
echo "::endgroup::"

echo "::group::1. Scan the images (Grype $GRYPE_VERSION: fail on critical with a fix)"
curl -sSL --fail -o grype.tar.gz \
  "https://github.com/anchore/grype/releases/download/v${GRYPE_VERSION}/grype_${GRYPE_VERSION}_linux_amd64.tar.gz"
echo "${GRYPE_SHA256}  grype.tar.gz" | sha256sum --check --strict
tar -xzf grype.tar.gz grype
# Every image the running stack is made of: the three built above and the three pulled by their pinned references
# (the proxy is the first thing users reach). compose prints each as docker-compose.yml names it.
mapfile -t images < <(compose config --images api web docs proxy postgres redis)
for image in "${images[@]}"; do
  ./grype "docker:${image}" --only-fixed --fail-on critical
done
summary "- Image scan: no critical vulnerability with a fix in ${images[*]}"
echo "::endgroup::"

echo "::group::2. Upgrade test: production's schema and synthetic data"
production_compose build api
compose up -d --wait postgres redis
production_compose run --rm api alembic upgrade head
production_compose up -d --wait api web proxy
wait_ready "production code"
python3 .github/scripts/acceptance_seed.py seed --base "$BASE" --pdf frontend/e2e/fixtures/sample.pdf --state "$STATE"
production_compose stop api
production_compose rm -f api
echo "::endgroup::"

echo "::group::2-3. Upgrade to staging's code and start the production-mode stack"
compose run --rm api alembic upgrade head
compose run --rm api alembic check
compose up -d --wait api web proxy
wait_ready "staging code"
python3 .github/scripts/acceptance_seed.py verify --base "$BASE" --state "$STATE"
summary "- Upgrade test: production's schema with synthetic data upgraded to staging's; the data reads back"
echo "::endgroup::"

echo "::group::4. ZAP baseline scan"
mkdir -p zap-out
chmod 777 zap-out # ZAP runs as its own user inside the container and writes its report here
zap_status=0
docker run --rm --network host -v "$PWD/zap-out:/zap/wrk:rw" "$ZAP_IMAGE" \
  zap-baseline.py -t "$BASE" -J zap.json -I || zap_status=$?
# With -I, warnings also return 0; 1 would be a FAIL rule and 3 means ZAP itself failed. 2 (warnings, without -I)
# cannot occur here, and is accepted in case a later ZAP version returns it.
if [ "$zap_status" -ne 0 ] && [ "$zap_status" -ne 2 ]; then
  echo "ZAP exited $zap_status"
  exit 1
fi
python3 - zap-out/zap.json << 'PY'
"""Fail on any High-risk ZAP alert; list every alert in the job summary."""
import json
import os
import sys

report = json.load(open(sys.argv[1], encoding="utf-8"))
alerts = [alert for site in report.get("site", []) for alert in site.get("alerts", [])]
risk_names = {"3": "High", "2": "Medium", "1": "Low", "0": "Informational"}
lines = ["", "ZAP baseline alerts:", "", "| Risk | Alert | Instances |", "| --- | --- | --- |"]
for alert in sorted(alerts, key=lambda a: -int(a.get("riskcode", "0"))):
    lines.append(f"| {risk_names.get(alert.get('riskcode'), '?')} | {alert.get('name')} | {alert.get('count')} |")
if not alerts:
    lines.append("| - | none | 0 |")
summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
if summary_path:
    with open(summary_path, "a", encoding="utf-8") as out:
        out.write("\n".join(lines) + "\n")
print("\n".join(lines))
high = [alert.get("name") for alert in alerts if alert.get("riskcode") == "3"]
if high:
    print(f"High-risk ZAP alerts: {', '.join(high)}")
    sys.exit(1)
PY
echo "::endgroup::"

echo "Acceptance stage passed."
