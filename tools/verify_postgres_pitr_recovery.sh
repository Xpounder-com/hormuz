#!/usr/bin/env bash
# Run the disposable Hormuz PostgreSQL point-in-time recovery drill.
#
# It creates only labelled, short-lived PostgreSQL containers and fixed
# metadata fixtures. The sole retained artifact is a content-free summary.

set -euo pipefail

REPOSITORY_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
source "$REPOSITORY_ROOT/tools/_verification_runtime.sh"
# PostgreSQL 16.14 multi-platform image index, inspected 2026-08-22.
POSTGRES_IMAGE="postgres@sha256:57c72fd2a128e416c7fcc499958864df5301e940bca0a56f58fddf30ffc07777"
POSTGRES_VERSION="16.14"
PITR_PYTHON="python3"
if printenv HORMUZ_POSTGRES_PITR_PYTHON >/dev/null 2>&1; then PITR_PYTHON="$HORMUZ_POSTGRES_PITR_PYTHON"; fi
EVIDENCE_DIR=""
if printenv HORMUZ_POSTGRES_PITR_EVIDENCE_DIR >/dev/null 2>&1; then EVIDENCE_DIR="$HORMUZ_POSTGRES_PITR_EVIDENCE_DIR"; fi
WORK_DIR=""
RUN_ID="$RANDOM$RANDOM$$"
NETWORK="hormuz-postgres-pitr-$RUN_ID"
SOURCE_CONTAINER="hormuz-postgres-pitr-source-$RUN_ID"
RECOVERY_CONTAINER="hormuz-postgres-pitr-recovery-$RUN_ID"
UNREACHABLE_CONTAINER="hormuz-postgres-pitr-unreachable-$RUN_ID"
MISSING_WAL_CONTAINER="hormuz-postgres-pitr-missing-wal-$RUN_ID"
DISPOSABLE_LABEL="io.hormuz.disposable-pitr"
DATABASE="hormuz_pitr"
SCHEMA="hormuz_pitr"
RUNTIME_ROLE="hormuz_pitr_runtime"
POLICY_CONTROL_ROLE="hormuz_pitr_policy_control"
DATABASE_PASSWORD="hormuz-pitr-ephemeral-password"
RUNTIME_PASSWORD="hormuz-pitr-runtime-password"
POLICY_CONTROL_PASSWORD="hormuz-pitr-policy-control-password"
PITR_TARGET="hormuz_pitr_target"
UNREACHABLE_TARGET="hormuz_pitr_target_missing"
PRE_TARGET_MARKER="before-target"
POST_TARGET_MARKER="after-target"
SOURCE_STATE=""
RECOVERY_STATE=""
BASE_BACKUP=""
RECOVERY_BASE_BACKUP=""
UNREACHABLE_BASE_BACKUP=""
MISSING_WAL_BASE_BACKUP=""
WAL_ARCHIVE=""
EMPTY_WAL_ARCHIVE=""
CONFIRMATION_VALUE="I_UNDERSTAND_DISPOSABLE_POSTGRESQL_PITR"

cleanup() {
  local status=$?
  remove_disposable_container "$SOURCE_CONTAINER"
  remove_disposable_container "$RECOVERY_CONTAINER"
  remove_disposable_container "$UNREACHABLE_CONTAINER"
  remove_disposable_container "$MISSING_WAL_CONTAINER"
  remove_disposable_network
  remove_disposable_work_dir
  exit "$status"
}
trap cleanup EXIT

failure() {
  printf 'PostgreSQL PITR recovery drill failed: %s\n' "$1" >&2
  exit 1
}

remove_disposable_container() {
  local container="$1"
  hormuz_remove_disposable_container "$container" "$DISPOSABLE_LABEL"
}

remove_disposable_network() {
  hormuz_remove_disposable_network "$NETWORK" "$DISPOSABLE_LABEL"
}

remove_disposable_work_dir() {
  if [[ -z "$WORK_DIR" ]]; then return; fi
  docker run --rm --user root --entrypoint bash --volume "$WORK_DIR:/pitr:rw" "$POSTGRES_IMAGE" \
    -ceu 'shopt -s dotglob nullglob; rm -rf /pitr/*' >/dev/null 2>&1 || true
  rm -rf "$WORK_DIR" >/dev/null 2>&1 || true
}

require_explicit_opt_in() {
  local acknowledgement
  acknowledgement="$(printenv HORMUZ_POSTGRES_PITR_ACKNOWLEDGEMENT 2>/dev/null || true)"
  if [[ "$acknowledgement" != "$CONFIRMATION_VALUE" ]]; then
    failure 'pitr_opt_in_required'
  fi
}

run_recovery_tool() {
  PYTHONPATH="${REPOSITORY_ROOT}/tools${PYTHONPATH:+:${PYTHONPATH}}" \
    PYTHONSAFEPATH=1 \
    "$PITR_PYTHON" "$REPOSITORY_ROOT/tools/verify_postgres_backup_restore.py" "$@"
}

run_pitr_tool() {
  PYTHONPATH="${REPOSITORY_ROOT}/tools${PYTHONPATH:+:${PYTHONPATH}}" \
    PYTHONSAFEPATH=1 \
    "$PITR_PYTHON" "$REPOSITORY_ROOT/tools/verify_postgres_pitr_recovery.py" "$@"
}

wait_for_postgres() {
  local container="$1"
  local attempt
  for attempt in $(seq 1 45); do
    if docker exec "$container" psql --username=postgres --dbname="$DATABASE" \
      --set=ON_ERROR_STOP=on --tuples-only --no-align --command 'SELECT 1' >/dev/null 2>&1; then
      return 0
    fi
    sleep 1
  done
  failure 'disposable_postgres_not_ready'
}

assert_disposable_target() {
  local container="$1"
  local label
  local image
  local version
  label="$(docker inspect --format "{{ index .Config.Labels \"$DISPOSABLE_LABEL\" }}" "$container" 2>/dev/null || true)"
  image="$(docker inspect --format '{{ .Config.Image }}' "$container" 2>/dev/null || true)"
  version="$(docker exec "$container" postgres --version 2>/dev/null || true)"
  if [[ "$label" != "true" || "$image" != "$POSTGRES_IMAGE" || "$version" != *"$POSTGRES_VERSION"* ]]; then
    failure 'disposable_target_attestation_failed'
  fi
}

host_port() {
  local container="$1"
  local port
  port="$(docker port "$container" 5432/tcp | sed -E -n '1s/.*:([0-9]+)$/\1/p')"
  if [[ -z "$port" ]]; then failure 'disposable_postgres_host_port_unavailable'; fi
  printf '%s\n' "$port"
}

wait_for_archived_wal() {
  local wal_file="$1"
  local attempt
  if [[ ! "$wal_file" =~ ^[0-9A-F]{24}$ ]]; then failure 'wal_archive_identifier_invalid'; fi
  for attempt in $(seq 1 45); do
    if [[ -f "$WAL_ARCHIVE/$wal_file" ]]; then return 0; fi
    sleep 1
  done
  failure 'required_wal_not_archived'
}

source_sql() {
  docker exec "$SOURCE_CONTAINER" psql --username=postgres --dbname="$DATABASE"     --set=ON_ERROR_STOP=on --tuples-only --no-align --command "$1"
}

prepare_recovery_data() {
  local relative_data_directory="$1"
  local archive_directory="$2"
  local recovery_target="$3"
  docker run --rm --user root --volume "$WORK_DIR:/pitr:rw" "$POSTGRES_IMAGE"     bash -ceu '
      data_directory="$1"
      archive_directory="$2"
      recovery_target="$3"
      test -f "$data_directory/PG_VERSION"
      chown -R postgres:postgres "$data_directory"
      chmod 0700 "$data_directory"
      printf "\nrestore_command = '\''cp %s/%%f %%p'\''\nrecovery_target_name = '\''%s'\''\nrecovery_target_action = '\''promote'\''\n" "$archive_directory" "$recovery_target" >> "$data_directory/postgresql.auto.conf"
      touch "$data_directory/recovery.signal"
      chown postgres:postgres "$data_directory/postgresql.auto.conf" "$data_directory/recovery.signal"
    ' bash "/pitr/$relative_data_directory" "$archive_directory" "$recovery_target" >/dev/null
}

copy_disposable_base_backup() {
  local source_directory="$1"
  local destination_directory="$2"
  docker run --rm --user root --entrypoint bash --volume "$WORK_DIR:/pitr:rw" "$POSTGRES_IMAGE" \
    -ceu '
      source_directory="$1"
      destination_directory="$2"
      test -f "$source_directory/PG_VERSION"
      test ! -e "$destination_directory"
      cp -a "$source_directory" "$destination_directory"
    ' bash "/pitr/$source_directory" "/pitr/$destination_directory" >/dev/null
}

start_recovery() {
  local container="$1"
  local relative_data_directory="$2"
  docker run --detach --name "$container" --label "$DISPOSABLE_LABEL=true" --network "$NETWORK"     --volume "$WORK_DIR:/pitr:rw" --publish 127.0.0.1::5432     --env PGDATA="/pitr/$relative_data_directory" "$POSTGRES_IMAGE" >/dev/null
}

start_negative_recovery() {
  local container="$1"
  local relative_data_directory="$2"
  docker run --detach --name "$container" --label "$DISPOSABLE_LABEL=true" --network "$NETWORK"     --volume "$WORK_DIR:/pitr:rw" --env PGDATA="/pitr/$relative_data_directory" "$POSTGRES_IMAGE" >/dev/null
}

wait_for_failed_recovery() {
  local container="$1"
  local attempt
  local status
  local exit_code
  for attempt in $(seq 1 45); do
    status="$(docker inspect --format '{{ .State.Status }}' "$container" 2>/dev/null || true)"
    if [[ "$status" == "exited" ]]; then
      exit_code="$(docker inspect --format '{{ .State.ExitCode }}' "$container" 2>/dev/null || true)"
      if [[ "$exit_code" != "0" ]]; then return 0; fi
      failure 'incomplete_recovery_was_promoted'
    fi
    sleep 1
  done
  failure 'incomplete_recovery_did_not_fail_closed'
}

duration_ms() {
  local started_seconds="$1"
  printf '%s\n' "$(( (SECONDS - started_seconds) * 1000 ))"
}

require_explicit_opt_in
if [[ -z "$EVIDENCE_DIR" ]]; then EVIDENCE_DIR="$(mktemp -d /tmp/hormuz-postgres-pitr-evidence.XXXXXX)"; fi
WORK_DIR="$(mktemp -d /tmp/hormuz-postgres-pitr.XXXXXX)"
chmod 0711 "$WORK_DIR"
SOURCE_STATE="$WORK_DIR/source-state.json"
RECOVERY_STATE="$WORK_DIR/recovery-state.json"
BASE_BACKUP="$WORK_DIR/base-backup"
RECOVERY_BASE_BACKUP="$WORK_DIR/recovery-base-backup"
UNREACHABLE_BASE_BACKUP="$WORK_DIR/unreachable-base-backup"
MISSING_WAL_BASE_BACKUP="$WORK_DIR/missing-wal-base-backup"
WAL_ARCHIVE="$WORK_DIR/wal-archive"
EMPTY_WAL_ARCHIVE="$WORK_DIR/empty-wal-archive"
mkdir -p "$EVIDENCE_DIR" "$WAL_ARCHIVE" "$EMPTY_WAL_ARCHIVE"
chmod 0700 "$EVIDENCE_DIR"
chmod 0733 "$WAL_ARCHIVE" "$EMPTY_WAL_ARCHIVE"
cd "$REPOSITORY_ROOT"

total_started_seconds=$SECONDS
docker network create --label "$DISPOSABLE_LABEL=true" "$NETWORK" >/dev/null
docker run --detach --name "$SOURCE_CONTAINER" --label "$DISPOSABLE_LABEL=true" --network "$NETWORK"   --network-alias source --volume "$WORK_DIR:/pitr:rw" --publish 127.0.0.1::5432   --env POSTGRES_DB="$DATABASE" --env POSTGRES_PASSWORD="$DATABASE_PASSWORD" "$POSTGRES_IMAGE"   -c wal_level=replica -c archive_mode=on   -c "archive_command=test ! -f /pitr/wal-archive/%f && cp %p /pitr/wal-archive/%f" >/dev/null
wait_for_postgres "$SOURCE_CONTAINER"
assert_disposable_target "$SOURCE_CONTAINER"

source_port="$(host_port "$SOURCE_CONTAINER")"
source_operator_dsn="postgresql://postgres:$DATABASE_PASSWORD@127.0.0.1:$source_port/$DATABASE"
source_runtime_dsn="postgresql://$RUNTIME_ROLE:$RUNTIME_PASSWORD@127.0.0.1:$source_port/$DATABASE"
source_policy_control_dsn="postgresql://$POLICY_CONTROL_ROLE:$POLICY_CONTROL_PASSWORD@127.0.0.1:$source_port/$DATABASE"

seed_started_seconds=$SECONDS
run_recovery_tool provision-roles --operator-dsn "$source_operator_dsn"   --runtime-role "$RUNTIME_ROLE" --runtime-password "$RUNTIME_PASSWORD"   --policy-control-role "$POLICY_CONTROL_ROLE" --policy-control-password "$POLICY_CONTROL_PASSWORD" >/dev/null
run_recovery_tool seed --operator-dsn "$source_operator_dsn" --runtime-dsn "$source_runtime_dsn"   --policy-control-dsn "$source_policy_control_dsn" --schema "$SCHEMA"   --runtime-role "$RUNTIME_ROLE" --policy-control-role "$POLICY_CONTROL_ROLE"   --state-output "$SOURCE_STATE" >/dev/null
seed_ms="$(duration_ms "$seed_started_seconds")"

base_backup_started_seconds=$SECONDS
mkdir -p "$BASE_BACKUP"
chmod 0733 "$BASE_BACKUP"
docker exec --env PGPASSWORD="$DATABASE_PASSWORD" "$SOURCE_CONTAINER"   pg_basebackup --host=127.0.0.1 --username=postgres --pgdata=/pitr/base-backup   --format=plain --wal-method=stream --progress >/dev/null
if [[ ! -f "$BASE_BACKUP/PG_VERSION" ]]; then failure 'physical_base_backup_missing'; fi
base_backup_ms="$(duration_ms "$base_backup_started_seconds")"

wal_archive_started_seconds=$SECONDS
source_sql "CREATE TABLE hormuz_pitr_markers (marker TEXT PRIMARY KEY); INSERT INTO hormuz_pitr_markers (marker) VALUES ('$PRE_TARGET_MARKER');" >/dev/null
source_sql "SELECT pg_create_restore_point('$PITR_TARGET');" >/dev/null
target_wal_file="$(source_sql 'SELECT pg_walfile_name(pg_current_wal_lsn())')"
target_wal_file="$(printf '%s' "$target_wal_file" | tr -d '\r\n')"
source_sql 'SELECT pg_switch_wal()' >/dev/null
wait_for_archived_wal "$target_wal_file"
source_sql "INSERT INTO hormuz_pitr_markers (marker) VALUES ('$POST_TARGET_MARKER');" >/dev/null
post_target_wal_file="$(source_sql 'SELECT pg_walfile_name(pg_current_wal_lsn())')"
post_target_wal_file="$(printf '%s' "$post_target_wal_file" | tr -d '\r\n')"
source_sql 'SELECT pg_switch_wal()' >/dev/null
wait_for_archived_wal "$post_target_wal_file"
wal_archive_ms="$(duration_ms "$wal_archive_started_seconds")"

docker stop --time 15 "$SOURCE_CONTAINER" >/dev/null

copy_disposable_base_backup "$(basename "$BASE_BACKUP")" "$(basename "$RECOVERY_BASE_BACKUP")"
prepare_recovery_data "$(basename "$RECOVERY_BASE_BACKUP")" "/pitr/wal-archive" "$PITR_TARGET"
restore_started_seconds=$SECONDS
start_recovery "$RECOVERY_CONTAINER" "$(basename "$RECOVERY_BASE_BACKUP")"
wait_for_postgres "$RECOVERY_CONTAINER"
assert_disposable_target "$RECOVERY_CONTAINER"
recovery_port="$(host_port "$RECOVERY_CONTAINER")"
recovery_runtime_dsn="postgresql://$RUNTIME_ROLE:$RUNTIME_PASSWORD@127.0.0.1:$recovery_port/$DATABASE"
recovery_policy_control_dsn="postgresql://$POLICY_CONTROL_ROLE:$POLICY_CONTROL_PASSWORD@127.0.0.1:$recovery_port/$DATABASE"
run_pitr_tool promotion-wait --container "$RECOVERY_CONTAINER" >/dev/null
restore_ms="$(duration_ms "$restore_started_seconds")"

marker_value="$(docker exec "$RECOVERY_CONTAINER" psql --username=postgres --dbname="$DATABASE" --tuples-only --no-align --command "SELECT string_agg(marker, ',' ORDER BY marker) FROM hormuz_pitr_markers")"
marker_value="$(printf '%s' "$marker_value" | tr -d '\r\n')"
if [[ "$marker_value" != "$PRE_TARGET_MARKER" ]]; then failure 'pitr_marker_state_invalid'; fi

verify_started_seconds=$SECONDS
run_recovery_tool verify --runtime-dsn "$recovery_runtime_dsn"   --policy-control-dsn "$recovery_policy_control_dsn" --schema "$SCHEMA"   --runtime-role "$RUNTIME_ROLE" --policy-control-role "$POLICY_CONTROL_ROLE"   --expected-state "$SOURCE_STATE" --state-output "$RECOVERY_STATE" >/dev/null
verify_ms="$(duration_ms "$verify_started_seconds")"

copy_disposable_base_backup "$(basename "$BASE_BACKUP")" "$(basename "$UNREACHABLE_BASE_BACKUP")"
prepare_recovery_data "$(basename "$UNREACHABLE_BASE_BACKUP")" "/pitr/wal-archive" "$UNREACHABLE_TARGET"
start_negative_recovery "$UNREACHABLE_CONTAINER" "$(basename "$UNREACHABLE_BASE_BACKUP")"
wait_for_failed_recovery "$UNREACHABLE_CONTAINER"

copy_disposable_base_backup "$(basename "$BASE_BACKUP")" "$(basename "$MISSING_WAL_BASE_BACKUP")"
prepare_recovery_data "$(basename "$MISSING_WAL_BASE_BACKUP")" "/pitr/empty-wal-archive" "$PITR_TARGET"
start_negative_recovery "$MISSING_WAL_CONTAINER" "$(basename "$MISSING_WAL_BASE_BACKUP")"
wait_for_failed_recovery "$MISSING_WAL_CONTAINER"

total_ms="$(duration_ms "$total_started_seconds")"
run_pitr_tool summary --database-image "$POSTGRES_IMAGE" --database-version "$POSTGRES_VERSION"   --base-backup-created --pre-target-wal-replayed --post-target-mutation-excluded   --hormuz-restricted-state-verified --missing-wal-not-promoted --unreachable-target-not-promoted   --seed-ms "$seed_ms" --base-backup-ms "$base_backup_ms" --wal-archive-ms "$wal_archive_ms"   --restore-ms "$restore_ms" --verify-ms "$verify_ms" --total-ms "$total_ms"   --output "$EVIDENCE_DIR/summary.json" >/dev/null

printf 'PostgreSQL PITR recovery drill passed: %s\n' "$EVIDENCE_DIR/summary.json"
