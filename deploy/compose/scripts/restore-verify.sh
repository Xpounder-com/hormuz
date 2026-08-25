#!/bin/sh

set -eu

compose_root=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
project_name=${HORMUZ_COMPOSE_PROJECT_NAME:-hormuz-pilot}
backup_path=$1
case "${backup_path}" in
    /*) ;;
    *) backup_path="${PWD}/${backup_path}" ;;
esac
if [ ! -f "${backup_path}" ] || [ -L "${backup_path}" ] || [ ! -s "${backup_path}" ]; then
    printf 'restore verification requires one non-empty regular backup file\n' >&2
    exit 2
fi

database_name="hormuz_restore_verify_$$"
compose() {
    docker compose --project-directory "${compose_root}" --project-name "${project_name}" \
        -f "${compose_root}/compose.yaml" "$@"
}
cleanup() {
    compose exec -T --user postgres postgres \
        dropdb --username postgres --if-exists "${database_name}" >/dev/null 2>&1 || true
}
trap cleanup EXIT HUP INT TERM

compose exec -T --user postgres postgres createdb --username postgres "${database_name}"
compose exec -T --user postgres postgres pg_restore \
    --username postgres --dbname "${database_name}" --exit-on-error --no-owner \
    <"${backup_path}"

migration_count=$(
    compose exec -T --user postgres postgres psql --username postgres --dbname "${database_name}" \
        --tuples-only --no-align \
        --command "SELECT COUNT(*) FROM hormuz.hormuz_schema_migrations WHERE state = 'applied'"
)
recovered_events=$(
    compose exec -T --user postgres postgres psql --username postgres --dbname "${database_name}" \
        --tuples-only --no-align --command 'SELECT COUNT(*) FROM hormuz.gateway_usage_events'
)
privilege_contract=$(
    compose exec -T --user postgres postgres psql --username postgres --dbname "${database_name}" \
        --tuples-only --no-align --set ON_ERROR_STOP=1 --command "
            SELECT CASE WHEN
                has_schema_privilege('hormuz_runtime', 'hormuz', 'USAGE')
                AND has_table_privilege('hormuz_runtime', 'hormuz.hormuz_schema_migrations', 'SELECT')
                AND has_table_privilege('hormuz_runtime', 'hormuz.gateway_usage_events', 'SELECT')
                AND has_table_privilege('hormuz_runtime', 'hormuz.gateway_usage_events', 'INSERT')
                AND has_table_privilege('hormuz_runtime', 'hormuz.gateway_usage_events', 'UPDATE')
                AND has_table_privilege('hormuz_runtime', 'hormuz.gateway_usage_events', 'DELETE')
                AND has_schema_privilege('hormuz_policy_control', 'hormuz', 'USAGE')
                AND has_table_privilege('hormuz_policy_control', 'hormuz.policy_versions', 'INSERT')
                AND has_schema_privilege('hormuz_custody_control', 'hormuz', 'USAGE')
                AND has_table_privilege('hormuz_custody_control', 'hormuz.custody_deletion_events', 'INSERT')
                AND has_schema_privilege('hormuz_custody_executor', 'hormuz', 'USAGE')
                AND has_table_privilege('hormuz_custody_executor', 'hormuz.custody_lifecycle_events', 'INSERT')
                AND NOT has_table_privilege('hormuz_runtime', 'hormuz.custody_lifecycle_events', 'DELETE')
            THEN 1 ELSE 0 END"
)

if [ "${migration_count}" -ne 8 ]; then
    printf 'restored migration ledger is incomplete\n' >&2
    exit 1
fi
if [ "${privilege_contract}" -ne 1 ]; then
    printf 'restored least-privilege role grants are incomplete\n' >&2
    exit 1
fi
compose exec -T --user postgres postgres psql --username postgres --dbname "${database_name}" \
    --set ON_ERROR_STOP=1 \
    --command 'SET ROLE hormuz_runtime; SELECT COUNT(*) FROM hormuz.hormuz_schema_migrations; RESET ROLE' \
    >/dev/null

digest=$(openssl dgst -sha256 -r "${backup_path}" | awk '{print $1}')
printf 'verified pilot logical restore: migrations=%s usage_events=%s privileges=verified sha256=%s\n' \
    "${migration_count}" "${recovered_events}" "${digest}"
