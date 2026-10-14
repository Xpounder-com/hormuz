#!/usr/bin/env bash
set -euo pipefail

HORMUZ_MODE="${1:-run}"
case "$HORMUZ_MODE" in
  run|--build-only|--verify|--debug|--logs|--telemetry) ;;
  *) echo "Usage: $0 [--build-only|--verify|--debug|--logs|--telemetry]" >&2; exit 2 ;;
esac
HORMUZ_MAC_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HORMUZ_BUNDLE="$HORMUZ_MAC_ROOT/dist/Hormuz.app"
HORMUZ_BINARY="$HORMUZ_BUNDLE/Contents/MacOS/Hormuz"
HORMUZ_CONTEXT_HELPER="$HORMUZ_BUNDLE/Contents/Resources/ContextHelper/hormuz-context"
HORMUZ_REPO_ROOT="$(cd "$HORMUZ_MAC_ROOT/../.." && pwd)"
HORMUZ_CARGO="${HORMUZ_CARGO:-cargo}"
HORMUZ_RELAY_TARGET="${CARGO_TARGET_DIR:-$HORMUZ_REPO_ROOT/clients/rust/target}"
if [ -z "${DEVELOPER_DIR:-}" ] && [ -d /Applications/Xcode.app/Contents/Developer ]; then
  # Process-local selection only; do not change the machine-wide xcode-select.
  export DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer
fi
case "$HORMUZ_RELAY_TARGET" in
  /*) ;;
  *) HORMUZ_RELAY_TARGET="$PWD/$HORMUZ_RELAY_TARGET" ;;
esac
(cd "$HORMUZ_REPO_ROOT/clients/rust"; "$HORMUZ_CARGO" build \
  --target-dir "$HORMUZ_RELAY_TARGET" --locked --package hormuz-client-relay --package hormuz-client-ui)
export HORMUZ_RUST_UI_LIBRARY_DIR="$HORMUZ_RELAY_TARGET/debug"

# Stop only the GUI from this build directory, never a helper or another copy.
if [ "$HORMUZ_MODE" != "--build-only" ]; then
  for HORMUZ_PID in $(pgrep -x Hormuz || true); do
    if [ "$(ps -p "$HORMUZ_PID" -o args=)" = "$HORMUZ_BINARY" ]; then
      kill "$HORMUZ_PID"
    fi
  done
fi
swift build --package-path "$HORMUZ_MAC_ROOT" --product Hormuz
HORMUZ_BUILD_DIR="$(swift build --package-path "$HORMUZ_MAC_ROOT" --show-bin-path)"
if ! nm -gU "$HORMUZ_BUILD_DIR/Hormuz" | grep ' _hormuz_ui_abi_version$' > /dev/null; then
  echo "The source-matched Rust UI ABI must be embedded in the Mac executable." >&2
  exit 1
fi
mkdir -p "$HORMUZ_BUNDLE/Contents/MacOS"
mkdir -p "$HORMUZ_BUNDLE/Contents/Resources/ContextHelper"
mkdir -p "$HORMUZ_BUNDLE/Contents/Helpers"
cp "$HORMUZ_BUILD_DIR/Hormuz" "$HORMUZ_BINARY"
cp "$HORMUZ_RELAY_TARGET/debug/hormuz-client-relay" "$HORMUZ_BUNDLE/Contents/Helpers/hormuz-client-relay"
cp "$HORMUZ_MAC_ROOT/Resources/Info.plist" "$HORMUZ_BUNDLE/Contents/Info.plist"
if [ -n "${HORMUZ_DESKTOP_ORIGIN:-}" ]; then
  python3 - "$HORMUZ_DESKTOP_ORIGIN" <<'PY'
import sys
from urllib.parse import urlsplit
url = urlsplit(sys.argv[1])
if (url.scheme != "https" and not (url.scheme == "http" and url.hostname in {"127.0.0.1", "localhost", "::1"})) or not url.hostname or url.username or url.password or url.path or url.query or url.fragment:
    raise SystemExit("HORMUZ_DESKTOP_ORIGIN must be an HTTPS or loopback origin")
PY
  plutil -replace HormuzDesktopOrigin -string "$HORMUZ_DESKTOP_ORIGIN" "$HORMUZ_BUNDLE/Contents/Info.plist"
fi
cp "$HORMUZ_MAC_ROOT/Resources/Hormuz.icns" "$HORMUZ_BUNDLE/Contents/Resources/Hormuz.icns"
cp "$HORMUZ_MAC_ROOT/Resources/HormuzMark.png" "$HORMUZ_MAC_ROOT/Resources/HormuzMenuMark.png" "$HORMUZ_BUNDLE/Contents/Resources/"
cp "$HORMUZ_MAC_ROOT/Resources/hormuz-context-dev" "$HORMUZ_CONTEXT_HELPER"
mkdir -p "$HORMUZ_BUNDLE/Contents/Resources/ThirdPartyNotices"
for HORMUZ_NOTICE in \
  Codenotch-LICENSE.txt \
  Python-LICENSE.txt \
  PyInstaller-COPYING.txt \
  regex-LICENSE.txt \
  tiktoken-LICENSE.txt; do
  cp "$HORMUZ_MAC_ROOT/Resources/ThirdPartyNotices/$HORMUZ_NOTICE" \
    "$HORMUZ_BUNDLE/Contents/Resources/ThirdPartyNotices/"
done
chmod 755 "$HORMUZ_BINARY" "$HORMUZ_CONTEXT_HELPER"
# The shell launcher is a sealed app resource. Shell scripts cannot carry a
# durable embedded signature, so signing it separately would rely on extended
# attributes that are lost during ordinary archive transfer.
codesign --force --sign - --identifier com.hormuz.mac.local.relay --options runtime --timestamp=none \
  "$HORMUZ_BUNDLE/Contents/Helpers/hormuz-client-relay"
codesign --force --sign - --identifier com.hormuz.mac.local --options runtime --timestamp=none "$HORMUZ_BUNDLE"
codesign --verify --strict "$HORMUZ_BUNDLE"

case "$HORMUZ_MODE" in
  --build-only) echo "Local app bundle: $HORMUZ_BUNDLE" ;;
  --debug) exec lldb -- "$HORMUZ_BINARY" ;;
  *)
    /usr/bin/open -n "$HORMUZ_BUNDLE"
    case "$HORMUZ_MODE" in
      --verify) sleep 1; pgrep -x Hormuz >/dev/null; echo "Hormuz process is running; this does not verify its UI state." ;;
      --logs) exec /usr/bin/log stream --info --style compact --predicate 'process == "Hormuz"' ;;
      --telemetry) exec /usr/bin/log stream --info --style compact --predicate 'subsystem == "com.hormuz.mac.local"' ;;
    esac
    ;;
esac
