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
if [ -z "${DEVELOPER_DIR:-}" ] && [ -d /Applications/Xcode.app/Contents/Developer ]; then
  # Process-local selection only; do not change the machine-wide xcode-select.
  export DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer
fi

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
mkdir -p "$HORMUZ_BUNDLE/Contents/MacOS"
mkdir -p "$HORMUZ_BUNDLE/Contents/Resources"
cp "$HORMUZ_BUILD_DIR/Hormuz" "$HORMUZ_BINARY"
cp "$HORMUZ_MAC_ROOT/Resources/Info.plist" "$HORMUZ_BUNDLE/Contents/Info.plist"
cp "$HORMUZ_MAC_ROOT/Resources/Hormuz.icns" "$HORMUZ_BUNDLE/Contents/Resources/Hormuz.icns"
chmod 755 "$HORMUZ_BINARY"
# Local debug signature only. Does not access a Developer ID private key, submit
# to Apple, notarize, or produce an artifact suitable for public distribution.
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
