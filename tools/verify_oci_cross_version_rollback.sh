#!/usr/bin/env bash
# Recursively retain two signed releases, verify their original identities,
# then select the prior immutable digest and prove its provider-free runtime.

set -euo pipefail

readonly REPOSITORY_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "${REPOSITORY_ROOT}/tools/_verification_runtime.sh"

readonly SOURCE_IMAGE="ghcr.io/xpounder-com/hormuz"
readonly CURRENT_TAG="v1.0.0"
readonly CURRENT_DIGEST="sha256:e74fd7c527d257ff337510436f25a0eaf1e2fb799e1258566c9f393025e6b5a3"
readonly CURRENT_COMMIT="2fc0605252e41f731c85cc9146fbff6eb3b34669"
readonly ROLLBACK_TAG="v0.1.1"
readonly ROLLBACK_DIGEST="sha256:1bbcca3490a7a5b004a880f42e8250acb91ce566a9c59f3263d7b279568efb5a"
readonly ROLLBACK_COMMIT="b9388cba8945dbdc86a55d79dd92283841aeecc4"
readonly REGISTRY_IMAGE="docker.io/library/registry:3.0.0@sha256:6c5666b861f3505b116bb9aa9b25175e71210414bd010d92035ff64018f9457e"
readonly ORAS_VERSION="1.3.4"
readonly COSIGN_VERSION="v3.1.3"
readonly REGISTRY_PORT="${HORMUZ_OCI_ROLLBACK_REGISTRY_PORT:-5500}"
readonly MIRROR_IMAGE="localhost:${REGISTRY_PORT}/hormuz-retained"
readonly CONTAINER_NAME="hormuz-oci-rollback-${RANDOM}-${RANDOM}"
readonly DISPOSABLE_LABEL="io.hormuz.disposable-oci-rollback"
readonly EVIDENCE_DIR="${HORMUZ_OCI_ROLLBACK_EVIDENCE_DIR:-$(mktemp -d "${TMPDIR:-/tmp}/hormuz-oci-rollback-evidence.XXXXXX")}"

temporary_root=""
registry_started=0
completed=0

cleanup() {
  local exit_status=$?
  trap - EXIT
  if [[ "${completed}" -ne 1 && "${exit_status}" -eq 0 ]]; then
    exit_status=1
  fi
  if [[ "${registry_started}" -eq 1 ]]; then
    hormuz_remove_disposable_container "${CONTAINER_NAME}" "${DISPOSABLE_LABEL}"
  fi
  if [[ -n "${temporary_root}" && -d "${temporary_root}" ]]; then
    rm -rf "${temporary_root}"
  fi
  exit "${exit_status}"
}

fail() {
  local message=$1
  if [[ "${registry_started}" -eq 1 ]]; then
    docker logs "${CONTAINER_NAME}" >&2 || true
  fi
  printf 'OCI cross-version rollback verification failed: %s\n' "${message}" >&2
  exit 1
}

descriptor_digest() {
  local artifact_ref=$1
  shift
  oras manifest fetch "$@" --descriptor "${artifact_ref}" | python3 -c '
import json
import re
import sys

value = json.load(sys.stdin)
digest = value.get("digest")
assert isinstance(digest, str) and re.fullmatch(r"sha256:[0-9a-f]{64}", digest)
print(digest)
'
}

referrer_digests() {
  local artifact_ref=$1
  shift
  oras manifest fetch "$@" "${artifact_ref}" | python3 -c '
import json
import re
import sys

value = json.load(sys.stdin)
manifests = value.get("manifests")
assert isinstance(manifests, list) and len(manifests) == 3
digests = []
for manifest in manifests:
    assert manifest.get("mediaType") == "application/vnd.oci.image.manifest.v1+json"
    assert manifest.get("artifactType") == "application/vnd.dev.sigstore.bundle.v0.3+json"
    digest = manifest.get("digest")
    assert isinstance(digest, str) and re.fullmatch(r"sha256:[0-9a-f]{64}", digest)
    digests.append(digest)
assert len(set(digests)) == 3
print(",".join(sorted(digests)))
'
}

signature_tag() {
  local digest=$1
  printf 'sha256-%s\n' "${digest#sha256:}"
}

cosign_for_registry() {
  local allow_http=$1
  local subcommand=$2
  shift 2
  if [[ "${allow_http}" == "1" ]]; then
    cosign "${subcommand}" --allow-http-registry "$@"
  else
    cosign "${subcommand}" "$@"
  fi
}

verify_crypto() {
  local image_ref=$1
  local tag=$2
  local commit=$3
  local label=$4
  local allow_http=$5
  local identity="https://github.com/Xpounder-com/hormuz/.github/workflows/release-oci.yml@refs/tags/${tag}"
  local -a identity_flags=(
    --certificate-identity "${identity}"
    --certificate-oidc-issuer "https://token.actions.githubusercontent.com"
    --certificate-github-workflow-name "Release signed OCI digest"
    --certificate-github-workflow-ref "refs/tags/${tag}"
    --certificate-github-workflow-repository "Xpounder-com/hormuz"
    --certificate-github-workflow-sha "${commit}"
    --certificate-github-workflow-trigger "push"
  )
  cosign_for_registry "${allow_http}" verify \
    "${identity_flags[@]}" \
    "${image_ref}" \
    >"${temporary_root}/${label}-signature.json" 2>"${temporary_root}/${label}-signature.stderr" \
    || fail "${label}_signature_verification_failed"
  cosign_for_registry "${allow_http}" verify-attestation \
    --type cyclonedx \
    --insecure-ignore-tlog \
    --use-signed-timestamps \
    "${identity_flags[@]}" \
    "${image_ref}" \
    >"${temporary_root}/${label}-cyclonedx.json" 2>"${temporary_root}/${label}-cyclonedx.stderr" \
    || fail "${label}_cyclonedx_verification_failed"
  cosign_for_registry "${allow_http}" verify-attestation \
    --type slsaprovenance1 \
    --insecure-ignore-tlog \
    --use-signed-timestamps \
    "${identity_flags[@]}" \
    "${image_ref}" \
    >"${temporary_root}/${label}-provenance.json" 2>"${temporary_root}/${label}-provenance.stderr" \
    || fail "${label}_provenance_verification_failed"
}

runtime_milliseconds() {
  local image_ref=$1
  local started_ns
  local finished_ns
  started_ns="$(python3 -c 'import time; print(time.monotonic_ns())')"
  HORMUZ_OCI_SKIP_BUILD=1 HORMUZ_OCI_TEST_IMAGE="${image_ref}" \
    "${REPOSITORY_ROOT}/tools/verify_oci_reference.sh" \
    >"${temporary_root}/runtime.stdout" 2>"${temporary_root}/runtime.stderr" \
    || fail "runtime_verification_failed"
  finished_ns="$(python3 -c 'import time; print(time.monotonic_ns())')"
  python3 -c 'import sys; print(max(1, (int(sys.argv[2]) - int(sys.argv[1])) // 1_000_000))' \
    "${started_ns}" "${finished_ns}"
}

trap cleanup EXIT

for command_name in cosign curl docker oras python3; do
  command -v "${command_name}" >/dev/null || fail "required_tool_unavailable:${command_name}"
done
[[ "${REGISTRY_PORT}" =~ ^[0-9]+$ ]] \
  && (( REGISTRY_PORT >= 1024 && REGISTRY_PORT <= 65535 )) \
  || fail "registry_port_invalid"

oras_version="$(oras version | awk '/^Version:/ { print $2; exit }')"
cosign_version="$(cosign version 2>/dev/null | awk '/^GitVersion:/ { print $2; exit }')"
docker_version="$(docker info --format '{{.ServerVersion}}')"
[[ "${oras_version}" == "${ORAS_VERSION}" ]] || fail "oras_version_mismatch"
[[ "${cosign_version}" == "${COSIGN_VERSION}" ]] || fail "cosign_version_mismatch"

mkdir -p "${EVIDENCE_DIR}"
chmod 0700 "${EVIDENCE_DIR}"
[[ ! -e "${EVIDENCE_DIR}/summary.json" ]] || fail "evidence_output_already_exists"
temporary_root="$(mktemp -d "${TMPDIR:-/tmp}/hormuz-oci-rollback.XXXXXX")"

current_source_digest="$(descriptor_digest "${SOURCE_IMAGE}:${CURRENT_TAG}")"
rollback_source_digest="$(descriptor_digest "${SOURCE_IMAGE}:${ROLLBACK_TAG}")"
[[ "${current_source_digest}" == "${CURRENT_DIGEST}" ]] || fail "current_tag_digest_mismatch"
[[ "${rollback_source_digest}" == "${ROLLBACK_DIGEST}" ]] || fail "rollback_tag_digest_mismatch"

current_signature_tag="$(signature_tag "${CURRENT_DIGEST}")"
rollback_signature_tag="$(signature_tag "${ROLLBACK_DIGEST}")"
current_source_referrers="$(referrer_digests "${SOURCE_IMAGE}:${current_signature_tag}")"
rollback_source_referrers="$(referrer_digests "${SOURCE_IMAGE}:${rollback_signature_tag}")"

verify_crypto "${SOURCE_IMAGE}@${CURRENT_DIGEST}" "${CURRENT_TAG}" "${CURRENT_COMMIT}" "source-current" 0
verify_crypto "${SOURCE_IMAGE}@${ROLLBACK_DIGEST}" "${ROLLBACK_TAG}" "${ROLLBACK_COMMIT}" "source-rollback" 0

docker run --detach --rm \
  --name "${CONTAINER_NAME}" \
  --label "${DISPOSABLE_LABEL}=true" \
  --publish "127.0.0.1:${REGISTRY_PORT}:5000" \
  "${REGISTRY_IMAGE}" \
  >"${temporary_root}/registry-container-id"
registry_started=1

registry_ready=0
for _attempt in $(seq 1 30); do
  if curl --silent --fail --max-time 2 "http://127.0.0.1:${REGISTRY_PORT}/v2/" >/dev/null; then
    registry_ready=1
    break
  fi
  sleep 1
done
[[ "${registry_ready}" -eq 1 ]] || fail "loopback_registry_unavailable"

oras cp --no-tty --recursive \
  --from-distribution-spec v1.1-referrers-tag \
  --to-distribution-spec v1.1-referrers-tag \
  --to-plain-http \
  "${SOURCE_IMAGE}@${CURRENT_DIGEST}" \
  "${MIRROR_IMAGE}:${CURRENT_TAG}" \
  >"${temporary_root}/copy-current.log"
oras cp --no-tty --recursive \
  --from-distribution-spec v1.1-referrers-tag \
  --to-distribution-spec v1.1-referrers-tag \
  --to-plain-http \
  "${SOURCE_IMAGE}@${ROLLBACK_DIGEST}" \
  "${MIRROR_IMAGE}:${ROLLBACK_TAG}" \
  >"${temporary_root}/copy-rollback.log"

current_mirror_digest="$(descriptor_digest "${MIRROR_IMAGE}:${CURRENT_TAG}" --plain-http)"
rollback_mirror_digest="$(descriptor_digest "${MIRROR_IMAGE}:${ROLLBACK_TAG}" --plain-http)"
[[ "${current_mirror_digest}" == "${CURRENT_DIGEST}" ]] || fail "current_mirror_digest_mismatch"
[[ "${rollback_mirror_digest}" == "${ROLLBACK_DIGEST}" ]] || fail "rollback_mirror_digest_mismatch"

current_mirror_referrers="$(referrer_digests "${MIRROR_IMAGE}:${current_signature_tag}" --plain-http)"
rollback_mirror_referrers="$(referrer_digests "${MIRROR_IMAGE}:${rollback_signature_tag}" --plain-http)"
[[ "${current_mirror_referrers}" == "${current_source_referrers}" ]] \
  || fail "current_referrer_set_mismatch"
[[ "${rollback_mirror_referrers}" == "${rollback_source_referrers}" ]] \
  || fail "rollback_referrer_set_mismatch"

verify_crypto "${MIRROR_IMAGE}@${CURRENT_DIGEST}" "${CURRENT_TAG}" "${CURRENT_COMMIT}" "mirror-current" 1
verify_crypto "${MIRROR_IMAGE}@${ROLLBACK_DIGEST}" "${ROLLBACK_TAG}" "${ROLLBACK_COMMIT}" "mirror-rollback" 1

docker pull --platform linux/amd64 "${MIRROR_IMAGE}@${CURRENT_DIGEST}" \
  >"${temporary_root}/pull-current.log"
current_runtime_ms="$(runtime_milliseconds "${MIRROR_IMAGE}@${CURRENT_DIGEST}")"

docker pull --platform linux/amd64 "${MIRROR_IMAGE}@${ROLLBACK_DIGEST}" \
  >"${temporary_root}/pull-rollback.log"
rollback_runtime_ms="$(runtime_milliseconds "${MIRROR_IMAGE}@${ROLLBACK_DIGEST}")"

[[ "$(descriptor_digest "${SOURCE_IMAGE}:${CURRENT_TAG}")" == "${CURRENT_DIGEST}" ]] \
  || fail "current_tag_changed_during_drill"
[[ "$(descriptor_digest "${SOURCE_IMAGE}:${ROLLBACK_TAG}")" == "${ROLLBACK_DIGEST}" ]] \
  || fail "rollback_tag_changed_during_drill"

python3 "${REPOSITORY_ROOT}/tools/write_oci_cross_version_rollback_evidence.py" \
  --current-digest "${CURRENT_DIGEST}" \
  --current-tag "${CURRENT_TAG}" \
  --current-commit "${CURRENT_COMMIT}" \
  --current-referrers "${current_source_referrers}" \
  --current-runtime-ms "${current_runtime_ms}" \
  --rollback-digest "${ROLLBACK_DIGEST}" \
  --rollback-tag "${ROLLBACK_TAG}" \
  --rollback-commit "${ROLLBACK_COMMIT}" \
  --rollback-referrers "${rollback_source_referrers}" \
  --rollback-runtime-ms "${rollback_runtime_ms}" \
  --registry-image "${REGISTRY_IMAGE}" \
  --oras-version "${oras_version}" \
  --cosign-version "${cosign_version}" \
  --docker-version "${docker_version}" \
  --output "${EVIDENCE_DIR}/summary.json"

printf 'verified signed OCI cross-version rollback in %s\n' "${EVIDENCE_DIR}"
completed=1
