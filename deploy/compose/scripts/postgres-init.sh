#!/bin/sh

set -eu

runtime_password=$(cat /run/hormuz-bootstrap-secrets/postgres_runtime_password)
case "${runtime_password}" in
    ""|*[!A-Za-z0-9_-]*)
        printf 'PostgreSQL runtime password must be non-empty URL-safe text\n' >&2
        exit 1
        ;;
esac
if [ "${#runtime_password}" -lt 32 ] || [ "${#runtime_password}" -gt 128 ]; then
    printf 'PostgreSQL runtime password must contain 32 to 128 characters\n' >&2
    exit 1
fi

psql --set ON_ERROR_STOP=1 --username "${POSTGRES_USER}" --dbname "${POSTGRES_DB}" <<SQL
BEGIN;
CREATE ROLE hormuz_runtime
  LOGIN PASSWORD '${runtime_password}'
  NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS;
CREATE ROLE hormuz_policy_control
  NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS;
CREATE ROLE hormuz_custody_control
  NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS;
CREATE ROLE hormuz_custody_executor
  NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS;
COMMIT;
SQL

unset runtime_password
touch "${PGDATA}/.hormuz-roles-initialized"
