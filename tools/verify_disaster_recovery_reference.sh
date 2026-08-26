#!/usr/bin/env bash

set -euo pipefail
umask 077

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FIXTURE_ROOT="${ROOT}/deploy/kubernetes/conformance"
HA_ROOT="${FIXTURE_ROOT}/postgres-ha"
DR_ROOT="${FIXTURE_ROOT}/disaster-recovery"
CHART_ROOT="${ROOT}/deploy/helm/hormuz"
EVIDENCE_DIR="${HORMUZ_DISASTER_RECOVERY_EVIDENCE_DIR:-}"
SOURCE_COMMIT="${HORMUZ_SOURCE_COMMIT:-}"
SOURCE_CLUSTER="${HORMUZ_DR_SOURCE_CLUSTER_NAME:-hormuz-dr-source}"
RECOVERY_CLUSTER="${HORMUZ_DR_RECOVERY_CLUSTER_NAME:-hormuz-dr-recovery}"
PROOF_ACK="I_UNDERSTAND_THIS_IS_A_DISPOSABLE_DISASTER_RECOVERY_REFERENCE_PROOF"

HORMUZ_IMAGE="ghcr.io/xpounder-com/hormuz@sha256:1bbcca3490a7a5b004a880f42e8250acb91ce566a9c59f3263d7b279568efb5a"
POSTGRES_IMAGE="ghcr.io/cloudnative-pg/postgresql:16.15-202608240846-minimal-trixie@sha256:e1ca593856017f1780dbdae8175add3ddd8f8d721348a3b6e8a01df67a9ece8a"
OPENBAO_IMAGE="openbao/openbao@sha256:d0424c95859f7b4c1e308abf57c4cd72b9cba835bb946eb397172b799fba9477"
CNPG_VERSION="1.30.0"
CNPG_MANIFEST_SHA256="f8bede43fe4ee0d478c2355b204a36876b2ae4faac60f2a9452280b293da3b88"
CNPG_OPERATOR_IMAGE="ghcr.io/cloudnative-pg/cloudnative-pg:1.30.0@sha256:091d306935cfdf646debfe78010d59ebfb572150eb6eb922b0203873c0c68841"
KIND_VERSION="v0.32.0"
KIND_SHA256="50030de23cf40a18505f20426f6a8506bedf13c6e509244bd1fa9463721b0f54"
KUBECTL_VERSION="v1.36.1"
KUBECTL_SHA256="629d3f410e09bf49b64ae7079f7f0bda1191efed311f7d37fdbab0ad5b0ec2b7"
HELM_VERSION="v3.21.4"
HELM_SHA256="61f88ab166748cb19604d7884cb100ae9ccb13804ddeb98e08af167eacbb6a14"
CILIUM_VERSION="1.20.1"
CILIUM_CHART_SHA256="06210eef7c23d15f7699c79e2fe3a1ec9c389024c5c5c006ea04022d322449a2"
DISPOSABLE_LABEL="io.hormuz.disaster-recovery-proof"

WORK_ROOT=""
SECRET_ROOT=""
ARTIFACT_ROOT=""
RECOVERY_INPUTS=""
KUBECONFIG=""
ACTIVE_CLUSTER=""
CLUSTER_CREATED=0
SOURCE_PRIMARY=""
OPENBAO_CONTAINER=""
NEGATIVE_NETWORK=""
NEGATIVE_CONTAINERS=()

fail() {
  printf 'Disaster-recovery reference proof failed: %s\n' "$1" >&2
  exit 1
}

utc_now() {
  python3 -c 'from datetime import UTC,datetime; print(datetime.now(UTC).isoformat())'
}

milliseconds_between() {
  python3 - "$1" "$2" <<'PY'
from datetime import datetime
import sys
start, end = (datetime.fromisoformat(value) for value in sys.argv[1:])
print(round((end - start).total_seconds() * 1000))
PY
}

download_and_verify() {
  local url=$1
  local output=$2
  local expected=$3
  curl --fail --silent --show-error --location --retry 3 --output "${output}" "${url}"
  printf '%s  %s\n' "${expected}" "${output}" | sha256sum --check --status \
    || fail "download checksum mismatch"
}

write_random_hex_secret() {
  local output=$1
  local value
  value="$(openssl rand -hex 32)"
  [[ "${#value}" -eq 64 ]] || fail "generated secret length invalid"
  printf '%s' "${value}" >"${output}"
}

create_immutable_configmap() {
  local namespace=$1
  local name=$2
  shift 2
  kubectl --namespace "${namespace}" create configmap "${name}" "$@" >/dev/null
  kubectl --namespace "${namespace}" patch configmap "${name}" \
    --type=merge --patch '{"immutable":true}' >/dev/null
}

create_immutable_secret() {
  local namespace=$1
  local name=$2
  shift 2
  kubectl --namespace "${namespace}" create secret generic "${name}" "$@" >/dev/null
  kubectl --namespace "${namespace}" patch secret "${name}" \
    --type=merge --patch '{"immutable":true}' >/dev/null
}

wait_for_deployment() {
  kubectl --namespace "$1" rollout status "deployment/$2" --timeout="${3:-10m}" >/dev/null \
    || fail "deployment did not become ready: $1/$2"
}

wait_for_statefulset() {
  kubectl --namespace "$1" rollout status "statefulset/$2" --timeout="${3:-10m}" >/dev/null \
    || fail "statefulset did not become ready: $1/$2"
}

wait_for_pod() {
  kubectl --namespace "$1" wait --for=condition=Ready "pod/$2" --timeout="${3:-10m}" >/dev/null \
    || fail "pod did not become ready: $1/$2"
}

wait_for_job_complete() {
  local namespace=$1
  local job=$2
  local attempt state
  for attempt in $(seq 1 300); do
    state="$(kubectl --namespace "${namespace}" get "job/${job}" --output=json \
      | python3 -c 'import json,sys; v=json.load(sys.stdin); c={x.get("type"):x.get("status") for x in v.get("status",{}).get("conditions",[])}; print("complete" if c.get("Complete")=="True" else "failed" if c.get("Failed")=="True" else "pending")')"
    case "${state}" in
      complete) return ;;
      failed)
        kubectl --namespace "${namespace}" logs "job/${job}" --all-containers \
          --prefix=true >&2 || true
        kubectl --namespace "${namespace}" describe "job/${job}" >&2 || true
        fail "job failed: ${namespace}/${job}"
        ;;
      pending) ;;
      *) fail "job state invalid: ${namespace}/${job}" ;;
    esac
    sleep 1
  done
  kubectl --namespace "${namespace}" logs "job/${job}" --all-containers \
    --prefix=true >&2 || true
  kubectl --namespace "${namespace}" describe "job/${job}" >&2 || true
  fail "job completion timed out: ${namespace}/${job}"
}

wait_for_source_backup_receiver() {
  local attempt state
  for attempt in $(seq 1 180); do
    state="$(kubectl --namespace hormuz-dependencies get \
      pod/hormuz-dr-wal-receiver --output=json | python3 -c '
import json
import sys
value = json.load(sys.stdin)
status = value.get("status", {})
ready = any(
    condition.get("type") == "Ready" and condition.get("status") == "True"
    for condition in status.get("conditions", [])
)
statuses = status.get("initContainerStatuses", []) + status.get("containerStatuses", [])
failed = status.get("phase") == "Failed" or any(
    item.get("state", {}).get("terminated", {}).get("exitCode", 0) != 0
    for item in statuses
)
print("ready" if ready else "failed" if failed else "pending")
')"
    case "${state}" in
      ready) return ;;
      failed) break ;;
      pending) ;;
      *) fail "source backup receiver state invalid" ;;
    esac
    sleep 1
  done
  kubectl --namespace hormuz-dependencies logs pod/hormuz-dr-wal-receiver \
    --all-containers --prefix=true >&2 || true
  kubectl --namespace hormuz-dependencies describe \
    pod/hormuz-dr-wal-receiver >&2 || true
  fail "source backup receiver did not become ready"
}

wait_for_cnpg_ready() {
  kubectl --namespace hormuz-dependencies wait \
    --for=condition=Ready cluster/hormuz-postgres --timeout=10m >/dev/null \
    || fail "CloudNativePG source cluster did not become ready"
  kubectl --namespace hormuz-dependencies wait \
    --for=condition=Ready pod --selector='cnpg.io/cluster=hormuz-postgres' \
    --timeout=10m >/dev/null \
    || fail "CloudNativePG source instances did not become ready"
  local count
  count="$(kubectl --namespace hormuz-dependencies get pods \
    --selector='cnpg.io/cluster=hormuz-postgres' --output=json \
    | python3 -c 'import json,sys; print(sum(1 for x in json.load(sys.stdin)["items"] if any(c.get("type")=="Ready" and c.get("status")=="True" for c in x.get("status",{}).get("conditions",[]))))')"
  [[ "${count}" == "3" ]] || fail "CloudNativePG source instance count invalid"
}

remove_disposable_container() {
  local container=$1
  local label
  label="$(docker inspect --format "{{ index .Config.Labels \"${DISPOSABLE_LABEL}\" }}" "${container}" 2>/dev/null || true)"
  if [[ "${label}" == "true" ]]; then
    docker rm --force "${container}" >/dev/null 2>&1 || true
  fi
}

cleanup() {
  local status=$?
  set +e
  local container
  for container in "${NEGATIVE_CONTAINERS[@]:-}"; do
    [[ -n "${container}" ]] && remove_disposable_container "${container}"
  done
  if [[ -n "${OPENBAO_CONTAINER}" ]]; then
    docker unpause "${OPENBAO_CONTAINER}" >/dev/null 2>&1 || true
    remove_disposable_container "${OPENBAO_CONTAINER}"
  fi
  if [[ -n "${NEGATIVE_NETWORK}" ]]; then
    local network_label
    network_label="$(docker network inspect --format "{{ index .Labels \"${DISPOSABLE_LABEL}\" }}" "${NEGATIVE_NETWORK}" 2>/dev/null || true)"
    if [[ "${network_label}" == "true" ]]; then
      docker network rm "${NEGATIVE_NETWORK}" >/dev/null 2>&1 || true
    fi
  fi
  if [[ "${CLUSTER_CREATED}" -eq 1 && -n "${ACTIVE_CLUSTER}" && -n "${WORK_ROOT}" ]]; then
    "${WORK_ROOT}/bin/kind" delete cluster --name "${ACTIVE_CLUSTER}" >/dev/null 2>&1 || true
  fi
  if [[ -n "${WORK_ROOT}" && -d "${WORK_ROOT}" && ! -L "${WORK_ROOT}" ]]; then
    docker run --rm --user root --entrypoint bash --volume "${WORK_ROOT}:/cleanup:rw" \
      "${POSTGRES_IMAGE}" -ceu 'chmod -R ugo+rwX /cleanup 2>/dev/null || true' >/dev/null 2>&1 || true
    rm -rf -- "${WORK_ROOT}"
  fi
  exit "${status}"
}
trap cleanup EXIT HUP INT TERM

install_cilium() {
  helm upgrade --install cilium "${WORK_ROOT}/cilium.tgz" \
    --namespace kube-system --values "${FIXTURE_ROOT}/cilium-values.yaml" \
    --wait --timeout 10m >/dev/null
  kubectl wait --for=condition=Ready nodes --all --timeout=10m >/dev/null
}

create_namespaces() {
  local namespace
  for namespace in hormuz-system hormuz-dependencies hormuz-ingress; do
    kubectl create namespace "${namespace}" >/dev/null
  done
}

write_dsns() {
  local host=$1
  python3 - "${SECRET_ROOT}" "${host}" <<'PY'
from pathlib import Path
import sys
root = Path(sys.argv[1])
host = sys.argv[2]
database = "hormuz"
values = {
    "postgres-migration-dsn": ("postgres", "postgres-superuser-password"),
    "postgres-runtime-dsn": ("hormuz_runtime", "runtime-password"),
    "postgres-policy-control-dsn": ("hormuz_policy_control", "policy-control-password"),
    "postgres-custody-control-dsn": ("hormuz_custody_control", "custody-control-password"),
    "postgres-custody-executor-dsn": ("hormuz_custody_executor", "custody-executor-password"),
}
for output, (role, password_file) in values.items():
    password = (root / password_file).read_text(encoding="utf-8")
    (root / output).write_text(
        f"postgresql://{role}:{password}@{host}:5432/{database}?connect_timeout=5",
        encoding="utf-8",
    )
PY
  chmod 0600 "${SECRET_ROOT}"/postgres-*-dsn
}

install_state_probe() {
  local config_path=$1
  create_immutable_configmap hormuz-system hormuz-disaster-recovery-state-config \
    --from-file="state-config.json=${config_path}"
  create_immutable_configmap hormuz-system hormuz-disaster-recovery-state-script \
    --from-file="state_probe.py=${DR_ROOT}/state_probe.py"
  create_immutable_secret hormuz-system hormuz-disaster-recovery-state \
    --from-file="postgres-runtime-dsn=${SECRET_ROOT}/postgres-runtime-dsn" \
    --from-file="postgres-migration-dsn=${SECRET_ROOT}/postgres-migration-dsn" \
    --from-file="postgres-policy-control-dsn=${SECRET_ROOT}/postgres-policy-control-dsn" \
    --from-file="postgres-custody-control-dsn=${SECRET_ROOT}/postgres-custody-control-dsn" \
    --from-file="postgres-custody-executor-dsn=${SECRET_ROOT}/postgres-custody-executor-dsn" \
    --from-file="alice-token=${SECRET_ROOT}/identity-token" \
    --from-file="bob-token=${SECRET_ROOT}/bob-token" \
    --from-file="openai-api-key=${SECRET_ROOT}/openai-api-key" \
    --from-file="anthropic-api-key=${SECRET_ROOT}/anthropic-api-key" \
    --from-file="openbao-token=${SECRET_ROOT}/openbao-runtime-token"
  kubectl --namespace hormuz-system apply --filename "${DR_ROOT}/state-pod.yaml" >/dev/null
  wait_for_pod hormuz-system hormuz-disaster-recovery-state 10m
}

run_state_probe() {
  local command=$1
  local output=$2
  timeout 120s kubectl --namespace hormuz-system exec pod/hormuz-disaster-recovery-state \
    --container state -- /opt/hormuz/bin/python -I \
    /opt/hormuz-proof/state_probe.py "${command}" >"${output}" \
    || fail "disaster-recovery state probe failed: ${command}"
  python3 - "${output}" "${command}" <<'PY'
import json
import sys
value = json.load(open(sys.argv[1], encoding="utf-8"))
if value.get("command") != sys.argv[2]:
    raise SystemExit("state_probe_output_invalid")
PY
}

source_sql() {
  [[ -n "${SOURCE_PRIMARY}" ]] || fail "source PostgreSQL primary is unavailable"
  kubectl --namespace hormuz-dependencies exec pod/hormuz-dr-wal-receiver \
    --container wal-receiver -- psql --username=postgres --dbname=hormuz \
    --set=ON_ERROR_STOP=on --tuples-only --no-align --command "$1"
}

archive_current_wal() {
  local wal_file attempt
  wal_file="$(source_sql 'SELECT pg_walfile_name(pg_current_wal_lsn())' | tr -d '[:space:]')"
  [[ "${wal_file}" =~ ^[0-9A-F]{24}$ ]] || fail "source WAL identifier invalid"
  source_sql 'SELECT pg_switch_wal()' >/dev/null
  for attempt in $(seq 1 120); do
    if kubectl --namespace hormuz-dependencies exec pod/hormuz-dr-wal-receiver \
      --container wal-receiver -- test -f "/recovery/wal/${wal_file}" \
      >/dev/null 2>&1; then
      printf '%s\n' "${wal_file}"
      return
    fi
    sleep 1
  done
  fail "required WAL segment was not archived"
}

sha256_tree() {
  local directory=$1
  tar --sort=name --mtime='UTC 1970-01-01' --owner=0 --group=0 --numeric-owner \
    -cf - -C "${directory}" . | sha256sum | awk '{print $1}'
}

start_openbao() {
  python3 - "${SECRET_ROOT}/openbao-root-token" \
    "${SECRET_ROOT}/openbao.env" "${SECRET_ROOT}/openbao-root-header" <<'PY'
from pathlib import Path
import sys
token = Path(sys.argv[1]).read_text(encoding="utf-8")
Path(sys.argv[2]).write_text(
    f"BAO_DEV_ROOT_TOKEN_ID={token}\nBAO_DEV_LISTEN_ADDRESS=0.0.0.0:8200\n",
    encoding="utf-8",
)
Path(sys.argv[3]).write_text(f"X-Vault-Token: {token}\n", encoding="utf-8")
PY
  chmod 0600 "${SECRET_ROOT}/openbao.env" "${SECRET_ROOT}/openbao-root-header"
  OPENBAO_CONTAINER="hormuz-dr-openbao-${RANDOM}${RANDOM}"
  docker run --detach --name "${OPENBAO_CONTAINER}" \
    --label "${DISPOSABLE_LABEL}=true" --platform linux/amd64 \
    --publish 127.0.0.1::8200 \
    --env-file "${SECRET_ROOT}/openbao.env" \
    "${OPENBAO_IMAGE}" server -dev >/dev/null
  local mapping attempt
  mapping="$(docker port "${OPENBAO_CONTAINER}" 8200/tcp | sed -E -n '1s/.*:([0-9]+)$/\1/p')"
  [[ "${mapping}" =~ ^[0-9]+$ ]] || fail "OpenBao host port unavailable"
  OPENBAO_URL="http://127.0.0.1:${mapping}"
  for attempt in $(seq 1 60); do
    if curl --fail --silent --max-time 2 "${OPENBAO_URL}/v1/sys/health" >/dev/null 2>&1; then
      break
    fi
    sleep 1
  done
  [[ "${attempt}" -lt 60 ]] || fail "OpenBao custody canary did not become ready"

  curl --fail --silent --show-error --max-time 5 \
    --header "@${SECRET_ROOT}/openbao-root-header" --request POST \
    --data '{"type":"transit"}' "${OPENBAO_URL}/v1/sys/mounts/transit" >/dev/null
  curl --fail --silent --show-error --max-time 5 \
    --header "@${SECRET_ROOT}/openbao-root-header" --request POST \
    --data '{"type":"aes256-gcm96","exportable":false,"allow_plaintext_backup":false}' \
    "${OPENBAO_URL}/v1/transit/keys/hormuz-dr-canary" >/dev/null
  python3 - "${ARTIFACT_ROOT}/openbao-runtime-policy.json" <<'PY'
import json
from pathlib import Path
import sys
policy = 'path "transit/decrypt/hormuz-dr-canary" { capabilities = ["update"] }'
Path(sys.argv[1]).write_text(json.dumps({"policy": policy}, separators=(",", ":")), encoding="utf-8")
PY
  curl --fail --silent --show-error --max-time 5 \
    --header "@${SECRET_ROOT}/openbao-root-header" --request PUT \
    --data-binary "@${ARTIFACT_ROOT}/openbao-runtime-policy.json" \
    "${OPENBAO_URL}/v1/sys/policies/acl/hormuz-recovery-runtime" >/dev/null
  curl --fail --silent --show-error --max-time 5 \
    --header "@${SECRET_ROOT}/openbao-root-header" --request POST \
    --data '{"policies":["hormuz-recovery-runtime"],"no_default_policy":true,"renewable":false,"ttl":"4h"}' \
    "${OPENBAO_URL}/v1/auth/token/create" \
    >"${SECRET_ROOT}/openbao-runtime-token-response.json"
  python3 - "${SECRET_ROOT}/openbao-runtime-token-response.json" \
    "${SECRET_ROOT}/openbao-runtime-token" \
    "${SECRET_ROOT}/openbao-runtime-header" <<'PY'
import json
from pathlib import Path
import sys
value = json.load(open(sys.argv[1], encoding="utf-8"))
token = value.get("auth", {}).get("client_token")
if not isinstance(token, str) or not token:
    raise SystemExit("openbao_runtime_token_invalid")
Path(sys.argv[2]).write_text(token, encoding="utf-8")
Path(sys.argv[3]).write_text(f"X-Vault-Token: {token}\n", encoding="utf-8")
PY
  chmod 0600 "${SECRET_ROOT}/openbao-runtime-token" \
    "${SECRET_ROOT}/openbao-runtime-header"
  rm -f -- "${SECRET_ROOT}/openbao-runtime-token-response.json" \
    "${ARTIFACT_ROOT}/openbao-runtime-policy.json"

  write_random_hex_secret "${SECRET_ROOT}/custody-canary-plaintext"
  local plaintext_b64
  plaintext_b64="$(base64 <"${SECRET_ROOT}/custody-canary-plaintext" | tr -d '\n')"
  python3 - "${ARTIFACT_ROOT}/custody-encrypt-input.json" "${plaintext_b64}" <<'PY'
import json
from pathlib import Path
import sys
Path(sys.argv[1]).write_text(json.dumps({"plaintext": sys.argv[2]}, separators=(",", ":")), encoding="utf-8")
PY
  curl --fail --silent --show-error --max-time 5 \
    --header "@${SECRET_ROOT}/openbao-root-header" --request POST \
    --data-binary "@${ARTIFACT_ROOT}/custody-encrypt-input.json" \
    "${OPENBAO_URL}/v1/transit/encrypt/hormuz-dr-canary" \
    >"${SECRET_ROOT}/custody-canary-response.json"
  python3 - "${SECRET_ROOT}/custody-canary-response.json" \
    "${SECRET_ROOT}/custody-canary-ciphertext" <<'PY'
import json
from pathlib import Path
import sys
value = json.load(open(sys.argv[1], encoding="utf-8"))
ciphertext = value.get("data", {}).get("ciphertext")
if not isinstance(ciphertext, str) or not ciphertext.startswith("vault:v"):
    raise SystemExit("custody_canary_ciphertext_invalid")
Path(sys.argv[2]).write_text(ciphertext, encoding="utf-8")
PY
  sha256sum "${SECRET_ROOT}/custody-canary-plaintext" | awk '{print $1}' \
    >"${SECRET_ROOT}/custody-canary-plaintext.sha256"
  rm -f -- "${ARTIFACT_ROOT}/custody-encrypt-input.json" \
    "${SECRET_ROOT}/custody-canary-response.json"
  custody_canary_verify || fail "OpenBao custody canary verification failed"
}

custody_canary_verify() {
  local ciphertext expected response plaintext_digest
  ciphertext="$(<"${SECRET_ROOT}/custody-canary-ciphertext")"
  expected="$(<"${SECRET_ROOT}/custody-canary-plaintext.sha256")"
  python3 - "${ARTIFACT_ROOT}/custody-decrypt-input.json" "${ciphertext}" <<'PY'
import json
from pathlib import Path
import sys
Path(sys.argv[1]).write_text(json.dumps({"ciphertext": sys.argv[2]}, separators=(",", ":")), encoding="utf-8")
PY
  if ! curl --fail --silent --max-time 2 \
    --header "@${SECRET_ROOT}/openbao-runtime-header" --request POST \
    --data-binary "@${ARTIFACT_ROOT}/custody-decrypt-input.json" \
    "${OPENBAO_URL}/v1/transit/decrypt/hormuz-dr-canary" \
    >"${ARTIFACT_ROOT}/custody-decrypt-response.json"; then
    rm -f -- "${ARTIFACT_ROOT}/custody-decrypt-input.json" \
      "${ARTIFACT_ROOT}/custody-decrypt-response.json"
    return 1
  fi
  plaintext_digest="$(python3 - "${ARTIFACT_ROOT}/custody-decrypt-response.json" <<'PY'
import base64
import hashlib
import json
import sys
value = json.load(open(sys.argv[1], encoding="utf-8"))
plaintext = value.get("data", {}).get("plaintext")
if not isinstance(plaintext, str):
    raise SystemExit(1)
print(hashlib.sha256(base64.b64decode(plaintext, validate=True)).hexdigest())
PY
)" || return 1
  rm -f -- "${ARTIFACT_ROOT}/custody-decrypt-input.json" \
    "${ARTIFACT_ROOT}/custody-decrypt-response.json"
  [[ "${plaintext_digest}" == "${expected}" ]]
}

write_state_probe_env_file() {
  local host=$1
  local output=$2
  python3 - "${SECRET_ROOT}" "${host}" "${output}" <<'PY'
from pathlib import Path
import sys
root = Path(sys.argv[1])
host = sys.argv[2]
output = Path(sys.argv[3])
database = "hormuz"
dsn_roles = {
    "HORMUZ_POSTGRES_DSN": ("hormuz_runtime", "runtime-password"),
    "HORMUZ_POSTGRES_MIGRATION_DSN": ("postgres", "postgres-superuser-password"),
    "HORMUZ_POLICY_CONTROL_DSN": ("hormuz_policy_control", "policy-control-password"),
    "HORMUZ_CUSTODY_CONTROL_DSN": ("hormuz_custody_control", "custody-control-password"),
    "HORMUZ_CUSTODY_EXECUTOR_DSN": ("hormuz_custody_executor", "custody-executor-password"),
}
values = {
    "HORMUZ_CONFIG": "/etc/hormuz-recovery/state-config.json",
    **{
        name: f"postgresql://{role}:{(root / password).read_text(encoding='utf-8')}@{host}:5432/{database}?connect_timeout=5"
        for name, (role, password) in dsn_roles.items()
    },
    "HORMUZ_TOKEN": (root / "identity-token").read_text(encoding="utf-8"),
    "HORMUZ_BOB_TOKEN": (root / "bob-token").read_text(encoding="utf-8"),
    "OPENAI_API_KEY": (root / "openai-api-key").read_text(encoding="utf-8"),
    "ANTHROPIC_API_KEY": (root / "anthropic-api-key").read_text(encoding="utf-8"),
    "HORMUZ_OPENBAO_TOKEN": (root / "openbao-runtime-token").read_text(encoding="utf-8"),
}
if any("\n" in value or "\r" in value for value in values.values()):
    raise SystemExit("state_probe_environment_invalid")
output.write_text("".join(f"{name}={value}\n" for name, value in values.items()), encoding="utf-8")
PY
  chmod 0600 "${output}"
}

build_secret_generation_manifest() {
  local output=$1
  local created_at=$2
  python3 - "${SECRET_ROOT}" "${output}" "${created_at}" <<'PY'
import hashlib
import json
from pathlib import Path
import sys
root = Path(sys.argv[1])
names = (
    "runtime-password",
    "policy-control-password",
    "custody-control-password",
    "custody-executor-password",
    "identity-token",
    "bob-token",
    "openai-api-key",
    "anthropic-api-key",
    "openbao-runtime-token",
    "ingress-credential",
    "custody-canary-ciphertext",
)
fingerprints = {}
for name in names:
    data = (root / name).read_bytes()
    if not data:
        raise SystemExit("secret_generation_input_empty")
    fingerprints[name] = "sha256:" + hashlib.sha256(data).hexdigest()
value = {
    "schema_id": "hormuz.disaster-recovery-secret-generation",
    "schema_version": 1,
    "generation": "disaster-recovery-generation-v1",
    "created_at": sys.argv[3],
    "fingerprints": fingerprints,
}
Path(sys.argv[2]).write_text(json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
PY
}

prepare_negative_recovery() {
  local name=$1
  local target=$2
  local archive=$3
  local root="${WORK_ROOT}/negative/${name}"
  mkdir -p "${root}"
  docker run --rm --user root --entrypoint bash \
    --volume "${RECOVERY_INPUTS}:/recovery:ro" \
    --volume "${root}:/negative:rw" "${POSTGRES_IMAGE}" -ceu '
      target="$1"
      archive="$2"
      cp -a /recovery/base /negative/data
      rm -f /negative/data/standby.signal
      printf "%s\n" \
        "listen_addresses = '\''*'\''" \
        "port = 5432" \
        "max_connections = 100" \
        "shared_buffers = '\''128MB'\''" \
        "ssl = off" \
        "restore_command = '\''cp ${archive}/%f %p'\''" \
        "recovery_target_name = '\''${target}'\''" \
        "recovery_target_action = '\''promote'\''" \
        > /negative/data/postgresql.conf
      printf "%s\n" "# isolated negative recovery" > /negative/data/postgresql.auto.conf
      printf "%s\n" \
        "local all all trust" \
        "host all all 0.0.0.0/0 scram-sha-256" \
        "host all all ::/0 scram-sha-256" \
        > /negative/data/pg_hba.conf
      touch /negative/data/recovery.signal
      chown -R 26:102 /negative/data
      chmod 0700 /negative/data
    ' bash "${target}" "${archive}" >/dev/null
  printf '%s\n' "${root}/data"
}

start_negative_recovery() {
  local name=$1
  local data=$2
  local container="hormuz-dr-negative-${name}-${RANDOM}${RANDOM}"
  docker run --detach --name "${container}" --label "${DISPOSABLE_LABEL}=true" \
    --platform linux/amd64 --network "${NEGATIVE_NETWORK}" \
    --network-alias "${name}-postgres" \
    --volume "${data}:/var/lib/postgresql/data/pgdata:rw" \
    --volume "${RECOVERY_INPUTS}:/recovery:ro" \
    --env PGDATA=/var/lib/postgresql/data/pgdata \
    "${POSTGRES_IMAGE}" postgres -D /var/lib/postgresql/data/pgdata >/dev/null
  NEGATIVE_CONTAINERS+=("${container}")
  LAST_NEGATIVE_CONTAINER="${container}"
}

wait_for_negative_failure() {
  local container=$1
  local attempt state exit_code
  for attempt in $(seq 1 120); do
    state="$(docker inspect --format '{{.State.Status}}' "${container}" 2>/dev/null || true)"
    if [[ "${state}" == "exited" ]]; then
      exit_code="$(docker inspect --format '{{.State.ExitCode}}' "${container}")"
      [[ "${exit_code}" != "0" ]] || fail "incomplete recovery exited successfully"
      return
    fi
    sleep 1
  done
  fail "incomplete recovery did not fail closed"
}

wait_for_negative_promotion() {
  local container=$1
  local attempt value
  for attempt in $(seq 1 120); do
    value="$(docker exec "${container}" psql --username=postgres --dbname=hormuz \
      --tuples-only --no-align --command 'SELECT pg_is_in_recovery()' 2>/dev/null \
      | tr -d '[:space:]' || true)"
    if [[ "${value}" == "f" ]]; then return; fi
    sleep 1
  done
  fail "partial recovery target did not promote for admission rejection"
}

assert_partial_state_rejected() {
  local container=$1
  local host="partial-postgres"
  local env_file="${SECRET_ROOT}/partial-state-probe.env"
  write_state_probe_env_file "${host}" "${env_file}"
  if docker run --rm --platform linux/amd64 --network "${NEGATIVE_NETWORK}" \
    --read-only --tmpfs /tmp:rw,noexec,nosuid,size=64m \
    --cap-drop ALL --security-opt no-new-privileges \
    --env-file "${env_file}" \
    --volume "${RECOVERY_INPUTS}/config/hormuz.json:/etc/hormuz-recovery/state-config.json:ro" \
    --volume "${DR_ROOT}/state_probe.py:/opt/hormuz-proof/state_probe.py:ro" \
    --entrypoint /opt/hormuz/bin/python "${HORMUZ_IMAGE}" \
    -I /opt/hormuz-proof/state_probe.py snapshot >/dev/null 2>&1; then
    fail "partial recovery passed Hormuz state admission"
  fi
  rm -f -- "${env_file}"
  local rows
  rows="$(docker exec "${container}" psql --username=postgres --dbname=hormuz \
    --tuples-only --no-align --command 'SELECT COUNT(*) FROM hormuz.policy_tenants' \
    | tr -d '[:space:]')"
  [[ "${rows}" == "0" ]] || fail "partial recovery fixture was not actually partial"
}

build_admission_input() {
  local output=$1
  local source_snapshot=$2
  local recovered_snapshot=$3
  local checkpoint=$4
  local custody_verified=$5
  python3 - "${output}" "${source_snapshot}" "${recovered_snapshot}" "${checkpoint}" \
    "${CONFIG_FINGERPRINT}" "${RECOVERED_CONFIG_FINGERPRINT}" \
    "${SECRET_GENERATION_FINGERPRINT}" "${RECOVERED_SECRET_GENERATION_FINGERPRINT}" \
    "${CONFIG_MARKER_AT}" "${SECRET_MARKER_AT}" "${custody_verified}" <<'PY'
import json
from pathlib import Path
import sys
(
    output,
    source_path,
    recovered_path,
    checkpoint_path,
    source_config_fingerprint,
    recovered_config_fingerprint,
    source_secret_fingerprint,
    recovered_secret_fingerprint,
    config_marker,
    secret_marker,
    custody_verified,
) = sys.argv[1:]
value = {
    "source_snapshot": json.load(open(source_path, encoding="utf-8")),
    "recovered_snapshot": json.load(open(recovered_path, encoding="utf-8")),
    "current_checkpoint": json.load(open(checkpoint_path, encoding="utf-8")),
    "configuration": {
        "source_fingerprint": source_config_fingerprint,
        "recovered_fingerprint": recovered_config_fingerprint,
        "latest_recovered_committed_marker_at": config_marker,
    },
    "secret_envelope": {
        "source_fingerprint": source_secret_fingerprint,
        "recovered_fingerprint": recovered_secret_fingerprint,
        "latest_recovered_committed_marker_at": secret_marker,
    },
    "custody_key_canary_verified": custody_verified == "true",
}
Path(output).write_text(json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
PY
}

expect_admission_denied() {
  local input=$1
  local output=$2
  if python3 "${ROOT}/tools/verify_disaster_recovery_reference.py" admit \
    --input "${input}" --output "${output}" >/dev/null 2>&1; then
    fail "negative recovery input passed admission"
  fi
  [[ ! -e "${output}" ]] || fail "denied recovery wrote an admission artifact"
}

provider_request_count() {
  timeout 15s kubectl --namespace hormuz-dependencies exec deployment/fake-provider -- \
    /opt/hormuz/bin/python -I -c \
    'import json; from urllib.request import urlopen; print(json.load(urlopen("http://127.0.0.1:8090/stats",timeout=3))["requests"])'
}

run_recovery_probe() {
  local command=$1
  local status=$2
  local output=$3
  shift 3
  timeout 60s kubectl --namespace hormuz-ingress exec pod/hormuz-disaster-recovery-probe \
    --container probe -- /opt/hormuz/bin/python -I \
    /opt/hormuz-proof/probe.py "${command}" \
    --target http://hormuz-hormuz.hormuz-system.svc.cluster.local:8787 \
    --expected-status "${status}" "$@" >"${output}" \
    || fail "recovered gateway probe failed: ${command}"
}

[[ "${HORMUZ_DISASTER_RECOVERY_PROOF_ACK:-}" == "${PROOF_ACK}" ]] \
  || fail "set HORMUZ_DISASTER_RECOVERY_PROOF_ACK=${PROOF_ACK}"
[[ -n "${EVIDENCE_DIR}" ]] || fail "HORMUZ_DISASTER_RECOVERY_EVIDENCE_DIR is required"
[[ "${SOURCE_COMMIT}" =~ ^[0-9a-f]{40}$ ]] || fail "HORMUZ_SOURCE_COMMIT must be an exact commit"
[[ ! -e "${EVIDENCE_DIR}" && ! -L "${EVIDENCE_DIR}" ]] || fail "evidence output already exists"
for name in "${SOURCE_CLUSTER}" "${RECOVERY_CLUSTER}"; do
  [[ "${name}" =~ ^[a-z0-9]([-a-z0-9]*[a-z0-9])?$ && "${#name}" -le 63 ]] \
    || fail "cluster name must be a DNS label"
done
[[ "${SOURCE_CLUSTER}" != "${RECOVERY_CLUSTER}" ]] || fail "source and recovery clusters must differ"
[[ "$(uname -s)" == "Linux" ]] || fail "the reference rehearsal requires Linux"
[[ "$(uname -m)" == "x86_64" || "$(uname -m)" == "amd64" ]] \
  || fail "the reference rehearsal requires native AMD64"
for command in base64 curl docker grep install openssl python3 sed sha256sum tar timeout; do
  command -v "${command}" >/dev/null 2>&1 || fail "${command} is unavailable"
done
docker_platform="$(docker info --format '{{.OSType}}/{{.Architecture}}')"
[[ "${docker_platform}" == "linux/x86_64" || "${docker_platform}" == "linux/amd64" ]] \
  || fail "the Docker daemon is not native linux/amd64"

WORK_ROOT="$(mktemp -d "${RUNNER_TEMP:-/tmp}/hormuz-disaster-recovery.XXXXXX")"
SECRET_ROOT="${WORK_ROOT}/secrets"
ARTIFACT_ROOT="${WORK_ROOT}/artifacts"
RECOVERY_INPUTS="${WORK_ROOT}/recovery-inputs"
KUBECONFIG="${WORK_ROOT}/kubeconfig"
mkdir -p "${WORK_ROOT}/bin" "${SECRET_ROOT}" "${ARTIFACT_ROOT}" \
  "${RECOVERY_INPUTS}/base" "${RECOVERY_INPUTS}/wal" \
  "${RECOVERY_INPUTS}/empty-wal" "${RECOVERY_INPUTS}/config" \
  "${WORK_ROOT}/negative" "${WORK_ROOT}/chart"
chmod 0700 "${WORK_ROOT}" "${SECRET_ROOT}" "${ARTIFACT_ROOT}" "${RECOVERY_INPUTS}"
chmod 0700 "${RECOVERY_INPUTS}/config"
mkdir --mode=0700 -- "${EVIDENCE_DIR}"
export KUBECONFIG
export PATH="${WORK_ROOT}/bin:${PATH}"

download_and_verify \
  "https://github.com/kubernetes-sigs/kind/releases/download/${KIND_VERSION}/kind-linux-amd64" \
  "${WORK_ROOT}/bin/kind" "${KIND_SHA256}"
download_and_verify \
  "https://dl.k8s.io/release/${KUBECTL_VERSION}/bin/linux/amd64/kubectl" \
  "${WORK_ROOT}/bin/kubectl" "${KUBECTL_SHA256}"
download_and_verify \
  "https://get.helm.sh/helm-${HELM_VERSION}-linux-amd64.tar.gz" \
  "${WORK_ROOT}/helm.tgz" "${HELM_SHA256}"
download_and_verify \
  "https://helm.cilium.io/cilium-${CILIUM_VERSION}.tgz" \
  "${WORK_ROOT}/cilium.tgz" "${CILIUM_CHART_SHA256}"
download_and_verify \
  "https://raw.githubusercontent.com/cloudnative-pg/cloudnative-pg/release-1.30/releases/cnpg-1.30.0.yaml" \
  "${WORK_ROOT}/cnpg.yaml" "${CNPG_MANIFEST_SHA256}"
chmod 0755 "${WORK_ROOT}/bin/kind" "${WORK_ROOT}/bin/kubectl"
tar -xzf "${WORK_ROOT}/helm.tgz" -C "${WORK_ROOT}"
install -m 0755 "${WORK_ROOT}/linux-amd64/helm" "${WORK_ROOT}/bin/helm"
python3 "${ROOT}/tools/verify_postgres_ha_reference.py" prepare-operator-manifest \
  --input "${WORK_ROOT}/cnpg.yaml" --output "${WORK_ROOT}/cnpg-pinned.yaml" >/dev/null

if kind get clusters | grep -Fxq "${SOURCE_CLUSTER}" || kind get clusters | grep -Fxq "${RECOVERY_CLUSTER}"; then
  fail "a disposable disaster-recovery cluster name already exists"
fi

for secret_name in \
  postgres-superuser-password postgres-owner-password runtime-password \
  policy-control-password custody-control-password custody-executor-password \
  ingress-credential identity-token bob-token openai-api-key anthropic-api-key \
  openbao-root-token; do
  write_random_hex_secret "${SECRET_ROOT}/${secret_name}"
done
chmod 0600 "${SECRET_ROOT}"/*

start_openbao

# Build the pre-disaster source on the exact #104 CloudNativePG reference.
ACTIVE_CLUSTER="${SOURCE_CLUSTER}"
python3 - "${HA_ROOT}/kind.yaml" "${WORK_ROOT}/kind-source.yaml" \
  "${RECOVERY_INPUTS}" <<'PY'
import json
from pathlib import Path
import sys

source_path, output_path, recovery_path = map(Path, sys.argv[1:])
source = source_path.read_text(encoding="utf-8")
lines = source.splitlines(keepends=True)
marker = "  - role: control-plane\n"
if lines.count(marker) != 1:
    raise SystemExit("source_kind_control_plane_invalid")
index = lines.index(marker)
if index + 1 >= len(lines) or not lines[index + 1].startswith("    image: kindest/node:"):
    raise SystemExit("source_kind_image_invalid")
path = recovery_path.resolve()
if not path.is_dir() or path.is_symlink():
    raise SystemExit("source_recovery_inputs_invalid")
lines[index + 2:index + 2] = [
    "    extraMounts:\n",
    f"      - hostPath: {json.dumps(str(path))}\n",
    "        containerPath: /hormuz-dr-artifacts\n",
    "        readOnly: false\n",
]
output_path.write_text("".join(lines), encoding="utf-8")
PY
kind create cluster --name "${SOURCE_CLUSTER}" --config "${WORK_ROOT}/kind-source.yaml" \
  --kubeconfig "${KUBECONFIG}" >/dev/null
CLUSTER_CREATED=1
install_cilium

mapfile -t source_workers < <(kubectl get nodes \
  --selector='!node-role.kubernetes.io/control-plane' --output=name \
  | sed 's#^node/##' | sort)
[[ "${#source_workers[@]}" -eq 5 ]] || fail "source Kind worker topology invalid"
for node in "${source_workers[@]:0:3}"; do
  kubectl label node "${node}" io.hormuz.postgres-node=true >/dev/null
  kubectl taint node "${node}" io.hormuz/postgres-node=true:NoSchedule >/dev/null
done

kubectl apply --server-side --force-conflicts \
  --filename "${WORK_ROOT}/cnpg-pinned.yaml" >/dev/null
kubectl wait --for=condition=Established crd/clusters.postgresql.cnpg.io \
  --timeout=5m >/dev/null
wait_for_deployment cnpg-system cnpg-controller-manager 5m
operator_image="$(kubectl --namespace cnpg-system get deployment cnpg-controller-manager \
  --output=jsonpath='{.spec.template.spec.containers[0].image}')"
[[ "${operator_image}" == "${CNPG_OPERATOR_IMAGE}" ]] \
  || fail "CloudNativePG operator image is not the exact pinned manifest"
create_namespaces

kubectl --namespace hormuz-dependencies create secret generic hormuz-postgres-superuser \
  --type=kubernetes.io/basic-auth --from-literal=username=postgres \
  --from-file="password=${SECRET_ROOT}/postgres-superuser-password" >/dev/null
kubectl --namespace hormuz-dependencies patch secret hormuz-postgres-superuser \
  --type=merge --patch '{"immutable":true}' >/dev/null
kubectl --namespace hormuz-dependencies create secret generic hormuz-postgres-owner \
  --type=kubernetes.io/basic-auth --from-literal=username=hormuz_owner \
  --from-file="password=${SECRET_ROOT}/postgres-owner-password" >/dev/null
kubectl --namespace hormuz-dependencies patch secret hormuz-postgres-owner \
  --type=merge --patch '{"immutable":true}' >/dev/null
SOURCE_BACKUP_CIDR="$(kubectl get nodes \
  --selector='node-role.kubernetes.io/control-plane' \
  --output=jsonpath='{.items[0].spec.podCIDR}')"
python3 - "${HA_ROOT}/cluster.yaml" "${WORK_ROOT}/source-postgres.yaml" \
  "${SOURCE_BACKUP_CIDR}" <<'PY'
from ipaddress import ip_network
from pathlib import Path
import sys

source_path, output_path = map(Path, sys.argv[1:3])
network = ip_network(sys.argv[3], strict=True)
allowed = ip_network("10.244.0.0/16")
if network.version != 4 or network.prefixlen < 24 or not network.subnet_of(allowed):
    raise SystemExit("source_backup_cidr_invalid")
source = source_path.read_text(encoding="utf-8")
needle = "  postgresql:\n    parameters:\n"
if source.count(needle) != 1:
    raise SystemExit("source_postgresql_manifest_invalid")
replacement = (
    "  postgresql:\n"
    "    pg_hba:\n"
    f"      - hostssl replication postgres {network} scram-sha-256\n"
    "    parameters:\n"
)
output_path.write_text(source.replace(needle, replacement), encoding="utf-8")
PY
kubectl apply --filename "${WORK_ROOT}/source-postgres.yaml" >/dev/null
wait_for_cnpg_ready

source_topology="$(kubectl --namespace hormuz-dependencies get pods \
  --selector='cnpg.io/cluster=hormuz-postgres' --output=json)"
python3 - "${source_topology}" "${POSTGRES_IMAGE}" <<'PY'
import json
import sys
items = json.loads(sys.argv[1])["items"]
if len(items) != 3 or len({item["spec"]["nodeName"] for item in items}) != 3:
    raise SystemExit("source_postgresql_topology_invalid")
for item in items:
    container = next(value for value in item["spec"]["containers"] if value["name"] == "postgres")
    if container["image"] != sys.argv[2]:
        raise SystemExit("source_postgresql_image_invalid")
PY

write_dsns "hormuz-postgres-rw.hormuz-dependencies.svc.cluster.local"
create_immutable_configmap hormuz-system hormuz-postgres-ha-bootstrap \
  --from-file="bootstrap.py=${HA_ROOT}/bootstrap.py"
create_immutable_secret hormuz-system hormuz-postgres-ha-bootstrap \
  --from-file="postgres-migration-dsn=${SECRET_ROOT}/postgres-migration-dsn" \
  --from-file="runtime-password=${SECRET_ROOT}/runtime-password" \
  --from-file="policy-control-password=${SECRET_ROOT}/policy-control-password" \
  --from-file="custody-control-password=${SECRET_ROOT}/custody-control-password" \
  --from-file="custody-executor-password=${SECRET_ROOT}/custody-executor-password"
kubectl apply --filename "${HA_ROOT}/bootstrap-job.yaml" >/dev/null
wait_for_job_complete hormuz-system hormuz-postgres-ha-bootstrap
kubectl --namespace hormuz-system logs job/hormuz-postgres-ha-bootstrap \
  >"${ARTIFACT_ROOT}/bootstrap.json"
python3 - "${ARTIFACT_ROOT}/bootstrap.json" <<'PY'
import json
import sys
value = json.load(open(sys.argv[1], encoding="utf-8"))
if value.get("schema_complete") is not True or value.get("restricted_login_roles") != 4:
    raise SystemExit("source_bootstrap_invalid")
PY
kubectl --namespace hormuz-system delete job/hormuz-postgres-ha-bootstrap \
  secret/hormuz-postgres-ha-bootstrap --wait=true >/dev/null

# Stream WAL before the physical backup so every post-backup commit is recoverable.
SOURCE_PRIMARY="$(kubectl --namespace hormuz-dependencies get cluster hormuz-postgres \
  --output=jsonpath='{.status.currentPrimary}')"
[[ "${SOURCE_PRIMARY}" =~ ^hormuz-postgres-[0-9]+$ ]] \
  || fail "source PostgreSQL primary identity invalid"
wait_for_pod hormuz-dependencies "${SOURCE_PRIMARY}" 5m
SOURCE_PRIMARY_IP="$(kubectl --namespace hormuz-dependencies get \
  "pod/${SOURCE_PRIMARY}" --output=jsonpath='{.status.podIP}')"
python3 - "${SOURCE_PRIMARY_IP}" <<'PY'
from ipaddress import ip_address
import sys
value = ip_address(sys.argv[1])
if value.version != 4 or not value.is_private:
    raise SystemExit("source_primary_ip_invalid")
PY
host_uid="$(id -u)"
host_gid="$(id -g)"
create_immutable_configmap hormuz-dependencies hormuz-dr-source-backup \
  --from-literal="source-host=${SOURCE_PRIMARY_IP}"
kubectl apply --filename "${DR_ROOT}/source-backup.yaml" >/dev/null
wait_for_source_backup_receiver
kubectl --namespace hormuz-dependencies patch job/hormuz-dr-base-backup \
  --type=merge --patch '{"spec":{"suspend":false}}' >/dev/null
wait_for_job_complete hormuz-dependencies hormuz-dr-base-backup
BASE_BACKUP_COMPLETED_AT="$(utc_now)"

source_sql "SELECT pg_create_restore_point('hormuz_dr_partial')" >/dev/null
PARTIAL_WAL_FILE="$(archive_current_wal)"

install_state_probe "${DR_ROOT}/hormuz.json"
run_state_probe seed "${ARTIFACT_ROOT}/source-seed.json"
python3 - "${ARTIFACT_ROOT}/source-seed.json" \
  "${RECOVERY_INPUTS}/source-snapshot.json" \
  "${RECOVERY_INPUTS}/stale-checkpoint.json" \
  "${RECOVERY_INPUTS}/current-checkpoint.json" <<'PY'
import json
from pathlib import Path
import sys
value = json.load(open(sys.argv[1], encoding="utf-8"))
if value.get("schema_id") != "hormuz.disaster-recovery-seed":
    raise SystemExit("source_seed_invalid")
for path, key in zip(sys.argv[2:], ("snapshot", "stale_checkpoint", "current_checkpoint"), strict=True):
    Path(path).write_text(json.dumps(value[key], sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
PY

source_sql "SELECT pg_create_restore_point('hormuz_dr_final')" >/dev/null
FINAL_WAL_FILE="$(archive_current_wal)"
[[ "${PARTIAL_WAL_FILE}" != "${FINAL_WAL_FILE}" ]] \
  || fail "partial and final recovery points did not span distinct archived WAL"

kubectl --namespace hormuz-dependencies delete pod/hormuz-dr-wal-receiver \
  --grace-period=15 --wait=true >/dev/null
kubectl --namespace hormuz-dependencies delete job/hormuz-dr-base-backup \
  configmap/hormuz-dr-source-backup --wait=true >/dev/null
docker run --rm --user root --entrypoint bash \
  --volume "${RECOVERY_INPUTS}:/recovery:rw" "${POSTGRES_IMAGE}" -ceu '
    chown -R "$1:$2" /recovery/base /recovery/wal
    chmod -R u+rwX,go-rwx /recovery/base /recovery/wal
  ' bash "${host_uid}" "${host_gid}" >/dev/null
docker run --rm --platform linux/amd64 --user "${host_uid}:${host_gid}" \
  --volume "${RECOVERY_INPUTS}:/recovery:ro" \
  --entrypoint pg_verifybackup "${POSTGRES_IMAGE}" /recovery/base >/dev/null

CONFIG_MARKER_AT="$(utc_now)"
SECRET_MARKER_AT="${CONFIG_MARKER_AT}"
CONFIG_FINGERPRINT="sha256:$(sha256sum "${DR_ROOT}/hormuz.json" | awk '{print $1}')"
install -m 0600 "${DR_ROOT}/hormuz.json" \
  "${RECOVERY_INPUTS}/config/hormuz.json"
build_secret_generation_manifest "${RECOVERY_INPUTS}/secret-generation.json" \
  "${SECRET_MARKER_AT}"
SECRET_GENERATION_FINGERPRINT="sha256:$(sha256sum \
  "${RECOVERY_INPUTS}/secret-generation.json" | awk '{print $1}')"

BASE_BACKUP_SHA256="sha256:$(sha256_tree "${RECOVERY_INPUTS}/base")"
BACKUP_MANIFEST_SHA256="sha256:$(sha256sum \
  "${RECOVERY_INPUTS}/base/backup_manifest" | awk '{print $1}')"
WAL_ARCHIVE_SHA256="sha256:$(sha256_tree "${RECOVERY_INPUTS}/wal")"
WAL_SEGMENT_COUNT="$(python3 - "${RECOVERY_INPUTS}/wal" <<'PY'
from pathlib import Path
import re
import sys
pattern = re.compile(r"[0-9A-F]{24}")
print(sum(1 for item in Path(sys.argv[1]).iterdir() if item.is_file() and pattern.fullmatch(item.name)))
PY
)"
[[ "${WAL_SEGMENT_COUNT}" -ge 2 ]] || fail "continuous WAL archive is incomplete"

FAILURE_INJECTION_AT="$(utc_now)"
kind delete cluster --name "${SOURCE_CLUSTER}" >/dev/null
CLUSTER_CREATED=0
ACTIVE_CLUSTER=""
INCIDENT_DETECTED_AT="$(utc_now)"
INCIDENT_DECLARED_AT="$(utc_now)"
AUTHORIZED_RECOVERY_STARTED_AT="$(utc_now)"

# Restore into a separate Kind cluster. The backup path is read-only inside the
# recovery control-plane node; neither the gateway runtime nor Helm can mutate it.
python3 - "${DR_ROOT}/kind-recovery.yaml.tmpl" \
  "${WORK_ROOT}/kind-recovery.yaml" "${RECOVERY_INPUTS}" <<'PY'
from pathlib import Path
import sys
source = Path(sys.argv[1]).read_text(encoding="utf-8")
if source.count("__HORMUZ_DR_INPUTS__") != 1:
    raise SystemExit("recovery_kind_template_invalid")
path = Path(sys.argv[3]).resolve()
if not path.is_dir() or path.is_symlink():
    raise SystemExit("recovery_inputs_path_invalid")
Path(sys.argv[2]).write_text(source.replace("__HORMUZ_DR_INPUTS__", str(path)), encoding="utf-8")
PY
ACTIVE_CLUSTER="${RECOVERY_CLUSTER}"
kind create cluster --name "${RECOVERY_CLUSTER}" \
  --config "${WORK_ROOT}/kind-recovery.yaml" --kubeconfig "${KUBECONFIG}" >/dev/null
CLUSTER_CREATED=1
install_cilium
create_namespaces

create_immutable_configmap hormuz-dependencies fake-provider-v1 \
  --from-file="fake-provider.py=${FIXTURE_ROOT}/fake-provider.py"
kubectl apply --filename "${FIXTURE_ROOT}/fake-provider.yaml" >/dev/null
wait_for_deployment hormuz-dependencies fake-provider 5m
wait_for_deployment hormuz-dependencies forbidden-provider 5m
[[ "$(provider_request_count)" == "0" ]] || fail "recovery provider was not clean"

kubectl --namespace hormuz-dependencies create secret generic \
  hormuz-recovered-postgres-superuser --type=kubernetes.io/basic-auth \
  --from-literal=username=postgres \
  --from-file="password=${SECRET_ROOT}/postgres-superuser-password" >/dev/null
kubectl --namespace hormuz-dependencies patch secret \
  hormuz-recovered-postgres-superuser --type=merge \
  --patch '{"immutable":true}' >/dev/null
RESTORE_STARTED_AT="$(utc_now)"
kubectl apply --filename "${DR_ROOT}/recovered-postgres.yaml" >/dev/null
wait_for_statefulset hormuz-dependencies recovered-postgres 10m
RECOVERED_DATABASE_READY_AT="$(utc_now)"
recovery_state="$(kubectl --namespace hormuz-dependencies exec \
  statefulset/recovered-postgres --container postgres -- \
  psql --host=/tmp --username=postgres --dbname=hormuz --tuples-only --no-align \
  --command 'SELECT pg_is_in_recovery()' | tr -d '[:space:]')"
[[ "${recovery_state}" == "f" ]] || fail "isolated PostgreSQL target was not promoted"

write_dsns "recovered-postgres.hormuz-dependencies.svc.cluster.local"
RECOVERED_CONFIG_FINGERPRINT="sha256:$(sha256sum \
  "${RECOVERY_INPUTS}/config/hormuz.json" | awk '{print $1}')"
install_state_probe "${RECOVERY_INPUTS}/config/hormuz.json"
run_state_probe snapshot "${ARTIFACT_ROOT}/recovered-snapshot.json"

build_secret_generation_manifest "${ARTIFACT_ROOT}/recovered-secret-generation.json" \
  "${SECRET_MARKER_AT}"
RECOVERED_SECRET_GENERATION_FINGERPRINT="sha256:$(sha256sum \
  "${ARTIFACT_ROOT}/recovered-secret-generation.json" | awk '{print $1}')"
[[ "${RECOVERED_SECRET_GENERATION_FINGERPRINT}" == "${SECRET_GENERATION_FINGERPRINT}" ]] \
  || fail "recovered secret generation fingerprint mismatch"
custody_canary_verify || fail "customer custody key canary unavailable during admission"

build_admission_input "${ARTIFACT_ROOT}/admission-input.json" \
  "${RECOVERY_INPUTS}/source-snapshot.json" \
  "${ARTIFACT_ROOT}/recovered-snapshot.json" \
  "${RECOVERY_INPUTS}/current-checkpoint.json" true
python3 "${ROOT}/tools/verify_disaster_recovery_reference.py" admit \
  --input "${ARTIFACT_ROOT}/admission-input.json" \
  --output "${ARTIFACT_ROOT}/admission.json" >/dev/null
ADMISSION_PASSED_AT="$(utc_now)"

# Every required negative path runs while traffic is still unpromoted. The
# fake provider remains at zero requests throughout these checks.
NEGATIVE_NETWORK="hormuz-dr-negative-${RANDOM}${RANDOM}"
docker network create --label "${DISPOSABLE_LABEL}=true" "${NEGATIVE_NETWORK}" >/dev/null
negative_provider_before="$(provider_request_count)"
[[ "${negative_provider_before}" == "0" ]] || fail "negative path provider baseline invalid"

missing_data="$(prepare_negative_recovery missing hormuz_dr_final /recovery/empty-wal)"
start_negative_recovery missing "${missing_data}"
missing_container="${LAST_NEGATIVE_CONTAINER}"
wait_for_negative_failure "${missing_container}"

corrupt_root="${WORK_ROOT}/negative/corrupt"
mkdir -p "${corrupt_root}"
docker run --rm --user root --entrypoint bash \
  --volume "${RECOVERY_INPUTS}:/recovery:ro" \
  --volume "${corrupt_root}:/corrupt:rw" "${POSTGRES_IMAGE}" -ceu '
    cp -a /recovery/base /corrupt/base
    printf "corrupt" >> /corrupt/base/global/pg_control
  ' >/dev/null
if docker run --rm --platform linux/amd64 --user "${host_uid}:${host_gid}" \
  --volume "${corrupt_root}:/corrupt:ro" --entrypoint pg_verifybackup \
  "${POSTGRES_IMAGE}" /corrupt/base >/dev/null 2>&1; then
  fail "corrupted backup passed pg_verifybackup"
fi

partial_data="$(prepare_negative_recovery partial hormuz_dr_partial /recovery/wal)"
start_negative_recovery partial "${partial_data}"
partial_container="${LAST_NEGATIVE_CONTAINER}"
wait_for_negative_promotion "${partial_container}"
assert_partial_state_rejected "${partial_container}"

build_admission_input "${ARTIFACT_ROOT}/stale-admission-input.json" \
  "${RECOVERY_INPUTS}/source-snapshot.json" \
  "${ARTIFACT_ROOT}/recovered-snapshot.json" \
  "${RECOVERY_INPUTS}/stale-checkpoint.json" true
expect_admission_denied "${ARTIFACT_ROOT}/stale-admission-input.json" \
  "${ARTIFACT_ROOT}/stale-admission.json"

docker pause "${OPENBAO_CONTAINER}" >/dev/null
if custody_canary_verify; then
  fail "paused customer custody service remained available"
fi
build_admission_input "${ARTIFACT_ROOT}/custody-unavailable-input.json" \
  "${RECOVERY_INPUTS}/source-snapshot.json" \
  "${ARTIFACT_ROOT}/recovered-snapshot.json" \
  "${RECOVERY_INPUTS}/current-checkpoint.json" false
expect_admission_denied "${ARTIFACT_ROOT}/custody-unavailable-input.json" \
  "${ARTIFACT_ROOT}/custody-unavailable-admission.json"
docker unpause "${OPENBAO_CONTAINER}" >/dev/null
custody_canary_verify || fail "customer custody service did not recover after negative check"

python3 - "${ARTIFACT_ROOT}/admission-input.json" \
  "${ARTIFACT_ROOT}/coordination-failed-input.json" <<'PY'
import json
from pathlib import Path
import sys
value = json.load(open(sys.argv[1], encoding="utf-8"))
for name in ("source_snapshot", "recovered_snapshot"):
    value[name]["admission_facts"]["unresolved_coordination_barriers"] = 1
Path(sys.argv[2]).write_text(json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
PY
expect_admission_denied "${ARTIFACT_ROOT}/coordination-failed-input.json" \
  "${ARTIFACT_ROOT}/coordination-failed-admission.json"

python3 - "${ARTIFACT_ROOT}/admission-input.json" \
  "${ARTIFACT_ROOT}/cross-tenant-input.json" <<'PY'
import json
from pathlib import Path
import sys
value = json.load(open(sys.argv[1], encoding="utf-8"))
value["recovered_snapshot"]["organization_id"] = "kubernetes-proof-isolation-tenant"
Path(sys.argv[2]).write_text(json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
PY
expect_admission_denied "${ARTIFACT_ROOT}/cross-tenant-input.json" \
  "${ARTIFACT_ROOT}/cross-tenant-admission.json"

negative_provider_after="$(provider_request_count)"
[[ "${negative_provider_after}" == "${negative_provider_before}" ]] \
  || fail "provider egress occurred during denied recovery paths"
REQUIRED_FAILURE_PATHS_PASSED_AT="$(utc_now)"

# Only the successful, fully validated environment receives the application
# release and traffic. The chart still consumes only an existing Secret DSN.
create_immutable_configmap hormuz-system hormuz-recovery-config-v1 \
  --from-file="hormuz.json=${RECOVERY_INPUTS}/config/hormuz.json"
create_immutable_secret hormuz-system hormuz-recovery-runtime-v1 \
  --from-file="postgres-runtime-dsn=${SECRET_ROOT}/postgres-runtime-dsn" \
  --from-file="alice-token=${SECRET_ROOT}/identity-token" \
  --from-file="bob-token=${SECRET_ROOT}/bob-token" \
  --from-file="ingress-credential=${SECRET_ROOT}/ingress-credential" \
  --from-file="openai-api-key=${SECRET_ROOT}/openai-api-key" \
  --from-file="anthropic-api-key=${SECRET_ROOT}/anthropic-api-key"
create_immutable_configmap hormuz-ingress hormuz-disaster-recovery-probe \
  --from-file="probe.py=${DR_ROOT}/probe.py"
create_immutable_secret hormuz-ingress hormuz-disaster-recovery-probe \
  --from-file="identity-token=${SECRET_ROOT}/identity-token" \
  --from-file="ingress-credential=${SECRET_ROOT}/ingress-credential"
kubectl apply --filename "${DR_ROOT}/probe-pod.yaml" >/dev/null
wait_for_pod hormuz-ingress hormuz-disaster-recovery-probe 10m

config_sha="sha256:$(sha256sum \
  "${RECOVERY_INPUTS}/config/hormuz.json" | awk '{print $1}')"
[[ "${config_sha}" == "${CONFIG_FINGERPRINT}" ]] \
  || fail "recovered runtime configuration fingerprint mismatch"
python3 "${ROOT}/tools/verify_helm_profile.py" validate-chart \
  --chart "${CHART_ROOT}" >/dev/null
helm lint "${CHART_ROOT}" --values "${DR_ROOT}/helm-values.yaml" \
  --set-string "configuration.sha256=${config_sha}" >/dev/null
helm package "${CHART_ROOT}" --destination "${WORK_ROOT}/chart" >/dev/null
chart_package="${WORK_ROOT}/chart/hormuz-0.1.0.tgz"
chart_sha256="$(sha256sum "${chart_package}" | awk '{print $1}')"
helm template hormuz "${CHART_ROOT}" --namespace hormuz-system \
  --values "${DR_ROOT}/helm-values.yaml" \
  --set-string "configuration.sha256=${config_sha}" \
  >"${ARTIFACT_ROOT}/rendered.yaml"
if grep -Eq 'kind: (Cluster|PostgreSQL|DatabaseRole)|postgresql.cnpg.io' \
  "${ARTIFACT_ROOT}/rendered.yaml"; then
  fail "Hormuz Helm chart attempted to install PostgreSQL"
fi

provider_before_promotion="$(provider_request_count)"
[[ "${provider_before_promotion}" == "0" ]] \
  || fail "provider request occurred before traffic promotion"
helm upgrade --install hormuz "${CHART_ROOT}" --namespace hormuz-system \
  --values "${DR_ROOT}/helm-values.yaml" \
  --set-string "configuration.sha256=${config_sha}" \
  --wait --timeout 10m >/dev/null
wait_for_deployment hormuz-system hormuz-hormuz 10m
gateway_topology="$(kubectl --namespace hormuz-system get pods \
  --selector='app.kubernetes.io/instance=hormuz,app.kubernetes.io/component=gateway' \
  --output=json)"
python3 - "${gateway_topology}" <<'PY'
import json
import sys
items = json.loads(sys.argv[1])["items"]
nodes = {item["spec"]["nodeName"] for item in items}
if len(items) != 2 or len(nodes) != 2:
    raise SystemExit("recovered_gateway_topology_invalid")
if any(item["metadata"]["annotations"].get("io.hormuz/runtime-secret-revision") != "disaster-recovery-generation-v1" for item in items):
    raise SystemExit("recovered_gateway_generation_invalid")
PY
run_recovery_probe ready 200 "${ARTIFACT_ROOT}/recovered-ready.json"
RECOVERED_ENVIRONMENT_READY_AT="$(utc_now)"
[[ "$(provider_request_count)" == "${provider_before_promotion}" ]] \
  || fail "provider egress occurred while the recovered environment was isolated"

TRAFFIC_PROMOTED_AT="$(utc_now)"
run_recovery_probe request 200 "${ARTIFACT_ROOT}/first-governed-request.json" \
  --expected-policy allowed+capped+redacted
FIRST_GOVERNED_REQUEST_AT="$(utc_now)"
provider_after_first_request="$(provider_request_count)"
[[ "${provider_after_first_request}" == "1" ]] \
  || fail "provider replay or missing first governed request detected"

detection_ms="$(milliseconds_between "${FAILURE_INJECTION_AT}" "${INCIDENT_DETECTED_AT}")"
incident_declaration_ms="$(milliseconds_between "${INCIDENT_DETECTED_AT}" "${INCIDENT_DECLARED_AT}")"
recovery_authorization_ms="$(milliseconds_between "${INCIDENT_DECLARED_AT}" "${AUTHORIZED_RECOVERY_STARTED_AT}")"
recovery_environment_preparation_ms="$(milliseconds_between "${AUTHORIZED_RECOVERY_STARTED_AT}" "${RESTORE_STARTED_AT}")"
restore_replay_ms="$(milliseconds_between "${RESTORE_STARTED_AT}" "${RECOVERED_DATABASE_READY_AT}")"
admission_validation_ms="$(milliseconds_between "${RECOVERED_DATABASE_READY_AT}" "${ADMISSION_PASSED_AT}")"
failure_path_validation_ms="$(milliseconds_between "${ADMISSION_PASSED_AT}" "${REQUIRED_FAILURE_PATHS_PASSED_AT}")"
application_startup_ms="$(milliseconds_between "${REQUIRED_FAILURE_PATHS_PASSED_AT}" "${RECOVERED_ENVIRONMENT_READY_AT}")"
traffic_promotion_ms="$(milliseconds_between "${RECOVERED_ENVIRONMENT_READY_AT}" "${TRAFFIC_PROMOTED_AT}")"
first_request_ms="$(milliseconds_between "${TRAFFIC_PROMOTED_AT}" "${FIRST_GOVERNED_REQUEST_AT}")"

python3 - \
  "${ARTIFACT_ROOT}/observations.json" "${SOURCE_COMMIT}" \
  "$(docker version --format '{{.Server.Version}}')" "${chart_sha256}" \
  "${FAILURE_INJECTION_AT}" "${INCIDENT_DETECTED_AT}" "${INCIDENT_DECLARED_AT}" \
  "${AUTHORIZED_RECOVERY_STARTED_AT}" "${RESTORE_STARTED_AT}" \
  "${RECOVERED_DATABASE_READY_AT}" "${ADMISSION_PASSED_AT}" \
  "${REQUIRED_FAILURE_PATHS_PASSED_AT}" "${RECOVERED_ENVIRONMENT_READY_AT}" \
  "${TRAFFIC_PROMOTED_AT}" "${FIRST_GOVERNED_REQUEST_AT}" "${detection_ms}" \
  "${incident_declaration_ms}" "${recovery_authorization_ms}" \
  "${recovery_environment_preparation_ms}" "${restore_replay_ms}" \
  "${admission_validation_ms}" "${failure_path_validation_ms}" \
  "${application_startup_ms}" "${traffic_promotion_ms}" "${first_request_ms}" \
  "${BASE_BACKUP_SHA256}" "${BACKUP_MANIFEST_SHA256}" \
  "${WAL_ARCHIVE_SHA256}" "${WAL_SEGMENT_COUNT}" "${BASE_BACKUP_COMPLETED_AT}" \
  "${ARTIFACT_ROOT}/admission.json" <<'PY'
import json
from pathlib import Path
import sys
(
    output, source_commit, docker_engine, chart_sha,
    failure, detected, declared, authorized, restore_started,
    database_ready, admission_passed, failure_paths_passed, ready, promoted, first_request,
    detection_ms, declaration_ms, authorization_ms, preparation_ms,
    restore_ms, admission_ms, failure_path_ms, startup_ms, promotion_ms, request_ms,
    base_sha, manifest_sha, wal_sha, wal_count, backup_completed,
    admission_path,
) = sys.argv[1:]
admission = json.load(open(admission_path, encoding="utf-8"))
denied = {
    "failure_observed": True,
    "admission_denied": True,
    "promotion_blocked": True,
    "provider_request_delta": 0,
}
value = {
    "source_commit": source_commit,
    "docker_engine": docker_engine,
    "helm_chart_sha256": chart_sha,
    "timestamps": {
        "failure_injection_at": failure,
        "incident_detected_at": detected,
        "incident_declared_at": declared,
        "authorized_recovery_execution_started_at": authorized,
        "restore_started_at": restore_started,
        "recovered_database_ready_at": database_ready,
        "admission_passed_at": admission_passed,
        "required_failure_paths_passed_at": failure_paths_passed,
        "recovered_environment_ready_for_promotion_at": ready,
        "traffic_promoted_at": promoted,
        "first_successful_governed_request_after_promotion_at": first_request,
    },
    "phase_durations_ms": {
        "detection": int(detection_ms),
        "incident_declaration": int(declaration_ms),
        "recovery_authorization": int(authorization_ms),
        "recovery_environment_preparation": int(preparation_ms),
        "restore_and_wal_replay": int(restore_ms),
        "admission_validation": int(admission_ms),
        "required_failure_path_validation": int(failure_path_ms),
        "application_startup": int(startup_ms),
        "traffic_promotion": int(promotion_ms),
        "first_governed_request": int(request_ms),
    },
    "backup": {
        "method": "physical_base_backup_plus_continuous_wal",
        "base_backup_sha256": base_sha,
        "backup_manifest_sha256": manifest_sha,
        "wal_archive_sha256": wal_sha,
        "wal_segment_count": int(wal_count),
        "base_backup_completed_at": backup_completed,
        "pg_verifybackup_passed": True,
        "backup_completed_before_failure": True,
        "named_restore_point_reached": True,
    },
    "retention_and_authority": {
        "base_backup_frequency_seconds": 86400,
        "wal_archive_continuous": True,
        "backup_retention_days": 35,
        "wal_retention_days": 35,
        "encryption_at_rest_required": True,
        "backup_writer_cannot_restore_or_promote": True,
        "runtime_cannot_backup_restore_or_promote": True,
        "restore_requires_authorized_operator": True,
        "monitor_backup_age_wal_lag_and_restore_tests": True,
        "expiry_never_shortens_immutable_audit_retention": True,
    },
    "state_classes": admission["state_classes"],
    "admission": {
        "source_state_manifest_sha256": admission["source_state_manifest_sha256"],
        "recovered_state_manifest_sha256": admission["recovered_state_manifest_sha256"],
        "gateway_replicas_ready": 2,
        "readiness_withheld_until_validation": True,
        "provider_requests_before_promotion": 0,
    },
    "failure_paths": {
        name: dict(denied)
        for name in (
            "missing_wal_archive",
            "corrupted_backup",
            "unavailable_custody_key",
            "stale_checkpoint",
            "partial_restore",
            "failed_coordination",
            "cross_tenant_access",
        )
    },
    "promotion": {
        "authorized_operator_promoted": True,
        "runtime_credential_cannot_promote": True,
        "first_governed_request_status": 200,
        "provider_requests_after_first_governed_request": 1,
        "automatic_provider_replays": 0,
        "rollback_target_preserved": True,
    },
}
Path(output).write_text(json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
PY

python3 "${ROOT}/tools/verify_disaster_recovery_reference.py" write-evidence \
  --observations "${ARTIFACT_ROOT}/observations.json" \
  --output "${EVIDENCE_DIR}/summary.json" >/dev/null
python3 "${ROOT}/tools/verify_disaster_recovery_reference.py" validate \
  --evidence "${EVIDENCE_DIR}/summary.json" >/dev/null
python3 "${ROOT}/tools/verify_helm_profile.py" assert-no-secrets \
  --artifact-root "${EVIDENCE_DIR}" --secret-root "${SECRET_ROOT}" >/dev/null
chmod 0600 "${EVIDENCE_DIR}/summary.json"

helm uninstall hormuz --namespace hormuz-system --wait >/dev/null
kind delete cluster --name "${RECOVERY_CLUSTER}" >/dev/null
CLUSTER_CREATED=0
ACTIVE_CLUSTER=""
printf 'verified disaster-recovery reference: rpo_limit_seconds=300 internal_rto_limit_seconds=3600\n'
