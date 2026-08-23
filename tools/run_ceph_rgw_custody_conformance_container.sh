#!/usr/bin/env bash
# Run the Ceph RGW custody gate from a disposable, pinned linux/amd64 image.
#
# This launcher is intentionally Linux-only: --network host keeps the harness
# on the lab host's loopback network, while the Python harness rejects all
# non-loopback RGW and OpenBao endpoints. It derives a local, content-addressed
# Docker image ID after the build and injects that provenance into the strictly
# validated v2 evidence record.

set -euo pipefail

readonly REPOSITORY_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
readonly RUNNER_DOCKERFILE="${REPOSITORY_ROOT}/Dockerfile.ceph-rgw-conformance"
readonly RUNNER_PLATFORM="linux/amd64"
readonly RUNNER_IMAGE_TAG="${HORMUZ_CEPH_RGW_RUNNER_IMAGE:-hormuz:ceph-rgw-conformance}"
readonly ENVIRONMENT_FILE="${HORMUZ_CEPH_RGW_CONFORMANCE_ENV_FILE:-}"
readonly TARGET_IMAGE_REFERENCE="quay.io/ceph/ceph@sha256:d195020de02512030118e772cef7859e92904e91eb4cb21acb503f8b94118137"
readonly TARGET_IMAGE_DIGEST="sha256:d195020de02512030118e772cef7859e92904e91eb4cb21acb503f8b94118137"
readonly TARGET_RELEASE="20.2.3"
readonly TARGET_VERSION_OUTPUT="ceph version 20.2.3 (06c2f9c35b67055a8a6fb99d1be236b3c4832ace) tentacle (stable)"

failure() {
  printf 'Ceph RGW container conformance failed: %s\n' "$1" >&2
  exit 1
}

if [[ "$(uname -s)" != "Linux" ]]; then
  failure 'linux_host_required'
fi
if [[ "$#" -ne 2 || "$1" != "--evidence-out" ]]; then
  failure 'usage: run_ceph_rgw_custody_conformance_container.sh --evidence-out /secure/path/evidence.json'
fi
if [[ -z "${ENVIRONMENT_FILE}" || ! -f "${ENVIRONMENT_FILE}" || ! -r "${ENVIRONMENT_FILE}" ]]; then
  failure 'conformance_environment_file_required'
fi

rgw_container="$(sed -n 's/^HORMUZ_CEPH_RGW_CONTAINER=//p' "${ENVIRONMENT_FILE}")"
if [[ ! "${rgw_container}" =~ ^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$ ]]; then
  failure 'rgw_container_invalid'
fi
state_and_image="$(docker inspect --format '{{.State.Running}}|{{.Image}}' "${rgw_container}")"
running="${state_and_image%%|*}"
target_image_id="${state_and_image#*|}"
if [[ "${running}" != "true" || "${target_image_id}" != sha256:* ]]; then
  failure 'candidate_container_unverified'
fi
if ! docker image inspect --format '{{range .RepoDigests}}{{println .}}{{end}}' "${target_image_id}" | grep -Fqx "${TARGET_IMAGE_REFERENCE}"; then
  failure 'candidate_digest_mismatch'
fi
if [[ "$(docker exec "${rgw_container}" ceph --version)" != "${TARGET_VERSION_OUTPUT}" ]]; then
  failure 'candidate_release_mismatch'
fi
target_platform="$(docker image inspect --format '{{.Os}}/{{.Architecture}}' "${target_image_id}")"
if [[ "${target_platform}" != "linux/amd64" && "${target_platform}" != "linux/arm64" ]]; then
  failure 'candidate_platform_invalid'
fi

readonly EVIDENCE_OUT="$2"
readonly EVIDENCE_DIRECTORY="$(dirname "${EVIDENCE_OUT}")"
readonly EVIDENCE_NAME="$(basename "${EVIDENCE_OUT}")"
if [[ -z "${EVIDENCE_NAME}" || "${EVIDENCE_NAME}" == "." || "${EVIDENCE_NAME}" == "/" ]]; then
  failure 'evidence_output_invalid'
fi

umask 077
mkdir -p "${EVIDENCE_DIRECTORY}"
image_id_file="$(mktemp "${TMPDIR:-/tmp}/hormuz-ceph-runner-image.XXXXXX")"
cleanup() {
  rm -f "${image_id_file}"
}
trap cleanup EXIT

docker build --pull --platform "${RUNNER_PLATFORM}" \
  --iidfile "${image_id_file}" \
  --file "${RUNNER_DOCKERFILE}" \
  --tag "${RUNNER_IMAGE_TAG}" \
  "${REPOSITORY_ROOT}" >/dev/null

runner_image_digest="$(<"${image_id_file}")"
if [[ ! "${runner_image_digest}" =~ ^sha256:[0-9a-f]{64}$ ]]; then
  failure 'runner_image_digest_invalid'
fi
runner_platform="$(docker image inspect --format '{{.Os}}/{{.Architecture}}' "${runner_image_digest}")"
if [[ "${runner_platform}" != "${RUNNER_PLATFORM}" ]]; then
  failure 'runner_platform_invalid'
fi

docker run --rm \
  --platform "${RUNNER_PLATFORM}" \
  --network host \
  --read-only \
  --tmpfs /tmp:rw,noexec,nosuid,size=64m \
  --cap-drop ALL \
  --security-opt no-new-privileges \
  --env-file "${ENVIRONMENT_FILE}" \
  --env HORMUZ_CEPH_RGW_TARGET_ATTESTED=1 \
  --env "HORMUZ_CEPH_RGW_TARGET_IMAGE_REFERENCE=${TARGET_IMAGE_REFERENCE}" \
  --env "HORMUZ_CEPH_RGW_TARGET_IMAGE_DIGEST=${TARGET_IMAGE_DIGEST}" \
  --env "HORMUZ_CEPH_RGW_TARGET_RELEASE=${TARGET_RELEASE}" \
  --env "HORMUZ_CEPH_RGW_TARGET_PLATFORM=${target_platform}" \
  --env "HORMUZ_CEPH_RGW_RUNNER_IMAGE_DIGEST=${runner_image_digest}" \
  --env "HORMUZ_CEPH_RGW_RUNNER_PLATFORM=${RUNNER_PLATFORM}" \
  --volume "${EVIDENCE_DIRECTORY}:/evidence:rw" \
  "${runner_image_digest}" \
  --evidence-out "/evidence/${EVIDENCE_NAME}"
