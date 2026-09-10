#!/usr/bin/env bash
set -euo pipefail
umask 077

if [ "$#" -ne 3 ] && [ "$#" -ne 5 ]; then
  echo "Usage: build_context_helper.sh PYTHON OUTPUT_DIRECTORY ARCH [CODESIGN_IDENTITY BUNDLE_ID]" >&2
  exit 2
fi

HORMUZ_CONTEXT_PYTHON="$1"
HORMUZ_CONTEXT_OUTPUT="$2"
HORMUZ_CONTEXT_ARCH="$3"
HORMUZ_CONTEXT_CODESIGN_IDENTITY="${4:-}"
HORMUZ_CONTEXT_BUNDLE_ID="${5:-}"
HORMUZ_CONTEXT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [ ! -x "$HORMUZ_CONTEXT_PYTHON" ] || [ -e "$HORMUZ_CONTEXT_OUTPUT" ]; then
  echo "Use an executable build Python and a new output directory." >&2
  exit 2
fi
if [ "$HORMUZ_CONTEXT_ARCH" != arm64 ]; then
  echo "Architecture must be arm64." >&2
  exit 2
fi
if [ "$(uname -m)" != "$HORMUZ_CONTEXT_ARCH" ]; then
  echo "Build Python must run natively as $HORMUZ_CONTEXT_ARCH." >&2
  exit 2
fi
if [ -n "$HORMUZ_CONTEXT_CODESIGN_IDENTITY" ]; then
  case "$HORMUZ_CONTEXT_CODESIGN_IDENTITY" in
    "Developer ID Application: "*) ;;
    *) echo "A Developer ID Application identity is required." >&2; exit 2 ;;
  esac
  if ! [[ "$HORMUZ_CONTEXT_BUNDLE_ID" =~ ^[A-Za-z0-9]+([.-][A-Za-z0-9]+)+$ ]]; then
    echo "A permanent reverse-DNS bundle identifier is required." >&2
    exit 2
  fi
fi

mkdir -p "$HORMUZ_CONTEXT_OUTPUT"
HORMUZ_CONTEXT_OUTPUT="$(cd "$HORMUZ_CONTEXT_OUTPUT" && pwd)"
export PYINSTALLER_CONFIG_DIR="$HORMUZ_CONTEXT_OUTPUT/pyinstaller-config"
"$HORMUZ_CONTEXT_PYTHON" -c 'import PyInstaller, tiktoken; assert tiktoken.__version__ == "0.12.0"'
HORMUZ_CONTEXT_IDENTIFIER="$HORMUZ_CONTEXT_BUNDLE_ID.context-helper.${HORMUZ_CONTEXT_ARCH//_/-}"
HORMUZ_CONTEXT_BUILD_ARGS=(
  --clean \
  --noconfirm \
  --onefile \
  --noupx \
  --name hormuz-context \
  --paths "$HORMUZ_CONTEXT_ROOT" \
  --collect-all tiktoken \
  --collect-submodules tiktoken_ext \
  --collect-data hormuz \
  --exclude-module keyring \
  --exclude-module requests \
  --target-arch "$HORMUZ_CONTEXT_ARCH" \
  --distpath "$HORMUZ_CONTEXT_OUTPUT/dist" \
  --workpath "$HORMUZ_CONTEXT_OUTPUT/build" \
  --specpath "$HORMUZ_CONTEXT_OUTPUT" \
)
if [ -n "$HORMUZ_CONTEXT_CODESIGN_IDENTITY" ]; then
  HORMUZ_CONTEXT_BUILD_ARGS+=(
    --codesign-identity "$HORMUZ_CONTEXT_CODESIGN_IDENTITY"
    --osx-bundle-identifier "$HORMUZ_CONTEXT_IDENTIFIER"
  )
fi
"$HORMUZ_CONTEXT_PYTHON" -m PyInstaller \
  "${HORMUZ_CONTEXT_BUILD_ARGS[@]}" \
  "$HORMUZ_CONTEXT_ROOT/tools/context_helper_entry.py"
if [ "$(lipo -archs "$HORMUZ_CONTEXT_OUTPUT/dist/hormuz-context")" != "$HORMUZ_CONTEXT_ARCH" ]; then
  echo "Context helper architecture does not match $HORMUZ_CONTEXT_ARCH." >&2
  exit 1
fi
if [ -n "$HORMUZ_CONTEXT_CODESIGN_IDENTITY" ]; then
  # PyInstaller signs embedded code as it assembles the one-file executable,
  # but its console executable keeps the file basename as the outer identifier.
  # Re-sign the completed outer binary with Hormuz's permanent release identity.
  codesign --force \
    --sign "$HORMUZ_CONTEXT_CODESIGN_IDENTITY" \
    --identifier "$HORMUZ_CONTEXT_IDENTIFIER" \
    --options runtime \
    --timestamp \
    "$HORMUZ_CONTEXT_OUTPUT/dist/hormuz-context"
  codesign --verify --strict --verbose=4 "$HORMUZ_CONTEXT_OUTPUT/dist/hormuz-context"
  HORMUZ_CONTEXT_SIGNATURE="$(codesign -dvvv "$HORMUZ_CONTEXT_OUTPUT/dist/hormuz-context" 2>&1)"
  printf '%s\n' "$HORMUZ_CONTEXT_SIGNATURE" | grep -Fqx "Identifier=$HORMUZ_CONTEXT_IDENTIFIER"
  printf '%s\n' "$HORMUZ_CONTEXT_SIGNATURE" | grep -Eq '^CodeDirectory .*flags=.*runtime'
  printf '%s\n' "$HORMUZ_CONTEXT_SIGNATURE" | grep -Fqx "Authority=$HORMUZ_CONTEXT_CODESIGN_IDENTITY"
fi
if [ "${HORMUZ_CONTEXT_SKIP_RUNTIME_CHECK:-0}" != "1" ]; then
  "$HORMUZ_CONTEXT_OUTPUT/dist/hormuz-context" context --help >/dev/null
fi
printf 'Context helper: %s\n' "$HORMUZ_CONTEXT_OUTPUT/dist/hormuz-context"
