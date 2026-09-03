#!/usr/bin/env bash
set -euo pipefail
HORMUZ_REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
exec "$HORMUZ_REPO_ROOT/clients/macos/script/notarize_release.sh" "$@"
