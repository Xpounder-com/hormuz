#!/bin/sh

set -eu
umask 077

compose_root=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
runtime_root="${compose_root}/runtime"
secrets_root="${runtime_root}/secrets"
secret_gid=${HORMUZ_SECRET_GID:-$(id -g)}

case "${secret_gid}" in
    ''|*[!0-9]*)
        printf 'HORMUZ_SECRET_GID must be a numeric host group ID\n' >&2
        exit 2
        ;;
esac

if [ -e "${runtime_root}" ]; then
    printf 'runtime directory already exists; existing credentials were not changed: %s\n' "${runtime_root}" >&2
    exit 2
fi
if ! command -v openssl >/dev/null 2>&1; then
    printf 'openssl is required to generate pilot credentials\n' >&2
    exit 2
fi

mkdir -p "${secrets_root}" "${runtime_root}/backups"
chgrp "${secret_gid}" "${runtime_root}" "${secrets_root}" "${runtime_root}/backups"
chmod 0700 "${runtime_root}" "${secrets_root}" "${runtime_root}/backups"
cp "${compose_root}/hormuz.example.json" "${runtime_root}/hormuz.json"
chgrp "${secret_gid}" "${runtime_root}/hormuz.json"
chmod 0640 "${runtime_root}/hormuz.json"

postgres_superuser_password=$(openssl rand -hex 32)
postgres_runtime_password=$(openssl rand -hex 32)

printf '%s' "${postgres_superuser_password}" >"${secrets_root}/postgres-superuser-password"
printf '%s' "${postgres_runtime_password}" >"${secrets_root}/postgres-runtime-password"
printf 'postgresql://postgres:%s@postgres:5432/hormuz' \
    "${postgres_superuser_password}" >"${secrets_root}/postgres-migration-dsn"
printf 'postgresql://hormuz_runtime:%s@postgres:5432/hormuz' \
    "${postgres_runtime_password}" >"${secrets_root}/postgres-runtime-dsn"
openssl rand -hex 32 >"${secrets_root}/hormuz-identity-token"
openssl rand -hex 32 >"${secrets_root}/hormuz-ingress-credential"
printf '%s' 'replace-with-openai-provider-key' >"${secrets_root}/openai-api-key"
printf '%s' 'replace-with-anthropic-provider-key' >"${secrets_root}/anthropic-api-key"
chgrp "${secret_gid}" "${secrets_root}"/*
chmod 0640 "${secrets_root}"/*

unset postgres_superuser_password postgres_runtime_password

printf 'prepared protected pilot inputs under %s\n' "${runtime_root}"
printf 'replace the two provider placeholder files before real provider traffic\n'
