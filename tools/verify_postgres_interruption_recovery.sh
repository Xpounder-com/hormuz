#!/usr/bin/env bash
# Run Hormuz's opt-in disposable PostgreSQL interruption-and-recovery drill.
#
# This command creates one labelled local PostgreSQL container, never accepts
# a customer DSN, and retains only the content-free evidence summary.  The
# runner independently verifies the label before it can interrupt the server.

set -euo pipefail

readonly REPOSITORY_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "${REPOSITORY_ROOT}/tools/_verification_runtime.sh"
# PostgreSQL 16.14 multi-platform image index, inspected 2026-08-22.
readonly POSTGRES_IMAGE="postgres@sha256:57c72fd2a128e416c7fcc499958864df5301e940bca0a56f58fddf30ffc07777"
readonly POSTGRES_VERSION="16.14"
readonly INTERRUPTION_PYTHON="${HORMUZ_POSTGRES_INTERRUPTION_PYTHON:-python3}"
readonly EVIDENCE_DIR="${HORMUZ_POSTGRES_INTERRUPTION_EVIDENCE_DIR:-$(mktemp -d "${TMPDIR:-/tmp}/hormuz-postgres-interruption-evidence.XXXXXX")}"
# Fixed-width decimal fields keep both Docker's disposable-name guard and the
# PostgreSQL role identifiers valid on short-lived CI processes too.
readonly RUN_ID="$(printf '%05d%05d%05d' "${RANDOM}" "${RANDOM}" "$$")"
readonly CONTAINER="hormuz-postgres-interruption-${RUN_ID}"
readonly DISPOSABLE_LABEL="io.hormuz.disposable-interruption"
readonly DATABASE="hormuz_interruption"
readonly DATABASE_PASSWORD="hormuz-interruption-ephemeral-password"
readonly RUNTIME_ROLE="hormuz_interrupt_runtime_${RUN_ID}"
readonly RUNTIME_PASSWORD="hormuz-interruption-runtime-password"
readonly POLICY_CONTROL_ROLE="hormuz_interrupt_control_${RUN_ID}"
readonly POLICY_CONTROL_PASSWORD="hormuz-interruption-control-password"

cleanup() {
  local status=$?
  hormuz_remove_disposable_container "${CONTAINER}" "${DISPOSABLE_LABEL}"
  exit "${status}"
}
trap cleanup EXIT

failure() {
  printf 'PostgreSQL interruption recovery failed: %s\n' "$1" >&2
  exit 1
}

select_loopback_port() {
  local selected
  if ! selected="$("${INTERRUPTION_PYTHON}" -c '
import socket
with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
    listener.bind(("127.0.0.1", 0))
    print(listener.getsockname()[1])
' 2>/dev/null)"; then
    failure 'disposable PostgreSQL host port selection failed'
  fi
  if [[ ! "${selected}" =~ ^[1-9][0-9]{3,4}$ ]] || (( 10#${selected} > 65535 )); then
    failure 'disposable PostgreSQL host port selection was invalid'
  fi
  printf '%s\n' "${selected}"
}

wait_for_postgres() {
  local attempt
  for attempt in $(seq 1 30); do
    if docker exec "${CONTAINER}" pg_isready --username=postgres --dbname="${DATABASE}" >/dev/null 2>&1; then
      return 0
    fi
    sleep 1
  done
  failure 'disposable PostgreSQL instance did not become ready'
}

published_host_port() {
  local port
  port="$(docker port "${CONTAINER}" 5432/tcp | sed -E -n '1s/.*:([0-9]+)$/\1/p')"
  if [[ -z "${port}" ]]; then
    failure 'disposable PostgreSQL host port was unavailable'
  fi
  printf '%s\n' "${port}"
}

start_disposable_postgres() {
  local attempt
  for attempt in $(seq 1 5); do
    host_port="$(select_loopback_port)"
    if docker run --detach \
      --name "${CONTAINER}" \
      --label "${DISPOSABLE_LABEL}=true" \
      --publish "127.0.0.1:${host_port}:5432" \
      --env POSTGRES_DB="${DATABASE}" \
      --env POSTGRES_PASSWORD="${DATABASE_PASSWORD}" \
      "${POSTGRES_IMAGE}" >/dev/null; then
      return 0
    fi
    hormuz_remove_disposable_container "${CONTAINER}" "${DISPOSABLE_LABEL}"
  done
  failure 'disposable PostgreSQL host port could not be reserved'
}

mkdir -p "${EVIDENCE_DIR}"
cd "${REPOSITORY_ROOT}"
host_port=""
start_disposable_postgres
wait_for_postgres

if [[ "$(published_host_port)" != "${host_port}" ]]; then
  failure 'disposable PostgreSQL host port mapping was not preserved'
fi
port="${host_port}"
operator_dsn="postgresql://postgres:${DATABASE_PASSWORD}@127.0.0.1:${port}/${DATABASE}"
runtime_dsn="postgresql://${RUNTIME_ROLE}:${RUNTIME_PASSWORD}@127.0.0.1:${port}/${DATABASE}"
policy_control_dsn="postgresql://${POLICY_CONTROL_ROLE}:${POLICY_CONTROL_PASSWORD}@127.0.0.1:${port}/${DATABASE}"

HORMUZ_RUN_POSTGRES_INTERRUPTION_RECOVERY=1 \
HORMUZ_POSTGRES_INTERRUPTION_CONFIRMATION=I_UNDERSTAND_DISPOSABLE_DATABASE_INTERRUPTION \
HORMUZ_POSTGRES_INTERRUPTION_OPERATOR_DSN="${operator_dsn}" \
HORMUZ_POSTGRES_INTERRUPTION_RUNTIME_DSN="${runtime_dsn}" \
HORMUZ_POSTGRES_INTERRUPTION_POLICY_CONTROL_DSN="${policy_control_dsn}" \
HORMUZ_POSTGRES_INTERRUPTION_RUNTIME_ROLE="${RUNTIME_ROLE}" \
HORMUZ_POSTGRES_INTERRUPTION_RUNTIME_PASSWORD="${RUNTIME_PASSWORD}" \
HORMUZ_POSTGRES_INTERRUPTION_POLICY_CONTROL_ROLE="${POLICY_CONTROL_ROLE}" \
HORMUZ_POSTGRES_INTERRUPTION_POLICY_CONTROL_PASSWORD="${POLICY_CONTROL_PASSWORD}" \
PYTHONSAFEPATH=1 \
"${INTERRUPTION_PYTHON}" \
  "${REPOSITORY_ROOT}/tools/verify_postgres_interruption_recovery.py" \
  run \
  --container "${CONTAINER}" \
  --database-image "${POSTGRES_IMAGE}" \
  --database-version "${POSTGRES_VERSION}" \
  --evidence-out "${EVIDENCE_DIR}/summary.json"

printf 'verified disposable PostgreSQL interruption recovery evidence in %s\n' "${EVIDENCE_DIR}"
