#!/bin/sh
# Start one relay invocation as the main process of a transient user service.
# The shell/native caller owns the unit name and must stop that unit on quit.
set -eu

if [ "$#" -lt 2 ]; then
    echo 'usage: run-in-user-service.sh <unit-token> <absolute-executable> [args...]' >&2
    exit 2
fi
unit_token=$1
shift
case "$unit_token" in
    ''|*[!A-Za-z0-9_-]*)
        echo 'relay unit token must use letters, digits, underscore, or hyphen' >&2
        exit 2 ;;
esac
case "$1" in
    /*) ;;
    *) echo 'relay executable must be absolute' >&2; exit 2 ;;
esac
if [ -z "${PATH-}" ]; then
    echo 'relay user service requires the caller session PATH' >&2
    exit 2
fi

# Fail closed on older systemd-run versions rather than silently changing the
# service environment or expanding an already-formed relay argv.
if ! systemd_run_help=$(/usr/bin/systemd-run --help 2>/dev/null); then
    echo 'could not inspect systemd-run capabilities' >&2
    exit 2
fi
case "$systemd_run_help" in
    *'--setenv='*) ;;
    *) echo 'systemd-run does not support explicit environment forwarding' >&2; exit 2 ;;
esac
case "$systemd_run_help" in
    *'--expand-environment='*) ;;
    *) echo 'systemd-run does not support literal relay arguments' >&2; exit 2 ;;
esac
unset systemd_run_help

# Resolve the local per-UID user bus independently of caller-controlled
# DBUS_SESSION_BUS_ADDRESS and XDG_RUNTIME_DIR. The Rust guard verifies the
# bus socket, service properties, and current MainPID before any client launch.
uid=$(/usr/bin/id -u)
if [ "$uid" = 0 ]; then
    echo 'relay user service requires a non-root login user' >&2
    exit 2
fi
XDG_RUNTIME_DIR="/run/user/$uid"
DBUS_SESSION_BUS_ADDRESS="unix:path=$XDG_RUNTIME_DIR/bus"
export XDG_RUNTIME_DIR DBUS_SESSION_BUS_ADDRESS

exec /usr/bin/systemd-run \
    --user --wait --collect --quiet --same-dir --pty --pipe \
    --expand-environment=no \
    --setenv=PATH \
    --unit="hormuz-relay-$unit_token.service" \
    --service-type=exec \
    --property=ExitType=main \
    --property=RemainAfterExit=no \
    --property=Restart=no \
    --property=KillMode=control-group \
    --property=KillSignal=SIGKILL \
    -- "$@"
