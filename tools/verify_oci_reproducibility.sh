#!/usr/bin/env bash
# Build the unsigned linux/amd64 payload twice from clean dependency resolution,
# normalize OCI timestamps, and require byte-for-byte identical archives.

set -euo pipefail

readonly REPOSITORY_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
readonly EVIDENCE_DIR="${HORMUZ_OCI_REPRODUCIBILITY_EVIDENCE_DIR:-$(mktemp -d "${TMPDIR:-/tmp}/hormuz-oci-reproducibility-evidence.XXXXXX")}"
readonly TEMPORARY_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/hormuz-oci-reproducibility.XXXXXX")"
readonly IMAGE_NAME="${HORMUZ_OCI_REPRODUCIBILITY_IMAGE:-ghcr.io/xpounder-com/hormuz}"
readonly TAG="${HORMUZ_OCI_REPRODUCIBILITY_TAG:-local-reproducibility}"

cleanup() {
  local exit_code=$?
  trap - EXIT
  rm -rf "${TEMPORARY_ROOT}"
  exit "${exit_code}"
}
trap cleanup EXIT

cd "${REPOSITORY_ROOT}"
readonly VERSION="$(python3 -c 'import pathlib, tomllib; print(tomllib.loads(pathlib.Path("pyproject.toml").read_text(encoding="utf-8"))["project"]["version"])')"
readonly REVISION="${HORMUZ_OCI_REPRODUCIBILITY_REVISION:-$(git rev-parse HEAD)}"
readonly SOURCE_DATE_EPOCH="${HORMUZ_OCI_REPRODUCIBILITY_SOURCE_DATE_EPOCH:-$(git show -s --format=%ct "${REVISION}")}"
readonly REFERENCE="${IMAGE_NAME}:${TAG}"

mkdir -p "${EVIDENCE_DIR}"

build_archive() {
  local destination=$1
  docker buildx build \
    --platform linux/amd64 \
    --pull \
    --no-cache \
    --provenance=false \
    --sbom=false \
    --output "type=oci,dest=${destination},name=${REFERENCE},rewrite-timestamp=true" \
    --build-arg "HORMUZ_VERSION=${VERSION}" \
    --build-arg "VCS_REF=${REVISION}" \
    --build-arg "SOURCE_DATE_EPOCH=${SOURCE_DATE_EPOCH}" \
    --file Dockerfile \
    .
}

build_archive "${TEMPORARY_ROOT}/first.tar"
build_archive "${TEMPORARY_ROOT}/second.tar"

PYTHONPATH="${REPOSITORY_ROOT}/tools${PYTHONPATH:+:${PYTHONPATH}}" \
PYTHONSAFEPATH=1 \
python3 "${REPOSITORY_ROOT}/tools/verify_oci_reproducibility.py" \
  --first "${TEMPORARY_ROOT}/first.tar" \
  --second "${TEMPORARY_ROOT}/second.tar" \
  --expected-reference "${TAG}" \
  --expected-version "${VERSION}" \
  --expected-commit "${REVISION}" \
  --source-date-epoch "${SOURCE_DATE_EPOCH}" \
  --output "${EVIDENCE_DIR}/summary.json"

printf 'verified reproducible OCI release input in %s\n' "${EVIDENCE_DIR}"
