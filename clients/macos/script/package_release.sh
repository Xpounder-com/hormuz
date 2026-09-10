#!/usr/bin/env bash
set -euo pipefail
umask 077

usage() {
  cat >&2 <<'EOF'
Usage: package_release.sh --output-directory PATH [options]

Options:
  --bundle-id ID       Distribution bundle identifier (default: com.xpounder.hormuz)
  --version VERSION    CFBundleShortVersionString (default: package version)
  --build NUMBER       Positive CFBundleVersion integer (default: 1)
  --identity NAME      Developer ID Application identity; may also be set with
                       HORMUZ_CODESIGN_IDENTITY
  --prebuilt-binary PATH
                       Package this previously built Apple Silicon Hormuz binary
                       instead of compiling on the signing machine.
  --prebuilt-dsym PATH Optional dSYM directory paired with --prebuilt-binary.
  --context-helper-directory PATH
                       Directory containing the arm64 standalone helper built
                       from this Hormuz release.
  --tokenizer-cache PATH
                       Directory containing the two verified tiktoken vocabularies.
  --ad-hoc             Build a distribution-shaped local validation archive.
                       This output is explicitly not distributable.
EOF
}

HORMUZ_MAC_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HORMUZ_REPO_ROOT="$(cd "$HORMUZ_MAC_ROOT/../.." && pwd)"
HORMUZ_OUTPUT_DIRECTORY=""
HORMUZ_BUNDLE_ID="com.xpounder.hormuz"
HORMUZ_VERSION=""
HORMUZ_BUILD_NUMBER="1"
HORMUZ_IDENTITY="${HORMUZ_CODESIGN_IDENTITY:-}"
HORMUZ_PREBUILT_BINARY=""
HORMUZ_PREBUILT_DSYM=""
HORMUZ_CONTEXT_HELPER_DIRECTORY="${HORMUZ_CONTEXT_HELPER_DIRECTORY:-}"
HORMUZ_TOKENIZER_CACHE="${HORMUZ_CONTEXT_TOKENIZER_CACHE:-}"
HORMUZ_AD_HOC=0

while [ "$#" -gt 0 ]; do
  case "$1" in
    --output-directory) HORMUZ_OUTPUT_DIRECTORY="${2:-}"; shift 2 ;;
    --bundle-id) HORMUZ_BUNDLE_ID="${2:-}"; shift 2 ;;
    --version) HORMUZ_VERSION="${2:-}"; shift 2 ;;
    --build) HORMUZ_BUILD_NUMBER="${2:-}"; shift 2 ;;
    --identity) HORMUZ_IDENTITY="${2:-}"; shift 2 ;;
    --prebuilt-binary) HORMUZ_PREBUILT_BINARY="${2:-}"; shift 2 ;;
    --prebuilt-dsym) HORMUZ_PREBUILT_DSYM="${2:-}"; shift 2 ;;
    --context-helper-directory) HORMUZ_CONTEXT_HELPER_DIRECTORY="${2:-}"; shift 2 ;;
    --tokenizer-cache) HORMUZ_TOKENIZER_CACHE="${2:-}"; shift 2 ;;
    --ad-hoc) HORMUZ_AD_HOC=1; shift ;;
    --help|-h) usage; exit 0 ;;
    *) usage; exit 2 ;;
  esac
done

if [ -z "$HORMUZ_VERSION" ]; then
  HORMUZ_VERSION="$(python3 - "$HORMUZ_REPO_ROOT/pyproject.toml" <<'PY'
import sys
import tomllib
from pathlib import Path

with Path(sys.argv[1]).open("rb") as source:
    print(tomllib.load(source)["project"]["version"])
PY
)"
fi

if [ -z "$HORMUZ_OUTPUT_DIRECTORY" ]; then
  usage
  exit 2
fi
if ! [[ "$HORMUZ_BUNDLE_ID" =~ ^[A-Za-z0-9]+([.-][A-Za-z0-9]+)+$ ]] || [[ "$HORMUZ_BUNDLE_ID" == *.local ]]; then
  echo "A permanent reverse-DNS bundle identifier is required." >&2
  exit 2
fi
if ! [[ "$HORMUZ_VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  echo "Version must contain three numeric components." >&2
  exit 2
fi
if ! [[ "$HORMUZ_BUILD_NUMBER" =~ ^[1-9][0-9]*$ ]]; then
  echo "Build number must be a positive integer." >&2
  exit 2
fi
if [ -n "$HORMUZ_PREBUILT_DSYM" ] && [ -z "$HORMUZ_PREBUILT_BINARY" ]; then
  echo "--prebuilt-dsym requires --prebuilt-binary." >&2
  exit 2
fi
if [ -z "$HORMUZ_CONTEXT_HELPER_DIRECTORY" ] || [ -L "$HORMUZ_CONTEXT_HELPER_DIRECTORY" ] || [ ! -d "$HORMUZ_CONTEXT_HELPER_DIRECTORY" ]; then
  echo "--context-helper-directory must contain the standalone arm64 helper for this release." >&2
  exit 2
fi
HORMUZ_CONTEXT_HELPER_DIRECTORY="$(cd "$HORMUZ_CONTEXT_HELPER_DIRECTORY" && pwd -P)"
HORMUZ_CONTEXT_HELPER_ARM64_SOURCE="$HORMUZ_CONTEXT_HELPER_DIRECTORY/hormuz-context-arm64"
if [ -L "$HORMUZ_CONTEXT_HELPER_ARM64_SOURCE" ] || [ ! -f "$HORMUZ_CONTEXT_HELPER_ARM64_SOURCE" ]; then
  echo "The context helper directory is incomplete or unsafe." >&2
  exit 2
fi
if [ "$(lipo -archs "$HORMUZ_CONTEXT_HELPER_ARM64_SOURCE")" != "arm64" ]; then
  echo "The context helper backend must contain exactly arm64." >&2
  exit 1
fi
if [ -z "$HORMUZ_TOKENIZER_CACHE" ] || [ -L "$HORMUZ_TOKENIZER_CACHE" ] || [ ! -d "$HORMUZ_TOKENIZER_CACHE" ]; then
  echo "--tokenizer-cache must name the verified release vocabulary directory." >&2
  exit 2
fi
HORMUZ_TOKENIZER_CACHE="$(cd "$HORMUZ_TOKENIZER_CACHE" && pwd -P)"
HORMUZ_CL100K="9b5ad71b2ce5302211f9c61530b329a4922fc6a4"
HORMUZ_O200K="fb374d419588a4632f3f557e76b4b70aebbca790"
if [ "$(shasum -a 256 "$HORMUZ_TOKENIZER_CACHE/$HORMUZ_CL100K" | awk '{print $1}')" != "223921b76ee99bde995b7ff738513eef100fb51d18c93597a113bcffe865b2a7" ] ||
   [ "$(shasum -a 256 "$HORMUZ_TOKENIZER_CACHE/$HORMUZ_O200K" | awk '{print $1}')" != "446a9538cb6c348e3516120d7c08b09f57c36495e2acfffe59a5bf8b0cfb1a2d" ]; then
  echo "Tokenizer vocabulary verification failed." >&2
  exit 1
fi
if [ -n "$HORMUZ_PREBUILT_BINARY" ]; then
  if [ -L "$HORMUZ_PREBUILT_BINARY" ] || [ ! -f "$HORMUZ_PREBUILT_BINARY" ]; then
    echo "The prebuilt Hormuz binary is not a regular file." >&2
    exit 2
  fi
  HORMUZ_PREBUILT_BINARY="$(cd "$(dirname "$HORMUZ_PREBUILT_BINARY")" && pwd -P)/$(basename "$HORMUZ_PREBUILT_BINARY")"
fi
if [ -n "$HORMUZ_PREBUILT_DSYM" ]; then
  if [ -L "$HORMUZ_PREBUILT_DSYM" ] || [ ! -d "$HORMUZ_PREBUILT_DSYM" ]; then
    echo "The prebuilt Hormuz dSYM is not a directory." >&2
    exit 2
  fi
  HORMUZ_PREBUILT_DSYM="$(cd "$(dirname "$HORMUZ_PREBUILT_DSYM")" && pwd -P)/$(basename "$HORMUZ_PREBUILT_DSYM")"
fi
if [ "$HORMUZ_AD_HOC" -eq 0 ]; then
  case "$HORMUZ_IDENTITY" in
    "Developer ID Application: "*) ;;
    *) echo "A full Developer ID Application identity is required." >&2; exit 2 ;;
  esac
  if [ "$(security find-identity -v -p codesigning | grep -F -c "\"$HORMUZ_IDENTITY\"")" -ne 1 ]; then
    echo "The requested Developer ID Application identity is not uniquely available." >&2
    exit 1
  fi
  codesign --verify --strict --verbose=4 "$HORMUZ_CONTEXT_HELPER_ARM64_SOURCE"
  HORMUZ_CONTEXT_SIGNATURE_DETAILS="$(codesign -dvvv "$HORMUZ_CONTEXT_HELPER_ARM64_SOURCE" 2>&1)"
  printf '%s\n' "$HORMUZ_CONTEXT_SIGNATURE_DETAILS" | \
    grep -Fqx "Identifier=$HORMUZ_BUNDLE_ID.context-helper.arm64"
  printf '%s\n' "$HORMUZ_CONTEXT_SIGNATURE_DETAILS" | grep -Eq '^CodeDirectory .*flags=.*runtime'
  printf '%s\n' "$HORMUZ_CONTEXT_SIGNATURE_DETAILS" | grep -Fqx "Authority=$HORMUZ_IDENTITY"
  printf '%s\n' "$HORMUZ_CONTEXT_SIGNATURE_DETAILS" | grep -Eq '^Timestamp=.+$'
fi
if [ -e "$HORMUZ_OUTPUT_DIRECTORY" ]; then
  echo "Output directory already exists; refusing to mix release artifacts." >&2
  exit 1
fi
mkdir -p "$(dirname "$HORMUZ_OUTPUT_DIRECTORY")"
mkdir "$HORMUZ_OUTPUT_DIRECTORY"
HORMUZ_OUTPUT_DIRECTORY="$(cd "$HORMUZ_OUTPUT_DIRECTORY" && pwd)"

if [ -z "${DEVELOPER_DIR:-}" ] && [ -d /Applications/Xcode.app/Contents/Developer ]; then
  export DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer
fi
if [ -z "$HORMUZ_PREBUILT_BINARY" ]; then
  xcrun --find swift >/dev/null
fi
xcrun --find codesign >/dev/null
xcrun --find lipo >/dev/null

HORMUZ_TEMPORARY="$(mktemp -d "${TMPDIR:-/tmp}/hormuz-macos-package.XXXXXX")"
HORMUZ_SUCCEEDED=0
cleanup() {
  rm -rf "$HORMUZ_TEMPORARY"
  if [ "$HORMUZ_SUCCEEDED" -ne 1 ]; then
    rm -rf "$HORMUZ_OUTPUT_DIRECTORY"
  fi
}
trap cleanup EXIT

export CLANG_MODULE_CACHE_PATH="$HORMUZ_TEMPORARY/clang-module-cache"
export SWIFTPM_MODULECACHE_OVERRIDE="$HORMUZ_TEMPORARY/swiftpm-module-cache"
if [ -n "$HORMUZ_PREBUILT_BINARY" ]; then
  HORMUZ_BINARY_SOURCE="$HORMUZ_PREBUILT_BINARY"
  HORMUZ_DSYM_SOURCE="$HORMUZ_PREBUILT_DSYM"
else
  HORMUZ_SCRATCH="$HORMUZ_TEMPORARY/build"
  swift build --package-path "$HORMUZ_MAC_ROOT" --scratch-path "$HORMUZ_SCRATCH" \
    --configuration release --arch arm64 --product Hormuz
  HORMUZ_BINARY_SOURCE="$(swift build --package-path "$HORMUZ_MAC_ROOT" --scratch-path "$HORMUZ_SCRATCH" \
    --configuration release --arch arm64 --show-bin-path)/Hormuz"
  HORMUZ_DSYM_SOURCE="$(dirname "$HORMUZ_BINARY_SOURCE")/Hormuz.dSYM"
fi
case "$(lipo -archs "$HORMUZ_BINARY_SOURCE")" in
  "arm64") ;;
  *)
    echo "The Hormuz release binary must contain exactly arm64." >&2
    exit 1
    ;;
esac

HORMUZ_BUNDLE="$HORMUZ_OUTPUT_DIRECTORY/Hormuz.app"
HORMUZ_BINARY="$HORMUZ_BUNDLE/Contents/MacOS/Hormuz"
HORMUZ_CONTEXT_HELPER_BINARY="$HORMUZ_BUNDLE/Contents/Resources/ContextHelper/hormuz-context"
HORMUZ_CONTEXT_HELPER_ARM64="$HORMUZ_BUNDLE/Contents/Helpers/hormuz-context-arm64"
HORMUZ_CONTEXT_TOKENIZERS="$HORMUZ_BUNDLE/Contents/Resources/ContextTokenizers"
mkdir -p "$HORMUZ_BUNDLE/Contents/MacOS" "$HORMUZ_BUNDLE/Contents/Helpers" \
  "$HORMUZ_BUNDLE/Contents/Resources/ContextHelper" "$HORMUZ_CONTEXT_TOKENIZERS"
cp "$HORMUZ_BINARY_SOURCE" "$HORMUZ_BINARY"
cp "$HORMUZ_MAC_ROOT/Resources/hormuz-context-release" "$HORMUZ_CONTEXT_HELPER_BINARY"
cp "$HORMUZ_CONTEXT_HELPER_ARM64_SOURCE" "$HORMUZ_CONTEXT_HELPER_ARM64"
cp "$HORMUZ_TOKENIZER_CACHE/$HORMUZ_CL100K" "$HORMUZ_TOKENIZER_CACHE/$HORMUZ_O200K" "$HORMUZ_CONTEXT_TOKENIZERS/"
case "$(lipo -archs "$HORMUZ_BINARY")" in
  "arm64") ;;
  *)
    echo "The copied Hormuz release binary changed architecture." >&2
    exit 1
    ;;
esac
cp "$HORMUZ_MAC_ROOT/Resources/Info.plist" "$HORMUZ_BUNDLE/Contents/Info.plist"
cp "$HORMUZ_MAC_ROOT/Resources/Hormuz.icns" "$HORMUZ_BUNDLE/Contents/Resources/Hormuz.icns"
cp "$HORMUZ_MAC_ROOT/Resources/HormuzMark.png" "$HORMUZ_MAC_ROOT/Resources/HormuzMenuMark.png" "$HORMUZ_BUNDLE/Contents/Resources/"
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
plutil -replace CFBundleIdentifier -string "$HORMUZ_BUNDLE_ID" "$HORMUZ_BUNDLE/Contents/Info.plist"
plutil -replace CFBundleShortVersionString -string "$HORMUZ_VERSION" "$HORMUZ_BUNDLE/Contents/Info.plist"
plutil -replace CFBundleVersion -string "$HORMUZ_BUILD_NUMBER" "$HORMUZ_BUNDLE/Contents/Info.plist"
chmod 755 "$HORMUZ_BINARY" "$HORMUZ_CONTEXT_HELPER_BINARY" \
  "$HORMUZ_CONTEXT_HELPER_ARM64"
chmod 644 "$HORMUZ_CONTEXT_TOKENIZERS/$HORMUZ_CL100K" "$HORMUZ_CONTEXT_TOKENIZERS/$HORMUZ_O200K"
xattr -cr "$HORMUZ_BUNDLE"

if [ "$HORMUZ_AD_HOC" -eq 1 ]; then
  HORMUZ_MODE="ad-hoc"
  HORMUZ_ARCHIVE="$HORMUZ_OUTPUT_DIRECTORY/Hormuz-$HORMUZ_VERSION-local-validation.zip"
  # A one-file PyInstaller helper's embedded binaries cannot be re-signed after
  # assembly. The ad-hoc rehearsal disables hardened runtime on these copies so
  # locally built ad-hoc internals remain loadable. Release inputs arrive fully
  # signed by PyInstaller with the Developer ID identity and are preserved below.
  codesign --force --sign - --identifier "$HORMUZ_BUNDLE_ID.context-helper.arm64" --timestamp=none "$HORMUZ_CONTEXT_HELPER_ARM64"
  codesign --force --sign - --identifier "$HORMUZ_BUNDLE_ID" --options runtime --timestamp=none "$HORMUZ_BUNDLE"
else
  HORMUZ_MODE="developer-id"
  HORMUZ_ARCHIVE="$HORMUZ_OUTPUT_DIRECTORY/Hormuz-$HORMUZ_VERSION-notarization-upload.zip"
  # Preserve the PyInstaller signatures: they cover the embedded Python and
  # extension binaries, which cannot be repaired by post-processing a one-file
  # executable. Their identity and hardened-runtime flags were checked above.
  codesign --verify --strict --verbose=4 "$HORMUZ_CONTEXT_HELPER_ARM64"
  codesign --force --sign "$HORMUZ_IDENTITY" --identifier "$HORMUZ_BUNDLE_ID" --options runtime --timestamp "$HORMUZ_BUNDLE"
fi
codesign --verify --strict --verbose=4 "$HORMUZ_BUNDLE"
COPYFILE_DISABLE=1 ditto --norsrc --noextattr --noqtn --noacl -c -k --keepParent \
  "$HORMUZ_BUNDLE" "$HORMUZ_ARCHIVE"

if [ -n "$HORMUZ_DSYM_SOURCE" ] && [ -d "$HORMUZ_DSYM_SOURCE" ]; then
  COPYFILE_DISABLE=1 ditto --norsrc --noextattr --noqtn --noacl -c -k --keepParent \
    "$HORMUZ_DSYM_SOURCE" "$HORMUZ_OUTPUT_DIRECTORY/Hormuz-$HORMUZ_VERSION.dSYM.zip"
fi
HORMUZ_VERIFICATION_ARGS=(
  --bundle "$HORMUZ_BUNDLE"
  --archive "$HORMUZ_ARCHIVE"
  --mode "$HORMUZ_MODE"
  --expected-bundle-id "$HORMUZ_BUNDLE_ID"
  --expected-version "$HORMUZ_VERSION"
  --expected-build "$HORMUZ_BUILD_NUMBER"
  --output "$HORMUZ_OUTPUT_DIRECTORY/package-metadata.json"
)
if [ "$HORMUZ_AD_HOC" -eq 1 ]; then
  HORMUZ_VERIFICATION_ARGS+=(--verify-executable-version --verify-context-helper)
fi
python3 "$HORMUZ_REPO_ROOT/tools/verify_macos_distribution.py" \
  "${HORMUZ_VERIFICATION_ARGS[@]}"

HORMUZ_SUCCEEDED=1
printf 'Bundle: %s\nArchive: %s\nMetadata: %s\n' \
  "$HORMUZ_BUNDLE" "$HORMUZ_ARCHIVE" "$HORMUZ_OUTPUT_DIRECTORY/package-metadata.json"
