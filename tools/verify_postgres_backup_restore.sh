#!/usr/bin/env bash
# Run the disposable Hormuz PostgreSQL logical backup-and-restore drill.
#
# The only retained artifact is a content-free summary. Source, recovery, and
# quarantine databases live in short-lived containers; no customer database,
# dump, or credentials are touched or retained.

set -euo pipefail

readonly REPOSITORY_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "${REPOSITORY_ROOT}/tools/_verification_runtime.sh"
# PostgreSQL 16.14 multi-platform image index, inspected 2026-08-22.
readonly POSTGRES_IMAGE="postgres@sha256:57c72fd2a128e416c7fcc499958864df5301e940bca0a56f58fddf30ffc07777"
readonly POSTGRES_VERSION="16.14"
readonly RECOVERY_PYTHON="${HORMUZ_POSTGRES_RECOVERY_PYTHON:-python3}"
readonly EVIDENCE_DIR="${HORMUZ_POSTGRES_RECOVERY_EVIDENCE_DIR:-$(mktemp -d "${TMPDIR:-/tmp}/hormuz-postgres-recovery-evidence.XXXXXX")}"
readonly WORK_DIR="$(mktemp -d "${TMPDIR:-/tmp}/hormuz-postgres-recovery.XXXXXX")"
readonly RUN_ID="${RANDOM}${RANDOM}$$"
readonly NETWORK="hormuz-postgres-recovery-${RUN_ID}"
readonly DISPOSABLE_LABEL="io.hormuz.disposable-backup-restore"
readonly SOURCE_CONTAINER="hormuz-postgres-source-${RUN_ID}"
readonly RECOVERY_CONTAINER="hormuz-postgres-target-${RUN_ID}"
readonly SOURCE_DATABASE="hormuz_recovery_source"
readonly RECOVERY_DATABASE="hormuz_recovery_target"
readonly QUARANTINE_DATABASE="hormuz_recovery_quarantine"
readonly SCHEMA="hormuz_recovery"
readonly RUNTIME_ROLE="hormuz_recovery_runtime"
readonly POLICY_CONTROL_ROLE="hormuz_recovery_policy_control"
readonly DATABASE_PASSWORD="hormuz-recovery-ephemeral-password"
readonly RUNTIME_PASSWORD="hormuz-recovery-runtime-password"
readonly POLICY_CONTROL_PASSWORD="hormuz-recovery-policy-control-password"
readonly SOURCE_STATE="${WORK_DIR}/source-state.json"
readonly RECOVERY_STATE="${WORK_DIR}/recovery-state.json"
readonly MISMATCH_STATE="${WORK_DIR}/mismatched-state.json"
readonly DUMP_PATH="${WORK_DIR}/hormuz.dump"
readonly CORRUPT_DUMP_PATH="${WORK_DIR}/hormuz.corrupt.dump"

cleanup() {
  local status=$?
  hormuz_remove_disposable_container "${SOURCE_CONTAINER}" "${DISPOSABLE_LABEL}"
  hormuz_remove_disposable_container "${RECOVERY_CONTAINER}" "${DISPOSABLE_LABEL}"
  hormuz_remove_disposable_network "${NETWORK}" "${DISPOSABLE_LABEL}"
  rm -rf "${WORK_DIR}"
  exit "${status}"
}
trap cleanup EXIT

failure() {
  printf 'PostgreSQL backup/restore drill failed: %s\n' "$1" >&2
  exit 1
}

wait_for_postgres() {
  local container="$1"
  local database="$2"
  local attempt
  for attempt in $(seq 1 30); do
    if docker exec "${container}" pg_isready --username=postgres --dbname="${database}" >/dev/null 2>&1; then
      return 0
    fi
    sleep 1
  done
  failure 'disposable PostgreSQL instance did not become ready'
}

host_port() {
  local container="$1"
  local port
  port="$(docker port "${container}" 5432/tcp | sed -E -n '1s/.*:([0-9]+)$/\1/p')"
  if [[ -z "${port}" ]]; then
    failure 'disposable PostgreSQL host port was unavailable'
  fi
  printf '%s\n' "${port}"
}

run_recovery_tool() {
  PYTHONPATH="${REPOSITORY_ROOT}/tools${PYTHONPATH:+:${PYTHONPATH}}" \
    PYTHONSAFEPATH=1 \
    "${RECOVERY_PYTHON}" "${REPOSITORY_ROOT}/tools/verify_postgres_backup_restore.py" "$@"
}

expect_recovery_tool_failure() {
  local expected_code="$1"
  shift
  local output
  if output="$(run_recovery_tool "$@" 2>&1)"; then
    failure "${expected_code} was accepted"
  fi
  if [[ "${output}" != *"PostgreSQL recovery drill failed: ${expected_code}"* ]]; then
    failure "expected ${expected_code} was not observed"
  fi
}

duration_ms() {
  local started_seconds="$1"
  printf '%s\n' "$(( (SECONDS - started_seconds) * 1000 ))"
}

mkdir -p "${EVIDENCE_DIR}"
cd "${REPOSITORY_ROOT}"

total_started_seconds=${SECONDS}
docker network create --label "${DISPOSABLE_LABEL}=true" "${NETWORK}" >/dev/null
docker run --detach --rm \
  --name "${SOURCE_CONTAINER}" \
  --label "${DISPOSABLE_LABEL}=true" \
  --network "${NETWORK}" \
  --network-alias source \
  --publish 127.0.0.1::5432 \
  --env POSTGRES_DB="${SOURCE_DATABASE}" \
  --env POSTGRES_PASSWORD="${DATABASE_PASSWORD}" \
  "${POSTGRES_IMAGE}" >/dev/null
docker run --detach --rm \
  --name "${RECOVERY_CONTAINER}" \
  --label "${DISPOSABLE_LABEL}=true" \
  --network "${NETWORK}" \
  --network-alias recovery \
  --publish 127.0.0.1::5432 \
  --env POSTGRES_DB="${RECOVERY_DATABASE}" \
  --env POSTGRES_PASSWORD="${DATABASE_PASSWORD}" \
  "${POSTGRES_IMAGE}" >/dev/null
wait_for_postgres "${SOURCE_CONTAINER}" "${SOURCE_DATABASE}"
wait_for_postgres "${RECOVERY_CONTAINER}" "${RECOVERY_DATABASE}"

source_port="$(host_port "${SOURCE_CONTAINER}")"
recovery_port="$(host_port "${RECOVERY_CONTAINER}")"
source_operator_dsn="postgresql://postgres:${DATABASE_PASSWORD}@127.0.0.1:${source_port}/${SOURCE_DATABASE}"
source_runtime_dsn="postgresql://${RUNTIME_ROLE}:${RUNTIME_PASSWORD}@127.0.0.1:${source_port}/${SOURCE_DATABASE}"
source_policy_control_dsn="postgresql://${POLICY_CONTROL_ROLE}:${POLICY_CONTROL_PASSWORD}@127.0.0.1:${source_port}/${SOURCE_DATABASE}"
recovery_operator_dsn="postgresql://postgres:${DATABASE_PASSWORD}@127.0.0.1:${recovery_port}/${RECOVERY_DATABASE}"
recovery_runtime_dsn="postgresql://${RUNTIME_ROLE}:${RUNTIME_PASSWORD}@127.0.0.1:${recovery_port}/${RECOVERY_DATABASE}"
recovery_policy_control_dsn="postgresql://${POLICY_CONTROL_ROLE}:${POLICY_CONTROL_PASSWORD}@127.0.0.1:${recovery_port}/${RECOVERY_DATABASE}"
quarantine_runtime_dsn="postgresql://${RUNTIME_ROLE}:${RUNTIME_PASSWORD}@127.0.0.1:${recovery_port}/${QUARANTINE_DATABASE}"
quarantine_policy_control_dsn="postgresql://${POLICY_CONTROL_ROLE}:${POLICY_CONTROL_PASSWORD}@127.0.0.1:${recovery_port}/${QUARANTINE_DATABASE}"

run_recovery_tool wait-ready --operator-dsn "${source_operator_dsn}" >/dev/null
run_recovery_tool wait-ready --operator-dsn "${recovery_operator_dsn}" >/dev/null

seed_started_seconds=${SECONDS}
run_recovery_tool provision-roles \
  --operator-dsn "${source_operator_dsn}" \
  --runtime-role "${RUNTIME_ROLE}" \
  --runtime-password "${RUNTIME_PASSWORD}" \
  --policy-control-role "${POLICY_CONTROL_ROLE}" \
  --policy-control-password "${POLICY_CONTROL_PASSWORD}" >/dev/null
run_recovery_tool seed \
  --operator-dsn "${source_operator_dsn}" \
  --runtime-dsn "${source_runtime_dsn}" \
  --policy-control-dsn "${source_policy_control_dsn}" \
  --schema "${SCHEMA}" \
  --runtime-role "${RUNTIME_ROLE}" \
  --policy-control-role "${POLICY_CONTROL_ROLE}" \
  --state-output "${SOURCE_STATE}" >/dev/null
migrate_and_seed_ms="$(duration_ms "${seed_started_seconds}")"

backup_started_seconds=${SECONDS}
docker run --rm \
  --network "${NETWORK}" \
  --env PGPASSWORD="${DATABASE_PASSWORD}" \
  --volume "${WORK_DIR}:/recovery:rw" \
  "${POSTGRES_IMAGE}" \
  pg_dump \
  --host=source \
  --username=postgres \
  --dbname="${SOURCE_DATABASE}" \
  --format=custom \
  --schema="${SCHEMA}" \
  --file=/recovery/hormuz.dump
run_recovery_tool validate-backup --dump "${DUMP_PATH}" >/dev/null
backup_ms="$(duration_ms "${backup_started_seconds}")"

expect_recovery_tool_failure recovery_backup_missing validate-backup --dump "${WORK_DIR}/missing.dump"

run_recovery_tool provision-roles \
  --operator-dsn "${recovery_operator_dsn}" \
  --runtime-role "${RUNTIME_ROLE}" \
  --runtime-password "${RUNTIME_PASSWORD}" \
  --policy-control-role "${POLICY_CONTROL_ROLE}" \
  --policy-control-password "${POLICY_CONTROL_PASSWORD}" >/dev/null
docker exec "${RECOVERY_CONTAINER}" createdb --username=postgres --owner=postgres "${QUARANTINE_DATABASE}"
run_recovery_tool make-corrupt-copy --source "${DUMP_PATH}" --output "${CORRUPT_DUMP_PATH}" >/dev/null
if docker run --rm \
  --network "${NETWORK}" \
  --env PGPASSWORD="${DATABASE_PASSWORD}" \
  --volume "${WORK_DIR}:/recovery:ro" \
  "${POSTGRES_IMAGE}" \
  pg_restore \
  --host=recovery \
  --username=postgres \
  --dbname="${QUARANTINE_DATABASE}" \
  --clean \
  --if-exists \
  --exit-on-error \
  /recovery/hormuz.corrupt.dump >/dev/null 2>&1; then
  failure 'corrupt backup artifact was accepted'
fi
if run_recovery_tool verify \
  --runtime-dsn "${quarantine_runtime_dsn}" \
  --policy-control-dsn "${quarantine_policy_control_dsn}" \
  --schema "${SCHEMA}" \
  --runtime-role "${RUNTIME_ROLE}" \
  --policy-control-role "${POLICY_CONTROL_ROLE}" \
  --expected-state "${SOURCE_STATE}" \
  --state-output "${WORK_DIR}/quarantine-state.json" >/dev/null 2>&1; then
  failure 'partial recovery target was accepted'
fi

restore_started_seconds=${SECONDS}
docker run --rm \
  --network "${NETWORK}" \
  --env PGPASSWORD="${DATABASE_PASSWORD}" \
  --volume "${WORK_DIR}:/recovery:ro" \
  "${POSTGRES_IMAGE}" \
  pg_restore \
  --host=recovery \
  --username=postgres \
  --dbname="${RECOVERY_DATABASE}" \
  --clean \
  --if-exists \
  --exit-on-error \
  /recovery/hormuz.dump
restore_ms="$(duration_ms "${restore_started_seconds}")"

verify_started_seconds=${SECONDS}
run_recovery_tool make-mismatched-state --source "${SOURCE_STATE}" --output "${MISMATCH_STATE}" >/dev/null
expect_recovery_tool_failure recovery_state_fingerprint_mismatch verify \
  --runtime-dsn "${recovery_runtime_dsn}" \
  --policy-control-dsn "${recovery_policy_control_dsn}" \
  --schema "${SCHEMA}" \
  --runtime-role "${RUNTIME_ROLE}" \
  --policy-control-role "${POLICY_CONTROL_ROLE}" \
  --expected-state "${MISMATCH_STATE}" \
  --state-output "${WORK_DIR}/mismatched-recovery-state.json"
run_recovery_tool verify \
  --runtime-dsn "${recovery_runtime_dsn}" \
  --policy-control-dsn "${recovery_policy_control_dsn}" \
  --schema "${SCHEMA}" \
  --runtime-role "${RUNTIME_ROLE}" \
  --policy-control-role "${POLICY_CONTROL_ROLE}" \
  --expected-state "${SOURCE_STATE}" \
  --state-output "${RECOVERY_STATE}" >/dev/null
verify_ms="$(duration_ms "${verify_started_seconds}")"
total_ms="$(duration_ms "${total_started_seconds}")"

run_recovery_tool summary \
  --database-image "${POSTGRES_IMAGE}" \
  --database-version "${POSTGRES_VERSION}" \
  --dump "${DUMP_PATH}" \
  --source-state "${SOURCE_STATE}" \
  --recovery-state "${RECOVERY_STATE}" \
  --migrate-and-seed-ms "${migrate_and_seed_ms}" \
  --backup-ms "${backup_ms}" \
  --restore-ms "${restore_ms}" \
  --verify-ms "${verify_ms}" \
  --total-ms "${total_ms}" \
  --missing-dump-rejected \
  --corrupt-dump-rejected \
  --partial-recovery-not-promoted \
  --state-fingerprint-matches \
  --output "${EVIDENCE_DIR}/summary.json" >/dev/null

printf 'verified disposable PostgreSQL backup/restore evidence in %s\n' "${EVIDENCE_DIR}"
