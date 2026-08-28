#!/usr/bin/env bash
# Promote the exact gate-approved v1 source archive. This script never builds.

set -euo pipefail
umask 077

readonly REPOSITORY="Xpounder-com/hormuz"
readonly FINAL_TAG="v1.0.0"
readonly ARCHIVE_NAME="hormuz-1.0.0.tar.gz"
readonly MANIFEST_NAME="hormuz-v1.0.0-candidate-manifest.json"

fail() {
  printf 'v1 promotion failed: %s\n' "$1" >&2
  exit 2
}

usage() {
  cat <<'EOF'
Usage:
  tools/promote_v1_candidate.sh \
    --candidate-tag CANDIDATE_TAG_FROM_MANIFEST \
    --evidence /private/path/policy-admin-usability-evidence.json \
    [--output /private/path/promotion-proof]

The command verifies the real #173 gate, creates the protected annotated
v1.0.0 tag, waits for the existing signed OCI release workflow, reverifies the
same immutable candidate assets, and publishes a metadata-only immutable final
release that links to those exact bytes. It never builds, copies, uploads,
replaces, or overwrites a release asset.
EOF
}

candidate_tag=""
evidence_path=""
output_path=""
while (($#)); do
  case "$1" in
    --candidate-tag)
      (($# >= 2)) || fail "candidate_tag_value_missing"
      candidate_tag="$2"
      shift 2
      ;;
    --evidence)
      (($# >= 2)) || fail "evidence_value_missing"
      evidence_path="$2"
      shift 2
      ;;
    --output)
      (($# >= 2)) || fail "output_value_missing"
      output_path="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      fail "unknown_argument"
      ;;
  esac
done

[[ "$candidate_tag" =~ ^candidate-v1\.0\.0-[0-9a-f]{64}$ ]] \
  || fail "candidate_tag_invalid"
[[ -n "$evidence_path" ]] || fail "evidence_required"

for command in gh git python3; do
  command -v "$command" >/dev/null 2>&1 || fail "required_command_missing:$command"
done

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
repository_root="$(cd "$script_dir/.." && pwd -P)"
tool="$script_dir/v1_candidate.py"
[[ -f "$tool" ]] || fail "candidate_tool_missing"
[[ "$(git -C "$repository_root" rev-parse --show-toplevel)" == "$repository_root" ]] \
  || fail "repository_checkout_required"
checkout_commit="$(git -C "$repository_root" rev-parse HEAD)"
[[ "$checkout_commit" =~ ^[0-9a-f]{40}$ ]] || fail "checkout_commit_invalid"
[[ -z "$(git -C "$repository_root" status --porcelain=v1 --untracked-files=all --ignore-submodules=none)" ]] \
  || fail "promotion_checkout_not_clean"

origin_url="$(git -C "$repository_root" remote get-url origin)"
case "$origin_url" in
  https://github.com/Xpounder-com/hormuz|https://github.com/Xpounder-com/hormuz.git|git@github.com:Xpounder-com/hormuz.git|ssh://git@github.com/Xpounder-com/hormuz.git)
    ;;
  *)
    fail "repository_origin_invalid"
    ;;
esac

cleanup="true"
if [[ -n "$output_path" ]]; then
  [[ ! -e "$output_path" && ! -L "$output_path" ]] || fail "output_exists"
  mkdir -m 700 "$output_path"
  work_dir="$(cd "$output_path" && pwd -P)"
  cleanup="false"
else
  work_dir="$(mktemp -d "${TMPDIR:-/tmp}/hormuz-v1-promotion.XXXXXX")"
fi
case "$work_dir/" in
  "$repository_root/"*) fail "promotion_output_inside_checkout" ;;
esac

cleanup_work_dir() {
  if [[ "$cleanup" == "true" && -n "${work_dir:-}" && -d "$work_dir" ]]; then
    rm -rf -- "$work_dir"
  fi
}
trap cleanup_work_dir EXIT

report_proof_location() {
  if [[ "$cleanup" == "true" ]]; then
    printf 'temporary promotion material will be removed\n'
  else
    printf 'proof directory: %s\n' "$work_dir"
  fi
}

gh auth status --hostname github.com >/dev/null 2>&1 || fail "github_authentication_required"
gh release verify-asset --help >/dev/null 2>&1 \
  || fail "github_cli_release_verification_unavailable"
git -C "$repository_root" fetch --no-tags origin \
  "refs/heads/main:refs/remotes/origin/main"
git -C "$repository_root" merge-base --is-ancestor "$checkout_commit" origin/main \
  || fail "promotion_checkout_not_on_main"

pinned_evidence_path="$work_dir/gate-evidence.json"
evidence_snapshot_report="$work_dir/gate-evidence-snapshot.json"
python3 "$tool" evidence-snapshot \
  --evidence "$evidence_path" \
  --output "$pinned_evidence_path" \
  >"$evidence_snapshot_report"
pinned_evidence_digest="$(python3 -c 'import json, pathlib, sys; print(json.loads(pathlib.Path(sys.argv[1]).read_text())["digest"])' "$evidence_snapshot_report")"
[[ "$pinned_evidence_digest" =~ ^sha256:[0-9a-f]{64}$ ]] \
  || fail "gate_evidence_snapshot_invalid"

release_exists() {
  local tag="$1"
  local response="$2"
  local error_file="$3"
  if gh api "/repos/$REPOSITORY/releases/tags/$tag" >"$response" 2>"$error_file"; then
    return 0
  fi
  if grep -Fq "HTTP 404" "$error_file"; then
    return 1
  fi
  cat "$error_file" >&2
  fail "release_lookup_failed"
}

snapshot_candidate() {
  local directory="$1"
  mkdir -m 700 "$directory"
  gh release download "$candidate_tag" \
    --repo "$REPOSITORY" \
    --pattern "$ARCHIVE_NAME" \
    --pattern "$MANIFEST_NAME" \
    --dir "$directory"
  gh api "/repos/$REPOSITORY/releases/tags/$candidate_tag" >"$directory/release-api.json"
  gh api "/repos/$REPOSITORY/immutable-releases" >"$directory/immutable-api.json"
}

verify_live_tag_immutability() {
  local ruleset_id
  local contract
  ruleset_id="$(gh api "/repos/$REPOSITORY/rulesets?includes_parents=true&per_page=100" --jq '[.[] | select(.name == "Immutable version tags" and .source_type == "Repository" and .target == "tag" and .enforcement == "active")] | if length == 1 then .[0].id else "" end')"
  [[ "$ruleset_id" =~ ^[1-9][0-9]*$ ]] || fail "live_tag_immutability_ruleset_missing"
  contract="$(gh api "/repos/$REPOSITORY/rulesets/$ruleset_id" --jq '(.bypass_actors == []) and (.conditions.ref_name.exclude == []) and ((.conditions.ref_name.include | sort) == (["refs/tags/candidate-v1.0.0-*", "refs/tags/v*"] | sort)) and (([.rules[].type] | sort) == (["deletion", "non_fast_forward", "update"] | sort))')"
  [[ "$contract" == "true" ]] || fail "live_tag_immutability_contract_invalid"
}

initial_dir="$work_dir/initial-verification"
verify_live_tag_immutability
release_exists \
  "$candidate_tag" \
  "$work_dir/initial-release-lookup.json" \
  "$work_dir/initial-release-lookup.err" \
  || fail "candidate_release_not_found"
snapshot_candidate "$initial_dir"
manifest_source_commit="$(python3 -c 'import json, pathlib, sys; print(json.loads(pathlib.Path(sys.argv[1]).read_text())["candidate"]["source_commit"])' "$initial_dir/$MANIFEST_NAME")"
[[ "$manifest_source_commit" =~ ^[0-9a-f]{40}$ ]] || fail "manifest_source_commit_invalid"
[[ "$manifest_source_commit" == "$checkout_commit" ]] \
  || fail "promotion_checkout_candidate_mismatch"
freeze_run_id="$(python3 -c 'import json, pathlib, sys; print(json.loads(pathlib.Path(sys.argv[1]).read_text())["build"]["run_id"])' "$initial_dir/$MANIFEST_NAME")"
[[ "$freeze_run_id" =~ ^[1-9][0-9]*$ ]] || fail "freeze_run_id_invalid"

snapshot_provenance() {
  local directory="$1"
  gh api "/repos/$REPOSITORY/actions/runs/$freeze_run_id" \
    >"$directory/freeze-run-api.json"
  gh api "/repos/$REPOSITORY/git/ref/tags/$candidate_tag" \
    >"$directory/custody-tag-api.json"
}

snapshot_provenance "$initial_dir"
python3 "$tool" promotion \
  --manifest "$initial_dir/$MANIFEST_NAME" \
  --archive "$initial_dir/$ARCHIVE_NAME" \
  --evidence "$pinned_evidence_path" \
  --release-api "$initial_dir/release-api.json" \
  --immutable-api "$initial_dir/immutable-api.json" \
  --freeze-run-api "$initial_dir/freeze-run-api.json" \
  --custody-tag-api "$initial_dir/custody-tag-api.json" \
  --output "$initial_dir/promotion-readiness.json" >/dev/null

source_commit="$(python3 -c 'import json, pathlib, sys; print(json.loads(pathlib.Path(sys.argv[1]).read_text())["source_commit"])' "$initial_dir/promotion-readiness.json")"
candidate_digest="$(python3 -c 'import json, pathlib, sys; print(json.loads(pathlib.Path(sys.argv[1]).read_text())["candidate_artifact_digest"])' "$initial_dir/promotion-readiness.json")"
manifest_custody_tag="$(python3 -c 'import json, pathlib, sys; print(json.loads(pathlib.Path(sys.argv[1]).read_text())["custody_release_tag"])' "$initial_dir/promotion-readiness.json")"
gate_generated_at="$(python3 -c 'import json, pathlib, sys; print(json.loads(pathlib.Path(sys.argv[1]).read_text())["gate_generated_at"])' "$initial_dir/promotion-readiness.json")"
gate_evidence_digest="$(python3 -c 'import json, pathlib, sys; print(json.loads(pathlib.Path(sys.argv[1]).read_text())["gate_evidence_digest"])' "$initial_dir/promotion-readiness.json")"
[[ "$manifest_custody_tag" == "$candidate_tag" ]] || fail "candidate_tag_manifest_mismatch"
[[ "$gate_evidence_digest" == "$pinned_evidence_digest" ]] \
  || fail "gate_evidence_snapshot_digest_mismatch"

assert_readiness_binding() {
  local readiness_path="$1"
  python3 -c '
import json, pathlib, sys
value = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
expected = {
    "source_commit": sys.argv[2],
    "candidate_artifact_digest": sys.argv[3],
    "custody_release_tag": sys.argv[4],
    "gate_evidence_digest": sys.argv[5],
    "gate_generated_at": sys.argv[6],
}
if any(value.get(key) != expected_value for key, expected_value in expected.items()):
    raise SystemExit(2)
' "$readiness_path" "$source_commit" "$candidate_digest" "$candidate_tag" "$gate_evidence_digest" "$gate_generated_at" \
    || fail "promotion_readiness_binding_changed"
}

assert_readiness_binding "$initial_dir/promotion-readiness.json"

verify_release_attestations() {
  local tag="$1"
  local directory="$2"
  local asset
  local attempt
  for asset in "$ARCHIVE_NAME" "$MANIFEST_NAME"; do
    for attempt in $(seq 1 12); do
      if gh release verify-asset "$tag" "$directory/$asset" \
        --repo "$REPOSITORY" \
        --format json >"$directory/${asset}.attestation-${attempt}.json" 2>"$directory/${asset}.attestation-${attempt}.err"; then
        break
      fi
      if [[ "$attempt" == "12" ]]; then
        cat "$directory/${asset}.attestation-${attempt}.err" >&2
        fail "release_attestation_verification_failed:$asset"
      fi
      sleep 5
    done
  done
}

verify_release_attestations "$candidate_tag" "$initial_dir"

validate_remote_final_tag() {
  local ref_path="$1"
  local object_path="$2"
  local tag_object_sha
  tag_object_sha="$(python3 -c '
import json, pathlib, sys
value = json.loads(pathlib.Path(sys.argv[1]).read_text())
obj = value.get("object", {})
if obj.get("type") != "tag" or not isinstance(obj.get("sha"), str):
    raise SystemExit(2)
print(obj["sha"])
' "$ref_path")" || fail "final_tag_not_annotated"
  gh api "/repos/$REPOSITORY/git/tags/$tag_object_sha" >"$object_path"
  python3 -c '
import json, pathlib, sys
value = json.loads(pathlib.Path(sys.argv[1]).read_text())
if value.get("tag") != sys.argv[2]:
    raise SystemExit(2)
obj = value.get("object", {})
if obj.get("type") != "commit" or obj.get("sha") != sys.argv[3]:
    raise SystemExit(2)
tagger = value.get("tagger", {})
tagged_at = tagger.get("date") if isinstance(tagger, dict) else None
if not isinstance(tagged_at, str):
    raise SystemExit(2)
message = value.get("message")
if not isinstance(message, str):
    raise SystemExit(2)
if f"Frozen source archive: {sys.argv[5]}" not in message or f"Gate evidence: {sys.argv[6]}" not in message:
    raise SystemExit(2)
from datetime import datetime
if datetime.fromisoformat(tagged_at.replace("Z", "+00:00")) < datetime.fromisoformat(sys.argv[4].replace("Z", "+00:00")):
    raise SystemExit(2)
' "$object_path" "$FINAL_TAG" "$source_commit" "$gate_generated_at" "$candidate_digest" "$gate_evidence_digest" \
    || fail "final_tag_target_or_chronology_invalid"
}

refresh_and_validate_final_tag() {
  local label="$1"
  local ref_path="$work_dir/final-tag-${label}-ref.json"
  local object_path="$work_dir/final-tag-${label}-object.json"
  local error_path="$work_dir/final-tag-${label}.err"
  if ! gh api "/repos/$REPOSITORY/git/ref/tags/$FINAL_TAG" >"$ref_path" 2>"$error_path"; then
    cat "$error_path" >&2
    fail "final_tag_lookup_failed"
  fi
  validate_remote_final_tag "$ref_path" "$object_path"
}

require_gate_time_current() {
  python3 -c '
from datetime import datetime, timezone
import sys
gate = datetime.fromisoformat(sys.argv[1].replace("Z", "+00:00"))
raise SystemExit(0 if datetime.now(timezone.utc) >= gate else 2)
' "$gate_generated_at" || fail "gate_evidence_not_yet_current"
}

final_ref="$work_dir/final-tag-ref.json"
final_ref_error="$work_dir/final-tag-ref.err"
verify_live_tag_immutability
if gh api "/repos/$REPOSITORY/git/ref/tags/$FINAL_TAG" >"$final_ref" 2>"$final_ref_error"; then
  validate_remote_final_tag "$final_ref" "$work_dir/final-tag-object.json"
else
  if ! grep -Fq "HTTP 404" "$final_ref_error"; then
    cat "$final_ref_error" >&2
    fail "final_tag_lookup_failed"
  fi
  if git -C "$repository_root" show-ref --verify --quiet "refs/tags/$FINAL_TAG"; then
    [[ "$(git -C "$repository_root" cat-file -t "refs/tags/$FINAL_TAG")" == "tag" ]] \
      || fail "local_final_tag_not_annotated"
    [[ "$(git -C "$repository_root" rev-list -n 1 "$FINAL_TAG")" == "$source_commit" ]] \
      || fail "local_final_tag_target_invalid"
    local_tagged_at="$(git -C "$repository_root" for-each-ref --format='%(taggerdate:iso-strict)' "refs/tags/$FINAL_TAG")"
    python3 -c '
from datetime import datetime
import sys
tagged = datetime.fromisoformat(sys.argv[1].replace("Z", "+00:00"))
gate = datetime.fromisoformat(sys.argv[2].replace("Z", "+00:00"))
raise SystemExit(0 if tagged >= gate else 2)
' "$local_tagged_at" "$gate_generated_at" || fail "local_final_tag_chronology_invalid"
    local_tag_message="$(git -C "$repository_root" for-each-ref --format='%(contents)' "refs/tags/$FINAL_TAG")"
    [[ "$local_tag_message" == *"Frozen source archive: $candidate_digest"* ]] \
      || fail "local_final_tag_candidate_digest_invalid"
    [[ "$local_tag_message" == *"Gate evidence: $gate_evidence_digest"* ]] \
      || fail "local_final_tag_gate_digest_invalid"
  else
    require_gate_time_current
    git -C "$repository_root" -c tag.gpgSign=false tag -a "$FINAL_TAG" "$source_commit" \
      -m "Hormuz v1.0.0" \
      -m "Frozen source archive: $candidate_digest" \
      -m "Gate evidence: $gate_evidence_digest"
  fi
  git -C "$repository_root" push origin "refs/tags/$FINAL_TAG"
  gh api "/repos/$REPOSITORY/git/ref/tags/$FINAL_TAG" >"$work_dir/final-tag-ref-after-push.json"
  validate_remote_final_tag \
    "$work_dir/final-tag-ref-after-push.json" \
    "$work_dir/final-tag-object-after-push.json"
fi

wait_for_signed_oci() {
  local deadline=$((SECONDS + 3000))
  local attempt=0
  local record=""
  local run_id=""
  local status=""
  local conclusion=""
  local url=""
  while ((SECONDS < deadline)); do
    attempt=$((attempt + 1))
    gh run list \
      --repo "$REPOSITORY" \
      --workflow release-oci.yml \
      --event push \
      --commit "$source_commit" \
      --limit 20 \
      --json databaseId,headBranch,headSha,status,conclusion,url \
      >"$work_dir/oci-runs-${attempt}.json"
    record="$(python3 -c '
import json, pathlib, sys
runs = json.loads(pathlib.Path(sys.argv[1]).read_text())
matches = [
    item for item in runs
    if item.get("headBranch") == sys.argv[2] and item.get("headSha") == sys.argv[3]
]
if matches:
    item = matches[0]
    print("|".join(str(item.get(key) or "") for key in ("databaseId", "status", "conclusion", "url")))
' "$work_dir/oci-runs-${attempt}.json" "$FINAL_TAG" "$source_commit")"
    if [[ -n "$record" ]]; then
      IFS='|' read -r run_id status conclusion url <<<"$record"
      if [[ "$status" == "completed" ]]; then
        [[ "$conclusion" == "success" ]] || fail "signed_oci_workflow_failed:$url"
        printf '%s\n' "$url" >"$work_dir/signed-oci-run-url.txt"
        return 0
      fi
    fi
    sleep 10
  done
  fail "signed_oci_workflow_timeout"
}

wait_for_signed_oci

# Re-download and revalidate the immutable candidate after signed OCI succeeds.
# The final release links to these bytes; it never stages or copies an asset.
prepublish_dir="$work_dir/prepublish-reverification"
verify_live_tag_immutability
snapshot_candidate "$prepublish_dir"
snapshot_provenance "$prepublish_dir"
python3 "$tool" promotion \
  --manifest "$prepublish_dir/$MANIFEST_NAME" \
  --archive "$prepublish_dir/$ARCHIVE_NAME" \
  --evidence "$pinned_evidence_path" \
  --release-api "$prepublish_dir/release-api.json" \
  --immutable-api "$prepublish_dir/immutable-api.json" \
  --freeze-run-api "$prepublish_dir/freeze-run-api.json" \
  --custody-tag-api "$prepublish_dir/custody-tag-api.json" \
  --output "$prepublish_dir/promotion-readiness.json" >/dev/null
assert_readiness_binding "$prepublish_dir/promotion-readiness.json"
refresh_and_validate_final_tag "prepublish"
verify_release_attestations "$candidate_tag" "$prepublish_dir"

final_notes_path="$work_dir/final-release-notes.md"
python3 "$tool" final-notes \
  --manifest "$prepublish_dir/$MANIFEST_NAME" \
  --evidence "$pinned_evidence_path" \
  --output "$final_notes_path" \
  >"$work_dir/final-release-notes-proof.json"

final_release_api="$work_dir/final-release-api.json"
final_release_error="$work_dir/final-release-api.err"
if ! release_exists "$FINAL_TAG" "$final_release_api" "$final_release_error"; then
  gh release create "$FINAL_TAG" \
    --repo "$REPOSITORY" \
    --verify-tag \
    --title "Hormuz v1.0.0" \
    --notes-file "$final_notes_path" \
    --latest \
    >"$work_dir/final-release-url.txt"
fi

publication_ready="false"
for attempt in $(seq 1 30); do
  gh api "/repos/$REPOSITORY/releases/tags/$FINAL_TAG" \
    >"$work_dir/published-release-${attempt}.json"
  if python3 -c '
import json, pathlib, sys
value = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
raise SystemExit(0 if value.get("draft") is False and value.get("immutable") is True else 2)
' "$work_dir/published-release-${attempt}.json"; then
    publication_ready="true"
    final_release_api="$work_dir/published-release-${attempt}.json"
    break
  fi
  sleep 2
done
[[ "$publication_ready" == "true" ]] || fail "immutable_publication_not_observed"

published_dir="$work_dir/published-reverification"
verify_live_tag_immutability
snapshot_candidate "$published_dir"
snapshot_provenance "$published_dir"
python3 "$tool" promotion \
  --manifest "$published_dir/$MANIFEST_NAME" \
  --archive "$published_dir/$ARCHIVE_NAME" \
  --evidence "$pinned_evidence_path" \
  --release-api "$published_dir/release-api.json" \
  --immutable-api "$published_dir/immutable-api.json" \
  --freeze-run-api "$published_dir/freeze-run-api.json" \
  --custody-tag-api "$published_dir/custody-tag-api.json" \
  --output "$published_dir/promotion-readiness.json" >/dev/null
assert_readiness_binding "$published_dir/promotion-readiness.json"
refresh_and_validate_final_tag "published"
python3 "$tool" final-release \
  --manifest "$published_dir/$MANIFEST_NAME" \
  --evidence "$pinned_evidence_path" \
  --release-api "$final_release_api" \
  --immutable-api "$published_dir/immutable-api.json" \
  --output "$published_dir/final-release-proof.json" >/dev/null
verify_release_attestations "$candidate_tag" "$published_dir"

printf 'published immutable v1.0.0 metadata for exact candidate %s\n' "$candidate_digest"
report_proof_location
