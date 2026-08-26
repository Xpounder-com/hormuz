#!/usr/bin/env bash

set -euo pipefail
umask 077

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE_ROOT="${ROOT}/deploy/compose"
BASE_FILE="${COMPOSE_ROOT}/compose.yaml"
EXTERNAL_FILE="${COMPOSE_ROOT}/compose.external-postgres.yaml"
VERIFY_FILE="${COMPOSE_ROOT}/compose.verify.yaml"
RUNTIME_ROOT="${COMPOSE_ROOT}/runtime"
SECRET_ROOT="${RUNTIME_ROOT}/secrets"
EVIDENCE_DIR="${HORMUZ_COMPOSE_EVIDENCE_DIR:-}"
PROJECT_NAME="${HORMUZ_COMPOSE_PROJECT_NAME:-hormuz-compose-proof}"
HORMUZ_IMAGE="ghcr.io/xpounder-com/hormuz@sha256:8ac24f5c7afb8ce09ec133616de06702f568a2e70594d8034146a131d86e5b67"
POSTGRES_IMAGE="postgres@sha256:57c72fd2a128e416c7fcc499958864df5301e940bca0a56f58fddf30ffc07777"
PROOF_ACK="I_UNDERSTAND_THIS_IS_A_DISPOSABLE_SINGLE_VM_PILOT_PROOF"
CLEANUP_ARMED=0
CLEANED=0
RUNTIME_CREATED=0

fail() {
  printf 'Compose reference proof failed: %s\n' "$1" >&2
  exit 1
}

compose() {
  docker compose --project-directory "${COMPOSE_ROOT}" --project-name "${PROJECT_NAME}" \
    -f "${BASE_FILE}" -f "${VERIFY_FILE}" "$@"
}

base_compose() {
  docker compose --project-directory "${COMPOSE_ROOT}" --project-name "${PROJECT_NAME}" \
    -f "${BASE_FILE}" "$@"
}

external_compose() {
  docker compose --project-directory "${COMPOSE_ROOT}" --project-name "${PROJECT_NAME}" \
    -f "${BASE_FILE}" -f "${EXTERNAL_FILE}" "$@"
}

cleanup() {
  if [[ "${CLEANUP_ARMED}" -eq 1 && "${CLEANED}" -eq 0 ]]; then
    set +e
    compose down --volumes --remove-orphans >/dev/null 2>&1
    set -e
  fi
  if [[ "${RUNTIME_CREATED}" -eq 1 ]]; then
    remove_proof_runtime
  fi
}

remove_proof_runtime() {
  [[ "${RUNTIME_ROOT}" == "${COMPOSE_ROOT}/runtime" ]] \
    || fail "refusing to remove an unexpected proof runtime path"
  if [[ -e "${RUNTIME_ROOT}" ]]; then
    [[ -d "${RUNTIME_ROOT}" && ! -L "${RUNTIME_ROOT}" ]] \
      || fail "refusing to remove an invalid proof runtime path"
    rm -rf -- "${RUNTIME_ROOT}"
  fi
  RUNTIME_CREATED=0
}
trap cleanup EXIT HUP INT TERM

[[ "${HORMUZ_COMPOSE_PROOF_ACK:-}" == "${PROOF_ACK}" ]] \
  || fail "set HORMUZ_COMPOSE_PROOF_ACK=${PROOF_ACK}"
[[ -n "${EVIDENCE_DIR}" ]] || fail "HORMUZ_COMPOSE_EVIDENCE_DIR is required"
[[ ! -e "${RUNTIME_ROOT}" ]] || fail "proof requires a clean checkout with no deploy/compose/runtime directory"
[[ ! -e "${EVIDENCE_DIR}" ]] || fail "evidence output already exists"
mkdir -p "${EVIDENCE_DIR}"
chmod 0700 "${EVIDENCE_DIR}"

command -v docker >/dev/null 2>&1 || fail "Docker is unavailable"
command -v python3 >/dev/null 2>&1 || fail "Python 3 is unavailable"
command -v openssl >/dev/null 2>&1 || fail "OpenSSL is unavailable"

host_architecture="$(uname -m)"
[[ "${host_architecture}" == "x86_64" || "${host_architecture}" == "amd64" ]] \
  || fail "the v1 reference requires a native AMD64 Linux host"
server_platform="$(docker info --format '{{.OSType}}/{{.Architecture}}')"
[[ "${server_platform}" == "linux/x86_64" || "${server_platform}" == "linux/amd64" ]] \
  || fail "the Docker daemon is not native linux/amd64"

compose_version="$(docker compose version --short | sed 's/^v//')"
IFS=. read -r compose_major compose_minor compose_patch _ <<<"${compose_version}"
compose_patch="${compose_patch%%[-+]*}"
if (( compose_major < 2 || (compose_major == 2 && compose_minor < 24) \
      || (compose_major == 2 && compose_minor == 24 && compose_patch < 4) )); then
  fail "Docker Compose 2.24.4 or newer is required"
fi
docker_engine_version="$(docker version --format '{{.Server.Version}}')"
export HORMUZ_COMPOSE_PLATFORM=linux/amd64
export HORMUZ_COMPOSE_PROJECT_NAME="${PROJECT_NAME}"
export HORMUZ_SECRET_GID="$(id -g)"

existing_containers="$(docker ps --all --quiet --filter "label=com.docker.compose.project=${PROJECT_NAME}")"
existing_volumes="$(docker volume ls --quiet --filter "label=com.docker.compose.project=${PROJECT_NAME}")"
existing_networks="$(docker network ls --quiet --filter "label=com.docker.compose.project=${PROJECT_NAME}")"
[[ -z "${existing_containers}${existing_volumes}${existing_networks}" ]] \
  || fail "the proof project name already owns Docker resources"
CLEANUP_ARMED=1

"${COMPOSE_ROOT}/hormuz-compose" prepare >/dev/null
RUNTIME_CREATED=1
install -m 0640 "${COMPOSE_ROOT}/verification/hormuz.allow.json" "${RUNTIME_ROOT}/hormuz.json"
initial_config_digest="$(openssl dgst -sha256 -r "${RUNTIME_ROOT}/hormuz.json" | awk '{print $1}')"

base_compose config --format json >"${EVIDENCE_DIR}/compose-bundled.json"
external_compose config --format json >"${EVIDENCE_DIR}/compose-external.json"
base_compose --profile operations config --format json >"${EVIDENCE_DIR}/compose-bundled-operations.json"
external_compose --profile operations config --format json >"${EVIDENCE_DIR}/compose-external-operations.json"
compose config --format json >"${EVIDENCE_DIR}/compose-verification.json"
python3 "${ROOT}/tools/verify_compose_profile.py" validate-model \
  --mode bundled \
  --model "${EVIDENCE_DIR}/compose-bundled.json" \
  --secret-root "${SECRET_ROOT}"
python3 "${ROOT}/tools/verify_compose_profile.py" validate-model \
  --mode external \
  --model "${EVIDENCE_DIR}/compose-external.json" \
  --secret-root "${SECRET_ROOT}"
python3 "${ROOT}/tools/verify_compose_profile.py" validate-model \
  --mode bundled-operations \
  --model "${EVIDENCE_DIR}/compose-bundled-operations.json" \
  --secret-root "${SECRET_ROOT}"
python3 "${ROOT}/tools/verify_compose_profile.py" validate-model \
  --mode external-operations \
  --model "${EVIDENCE_DIR}/compose-external-operations.json" \
  --secret-root "${SECRET_ROOT}"
python3 "${ROOT}/tools/verify_compose_profile.py" validate-model \
  --mode verification \
  --model "${EVIDENCE_DIR}/compose-verification.json" \
  --secret-root "${SECRET_ROOT}"

compose pull gateway postgres fake-provider
hormuz_repo_digest="$(docker image inspect --format '{{index .RepoDigests 0}}' "${HORMUZ_IMAGE}")"
hormuz_repo_digest="${hormuz_repo_digest##*@}"
postgres_repo_digest="$(docker image inspect --format '{{index .RepoDigests 0}}' "${POSTGRES_IMAGE}")"
postgres_repo_digest="${postgres_repo_digest##*@}"
[[ "${hormuz_repo_digest}" == "${HORMUZ_IMAGE##*@}" ]] || fail "Hormuz repo digest mismatch"
[[ "${postgres_repo_digest}" == "${POSTGRES_IMAGE##*@}" ]] || fail "PostgreSQL repo digest mismatch"
hormuz_image_id="$(docker image inspect --format '{{.Id}}' "${HORMUZ_IMAGE}")"
postgres_image_id="$(docker image inspect --format '{{.Id}}' "${POSTGRES_IMAGE}")"
[[ "$(docker image inspect --format '{{.Os}}/{{.Architecture}}' "${HORMUZ_IMAGE}")" == "linux/amd64" ]] \
  || fail "Hormuz image architecture mismatch"
[[ "$(docker image inspect --format '{{.Os}}/{{.Architecture}}' "${POSTGRES_IMAGE}")" == "linux/amd64" ]] \
  || fail "PostgreSQL image architecture mismatch"

compose up --detach --wait postgres fake-provider
postgres_id="$(compose ps --quiet postgres)"
[[ -n "${postgres_id}" ]] || fail "PostgreSQL container is unavailable"
compose exec -T --user postgres postgres /bin/sh -c \
  'postgres_uid=$(id -u postgres)
   postgres_gid=$(id -g postgres)
   test "$(stat -c %u /proc/1)" = "${postgres_uid}" &&
   test "$(stat -c %u:%g:%a /run/hormuz-bootstrap-secrets)" = "0:${postgres_gid}:510" &&
   test "$(stat -c %u:%g:%a /run/hormuz-bootstrap-secrets/postgres_superuser_password)" = "${postgres_uid}:${postgres_gid}:400" &&
   test "$(stat -c %u:%g:%a /run/hormuz-bootstrap-secrets/postgres_runtime_password)" = "${postgres_uid}:${postgres_gid}:400" &&
   test -f "${PGDATA}/.hormuz-roles-initialized"' \
  || fail "PostgreSQL private bootstrap copy or initialization marker is invalid"
runtime_role_count="$(
  compose exec -T --user postgres postgres \
    psql --username postgres --dbname hormuz --tuples-only --no-align \
      --command "SELECT COUNT(*) FROM pg_roles WHERE rolname = 'hormuz_runtime'" \
    | tr -d '[:space:]'
)"
[[ "${runtime_role_count}" == "1" ]] || fail "PostgreSQL runtime role initialization is incomplete"
compose --profile operations run --rm migrate
compose --profile operations run --rm migrate storage verify
compose --profile operations run --rm migrate doctor
compose up --detach --wait gateway

gateway_count="$(
  docker ps \
    --filter "label=com.docker.compose.project=${PROJECT_NAME}" \
    --filter "label=io.hormuz.role=gateway" \
    --format '{{.ID}}' | awk 'NF {count++} END {print count+0}'
)"
[[ "${gateway_count}" -eq 1 ]] || fail "the proof did not start exactly one gateway replica"
gateway_id="$(compose ps --quiet gateway)"
[[ -n "${gateway_id}" ]] || fail "gateway container is unavailable"
[[ "$(docker inspect --format '{{.Config.User}}' "${gateway_id}")" == "65532:65532" ]] \
  || fail "gateway did not run as the fixed non-root identity"
[[ "$(docker inspect --format '{{.HostConfig.ReadonlyRootfs}}' "${gateway_id}")" == "true" ]] \
  || fail "gateway root filesystem was writable"
[[ "$(docker inspect --format '{{join .HostConfig.GroupAdd ","}}' "${gateway_id}")" == "${HORMUZ_SECRET_GID}" ]] \
  || fail "gateway did not receive the protected-file supplemental group"
compose exec -T gateway /bin/sh -c \
  'test -r /etc/hormuz/hormuz.json && test ! -w /etc/hormuz/hormuz.json && test -r /run/secrets/postgres_runtime_dsn && test ! -w /run/secrets/postgres_runtime_dsn && test ! -e /run/secrets/postgres_migration_dsn' \
  || fail "gateway protected-file mounts were unreadable, writable, or over-broad"

python3 "${COMPOSE_ROOT}/verification/probe.py" health \
  --runtime-root "${RUNTIME_ROOT}" --expected-status 401 --without-ingress \
  >"${EVIDENCE_DIR}/probe-unauthenticated.json"
python3 "${COMPOSE_ROOT}/verification/probe.py" health \
  --runtime-root "${RUNTIME_ROOT}" --expected-status 200 \
  >"${EVIDENCE_DIR}/probe-health.json"
python3 "${COMPOSE_ROOT}/verification/probe.py" request \
  --runtime-root "${RUNTIME_ROOT}" --expected-status 200 \
  --expected-policy fallback+capped+redacted \
  >"${EVIDENCE_DIR}/probe-before-restart.json"

provider_stats() {
  local output_path="$1"
  compose exec -T gateway /opt/hormuz/bin/python -I -c \
    'from urllib.request import urlopen; print(urlopen("http://fake-provider:8090/stats", timeout=3).read().decode("utf-8"))' \
    >"${output_path}"
}

assert_provider_stats() {
  local input_path="$1"
  local expected_requests="$2"
  python3 - "${input_path}" "${expected_requests}" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as stream:
    value = json.load(stream)
expected = int(sys.argv[2])
if value != {
    "requests": expected,
    "redaction_marker_seen": True,
    "unredacted_secret_seen": False,
    "provider_authorization_seen": True,
    "capped_output_seen": True,
    "routed_model_seen": True,
}:
    raise SystemExit("fake_provider_observation_invalid")
PY
}

usage_events() {
  compose exec -T --user postgres postgres \
    psql --username postgres --dbname hormuz --tuples-only --no-align \
      --command 'SELECT COUNT(*) FROM hormuz.gateway_usage_events'
}

provider_stats "${EVIDENCE_DIR}/provider-before-restart.json"
assert_provider_stats "${EVIDENCE_DIR}/provider-before-restart.json" 1
requests_before_restart=1
usage_events_before_restart="$(usage_events | tr -d '[:space:]')"
[[ "${usage_events_before_restart}" -ge 1 ]] || fail "metadata-only usage evidence was not committed"

compose restart gateway
compose up --detach --wait gateway
python3 "${COMPOSE_ROOT}/verification/probe.py" request \
  --runtime-root "${RUNTIME_ROOT}" --expected-status 200 \
  --expected-policy fallback+capped+redacted \
  >"${EVIDENCE_DIR}/probe-after-restart.json"
provider_stats "${EVIDENCE_DIR}/provider-after-restart.json"
assert_provider_stats "${EVIDENCE_DIR}/provider-after-restart.json" 2

install -m 0640 "${COMPOSE_ROOT}/verification/hormuz.deny.json" "${RUNTIME_ROOT}/hormuz.json"
replacement_config_digest="$(openssl dgst -sha256 -r "${RUNTIME_ROOT}/hormuz.json" | awk '{print $1}')"
[[ "${replacement_config_digest}" != "${initial_config_digest}" ]] || fail "replacement config was not distinct"
compose up --detach --wait --force-recreate --no-deps gateway
python3 "${COMPOSE_ROOT}/verification/probe.py" request \
  --runtime-root "${RUNTIME_ROOT}" --expected-status 403 \
  >"${EVIDENCE_DIR}/probe-replacement-deny.json"
provider_stats "${EVIDENCE_DIR}/provider-after-deny.json"
assert_provider_stats "${EVIDENCE_DIR}/provider-after-deny.json" 2

install -m 0640 "${COMPOSE_ROOT}/verification/hormuz.allow.json" "${RUNTIME_ROOT}/hormuz.json"
rollback_config_digest="$(openssl dgst -sha256 -r "${RUNTIME_ROOT}/hormuz.json" | awk '{print $1}')"
[[ "${rollback_config_digest}" == "${initial_config_digest}" ]] || fail "configuration rollback did not restore the approved bytes"
compose up --detach --wait --force-recreate --no-deps gateway
python3 "${COMPOSE_ROOT}/verification/probe.py" request \
  --runtime-root "${RUNTIME_ROOT}" --expected-status 200 \
  --expected-policy fallback+capped+redacted \
  >"${EVIDENCE_DIR}/probe-after-rollback.json"
provider_stats "${EVIDENCE_DIR}/provider-after-rollback.json"
assert_provider_stats "${EVIDENCE_DIR}/provider-after-rollback.json" 3
usage_events_at_backup="$(usage_events | tr -d '[:space:]')"
[[ "${usage_events_at_backup}" -gt "${usage_events_before_restart}" ]] \
  || fail "metadata-only evidence did not survive restart and configuration changes"

backup_path="${RUNTIME_ROOT}/backups/pilot-metadata.dump"
"${COMPOSE_ROOT}/scripts/backup.sh" "${backup_path}" >/dev/null
python3 "${COMPOSE_ROOT}/verification/probe.py" request \
  --runtime-root "${RUNTIME_ROOT}" --expected-status 200 \
  --expected-policy fallback+capped+redacted \
  >"${EVIDENCE_DIR}/probe-after-backup.json"
provider_stats "${EVIDENCE_DIR}/provider-after-backup.json"
assert_provider_stats "${EVIDENCE_DIR}/provider-after-backup.json" 4
requests_after_restart=4
usage_events_after_backup="$(usage_events | tr -d '[:space:]')"
[[ "${usage_events_after_backup}" -gt "${usage_events_at_backup}" ]] \
  || fail "the live database did not advance after the backup snapshot"
restore_result="$("${COMPOSE_ROOT}/scripts/restore-verify.sh" "${backup_path}")"
restored_usage_events="$(printf '%s\n' "${restore_result}" | sed -n 's/.*usage_events=\([0-9][0-9]*\).*/\1/p')"
[[ "${restore_result}" == *"privileges=verified"* ]] \
  || fail "logical restore did not verify least-privilege role grants"
[[ "${restored_usage_events}" == "${usage_events_at_backup}" ]] \
  || fail "logical restore did not recover the delayed backup snapshot"
rm -f -- "${backup_path}"

compose logs --no-color >"${EVIDENCE_DIR}/compose.log"
docker inspect $(compose ps --quiet) >"${EVIDENCE_DIR}/container-inspect.json"
python3 "${ROOT}/tools/verify_compose_profile.py" assert-no-secrets \
  --secret-root "${SECRET_ROOT}" \
  --artifact-root "${EVIDENCE_DIR}"

compose down --volumes --remove-orphans
[[ -z "$(docker ps --all --quiet --filter "label=com.docker.compose.project=${PROJECT_NAME}")" ]] \
  || fail "Compose containers remained after clean removal"
[[ -z "$(docker volume ls --quiet --filter "label=com.docker.compose.project=${PROJECT_NAME}")" ]] \
  || fail "Compose volumes remained after clean removal"
[[ -z "$(docker network ls --quiet --filter "label=com.docker.compose.project=${PROJECT_NAME}")" ]] \
  || fail "Compose networks remained after clean removal"
remove_proof_runtime
[[ ! -e "${RUNTIME_ROOT}" ]] || fail "generated proof runtime remained after clean removal"
CLEANED=1

os_version="$(awk -F= '$1 == "VERSION_ID" {gsub(/"/, "", $2); print $2}' /etc/os-release)"
python3 "${ROOT}/tools/verify_compose_profile.py" write-evidence \
  --output "${EVIDENCE_DIR}/summary.json" \
  --os-version "${os_version}" \
  --docker-engine "${docker_engine_version}" \
  --docker-compose "${compose_version}" \
  --hormuz-repo-digest "${hormuz_repo_digest}" \
  --hormuz-image-id "${hormuz_image_id}" \
  --postgres-repo-digest "${postgres_repo_digest}" \
  --postgres-image-id "${postgres_image_id}" \
  --requests-before-restart "${requests_before_restart}" \
  --requests-after-restart "${requests_after_restart}" \
  --usage-events-before-restart "${usage_events_before_restart}" \
  --usage-events-at-backup "${usage_events_at_backup}" \
  --usage-events-after-backup "${usage_events_after_backup}" \
  --restored-usage-events "${restored_usage_events}"
python3 "${ROOT}/tools/verify_compose_profile.py" validate-evidence \
  --evidence "${EVIDENCE_DIR}/summary.json"

find "${EVIDENCE_DIR}" -type f ! -name summary.json -delete
printf 'verified single-VM Compose pilot reference; content-free summary: %s\n' \
  "${EVIDENCE_DIR}/summary.json"
