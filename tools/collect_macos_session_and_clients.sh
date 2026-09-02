#!/bin/bash
set -euo pipefail
umask 077

fail() {
  printf 'macos_session_client_error=%s\n' "$1" >&2
  exit 1
}

[[ "$#" -eq 2 ]] || fail invalid_arguments
INPUTS="$1"
OUTPUT_DIRECTORY="$2"
[[ -f "$INPUTS" && ! -L "$INPUTS" ]] || fail inputs_unsafe
[[ -d "$OUTPUT_DIRECTORY" && ! -L "$OUTPUT_DIRECTORY" ]] || fail output_directory_unsafe
/bin/chmod 700 "$OUTPUT_DIRECTORY"
for output_name in lifecycle.json codex-recovery.json claude-recovery.json; do
  [[ ! -e "$OUTPUT_DIRECTORY/$output_name" && ! -L "$OUTPUT_DIRECTORY/$output_name" ]] \
    || fail output_path_unsafe
done

input_value() {
  /usr/bin/plutil -extract "$1" raw -o - "$INPUTS" 2>/dev/null \
    || fail inputs_invalid
}

[[ "$(input_value schema_id)" == "hormuz.macos-pilot-operations-inputs" ]] \
  || fail inputs_schema_invalid
[[ "$(input_value schema_version)" == "1" ]] || fail inputs_schema_invalid
SOURCE_COMMIT="$(input_value source_commit)"
CANDIDATE_SOURCE="$(input_value candidate.source_commit)"
CANDIDATE_NAME="$(input_value candidate.archive_name)"
CANDIDATE_BYTES="$(input_value candidate.archive_bytes)"
CANDIDATE_SHA256="$(input_value candidate.archive_sha256)"
CANDIDATE_VERSION="$(input_value candidate.version)"
CANDIDATE_BUILD="$(input_value candidate.build)"
PREVIOUS_NAME="$(input_value previous.archive_name)"
PREVIOUS_BYTES="$(input_value previous.archive_bytes)"
PREVIOUS_SHA256="$(input_value previous.archive_sha256)"
PREVIOUS_VERSION="$(input_value previous.version)"
PREVIOUS_BUILD="$(input_value previous.build)"
EXPECTED_GATEWAY="$(input_value gateway.origin)"
EXPECTED_SERVICE_ID="$(input_value gateway.service_id)"
[[ "$SOURCE_COMMIT" =~ ^[0-9a-f]{40}$ && "$CANDIDATE_SOURCE" == "$SOURCE_COMMIT" ]] \
  || fail source_commit_invalid
for version in "$CANDIDATE_VERSION" "$PREVIOUS_VERSION"; do
  [[ "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || fail version_invalid
done
[[ "$CANDIDATE_NAME" == "Hormuz-${CANDIDATE_VERSION}-notarized.zip" \
      && "$PREVIOUS_NAME" == "Hormuz-${PREVIOUS_VERSION}-notarized.zip" \
      && "$CANDIDATE_NAME" != "$PREVIOUS_NAME" ]] \
  || fail archive_name_invalid
for size in "$CANDIDATE_BYTES" "$PREVIOUS_BYTES"; do
  [[ "$size" =~ ^[1-9][0-9]{0,9}$ && "$size" -le 1073741824 ]] \
    || fail archive_size_invalid
done
for digest in "$CANDIDATE_SHA256" "$PREVIOUS_SHA256"; do
  [[ "$digest" =~ ^[0-9a-f]{64}$ ]] || fail archive_digest_invalid
done
for build in "$CANDIDATE_BUILD" "$PREVIOUS_BUILD"; do
  [[ "$build" =~ ^[1-9][0-9]{0,17}$ ]] || fail build_invalid
done
[[ "$CANDIDATE_BUILD" -gt "$PREVIOUS_BUILD" ]] || fail build_sequence_invalid
[[ "$EXPECTED_GATEWAY" =~ ^https://[A-Za-z0-9.-]+$ ]] || fail gateway_origin_invalid
[[ "$EXPECTED_SERVICE_ID" =~ ^srv-[a-z0-9]{16,32}$ ]] || fail gateway_service_id_invalid

[[ "${RUNNER_TEMP:-}" == /* && "${RUNNER_TEMP:-}" != "/" \
      && "${GITHUB_RUN_ID:-}" =~ ^[1-9][0-9]*$ \
      && "${GITHUB_RUN_ATTEMPT:-}" =~ ^[1-9][0-9]*$ ]] \
  || fail runner_environment_invalid
WORK_ROOT="$RUNNER_TEMP/hormuz-macos-session-clients-$GITHUB_RUN_ID-$GITHUB_RUN_ATTEMPT"
[[ "$WORK_ROOT" == /* && ! -e "$WORK_ROOT" && ! -L "$WORK_ROOT" ]] \
  || fail work_root_unsafe
/bin/mkdir -m 700 "$WORK_ROOT"
cleanup() {
  /usr/bin/pkill -P "$$" >/dev/null 2>&1 || true
  /bin/rm -rf "$WORK_ROOT"
}
trap cleanup EXIT

# These executable hashes are derived from the immutable npm releases recorded
# in docs/MACOS_PILOT_QUALIFICATION.md. The collector invokes the authenticated
# native binaries directly so PATH wrappers cannot satisfy official-client proof.
readonly CODEX_ENTRYPOINT_SHA256=134063e133f0b4244fa3b251acf973d4fe4b4aeeacbdc135211bf480f59f1477
readonly CODEX_RUNTIME_SHA256=19c4f144c5226a9f17c58e6f0fa854843b0f77a6eb420f40e2745a12f10f5d37
readonly CLAUDE_RUNTIME_SHA256=bc466b6cde63edafc773f471a1fb98787fabb31f52240c8616ce7e1f587b212d
readonly PILOT_CLIENT_ROOT="$HOME/.hormuz-pilot-clients"
CODEX_COMMAND_PATH=""
CLAUDE_COMMAND_PATH=""

verify_client_file() {
  local path="$1"
  local expected_sha256="$2"
  [[ -f "$path" && ! -L "$path" && -x "$path" ]] || fail client_file_unsafe
  [[ "$(/usr/bin/stat -f '%Su' "$path")" == "$(/usr/bin/id -un)" ]] \
    || fail client_file_owner_invalid
  local mode
  mode="$(/usr/bin/stat -f '%Lp' "$path")"
  [[ "$mode" =~ ^[0-7]{3,4}$ ]] || fail client_file_mode_invalid
  (( (8#$mode & 022) == 0 )) || fail client_file_mode_invalid
  [[ "$(/usr/bin/shasum -a 256 "$path" | /usr/bin/awk '{print $1}')" == "$expected_sha256" ]] \
    || fail client_file_digest_invalid
}

verify_client_binary() {
  local path="$1"
  local expected_sha256="$2"
  verify_client_file "$path" "$expected_sha256"
}

authenticate_codex_client() {
  [[ -d "$PILOT_CLIENT_ROOT" && ! -L "$PILOT_CLIENT_ROOT" \
        && "$(/usr/bin/stat -f '%Lp' "$PILOT_CLIENT_ROOT")" == "700" \
        && "$(/usr/bin/stat -f '%Su' "$PILOT_CLIENT_ROOT")" == "$(/usr/bin/id -un)" ]] \
    || fail client_install_root_unsafe
  local entrypoint="$PILOT_CLIENT_ROOT/node_modules/@openai/codex/bin/codex.js"
  verify_client_file "$entrypoint" "$CODEX_ENTRYPOINT_SHA256"
  local nested="$PILOT_CLIENT_ROOT/node_modules/@openai/codex/node_modules/@openai/codex-darwin-arm64/vendor/aarch64-apple-darwin/bin/codex"
  local hoisted="$PILOT_CLIENT_ROOT/node_modules/@openai/codex-darwin-arm64/vendor/aarch64-apple-darwin/bin/codex"
  local runtime=""
  local candidate
  for candidate in "$nested" "$hoisted"; do
    if [[ -f "$candidate" && ! -L "$candidate" ]]; then
      [[ -z "$runtime" ]] || fail codex_runtime_ambiguous
      runtime="$candidate"
    fi
  done
  [[ -n "$runtime" ]] || fail codex_runtime_missing
  verify_client_binary "$runtime" "$CODEX_RUNTIME_SHA256"
  CODEX_COMMAND_PATH="$runtime"
}

authenticate_claude_client() {
  [[ -d "$PILOT_CLIENT_ROOT" && ! -L "$PILOT_CLIENT_ROOT" \
        && "$(/usr/bin/stat -f '%Lp' "$PILOT_CLIENT_ROOT")" == "700" \
        && "$(/usr/bin/stat -f '%Su' "$PILOT_CLIENT_ROOT")" == "$(/usr/bin/id -un)" ]] \
    || fail client_install_root_unsafe
  local runtime="$PILOT_CLIENT_ROOT/node_modules/@anthropic-ai/claude-code/bin/claude.exe"
  verify_client_binary "$runtime" "$CLAUDE_RUNTIME_SHA256"
  CLAUDE_COMMAND_PATH="$runtime"
}

check_archive() {
  local archive="$1"
  local expected_bytes="$2"
  local expected_digest="$3"
  [[ -f "$archive" && ! -L "$archive" ]] || fail retained_archive_missing
  [[ "$(/usr/bin/stat -f '%z' "$archive")" == "$expected_bytes" ]] \
    || fail retained_archive_size_mismatch
  [[ "$(/usr/bin/shasum -a 256 "$archive" | /usr/bin/awk '{print $1}')" == "$expected_digest" ]] \
    || fail retained_archive_digest_mismatch
  local quarantine
  quarantine="$(/usr/bin/xattr -p com.apple.quarantine "$archive" 2>/dev/null)" \
    || fail retained_archive_quarantine_missing
  [[ "$quarantine" == *Safari* ]] || fail retained_archive_not_downloaded_with_safari
}

CANDIDATE_ARCHIVE="$HOME/Downloads/$CANDIDATE_NAME"
PREVIOUS_ARCHIVE="$HOME/Downloads/$PREVIOUS_NAME"
check_archive "$CANDIDATE_ARCHIVE" "$CANDIDATE_BYTES" "$CANDIDATE_SHA256"
check_archive "$PREVIOUS_ARCHIVE" "$PREVIOUS_BYTES" "$PREVIOUS_SHA256"
/bin/mkdir -m 700 "$WORK_ROOT/candidate" "$WORK_ROOT/previous"
/usr/bin/ditto -x -k "$CANDIDATE_ARCHIVE" "$WORK_ROOT/candidate" \
  || fail candidate_extraction_failed
/usr/bin/ditto -x -k "$PREVIOUS_ARCHIVE" "$WORK_ROOT/previous" \
  || fail previous_extraction_failed
CANDIDATE_APP="$WORK_ROOT/candidate/Hormuz.app"
PREVIOUS_APP="$WORK_ROOT/previous/Hormuz.app"

verify_bundle() {
  local bundle="$1"
  local version="$2"
  local build="$3"
  local plist="$bundle/Contents/Info.plist"
  [[ -d "$bundle" && ! -L "$bundle" && -f "$plist" && -x "$bundle/Contents/MacOS/Hormuz" ]] \
    || fail bundle_layout_invalid
  [[ "$(/usr/bin/plutil -extract CFBundleIdentifier raw -o - "$plist")" == "com.xpounder.hormuz" ]] \
    || fail bundle_identifier_invalid
  [[ "$(/usr/bin/plutil -extract CFBundleShortVersionString raw -o - "$plist")" == "$version" ]] \
    || fail bundle_version_invalid
  [[ "$(/usr/bin/plutil -extract CFBundleVersion raw -o - "$plist")" == "$build" ]] \
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

verify_bundle "$CANDIDATE_APP" "$CANDIDATE_VERSION" "$CANDIDATE_BUILD"
verify_bundle "$PREVIOUS_APP" "$PREVIOUS_VERSION" "$PREVIOUS_BUILD"

restart_app() {
  local candidate_pid candidate_command
  /usr/bin/pkill -x Hormuz >/dev/null 2>&1 || true
  local stopped=false
  for _attempt in {1..10}; do
    if ! /usr/bin/pgrep -x Hormuz >/dev/null 2>&1; then
      stopped=true
      break
    fi
    /bin/sleep 1
  done
  [[ "$stopped" == true ]] || fail app_process_not_stopped
  local app_binary=/Applications/Hormuz.app/Contents/MacOS/Hormuz
  /usr/bin/open -n /Applications/Hormuz.app || fail app_launch_failed
  local running=false
  for _attempt in {1..30}; do
    for candidate_pid in $(/usr/bin/pgrep -x Hormuz 2>/dev/null || true); do
      [[ "$candidate_pid" =~ ^[1-9][0-9]*$ ]] || continue
      candidate_command="$(/bin/ps -ww -p "$candidate_pid" -o command= 2>/dev/null || true)"
      if [[ "$candidate_command" == "$app_binary" || "$candidate_command" == "$app_binary "* ]]; then
        /bin/sleep 2
        candidate_command="$(/bin/ps -ww -p "$candidate_pid" -o command= 2>/dev/null || true)"
        if /bin/kill -0 "$candidate_pid" 2>/dev/null \
            && [[ "$candidate_command" == "$app_binary" || "$candidate_command" == "$app_binary "* ]]; then
          running=true
          break 2
        fi
      fi
    done
    /bin/sleep 1
  done
  [[ "$running" == true ]] || fail app_launch_failed
}

install_bundle() {
  local source="$1"
  local version="$2"
  local build="$3"
  if [[ -e /Applications/Hormuz.app || -L /Applications/Hormuz.app ]]; then
    [[ -d /Applications/Hormuz.app && ! -L /Applications/Hormuz.app ]] \
      || fail applications_destination_unsafe
    [[ "$(/usr/bin/plutil -extract CFBundleIdentifier raw -o - /Applications/Hormuz.app/Contents/Info.plist 2>/dev/null)" == "com.xpounder.hormuz" ]] \
      || fail applications_destination_unsafe
    /bin/rm -rf /Applications/Hormuz.app
  fi
  /usr/bin/ditto "$source" /Applications/Hormuz.app || fail app_install_failed
  verify_bundle /Applications/Hormuz.app "$version" "$build"
  restart_app
}

STATE_DIRECTORY="$HOME/Library/Application Support/Hormuz"
ACTIVE_PROFILE_ID=""
ACTIVE_GATEWAY=""
ACTIVE_MODEL=""

profile_is_safe() {
  [[ -d "$STATE_DIRECTORY" && ! -L "$STATE_DIRECTORY" ]] || return 1
  [[ "$(/usr/bin/stat -f '%Lp' "$STATE_DIRECTORY")" == "700" ]] || return 1
  local profile="$STATE_DIRECTORY/profile.json"
  [[ -f "$profile" && ! -L "$profile" ]] || return 1
  [[ "$(/usr/bin/stat -f '%Lp' "$profile")" == "600" ]] || return 1
}

wait_for_active_profile() {
  local expected_client="$1"
  printf 'macos_pilot_action=sign_in_%s_in_the_Hormuz_app\n' "$expected_client"
  for _attempt in {1..450}; do
    if profile_is_safe; then
      local profile="$STATE_DIRECTORY/profile.json"
      local client profile_id gateway model allow_http
      client="$(/usr/bin/plutil -extract client raw -o - "$profile" 2>/dev/null || true)"
      profile_id="$(/usr/bin/plutil -extract id raw -o - "$profile" 2>/dev/null || true)"
      gateway="$(/usr/bin/plutil -extract gateway raw -o - "$profile" 2>/dev/null || true)"
      model="$(/usr/bin/plutil -extract model raw -o - "$profile" 2>/dev/null || true)"
      allow_http="$(/usr/bin/plutil -extract allowLoopbackHTTP raw -o - "$profile" 2>/dev/null || true)"
      profile_id="$(printf '%s' "$profile_id" | /usr/bin/tr '[:upper:]' '[:lower:]')"
      if [[ "$client" == "$expected_client" \
            && "$profile_id" =~ ^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$ \
            && "$gateway" == "$EXPECTED_GATEWAY" \
            && "$model" =~ ^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$ \
            && "$allow_http" == "false" ]] \
         && /Applications/Hormuz.app/Contents/MacOS/Hormuz \
              pilot-evidence verify-session --profile "$profile_id" \
              --state-directory "$STATE_DIRECTORY" >/dev/null 2>&1; then
        ACTIVE_PROFILE_ID="$profile_id"
        ACTIVE_GATEWAY="$gateway"
        ACTIVE_MODEL="$model"
        return
      fi
    fi
    /bin/sleep 2
  done
  fail browser_sign_in_timed_out
}

verify_session() {
  /Applications/Hormuz.app/Contents/MacOS/Hormuz \
    pilot-evidence verify-session --profile "$ACTIVE_PROFILE_ID" \
    --state-directory "$STATE_DIRECTORY" >/dev/null \
    || fail active_session_verification_failed
}

credential_files_absent() {
  if LC_ALL=C /usr/bin/grep -R -E -q 'hox_[ar]_[A-Za-z0-9_-]{43}' \
      "$STATE_DIRECTORY" "$WORK_ROOT" 2>/dev/null; then
    fail credential_file_detected
  fi
}

require_empty_session_store() {
  /Applications/Hormuz.app/Contents/MacOS/Hormuz \
    pilot-evidence session-store-empty --state-directory "$STATE_DIRECTORY" >/dev/null \
    || fail preexisting_session_detected
}

console_locked() {
  local lock_state
  if lock_state="$(/usr/sbin/ioreg -n Root -d1 -a \
      | /usr/bin/plutil -extract IOConsoleLocked raw -o - - 2>/dev/null)"; then
    :
  elif lock_state="$(/usr/sbin/ioreg -n Root -d1 -a \
      | /usr/bin/plutil -extract 0.IOConsoleLocked raw -o - - 2>/dev/null)"; then
    :
  else
    fail console_lock_state_unavailable
  fi
  [[ "$lock_state" == "true" || "$lock_state" == "false" ]] \
    || fail console_lock_state_invalid
  printf '%s\n' "$lock_state"
}

EXPECTED_INSTANCE_FINGERPRINT=""
reliability_snapshot() {
  local output="$1"
  /Applications/Hormuz.app/Contents/MacOS/Hormuz \
    pilot-evidence reliability --profile "$ACTIVE_PROFILE_ID" \
    --state-directory "$STATE_DIRECTORY" >"$output" \
    || fail reliability_snapshot_failed
  local instance_fingerprint
  instance_fingerprint="$(/usr/bin/plutil -extract deployment.instanceFingerprint raw -o - "$output" 2>/dev/null || true)"
  [[ "$(/usr/bin/plutil -extract schemaId raw -o - "$output")" == "hormuz.provider-reliability-summary" \
        && "$(/usr/bin/plutil -extract schemaVersion raw -o - "$output")" == "1" \
        && "$(/usr/bin/plutil -extract scope raw -o - "$output")" == "current_actor" \
        && "$(/usr/bin/plutil -extract deployment.platform raw -o - "$output")" == "render" \
        && "$(/usr/bin/plutil -extract deployment.sourceCommit raw -o - "$output")" == "$SOURCE_COMMIT" \
        && "$(/usr/bin/plutil -extract deployment.sourceBranch raw -o - "$output")" == "main" \
        && "$(/usr/bin/plutil -extract deployment.repository raw -o - "$output")" == "Xpounder-com/hormuz" \
        && "$(/usr/bin/plutil -extract deployment.cpuCount raw -o - "$output")" == "0.5" \
        && "$(/usr/bin/plutil -extract deployment.webConcurrency raw -o - "$output")" == "1" \
        && "$(/usr/bin/plutil -extract deployment.externalOrigin raw -o - "$output")" == "$EXPECTED_GATEWAY" \
        && "$(/usr/bin/plutil -extract deployment.serviceId raw -o - "$output")" == "$EXPECTED_SERVICE_ID" \
        && "$instance_fingerprint" =~ ^[0-9a-f]{16}$ ]] \
    || fail reliability_snapshot_invalid
  if [[ -z "$EXPECTED_INSTANCE_FINGERPRINT" ]]; then
    EXPECTED_INSTANCE_FINGERPRINT="$instance_fingerprint"
  else
    [[ "$instance_fingerprint" == "$EXPECTED_INSTANCE_FINGERPRINT" ]] \
      || fail reliability_instance_changed
  fi
}

live_count() {
  /usr/bin/plutil -extract liveProviderRequestCount raw -o - "$1" 2>/dev/null \
    || fail reliability_snapshot_invalid
}

attempt_count() {
  /usr/bin/plutil -extract providerAttemptRecordCount raw -o - "$1" 2>/dev/null \
    || fail reliability_snapshot_invalid
}

write_auth_wrapper() {
  local wrapper="$1"
  [[ "$wrapper" =~ ^/[A-Za-z0-9._/\ -]+$ ]] || fail helper_path_unsafe
  cat >"$wrapper" <<'SH'
#!/bin/bash
set -euo pipefail
umask 077
: "${HORMUZ_PILOT_BINARY:?}"
: "${HORMUZ_PILOT_PROFILE_ID:?}"
: "${HORMUZ_PILOT_STATE_DIRECTORY:?}"
: "${HORMUZ_PILOT_HELPER_COUNT:?}"
lock="${HORMUZ_PILOT_HELPER_COUNT}.lock"
for _attempt in {1..200}; do
  if /bin/mkdir "$lock" 2>/dev/null; then break; fi
  /bin/sleep 0.05
done
[[ -d "$lock" ]] || exit 1
trap '/bin/rmdir "$lock" >/dev/null 2>&1 || true' EXIT
count=0
if [[ -f "$HORMUZ_PILOT_HELPER_COUNT" && ! -L "$HORMUZ_PILOT_HELPER_COUNT" ]]; then
  IFS= read -r count <"$HORMUZ_PILOT_HELPER_COUNT"
fi
[[ "$count" =~ ^[0-9]{1,2}$ ]] || exit 1
count=$((count + 1))
temporary="${HORMUZ_PILOT_HELPER_COUNT}.tmp.$$"
printf '%s\n' "$count" >"$temporary"
/bin/chmod 600 "$temporary"
/bin/mv "$temporary" "$HORMUZ_PILOT_HELPER_COUNT"
if [[ "$count" -eq 1 ]]; then
  stale="$("$HORMUZ_PILOT_BINARY" credential --profile "$HORMUZ_PILOT_PROFILE_ID" --state-directory "$HORMUZ_PILOT_STATE_DIRECTORY")"
  "$HORMUZ_PILOT_BINARY" credential --profile "$HORMUZ_PILOT_PROFILE_ID" \
    --state-directory "$HORMUZ_PILOT_STATE_DIRECTORY" --force-refresh >/dev/null
  printf '%s\n' "$stale"
else
  exec "$HORMUZ_PILOT_BINARY" credential --profile "$HORMUZ_PILOT_PROFILE_ID" \
    --state-directory "$HORMUZ_PILOT_STATE_DIRECTORY"
fi
SH
  /bin/chmod 700 "$wrapper"
}

run_bounded() {
  local stdout="$1"
  local stderr="$2"
  shift 2
  "$@" >"$stdout" 2>"$stderr" &
  local pid=$!
  for _attempt in {1..120}; do
    if ! /bin/kill -0 "$pid" 2>/dev/null; then
      wait "$pid"
      return $?
    fi
    /bin/sleep 1
  done
  /usr/bin/pkill -TERM -P "$pid" >/dev/null 2>&1 || true
  /bin/kill -TERM "$pid" >/dev/null 2>&1 || true
  wait "$pid" >/dev/null 2>&1 || true
  return 124
}

write_client_record() {
  local output="$1"
  local client="$2"
  local version="$3"
  local automatic_replay="$4"
  local explicit_retry="$5"
  local temporary="$output.tmp"
  /usr/bin/plutil -create json "$temporary"
  /usr/bin/plutil -insert client -string "$client" "$temporary"
  /usr/bin/plutil -insert client_version -string "$version" "$temporary"
  /usr/bin/plutil -insert artifact_sha256 -string "$CANDIDATE_SHA256" "$temporary"
  /usr/bin/plutil -insert first_status -integer 401 "$temporary"
  /usr/bin/plutil -insert refresh_count -integer 1 "$temporary"
  /usr/bin/plutil -insert automatic_replay_count -integer "$automatic_replay" "$temporary"
  /usr/bin/plutil -insert explicit_retry_count -integer "$explicit_retry" "$temporary"
  /usr/bin/plutil -insert provider_egress_on_rejected_turn -integer 0 "$temporary"
  /usr/bin/plutil -insert provider_egress_after_success -integer 1 "$temporary"
  /usr/bin/plutil -insert completed -bool true "$temporary"
  /usr/bin/plutil -insert native_keychain_helper -bool true "$temporary"
  /bin/chmod 600 "$temporary"
  /bin/mv "$temporary" "$output"
}

run_codex_recovery() {
  authenticate_codex_client
  local command_path="$CODEX_COMMAND_PATH"
  local version_output
  version_output="$("$command_path" --version 2>/dev/null || true)"
  [[ "$version_output" =~ (^|[^0-9])0\.147\.0([^0-9]|$) ]] || fail codex_version_invalid
  local client_root="$WORK_ROOT/codex"
  /bin/mkdir -m 700 "$client_root" "$client_root/home"
  local wrapper="$client_root/auth-helper.sh"
  local counter="$client_root/helper-count"
  write_auth_wrapper "$wrapper"
  export HORMUZ_PILOT_BINARY=/Applications/Hormuz.app/Contents/MacOS/Hormuz
  export HORMUZ_PILOT_PROFILE_ID="$ACTIVE_PROFILE_ID"
  export HORMUZ_PILOT_STATE_DIRECTORY="$STATE_DIRECTORY"
  export HORMUZ_PILOT_HELPER_COUNT="$counter"
  export CODEX_HOME="$client_root/home"
  unset OPENAI_API_KEY OPENAI_BASE_URL OPENAI_ORGANIZATION OPENAI_PROJECT CODEX_API_KEY
  reliability_snapshot "$client_root/before.json"
  local provider
  provider="{name=\"Hormuz\",base_url=\"${ACTIVE_GATEWAY}/v1\",wire_api=\"responses\",requires_openai_auth=false,auth={command=\"${wrapper}\",args=[],refresh_interval_ms=0}}"
  set +e
  run_bounded "$client_root/stdout" "$client_root/stderr" \
    "$command_path" exec --ignore-user-config --skip-git-repo-check --ephemeral \
    --sandbox read-only -C "$client_root" -m "$ACTIVE_MODEL" \
    -c 'model_provider="hormuz_connector"' \
    -c "model_providers.hormuz_connector=$provider" \
    'Reply with exactly GATEWAY_OK. Do not call tools.'
  local result=$?
  set -e
  [[ "$result" -eq 0 ]] || fail codex_recovery_failed
  [[ -f "$counter" && "$(cat "$counter")" == "2" ]] || fail codex_refresh_count_invalid
  /usr/bin/grep -q 'GATEWAY_OK' "$client_root/stdout" "$client_root/stderr" \
    || fail codex_completion_marker_missing
  reliability_snapshot "$client_root/after.json"
  local before_live after_live before_attempt after_attempt
  before_live="$(live_count "$client_root/before.json")"
  after_live="$(live_count "$client_root/after.json")"
  before_attempt="$(attempt_count "$client_root/before.json")"
  after_attempt="$(attempt_count "$client_root/after.json")"
  [[ "$after_live" -eq $((before_live + 1)) \
        && "$after_attempt" -eq $((before_attempt + 1)) ]] \
    || fail codex_provider_egress_invalid
  write_client_record "$OUTPUT_DIRECTORY/codex-recovery.json" codex 0.147.0 1 0
}

run_claude_recovery() {
  authenticate_claude_client
  local command_path="$CLAUDE_COMMAND_PATH"
  local version_output
  version_output="$("$command_path" --version 2>/dev/null || true)"
  [[ "$version_output" =~ (^|[^0-9])2\.1\.233([^0-9]|$) ]] || fail claude_version_invalid
  local client_root="$WORK_ROOT/claude"
  /bin/mkdir -m 700 "$client_root" "$client_root/home"
  local wrapper="$client_root/auth-helper.sh"
  local counter="$client_root/helper-count"
  local settings="$client_root/settings.json"
  write_auth_wrapper "$wrapper"
  /usr/bin/plutil -create json "$settings"
  /usr/bin/plutil -insert apiKeyHelper -string "'$wrapper'" "$settings"
  /usr/bin/plutil -insert env -json '{}' "$settings"
  /usr/bin/plutil -insert env.ANTHROPIC_BASE_URL -string "$ACTIVE_GATEWAY" "$settings"
  /usr/bin/plutil -insert env.CLAUDE_CODE_API_KEY_HELPER_TTL_MS -string 60000 "$settings"
  /usr/bin/plutil -insert env.ANTHROPIC_API_KEY -string '' "$settings"
  /usr/bin/plutil -insert env.ANTHROPIC_AUTH_TOKEN -string '' "$settings"
  /usr/bin/plutil -insert env.CLAUDE_CODE_OAUTH_TOKEN -string '' "$settings"
  /usr/bin/plutil -insert env.CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY -string 0 "$settings"
  /usr/bin/plutil -insert env.CLAUDE_CODE_DISABLE_UNKNOWN_MODEL_WINDOW_ENFORCEMENT -string 1 "$settings"
  /usr/bin/plutil -insert env.DISABLE_AUTOUPDATER -string 1 "$settings"
  /usr/bin/plutil -insert env.DISABLE_TELEMETRY -string 1 "$settings"
  /usr/bin/plutil -insert env.DISABLE_ERROR_REPORTING -string 1 "$settings"
  export HORMUZ_PILOT_BINARY=/Applications/Hormuz.app/Contents/MacOS/Hormuz
  export HORMUZ_PILOT_PROFILE_ID="$ACTIVE_PROFILE_ID"
  export HORMUZ_PILOT_STATE_DIRECTORY="$STATE_DIRECTORY"
  export HORMUZ_PILOT_HELPER_COUNT="$counter"
  export CLAUDE_CONFIG_DIR="$client_root/home"
  unset ANTHROPIC_API_KEY ANTHROPIC_AUTH_TOKEN CLAUDE_CODE_OAUTH_TOKEN ANTHROPIC_CUSTOM_HEADERS
  reliability_snapshot "$client_root/before.json"
  local command=(
    "$command_path" -p --bare --no-session-persistence --tools ''
    --settings "$settings" --model "$ACTIVE_MODEL"
    'Reply with exactly ok. Do not call tools.'
  )
  set +e
  run_bounded "$client_root/rejected-stdout" "$client_root/rejected-stderr" "${command[@]}"
  local rejected_result=$?
  set -e
  [[ "$rejected_result" -ne 0 && "$rejected_result" -ne 124 ]] \
    || fail claude_rejected_turn_invalid
  [[ -f "$counter" && "$(cat "$counter")" == "2" ]] || fail claude_refresh_count_invalid
  reliability_snapshot "$client_root/rejected.json"
  [[ "$(live_count "$client_root/rejected.json")" == "$(live_count "$client_root/before.json")" \
        && "$(attempt_count "$client_root/rejected.json")" == "$(attempt_count "$client_root/before.json")" ]] \
    || fail claude_rejected_turn_provider_egress_invalid
  set +e
  run_bounded "$client_root/retry-stdout" "$client_root/retry-stderr" "${command[@]}"
  local retry_result=$?
  set -e
  [[ "$retry_result" -eq 0 ]] || fail claude_explicit_retry_failed
  [[ "$(cat "$counter")" == "3" ]] || fail claude_explicit_retry_count_invalid
  /usr/bin/grep -E -q '(^|[^[:alnum:]_])ok([^[:alnum:]_]|$)' \
    "$client_root/retry-stdout" "$client_root/retry-stderr" \
    || fail claude_completion_marker_missing
  reliability_snapshot "$client_root/after.json"
  [[ "$(live_count "$client_root/after.json")" -eq $(($(live_count "$client_root/before.json") + 1)) \
        && "$(attempt_count "$client_root/after.json")" -eq $(($(attempt_count "$client_root/before.json") + 1)) ]] \
    || fail claude_provider_egress_invalid
  write_client_record "$OUTPUT_DIRECTORY/claude-recovery.json" claude-code 2.1.233 0 1
}

verify_bundle /Applications/Hormuz.app "$CANDIDATE_VERSION" "$CANDIDATE_BUILD"
restart_app
require_empty_session_store
wait_for_active_profile codex
verify_session
credential_files_absent

restart_app
verify_session

printf 'macos_pilot_action=lock_then_unlock_the_mac_once\n'
[[ "$(console_locked)" == "false" ]] || fail console_initially_locked
LOCK_SEEN=false
UNLOCK_SEEN=false
for _attempt in {1..600}; do
  lock_state="$(console_locked)"
  if [[ "$lock_state" == "true" ]]; then
    LOCK_SEEN=true
  elif [[ "$lock_state" == "false" && "$LOCK_SEEN" == true ]]; then
    UNLOCK_SEEN=true
    break
  elif [[ "$lock_state" != "false" ]]; then
    fail console_lock_state_invalid
  fi
  /bin/sleep 1
done
[[ "$LOCK_SEEN" == true && "$UNLOCK_SEEN" == true ]] || fail lock_unlock_not_observed
verify_session

/Applications/Hormuz.app/Contents/MacOS/Hormuz \
  pilot-evidence refresh --profile "$ACTIVE_PROFILE_ID" \
  --state-directory "$STATE_DIRECTORY" >/dev/null \
  || fail session_refresh_failed
verify_session

install_bundle "$CANDIDATE_APP" "$CANDIDATE_VERSION" "$CANDIDATE_BUILD"
verify_session
install_bundle "$PREVIOUS_APP" "$PREVIOUS_VERSION" "$PREVIOUS_BUILD"
verify_session
install_bundle "$CANDIDATE_APP" "$CANDIDATE_VERSION" "$CANDIDATE_BUILD"
verify_session
install_bundle "$PREVIOUS_APP" "$PREVIOUS_VERSION" "$PREVIOUS_BUILD"
verify_session
install_bundle "$CANDIDATE_APP" "$CANDIDATE_VERSION" "$CANDIDATE_BUILD"
verify_session

run_codex_recovery
/Applications/Hormuz.app/Contents/MacOS/Hormuz \
  pilot-evidence server-revoke --profile "$ACTIVE_PROFILE_ID" \
  --state-directory "$STATE_DIRECTORY" >/dev/null \
  || fail server_revocation_failed
/Applications/Hormuz.app/Contents/MacOS/Hormuz \
  pilot-evidence verify-denied --profile "$ACTIVE_PROFILE_ID" \
  --state-directory "$STATE_DIRECTORY" >/dev/null \
  || fail server_revocation_denial_failed
/Applications/Hormuz.app/Contents/MacOS/Hormuz \
  pilot-evidence sign-out --profile "$ACTIVE_PROFILE_ID" \
  --state-directory "$STATE_DIRECTORY" >/dev/null \
  || fail sign_out_failed
/Applications/Hormuz.app/Contents/MacOS/Hormuz \
  pilot-evidence session-absent --profile "$ACTIVE_PROFILE_ID" \
  --state-directory "$STATE_DIRECTORY" >/dev/null \
  || fail session_removal_failed
credential_files_absent

restart_app
require_empty_session_store
wait_for_active_profile claude-code
verify_session
run_claude_recovery
/Applications/Hormuz.app/Contents/MacOS/Hormuz \
  pilot-evidence sign-out --profile "$ACTIVE_PROFILE_ID" \
  --state-directory "$STATE_DIRECTORY" >/dev/null \
  || fail claude_sign_out_failed
/Applications/Hormuz.app/Contents/MacOS/Hormuz \
  pilot-evidence session-absent --profile "$ACTIVE_PROFILE_ID" \
  --state-directory "$STATE_DIRECTORY" >/dev/null \
  || fail claude_session_removal_failed
credential_files_absent

LIFECYCLE_TMP="$OUTPUT_DIRECTORY/lifecycle.json.tmp"
/usr/bin/plutil -create json "$LIFECYCLE_TMP"
/usr/bin/plutil -insert update_from_build -string "$PREVIOUS_BUILD" "$LIFECYCLE_TMP"
/usr/bin/plutil -insert update_to_build -string "$CANDIDATE_BUILD" "$LIFECYCLE_TMP"
/usr/bin/plutil -insert rollback_to_build -string "$PREVIOUS_BUILD" "$LIFECYCLE_TMP"
for field in real_oidc_login keychain_session_created restart_preserved_session \
  lock_unlock_preserved_session refresh_rotated_session sign_out_removed_session \
  server_revocation_denied_session same_build_reinstall_verified \
  newer_build_update_verified previous_build_rollback_verified \
  credential_file_absent native_helper_used previous_notarized_archive_retained; do
  /usr/bin/plutil -insert "$field" -bool true "$LIFECYCLE_TMP"
done
/bin/chmod 600 "$LIFECYCLE_TMP"
/bin/mv "$LIFECYCLE_TMP" "$OUTPUT_DIRECTORY/lifecycle.json"
printf 'macos_session_client_status=passed\n'
