#!/bin/bash
set -euo pipefail
umask 077

fail() {
  printf 'macos_clean_machine_error=%s\n' "$1" >&2
  exit 1
}

[[ "$#" -eq 3 ]] || fail invalid_arguments
INPUTS="$1"
EXPECTED_ARCHITECTURE="$2"
OUTPUT="$3"
[[ "$EXPECTED_ARCHITECTURE" == "arm64" || "$EXPECTED_ARCHITECTURE" == "x86_64" ]] \
  || fail architecture_invalid
[[ "$OUTPUT" == /* && ! -e "$OUTPUT" && ! -L "$OUTPUT" ]] || fail output_path_unsafe
[[ -f "$INPUTS" && ! -L "$INPUTS" ]] || fail inputs_unsafe
[[ "$(/usr/bin/stat -f '%z' "$INPUTS")" -le 1048576 ]] || fail inputs_too_large

input_value() {
  /usr/bin/plutil -extract "$1" raw -o - "$INPUTS" 2>/dev/null \
    || fail inputs_invalid
}

SOURCE_COMMIT="$(input_value source_commit)"
SCHEMA_ID="$(input_value schema_id)"
SCHEMA_VERSION="$(input_value schema_version)"
CANDIDATE_SOURCE="$(input_value candidate.source_commit)"
ARCHIVE_NAME="$(input_value candidate.archive_name)"
ARCHIVE_BYTES="$(input_value candidate.archive_bytes)"
ARCHIVE_SHA256="$(input_value candidate.archive_sha256)"
VERSION="$(input_value candidate.version)"
BUILD="$(input_value candidate.build)"
[[ "$SCHEMA_ID" == "hormuz.macos-pilot-operations-inputs" && "$SCHEMA_VERSION" == "1" ]] \
  || fail inputs_schema_invalid
[[ "$SOURCE_COMMIT" =~ ^[0-9a-f]{40}$ && "$CANDIDATE_SOURCE" == "$SOURCE_COMMIT" ]] \
  || fail source_binding_invalid
[[ "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || fail version_invalid
[[ "$BUILD" =~ ^[1-9][0-9]{0,17}$ ]] || fail build_invalid
[[ "$ARCHIVE_NAME" == "Hormuz-${VERSION}-notarized.zip" ]] || fail archive_name_invalid
[[ "$ARCHIVE_BYTES" =~ ^[1-9][0-9]{0,9}$ ]] || fail archive_size_invalid
[[ "$ARCHIVE_BYTES" -le 1073741824 ]] || fail archive_size_invalid
[[ "$ARCHIVE_SHA256" =~ ^[0-9a-f]{64}$ ]] || fail archive_digest_invalid

STARTED_AT="$(/bin/date -u '+%Y-%m-%dT%H:%M:%SZ')"
RUN_ID="mcr:$(/usr/bin/uuidgen | /usr/bin/tr '[:upper:]' '[:lower:]')"
ACTUAL_ARCHITECTURE="$(/usr/bin/uname -m)"
[[ "$ACTUAL_ARCHITECTURE" == "$EXPECTED_ARCHITECTURE" ]] || fail wrong_machine_architecture
MACOS_VERSION="$(/usr/bin/sw_vers -productVersion)"
MACOS_MAJOR="${MACOS_VERSION%%.*}"
[[ "$MACOS_MAJOR" =~ ^[0-9]{2}$ && "$MACOS_MAJOR" -ge 14 ]] || fail unsupported_macos
[[ "${RUNNER_TEMP:-}" == /* && "${RUNNER_TEMP:-}" != "/" \
      && "${GITHUB_RUN_ID:-}" =~ ^[1-9][0-9]*$ \
      && "${GITHUB_RUN_ATTEMPT:-}" =~ ^[1-9][0-9]*$ ]] \
  || fail runner_environment_invalid
[[ ! -e /Applications/Xcode.app && ! -e /Library/Developer/CommandLineTools ]] \
  || fail developer_tools_present
if /usr/bin/xcode-select -p >/dev/null 2>&1; then
  fail developer_tools_present
fi

ARCHIVE="$HOME/Downloads/$ARCHIVE_NAME"
DOWNLOADED_APP="$HOME/Downloads/Hormuz.app"
[[ -f "$ARCHIVE" && ! -L "$ARCHIVE" ]] || fail safari_archive_missing
[[ "$(/usr/bin/stat -f '%z' "$ARCHIVE")" == "$ARCHIVE_BYTES" ]] \
  || fail archive_size_mismatch
[[ "$(/usr/bin/shasum -a 256 "$ARCHIVE" | /usr/bin/awk '{print $1}')" == "$ARCHIVE_SHA256" ]] \
  || fail archive_digest_mismatch
ARCHIVE_QUARANTINE="$(/usr/bin/xattr -p com.apple.quarantine "$ARCHIVE" 2>/dev/null)" \
  || fail archive_quarantine_missing
[[ "$ARCHIVE_QUARANTINE" == *Safari* ]] || fail safari_quarantine_missing
[[ -d "$DOWNLOADED_APP" && ! -L "$DOWNLOADED_APP" ]] || fail archive_utility_app_missing
APP_QUARANTINE="$(/usr/bin/xattr -p com.apple.quarantine "$DOWNLOADED_APP" 2>/dev/null)" \
  || fail app_quarantine_missing
[[ "$APP_QUARANTINE" == *Safari* ]] || fail app_safari_quarantine_missing

BINDING_ROOT="$RUNNER_TEMP/hormuz-clean-binding-$GITHUB_RUN_ID-$GITHUB_RUN_ATTEMPT-$EXPECTED_ARCHITECTURE"
[[ ! -e "$BINDING_ROOT" && ! -L "$BINDING_ROOT" ]] || fail binding_path_unsafe
/bin/mkdir -m 700 "$BINDING_ROOT"
cleanup() {
  /bin/rm -rf "$BINDING_ROOT"
}
trap cleanup EXIT
/usr/bin/ditto -x -k "$ARCHIVE" "$BINDING_ROOT" \
  || fail archive_binding_extraction_failed
ARCHIVE_APP="$BINDING_ROOT/Hormuz.app"
[[ -d "$ARCHIVE_APP" && ! -L "$ARCHIVE_APP" ]] || fail archive_binding_app_missing
/usr/bin/diff -qr "$DOWNLOADED_APP" "$ARCHIVE_APP" >/dev/null 2>&1 \
  || fail archive_contents_mismatch

verify_bundle() {
  local bundle="$1"
  local plist="$bundle/Contents/Info.plist"
  local binary="$bundle/Contents/MacOS/Hormuz"
  [[ -d "$bundle" && ! -L "$bundle" && -f "$plist" && -x "$binary" ]] \
    || fail bundle_layout_invalid
  [[ "$(/usr/bin/plutil -extract CFBundleIdentifier raw -o - "$plist")" == "com.xpounder.hormuz" ]] \
    || fail bundle_identifier_invalid
  [[ "$(/usr/bin/plutil -extract CFBundleShortVersionString raw -o - "$plist")" == "$VERSION" ]] \
    || fail bundle_version_invalid
  [[ "$(/usr/bin/plutil -extract CFBundleVersion raw -o - "$plist")" == "$BUILD" ]] \
    || fail bundle_build_invalid
  /usr/bin/codesign --verify --deep --strict --verbose=2 "$bundle" >/dev/null 2>&1 \
    || fail codesign_rejected
  local signing
  signing="$(/usr/bin/codesign -dv --verbose=4 "$bundle" 2>&1)" \
    || fail codesign_identity_unavailable
  printf '%s\n' "$signing" | /usr/bin/grep -qx 'TeamIdentifier=R267LZMUTY' \
    || fail team_identifier_invalid
  /usr/sbin/spctl --assess --type execute --verbose=2 "$bundle" >/dev/null 2>&1 \
    || fail gatekeeper_rejected
}

verify_bundle "$DOWNLOADED_APP"
[[ ! -e /Applications/Hormuz.app && ! -L /Applications/Hormuz.app ]] \
  || fail applications_destination_not_clean
/usr/bin/ditto "$DOWNLOADED_APP" /Applications/Hormuz.app \
  || fail applications_install_failed
verify_bundle /Applications/Hormuz.app
/usr/bin/open -n /Applications/Hormuz.app || fail launch_failed
launched=false
for _attempt in {1..30}; do
  if /usr/bin/pgrep -x Hormuz >/dev/null 2>&1; then
    launched=true
    break
  fi
  /bin/sleep 1
done
[[ "$launched" == true ]] || fail launch_failed

TMP_OUTPUT="$OUTPUT.tmp"
[[ ! -e "$TMP_OUTPUT" && ! -L "$TMP_OUTPUT" ]] || fail output_path_unsafe
/usr/bin/plutil -create json "$TMP_OUTPUT"
/usr/bin/plutil -insert run_id -string "$RUN_ID" "$TMP_OUTPUT"
/usr/bin/plutil -insert artifact_sha256 -string "$ARCHIVE_SHA256" "$TMP_OUTPUT"
/usr/bin/plutil -insert started_at -string "$STARTED_AT" "$TMP_OUTPUT"
/usr/bin/plutil -insert architecture -string "$ACTUAL_ARCHITECTURE" "$TMP_OUTPUT"
/usr/bin/plutil -insert macos_major -integer "$MACOS_MAJOR" "$TMP_OUTPUT"
for field in developer_tools_absent quarantine_present gatekeeper_accepted \
  installed_in_applications launch_succeeded; do
  /usr/bin/plutil -insert "$field" -bool true "$TMP_OUTPUT"
done
/bin/chmod 600 "$TMP_OUTPUT"
/bin/mv "$TMP_OUTPUT" "$OUTPUT"
printf 'macos_clean_machine_status=passed\n'
