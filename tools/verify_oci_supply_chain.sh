#!/usr/bin/env bash
# Build the Hormuz reference image, generate an SBOM, and enforce the narrow
# fix-aware vulnerability policy. The scanner image is immutable; its
# vulnerability database is intentionally refreshed at scan time.

set -euo pipefail

readonly REPOSITORY_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
readonly IMAGE_NAME="${HORMUZ_OCI_SUPPLY_CHAIN_IMAGE:-hormuz:oci-supply-chain-test}"
readonly SKIP_BUILD="${HORMUZ_OCI_SUPPLY_CHAIN_SKIP_BUILD:-0}"
# Trivy 0.74.0 multi-platform image index, inspected 2026-08-22.
readonly SCANNER_IMAGE="aquasec/trivy@sha256:62b1e65e8869bc4b4c6aa4fa2b21595256c7c2f6018a9d9ad61caf87187c1969"
readonly SCANNER_VERSION="0.74.0"
readonly EVIDENCE_DIR="${HORMUZ_OCI_SUPPLY_CHAIN_EVIDENCE_DIR:-$(mktemp -d "${TMPDIR:-/tmp}/hormuz-oci-supply-chain.XXXXXX")}"

mkdir -p "${EVIDENCE_DIR}"
cd "${REPOSITORY_ROOT}"

if [[ "${SKIP_BUILD}" == "1" ]]; then
  [[ "${IMAGE_NAME}" =~ ^[^[:space:]@]+@sha256:[0-9a-f]{64}$ ]] \
    || { printf 'OCI supply-chain verification failed: skipped build requires a digest-pinned image\n' >&2; exit 1; }
  docker image inspect "${IMAGE_NAME}" >/dev/null \
    || { printf 'OCI supply-chain verification failed: image is not present locally\n' >&2; exit 1; }
elif [[ "${SKIP_BUILD}" == "0" ]]; then
  version="$(python3 -c 'import pathlib, tomllib; print(tomllib.loads(pathlib.Path("pyproject.toml").read_text(encoding="utf-8"))["project"]["version"])')"
  revision="$(git rev-parse HEAD)"
  source_date_epoch="$(git show -s --format=%ct "${revision}")"
  docker build \
    --platform linux/amd64 \
    --provenance=false \
    --sbom=false \
    --tag "${IMAGE_NAME}" \
    --build-arg "HORMUZ_VERSION=${version}" \
    --build-arg "VCS_REF=${revision}" \
    --build-arg "SOURCE_DATE_EPOCH=${source_date_epoch}" \
    --file Dockerfile \
    .
else
  printf 'OCI supply-chain verification failed: HORMUZ_OCI_SUPPLY_CHAIN_SKIP_BUILD must be 0 or 1\n' >&2
  exit 1
fi
[[ "$(docker image inspect --format '{{.Os}}/{{.Architecture}}' "${IMAGE_NAME}")" == "linux/amd64" ]] \
  || { printf 'OCI supply-chain verification failed: image platform is not linux/amd64\n' >&2; exit 1; }
image_id="$(docker image inspect --format '{{.Id}}' "${IMAGE_NAME}")"

docker run --rm \
  --volume /var/run/docker.sock:/var/run/docker.sock \
  --volume "${EVIDENCE_DIR}:/evidence" \
  "${SCANNER_IMAGE}" image \
  --format cyclonedx \
  --output /evidence/hormuz.cdx.json \
  "${IMAGE_NAME}"

docker run --rm \
  --volume /var/run/docker.sock:/var/run/docker.sock \
  --volume "${EVIDENCE_DIR}:/evidence" \
  "${SCANNER_IMAGE}" image \
  --exit-code 0 \
  --format json \
  --ignore-unfixed=false \
  --scanners vuln \
  --severity UNKNOWN,LOW,MEDIUM,HIGH,CRITICAL \
  --output /evidence/trivy-vulnerabilities.json \
  "${IMAGE_NAME}"

python3 "${REPOSITORY_ROOT}/tools/verify_oci_supply_chain.py" \
  --image-reference "${IMAGE_NAME}" \
  --image-id "${image_id}" \
  --scanner-image "${SCANNER_IMAGE}" \
  --scanner-version "${SCANNER_VERSION}" \
  --sbom "${EVIDENCE_DIR}/hormuz.cdx.json" \
  --vulnerabilities "${EVIDENCE_DIR}/trivy-vulnerabilities.json" \
  --output "${EVIDENCE_DIR}/summary.json"

printf 'verified OCI supply-chain evidence in %s\n' "${EVIDENCE_DIR}"
