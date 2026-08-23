#!/usr/bin/env bash
# Build the Hormuz reference image, generate an SBOM, and enforce the narrow
# fix-aware vulnerability policy. The scanner image is immutable; its
# vulnerability database is intentionally refreshed at scan time.

set -euo pipefail

readonly REPOSITORY_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
readonly IMAGE_NAME="${HORMUZ_OCI_SUPPLY_CHAIN_IMAGE:-hormuz:oci-supply-chain-test}"
# Trivy 0.74.0 multi-platform image index, inspected 2026-08-22.
readonly SCANNER_IMAGE="aquasec/trivy@sha256:62b1e65e8869bc4b4c6aa4fa2b21595256c7c2f6018a9d9ad61caf87187c1969"
readonly SCANNER_VERSION="0.74.0"
readonly EVIDENCE_DIR="${HORMUZ_OCI_SUPPLY_CHAIN_EVIDENCE_DIR:-$(mktemp -d "${TMPDIR:-/tmp}/hormuz-oci-supply-chain.XXXXXX")}"

mkdir -p "${EVIDENCE_DIR}"
cd "${REPOSITORY_ROOT}"

docker build --tag "${IMAGE_NAME}" --file Dockerfile .
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
