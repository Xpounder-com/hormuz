#!/bin/sh

set -eu

read_secret() {
    secret_path=$1
    secret_name=$2
    if [ ! -f "${secret_path}" ] || [ -L "${secret_path}" ]; then
        printf 'compose secret unavailable: %s\n' "${secret_name}" >&2
        exit 2
    fi
    secret_value=$(cat "${secret_path}")
    if [ -z "${secret_value}" ]; then
        printf 'compose secret empty: %s\n' "${secret_name}" >&2
        exit 2
    fi
    printf '%s' "${secret_value}"
}

export HORMUZ_POSTGRES_DSN="$(read_secret /run/secrets/postgres_runtime_dsn postgres_runtime_dsn)"

if [ -f /run/secrets/postgres_migration_dsn ]; then
    export HORMUZ_POSTGRES_MIGRATION_DSN="$(
        read_secret /run/secrets/postgres_migration_dsn postgres_migration_dsn
    )"
fi

case "${1:-}" in
    serve)
        if [ -f /run/secrets/postgres_migration_dsn ]; then
            printf 'gateway runtime must not receive the PostgreSQL migration credential\n' >&2
            exit 2
        fi
        export HORMUZ_TOKEN="$(read_secret /run/secrets/hormuz_identity_token hormuz_identity_token)"
        export HORMUZ_INGRESS_CREDENTIAL="$(
            read_secret /run/secrets/hormuz_ingress_credential hormuz_ingress_credential
        )"
        export OPENAI_API_KEY="$(read_secret /run/secrets/openai_api_key openai_api_key)"
        export ANTHROPIC_API_KEY="$(read_secret /run/secrets/anthropic_api_key anthropic_api_key)"
        ;;
    storage|doctor)
        if [ ! -f /run/secrets/postgres_migration_dsn ]; then
            printf 'operator command requires the PostgreSQL migration credential\n' >&2
            exit 2
        fi
        ;;
    *)
        printf 'unsupported Compose launcher command\n' >&2
        exit 2
        ;;
esac

exec /opt/hormuz/bin/hormuz "$@"
