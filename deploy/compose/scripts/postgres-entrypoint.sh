#!/bin/sh

set -eu
umask 077

bootstrap_root=/run/hormuz-bootstrap-secrets

if [ "$(id -u)" -ne 0 ]; then
    printf 'PostgreSQL bootstrap entrypoint requires its bounded root startup identity\n' >&2
    exit 2
fi

copy_bootstrap_secret() {
    source_path=$1
    target_name=$2
    if [ ! -f "${source_path}" ] || [ -L "${source_path}" ]; then
        printf 'PostgreSQL bootstrap secret is unavailable: %s\n' "${target_name}" >&2
        exit 2
    fi
    cp "${source_path}" "${bootstrap_root}/${target_name}"
    chown postgres:postgres "${bootstrap_root}/${target_name}"
    chmod 0400 "${bootstrap_root}/${target_name}"
}

chown root:root "${bootstrap_root}"
chmod 0700 "${bootstrap_root}"
copy_bootstrap_secret \
    /run/secrets/postgres_superuser_password postgres_superuser_password
if [ ! -f "${PGDATA}/.hormuz-roles-initialized" ]; then
    copy_bootstrap_secret \
        /run/secrets/postgres_runtime_password postgres_runtime_password
fi
chown root:postgres "${bootstrap_root}"
chmod 0510 "${bootstrap_root}"

exec /usr/local/bin/docker-entrypoint.sh "$@"
