#!/bin/sh
set -eu

hormuz_port=${PORT:-10000}
case "$hormuz_port" in
    ''|*[!0-9]*|0*)
        printf '%s\n' 'preflight_invalid_port' >&2
        exit 1
        ;;
esac
if [ "${#hormuz_port}" -gt 5 ] || [ "$hormuz_port" -lt 1024 ] || [ "$hormuz_port" -gt 65535 ]; then
    printf '%s\n' 'preflight_invalid_port' >&2
    exit 1
fi

# Render supplies this public revision. Never reflect an arbitrary env value
# into a response or log if it is not a complete lowercase Git SHA-1.
hormuz_revision=${RENDER_GIT_COMMIT:-unavailable}
case "$hormuz_revision" in
    *[!0-9a-f]*|'') hormuz_revision=unavailable ;;
esac
if [ "${#hormuz_revision}" -ne 40 ]; then
    hormuz_revision=unavailable
fi

export PORT="$hormuz_port"
export HORMUZ_PREFLIGHT_REVISION="$hormuz_revision"
printf '{"event":"https_preflight_started","source_commit":"%s","gateway_ready":false}\n' "$hormuz_revision"
exec caddy run --config /etc/caddy/Caddyfile --adapter caddyfile
