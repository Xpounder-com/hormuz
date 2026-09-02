#!/usr/bin/env bash
set -euo pipefail
umask 077

usage() {
  cat >&2 <<'EOF'
Usage: notarize_release.sh --bundle PATH --upload-archive PATH \
  --keychain-profile NAME [--keychain PATH]

The profile must already exist in the selected Keychain. Never pass an API key,
Apple password, or other credential to this script.
EOF
}

HORMUZ_MAC_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HORMUZ_REPO_ROOT="$(cd "$HORMUZ_MAC_ROOT/../.." && pwd)"
HORMUZ_BUNDLE=""
HORMUZ_UPLOAD=""
HORMUZ_PROFILE=""
HORMUZ_KEYCHAIN=""

while [ "$#" -gt 0 ]; do
  case "$1" in
    --bundle) HORMUZ_BUNDLE="${2:-}"; shift 2 ;;
    --upload-archive) HORMUZ_UPLOAD="${2:-}"; shift 2 ;;
    --keychain-profile) HORMUZ_PROFILE="${2:-}"; shift 2 ;;
    --keychain) HORMUZ_KEYCHAIN="${2:-}"; shift 2 ;;
    --help|-h) usage; exit 0 ;;
    *) usage; exit 2 ;;
  esac
done

if [ ! -d "$HORMUZ_BUNDLE" ] || [ ! -f "$HORMUZ_UPLOAD" ] || [ -z "$HORMUZ_PROFILE" ]; then
  usage
  exit 2
fi
if [ -z "${DEVELOPER_DIR:-}" ] && [ -d /Applications/Xcode.app/Contents/Developer ]; then
  export DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer
fi
xcrun --find notarytool >/dev/null
xcrun --find stapler >/dev/null

HORMUZ_PLIST="$HORMUZ_BUNDLE/Contents/Info.plist"
HORMUZ_BUNDLE_ID="$(plutil -extract CFBundleIdentifier raw -o - "$HORMUZ_PLIST")"
HORMUZ_VERSION="$(plutil -extract CFBundleShortVersionString raw -o - "$HORMUZ_PLIST")"
HORMUZ_BUILD_NUMBER="$(plutil -extract CFBundleVersion raw -o - "$HORMUZ_PLIST")"
HORMUZ_OUTPUT_DIRECTORY="$(cd "$(dirname "$HORMUZ_BUNDLE")" && pwd)"
HORMUZ_FINAL_ARCHIVE="$HORMUZ_OUTPUT_DIRECTORY/Hormuz-$HORMUZ_VERSION-notarized.zip"
HORMUZ_NOTARY_SUMMARY="$HORMUZ_OUTPUT_DIRECTORY/notarization.json"
HORMUZ_DISTRIBUTION_PROOF="$HORMUZ_OUTPUT_DIRECTORY/distribution-proof.json"
HORMUZ_PRE_NOTARY_PROOF="$HORMUZ_OUTPUT_DIRECTORY/pre-notarization-proof.json"
for HORMUZ_TARGET in "$HORMUZ_FINAL_ARCHIVE" "$HORMUZ_NOTARY_SUMMARY" "$HORMUZ_DISTRIBUTION_PROOF" "$HORMUZ_PRE_NOTARY_PROOF"; do
  if [ -e "$HORMUZ_TARGET" ]; then
    echo "Notarization output already exists; refusing to overwrite it." >&2
    exit 1
  fi
done

python3 "$HORMUZ_REPO_ROOT/tools/verify_macos_distribution.py" \
  --bundle "$HORMUZ_BUNDLE" \
  --archive "$HORMUZ_UPLOAD" \
  --mode developer-id \
  --expected-bundle-id "$HORMUZ_BUNDLE_ID" \
  --expected-version "$HORMUZ_VERSION" \
  --expected-build "$HORMUZ_BUILD_NUMBER" \
  --output "$HORMUZ_PRE_NOTARY_PROOF"

HORMUZ_NOTARY_TEMPORARY="$(mktemp -d "${TMPDIR:-/tmp}/hormuz-notary.XXXXXX")"
HORMUZ_SUBMISSION="$HORMUZ_NOTARY_TEMPORARY/submission.json"
HORMUZ_NOTARY_LOG="$HORMUZ_NOTARY_TEMPORARY/log.json"
trap 'rm -rf "$HORMUZ_NOTARY_TEMPORARY"' EXIT
HORMUZ_NOTARY_COMMAND=(xcrun notarytool submit "$HORMUZ_UPLOAD" --keychain-profile "$HORMUZ_PROFILE" \
  --wait --timeout 30m --no-progress --output-format json)
if [ -n "$HORMUZ_KEYCHAIN" ]; then
  HORMUZ_NOTARY_COMMAND+=(--keychain "$HORMUZ_KEYCHAIN")
fi
"${HORMUZ_NOTARY_COMMAND[@]}" > "$HORMUZ_SUBMISSION"

python3 - "$HORMUZ_SUBMISSION" "$HORMUZ_NOTARY_SUMMARY" <<'PY'
import json
import os
import sys
from pathlib import Path

source, output = map(Path, sys.argv[1:])
value = json.loads(source.read_text())
safe = {
    "schema_id": "hormuz.apple-notarization",
    "schema_version": 1,
    "submission_id": value.get("id"),
    "status": value.get("status"),
    "accepted": value.get("status") == "Accepted",
}
output.write_text(json.dumps(safe, indent=2, sort_keys=True) + "\n")
os.chmod(output, 0o600)
if not safe["accepted"]:
    print(f"Apple notarization did not accept submission {safe['submission_id']}: {safe['status']}", file=sys.stderr)
    raise SystemExit(1)
PY

HORMUZ_SUBMISSION_ID="$(python3 - "$HORMUZ_NOTARY_SUMMARY" <<'PY'
import json
import sys
from pathlib import Path

print(json.loads(Path(sys.argv[1]).read_text())["submission_id"])
PY
)"
HORMUZ_LOG_COMMAND=(xcrun notarytool log --keychain-profile "$HORMUZ_PROFILE")
if [ -n "$HORMUZ_KEYCHAIN" ]; then
  HORMUZ_LOG_COMMAND+=(--keychain "$HORMUZ_KEYCHAIN")
fi
HORMUZ_LOG_COMMAND+=("$HORMUZ_SUBMISSION_ID" "$HORMUZ_NOTARY_LOG")
"${HORMUZ_LOG_COMMAND[@]}"

python3 - "$HORMUZ_NOTARY_LOG" "$HORMUZ_NOTARY_SUMMARY" <<'PY'
import json
import os
import sys
from collections import Counter
from pathlib import Path

log_path, summary_path = map(Path, sys.argv[1:])
log = json.loads(log_path.read_text())
summary = json.loads(summary_path.read_text())
issues = log.get("issues") or []
tickets = log.get("ticketContents") or []
if not isinstance(issues, list) or not isinstance(tickets, list):
    raise SystemExit("Unexpected Apple notarization log structure.")
if log.get("jobId") != summary["submission_id"] or log.get("status") != "Accepted":
    raise SystemExit("Apple notarization log did not match the accepted submission.")
severities = Counter(str(item.get("severity", "unknown")) for item in issues if isinstance(item, dict))
summary.update(
    {
        "issue_count": len(issues),
        "issue_severities": dict(sorted(severities.items())),
        "ticket_entry_count": len(tickets),
    }
)
summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
os.chmod(summary_path, 0o600)
if issues:
    raise SystemExit("Apple accepted the submission with issues; inspect the private raw log before release.")
if len(tickets) < 2:
    raise SystemExit("Apple notarization log did not contain both universal app ticket entries.")
PY

xcrun stapler staple "$HORMUZ_BUNDLE"
xcrun stapler validate "$HORMUZ_BUNDLE"
COPYFILE_DISABLE=1 ditto --norsrc --noextattr --noqtn --noacl -c -k --keepParent \
  "$HORMUZ_BUNDLE" "$HORMUZ_FINAL_ARCHIVE"
python3 "$HORMUZ_REPO_ROOT/tools/verify_macos_distribution.py" \
  --bundle "$HORMUZ_BUNDLE" \
  --archive "$HORMUZ_FINAL_ARCHIVE" \
  --mode notarized \
  --expected-bundle-id "$HORMUZ_BUNDLE_ID" \
  --expected-version "$HORMUZ_VERSION" \
  --expected-build "$HORMUZ_BUILD_NUMBER" \
  --output "$HORMUZ_DISTRIBUTION_PROOF"

printf 'Notarized archive: %s\nProof: %s\n' "$HORMUZ_FINAL_ARCHIVE" "$HORMUZ_DISTRIBUTION_PROOF"
