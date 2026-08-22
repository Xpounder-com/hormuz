#!/usr/bin/env bash
# Build and exercise the deliberately narrow non-root Hormuz OCI reference
# runtime. This script never sends a request to an AI provider and uses only
# fixed placeholder credentials supplied at runtime.

set -euo pipefail

readonly REPOSITORY_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
readonly IMAGE_NAME="${HORMUZ_OCI_TEST_IMAGE:-hormuz:oci-reference-test}"
readonly CONTAINER_NAME="hormuz-oci-reference-${RANDOM}-${RANDOM}"
readonly FIXTURE_PATH="${REPOSITORY_ROOT}/tests/fixtures/oci/reference-config.json"

temporary_root=""
container_started=0

cleanup() {
  local exit_status=$?
  trap - EXIT
  if [[ "${container_started}" -eq 1 ]]; then
    docker rm --force "${CONTAINER_NAME}" >/dev/null 2>&1 || true
  fi
  if [[ -n "${temporary_root}" && -d "${temporary_root}" ]]; then
    rm -rf "${temporary_root}"
  fi
  exit "${exit_status}"
}

fail() {
  local message=$1
  if [[ "${container_started}" -eq 1 ]]; then
    docker logs "${CONTAINER_NAME}" >&2 || true
  fi
  printf 'OCI reference verification failed: %s\n' "${message}" >&2
  exit 1
}

assert_contract() {
  local payload=$1
  local expected_schema=$2
  local expected_status=$3
  printf '%s' "${payload}" | python3 -c '
import json
import sys

value = json.load(sys.stdin)
assert value["schema_id"] == sys.argv[1], value
assert value["schema_version"] == 1, value
assert value["status"] == sys.argv[2], value
' "${expected_schema}" "${expected_status}"
}

trap cleanup EXIT

cd "${REPOSITORY_ROOT}"
docker build --tag "${IMAGE_NAME}" --file Dockerfile .

[[ "$(docker image inspect --format '{{.Config.User}}' "${IMAGE_NAME}")" == "65532:65532" ]] \
  || fail "image does not declare the fixed 65532:65532 runtime identity"
[[ "$(docker image inspect --format '{{json .Config.Entrypoint}}' "${IMAGE_NAME}")" == '["hormuz"]' ]] \
  || fail "image does not use the Hormuz CLI entrypoint"
[[ "$(docker image inspect --format '{{json .Config.Cmd}}' "${IMAGE_NAME}")" == '["serve"]' ]] \
  || fail "image does not default to serving the gateway"
[[ "$(docker image inspect --format '{{json .Config.Healthcheck.Test}}' "${IMAGE_NAME}")" == *'/health'* ]] \
  || fail "image does not declare the liveness health check"

set +e
missing_config_output="$(
  docker run --rm \
    --read-only \
    --tmpfs /tmp:mode=1777 \
    --cap-drop=ALL \
    --security-opt=no-new-privileges \
    "${IMAGE_NAME}" doctor 2>&1
)"
missing_config_status=$?
set -e
[[ "${missing_config_status}" -eq 2 ]] \
  || fail "image unexpectedly starts without a runtime-mounted configuration"
[[ "${missing_config_output}" == 'configuration error: configuration_unavailable' ]] \
  || fail "missing runtime configuration did not fail with the stable content-free error"

docker run --rm \
  --read-only \
  --tmpfs /tmp:mode=1777 \
  --cap-drop=ALL \
  --security-opt=no-new-privileges \
  --entrypoint /opt/hormuz/bin/python \
  "${IMAGE_NAME}" -I -c '
import importlib.util
import os

assert (os.getuid(), os.getgid()) == (65532, 65532)
assert importlib.util.find_spec("hormuz.context") is None
assert importlib.util.find_spec("hormuz_context_experiment") is None
'

temporary_root="$(mktemp -d "${TMPDIR:-/tmp}/hormuz-oci-reference.XXXXXX")"
config_directory="${temporary_root}/config"
data_directory="${temporary_root}/data"
mkdir -p "${config_directory}" "${data_directory}"
cp "${FIXTURE_PATH}" "${config_directory}/hormuz.json"
# The fixture directory is intentionally writable before it is mounted. The
# unsuccessful in-container write below therefore proves the mount is read-only
# rather than merely protected by host ownership.
chmod 0777 "${config_directory}" "${data_directory}"

docker run --detach \
  --name "${CONTAINER_NAME}" \
  --read-only \
  --tmpfs /tmp:mode=1777 \
  --cap-drop=ALL \
  --security-opt=no-new-privileges \
  --mount "type=bind,source=${config_directory},target=/etc/hormuz,readonly" \
  --mount "type=bind,source=${data_directory},target=/var/lib/hormuz" \
  --env HORMUZ_CONTAINER_TEST_TOKEN=container-test-identity-token \
  --env HORMUZ_CONTAINER_TEST_OPENAI_KEY=container-test-openai-placeholder \
  --env HORMUZ_CONTAINER_TEST_ANTHROPIC_KEY=container-test-anthropic-placeholder \
  --publish 127.0.0.1::8787 \
  "${IMAGE_NAME}" >/dev/null
container_started=1

docker inspect "${CONTAINER_NAME}" | python3 -c '
import json
import sys

container = json.load(sys.stdin)[0]
assert container["HostConfig"]["ReadonlyRootfs"] is True
mounts = {mount["Destination"]: mount for mount in container["Mounts"]}
assert mounts["/etc/hormuz"]["RW"] is False
assert mounts["/var/lib/hormuz"]["RW"] is True
'

docker exec "${CONTAINER_NAME}" /opt/hormuz/bin/python -I -c '
import os
assert (os.getuid(), os.getgid()) == (65532, 65532)
'

set +e
docker exec "${CONTAINER_NAME}" /opt/hormuz/bin/python -I -c '
from pathlib import Path
Path("/etc/hormuz/write-probe").write_text("must-not-write", encoding="utf-8")
' >/dev/null 2>&1
config_write_status=$?
set -e
[[ "${config_write_status}" -ne 0 ]] || fail "configuration mount was writable inside the running container"
[[ ! -e "${config_directory}/write-probe" ]] || fail "configuration write reached the host fixture"

host_port="$(docker port "${CONTAINER_NAME}" 8787/tcp | awk -F: 'NR == 1 { print $NF }')"
[[ "${host_port}" =~ ^[0-9]+$ ]] || fail "container did not publish the configured listener"

health_payload=""
for _attempt in $(seq 1 30); do
  if health_payload="$(curl --fail --silent --max-time 2 "http://127.0.0.1:${host_port}/health")"; then
    break
  fi
  sleep 1
done
[[ -n "${health_payload}" ]] || fail "liveness endpoint did not become available"
assert_contract "${health_payload}" "hormuz.gateway-health" "ok" \
  || fail "liveness response did not satisfy its versioned contract"

readiness_payload="$(curl --fail --silent --max-time 2 "http://127.0.0.1:${host_port}/ready")" \
  || fail "readiness endpoint did not report ready"
assert_contract "${readiness_payload}" "hormuz.gateway-readiness" "ready" \
  || fail "readiness response did not satisfy its versioned contract"
[[ -f "${data_directory}/hormuz.sqlite3" ]] \
  || fail "SQLite evidence did not remain on the explicit durable data mount"

docker stop --time 10 "${CONTAINER_NAME}" >/dev/null
[[ "$(docker inspect --format '{{.State.ExitCode}}' "${CONTAINER_NAME}")" == "0" ]] \
  || fail "SIGTERM did not produce a clean graceful gateway exit"

printf 'verified OCI reference runtime: non-root, mounted config, read-only rootfs, probes, and SIGTERM drain\n'
