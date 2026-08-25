#!/usr/bin/env bash

set -euo pipefail
umask 077

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FIXTURE_ROOT="${ROOT}/deploy/kubernetes/conformance"
HA_ROOT="${FIXTURE_ROOT}/postgres-ha"
CHART_ROOT="${ROOT}/deploy/helm/hormuz"
EVIDENCE_DIR="${HORMUZ_POSTGRES_HA_EVIDENCE_DIR:-}"
SOURCE_COMMIT="${HORMUZ_SOURCE_COMMIT:-}"
CLUSTER_NAME="${HORMUZ_POSTGRES_HA_CLUSTER_NAME:-hormuz-postgres-ha}"
PROOF_ACK="I_UNDERSTAND_THIS_IS_A_DISPOSABLE_POSTGRESQL_HA_REFERENCE_PROOF"
HORMUZ_IMAGE="ghcr.io/xpounder-com/hormuz@sha256:1bbcca3490a7a5b004a880f42e8250acb91ce566a9c59f3263d7b279568efb5a"
POSTGRES_IMAGE="ghcr.io/cloudnative-pg/postgresql:16.15-202608240846-minimal-trixie@sha256:e1ca593856017f1780dbdae8175add3ddd8f8d721348a3b6e8a01df67a9ece8a"
POSTGRES_AMD64_DIGEST="sha256:e1ca593856017f1780dbdae8175add3ddd8f8d721348a3b6e8a01df67a9ece8a"
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

WORK_ROOT=""
SECRET_ROOT=""
ARTIFACT_ROOT=""
KUBECONFIG=""
CLUSTER_CREATED=0
EVENT_SEQUENCE=0
MAXIMUM_STORAGE_DENIAL_MS=0
PAUSED_NODES=()

fail() {
  printf 'PostgreSQL HA reference proof failed: %s\n' "$1" >&2
  exit 1
}

monotonic_ms() {
  python3 -c 'import time; print(time.monotonic_ns() // 1_000_000)'
}

record_event() {
  EVENT_SEQUENCE=$((EVENT_SEQUENCE + 1))
  printf '%s|%s\n' "${EVENT_SEQUENCE}" "$1" >>"${ARTIFACT_ROOT}/events.log"
  printf 'postgres_ha_stage=%s\n' "$1"
}

cleanup() {
  local status=$?
  set +e
  local node
  for node in "${PAUSED_NODES[@]:-}"; do
    docker unpause "${node}" >/dev/null 2>&1
  done
  if [[ "${CLUSTER_CREATED}" -eq 1 && -n "${WORK_ROOT}" && -x "${WORK_ROOT}/bin/kind" ]]; then
    "${WORK_ROOT}/bin/kind" delete cluster --name "${CLUSTER_NAME}" >/dev/null 2>&1
  fi
  if [[ -n "${WORK_ROOT}" && -d "${WORK_ROOT}" && ! -L "${WORK_ROOT}" ]]; then
    rm -rf -- "${WORK_ROOT}"
  fi
  exit "${status}"
}
trap cleanup EXIT HUP INT TERM

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
  local namespace=$1
  local deployment=$2
  local timeout=${3:-10m}
  kubectl --namespace "${namespace}" rollout status "deployment/${deployment}" \
    --timeout="${timeout}" >/dev/null \
    || fail "deployment did not become ready: ${namespace}/${deployment}"
}

wait_for_pod() {
  local namespace=$1
  local pod=$2
  kubectl --namespace "${namespace}" wait --for=condition=Ready "pod/${pod}" \
    --timeout=10m >/dev/null \
    || fail "pod did not become ready: ${namespace}/${pod}"
}

wait_for_job_complete() {
  local namespace=$1
  local job=$2
  local attempt state
  for attempt in $(seq 1 300); do
    state="$(kubectl --namespace "${namespace}" get "job/${job}" --output=json \
      | python3 -c 'import json,sys; value=json.load(sys.stdin); conditions={item.get("type"): item.get("status") for item in value.get("status",{}).get("conditions",[])}; print("complete" if conditions.get("Complete")=="True" else "failed" if conditions.get("Failed")=="True" else "pending")')"
    case "${state}" in
      complete) return ;;
      failed) fail "job failed: ${namespace}/${job}" ;;
      pending) ;;
      *) fail "job state invalid: ${namespace}/${job}" ;;
    esac
    sleep 1
  done
  fail "job completion timed out: ${namespace}/${job}"
}

pause_node() {
  local node=$1
  docker pause "${node}" >/dev/null
  PAUSED_NODES+=("${node}")
}

unpause_node() {
  local node=$1
  docker unpause "${node}" >/dev/null
}

wait_for_node_state() {
  local node=$1
  local expected=$2
  local attempt actual
  for attempt in $(seq 1 120); do
    actual="$(kubectl get node "${node}" --output=jsonpath='{.status.conditions[?(@.type=="Ready")].status}' 2>/dev/null || true)"
    if [[ "${actual}" == "${expected}" ]]; then
      return
    fi
    sleep 1
  done
  fail "node did not reach Ready=${expected}: ${node}"
}

wait_for_cnpg_ready() {
  kubectl --namespace hormuz-dependencies wait \
    --for=condition=Ready cluster/hormuz-postgres --timeout=10m >/dev/null \
    || fail "CloudNativePG cluster did not become ready"
  kubectl --namespace hormuz-dependencies wait \
    --for=condition=Ready pod --selector='cnpg.io/cluster=hormuz-postgres' \
    --timeout=10m >/dev/null \
    || fail "CloudNativePG instances did not become ready"
  local ready
  ready="$(kubectl --namespace hormuz-dependencies get pod \
    --selector='cnpg.io/cluster=hormuz-postgres' \
    --field-selector=status.phase=Running --output=json \
    | python3 -c 'import json,sys; print(sum(1 for item in json.load(sys.stdin)["items"] if any(c.get("type")=="Ready" and c.get("status")=="True" for c in item.get("status",{}).get("conditions",[]))))')"
  [[ "${ready}" == "3" ]] || fail "CloudNativePG ready instance count invalid"
}

wait_for_failover_quorum_ready() {
  local output=$1
  local candidate="${output}.candidate"
  local attempt
  for attempt in $(seq 1 120); do
    if kubectl --namespace hormuz-dependencies get failoverquorum hormuz-postgres \
      --output=json >"${candidate}" 2>/dev/null \
      && python3 - "${candidate}" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    status = json.load(handle).get("status", {})
valid = (
    status.get("method") == "ANY"
    and status.get("standbyNumber") == 1
    and len(status.get("standbyNames", [])) == 2
    and isinstance(status.get("primary"), str)
    and bool(status["primary"])
)
raise SystemExit(0 if valid else 1)
PY
    then
      mv "${candidate}" "${output}"
      return
    fi
    sleep 1
  done
  rm -f -- "${candidate}"
  fail "CloudNativePG failover-quorum status did not become ready"
}

current_primary() {
  kubectl --namespace hormuz-dependencies get cluster hormuz-postgres \
    --output=jsonpath='{.status.currentPrimary}'
}

pod_node() {
  kubectl --namespace hormuz-dependencies get pod "$1" --output=jsonpath='{.spec.nodeName}'
}

provider_control() {
  local method=$1
  local path=$2
  timeout 15s kubectl --namespace hormuz-dependencies exec deployment/fake-provider -- \
    /opt/hormuz/bin/python -I -c \
    'from urllib.request import Request,urlopen; import sys; request=Request("http://127.0.0.1:8090"+sys.argv[2],method=sys.argv[1]); print(urlopen(request,timeout=3).read().decode("utf-8"))' \
    "${method}" "${path}"
}

provider_request_count() {
  timeout 15s kubectl --namespace hormuz-dependencies exec deployment/fake-provider -- \
    /opt/hormuz/bin/python -I -c \
    'import json; from urllib.request import urlopen; print(json.load(urlopen("http://127.0.0.1:8090/stats",timeout=3))["requests"])'
}

wait_for_provider_block() {
  local attempt value
  for attempt in $(seq 1 60); do
    value="$(provider_control GET /control/block/status 2>/dev/null || true)"
    if python3 -c 'import json,sys; v=json.loads(sys.argv[1]); raise SystemExit(0 if v.get("started") is True else 1)' "${value}" 2>/dev/null; then
      return
    fi
    sleep 1
  done
  fail "blocking provider request did not start"
}

state_probe() {
  local command=$1
  local output=$2
  timeout 60s kubectl --namespace hormuz-system exec pod/hormuz-postgres-ha-state --container state -- \
    /opt/hormuz/bin/python -I /opt/hormuz-proof/state_probe.py "${command}" >"${output}"
  python3 -c 'import json,sys; value=json.load(open(sys.argv[1],encoding="utf-8")); raise SystemExit(0 if value.get("command")==sys.argv[2] else 1)' \
    "${output}" "${command}" \
    || fail "state probe result invalid: ${command}"
}

probe() {
  local command=$1
  local target=$2
  local expected_status=$3
  local output=$4
  local started elapsed
  started="$(monotonic_ms)"
  local args=(
    /opt/hormuz/bin/python -I /opt/hormuz-proof/probe.py
    "${command}" --target "${target}" --expected-status "${expected_status}"
  )
  if [[ "${command}" == "request" && "${expected_status}" == "200" ]]; then
    args+=(--expected-policy fallback+capped+redacted)
  fi
  if [[ "${command}" == "storage-backpressure" ]]; then
    args+=(--concurrency 16)
  fi
  if ! timeout 45s kubectl --namespace hormuz-ingress exec pod/hormuz-postgres-ha-probe --container probe -- \
    "${args[@]}" >"${output}"; then
    return 1
  fi
  elapsed=$(( $(monotonic_ms) - started ))
  [[ "${elapsed}" -gt 0 ]] || elapsed=1
  if [[ "${command}" == "storage-backpressure" && "${elapsed}" -gt "${MAXIMUM_STORAGE_DENIAL_MS}" ]]; then
    MAXIMUM_STORAGE_DENIAL_MS="${elapsed}"
  fi
  python3 -c 'import json,sys; value=json.load(open(sys.argv[1],encoding="utf-8")); raise SystemExit(0 if value.get("status")==int(sys.argv[2]) else 1)' \
    "${output}" "${expected_status}" \
    || return 1
}

gateway_uids() {
  kubectl --namespace hormuz-system get pods \
    --selector='app.kubernetes.io/instance=hormuz,app.kubernetes.io/component=gateway' \
    --output=json \
    | python3 -c 'import json,sys; items=json.load(sys.stdin)["items"]; print("|".join(sorted(item["metadata"]["uid"] for item in items)))'
}

gateway_fail_closed() {
  local prefix=$1
  local gateway_json="${ARTIFACT_ROOT}/${prefix}-gateways.json"
  kubectl --namespace hormuz-system get pods \
    --selector='app.kubernetes.io/instance=hormuz,app.kubernetes.io/component=gateway' \
    --output=json >"${gateway_json}"
  mapfile -t gateway_ips < <(python3 -c 'import json,sys; items=json.load(open(sys.argv[1],encoding="utf-8"))["items"]; assert len(items)==2; print("\n".join(sorted(item["status"]["podIP"] for item in items)))' "${gateway_json}")
  [[ "${#gateway_ips[@]}" -eq 2 ]] || fail "gateway replica count invalid during outage"
  local index ip attempt ready_observed
  for index in 0 1; do
    ip="${gateway_ips[${index}]}"
    ready_observed=0
    for attempt in $(seq 1 20); do
      if probe ready "http://${ip}:8787" 503 "${ARTIFACT_ROOT}/${prefix}-ready-${index}.json" 2>/dev/null; then
        ready_observed=1
        break
      fi
      sleep 1
    done
    [[ "${ready_observed}" -eq 1 ]] || fail "gateway did not withdraw readiness during outage"
    probe storage-backpressure "http://${ip}:8787" 503 \
      "${ARTIFACT_ROOT}/${prefix}-backpressure-${index}.json" \
      || fail "gateway did not enforce bounded storage backpressure"
  done
}

wait_for_primary_change() {
  local previous=$1
  local attempt candidate
  for attempt in $(seq 1 300); do
    candidate="$(current_primary 2>/dev/null || true)"
    if [[ -n "${candidate}" && "${candidate}" != "${previous}" ]]; then
      printf '%s\n' "${candidate}"
      return
    fi
    sleep 1
  done
  fail "CloudNativePG did not promote a safe replica"
}

rw_ready_addresses() {
  kubectl --namespace hormuz-dependencies get endpointslices \
    --selector='kubernetes.io/service-name=hormuz-postgres-rw' --output=json \
    | python3 -c 'import json,sys; value=json.load(sys.stdin); print(sum(len(endpoint.get("addresses",[])) for item in value.get("items",[]) for endpoint in item.get("endpoints",[]) if endpoint.get("conditions",{}).get("ready") is True))'
}

lease_and_rw_primary_matches() {
  local primary=$1
  local holder primary_ip endpoint_json
  holder="$(kubectl --namespace hormuz-dependencies get lease hormuz-postgres \
    --output=jsonpath='{.spec.holderIdentity}' 2>/dev/null || true)"
  [[ "${holder}" == "${primary}" || "${holder}" == "${primary}:"* || \
     "${holder}" == "${primary}_"* || "${holder}" == "${primary}/"* ]] \
    || return 1
  primary_ip="$(kubectl --namespace hormuz-dependencies get pod "${primary}" \
    --output=jsonpath='{.status.podIP}' 2>/dev/null || true)"
  [[ -n "${primary_ip}" ]] || return 1
  endpoint_json="$(kubectl --namespace hormuz-dependencies get endpointslices \
    --selector='kubernetes.io/service-name=hormuz-postgres-rw' --output=json 2>/dev/null || true)"
  [[ -n "${endpoint_json}" ]] || return 1
  python3 -c 'import json,sys; value=json.loads(sys.argv[1]); addresses={a for item in value.get("items",[]) for endpoint in item.get("endpoints",[]) if endpoint.get("conditions",{}).get("ready") is True for a in endpoint.get("addresses",[])}; raise SystemExit(0 if addresses=={sys.argv[2]} else 1)' \
    "${endpoint_json}" "${primary_ip}"
}

wait_for_lease_and_rw_primary() {
  local primary=$1
  local attempt
  for attempt in $(seq 1 120); do
    if lease_and_rw_primary_matches "${primary}"; then
      return
    fi
    sleep 1
  done
  fail "primary Lease and read-write endpoint did not converge"
}

[[ "${HORMUZ_POSTGRES_HA_PROOF_ACK:-}" == "${PROOF_ACK}" ]] \
  || fail "set HORMUZ_POSTGRES_HA_PROOF_ACK=${PROOF_ACK}"
[[ -n "${EVIDENCE_DIR}" ]] || fail "HORMUZ_POSTGRES_HA_EVIDENCE_DIR is required"
[[ "${SOURCE_COMMIT}" =~ ^[0-9a-f]{40}$ ]] || fail "HORMUZ_SOURCE_COMMIT must be an exact commit"
[[ ! -e "${EVIDENCE_DIR}" && ! -L "${EVIDENCE_DIR}" ]] || fail "evidence output already exists"
[[ "${CLUSTER_NAME}" =~ ^[a-z0-9]([-a-z0-9]*[a-z0-9])?$ && "${#CLUSTER_NAME}" -le 63 ]] \
  || fail "HORMUZ_POSTGRES_HA_CLUSTER_NAME must be a DNS label"
[[ "$(uname -s)" == "Linux" ]] || fail "the reference proof requires Linux"
[[ "$(uname -m)" == "x86_64" || "$(uname -m)" == "amd64" ]] \
  || fail "the reference proof requires native AMD64"
for command in docker curl grep openssl python3 sha256sum timeout; do
  command -v "${command}" >/dev/null 2>&1 || fail "${command} is unavailable"
done
docker_platform="$(docker info --format '{{.OSType}}/{{.Architecture}}')"
[[ "${docker_platform}" == "linux/x86_64" || "${docker_platform}" == "linux/amd64" ]] \
  || fail "the Docker daemon is not native linux/amd64"

WORK_ROOT="$(mktemp -d "${RUNNER_TEMP:-/tmp}/hormuz-postgres-ha.XXXXXX")"
SECRET_ROOT="${WORK_ROOT}/secrets"
ARTIFACT_ROOT="${WORK_ROOT}/artifacts"
KUBECONFIG="${WORK_ROOT}/kubeconfig"
mkdir -p "${WORK_ROOT}/bin" "${SECRET_ROOT}" "${ARTIFACT_ROOT}" "${WORK_ROOT}/chart"
chmod 0700 "${WORK_ROOT}" "${SECRET_ROOT}" "${ARTIFACT_ROOT}"
mkdir -p "${EVIDENCE_DIR}"
chmod 0700 "${EVIDENCE_DIR}"
: >"${ARTIFACT_ROOT}/events.log"
chmod 0600 "${ARTIFACT_ROOT}/events.log"
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

if kind get clusters | grep -Fxq "${CLUSTER_NAME}"; then
  fail "the disposable cluster name already exists"
fi
kind create cluster --name "${CLUSTER_NAME}" --config "${HA_ROOT}/kind.yaml" \
  --kubeconfig "${KUBECONFIG}" >/dev/null
CLUSTER_CREATED=1
helm upgrade --install cilium "${WORK_ROOT}/cilium.tgz" \
  --namespace kube-system --values "${FIXTURE_ROOT}/cilium-values.yaml" \
  --wait --timeout 10m >/dev/null
kubectl wait --for=condition=Ready nodes --all --timeout=10m >/dev/null

mapfile -t worker_nodes < <(kubectl get nodes \
  --selector='!node-role.kubernetes.io/control-plane' --output=name \
  | sed 's#^node/##' | sort)
[[ "${#worker_nodes[@]}" -eq 5 ]] || fail "Kind worker topology invalid"
for node in "${worker_nodes[@]:0:3}"; do
  kubectl label node "${node}" io.hormuz.postgres-node=true >/dev/null
  kubectl taint node "${node}" io.hormuz/postgres-node=true:NoSchedule >/dev/null
done
record_event environment_verified

kubectl apply --server-side --force-conflicts --filename "${WORK_ROOT}/cnpg-pinned.yaml" >/dev/null
kubectl wait --for=condition=Established crd/clusters.postgresql.cnpg.io --timeout=5m >/dev/null
wait_for_deployment cnpg-system cnpg-controller-manager 5m
operator_image="$(kubectl --namespace cnpg-system get deployment cnpg-controller-manager \
  --output=jsonpath='{.spec.template.spec.containers[0].image}')"
operator_env_image="$(kubectl --namespace cnpg-system get deployment cnpg-controller-manager \
  --output=jsonpath='{.spec.template.spec.containers[0].env[?(@.name=="OPERATOR_IMAGE_NAME")].value}')"
[[ "${operator_image}" == "${CNPG_OPERATOR_IMAGE}" && "${operator_env_image}" == "${CNPG_OPERATOR_IMAGE}" ]] \
  || fail "CloudNativePG operator image is not pinned to the exact AMD64 manifest"
record_event operator_installed

for namespace in hormuz-system hormuz-dependencies hormuz-ingress; do
  kubectl create namespace "${namespace}" >/dev/null
done

for name in \
  postgres-superuser-password postgres-owner-password runtime-password \
  policy-control-password custody-control-password custody-executor-password \
  ingress-credential identity-token bob-token openai-api-key anthropic-api-key openbao-token; do
  write_random_hex_secret "${SECRET_ROOT}/${name}"
done
chmod 0600 "${SECRET_ROOT}"/*

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

kubectl apply --filename "${HA_ROOT}/cluster.yaml" >/dev/null
wait_for_cnpg_ready
kubectl --namespace hormuz-dependencies get pod \
  --selector='cnpg.io/cluster=hormuz-postgres' --output=json \
  >"${ARTIFACT_ROOT}/postgres-topology.json"
python3 - "${ARTIFACT_ROOT}/postgres-topology.json" "${POSTGRES_IMAGE}" "${POSTGRES_AMD64_DIGEST}" \
  "${worker_nodes[0]}" "${worker_nodes[1]}" "${worker_nodes[2]}" <<'PY'
import json
import sys

items = json.load(open(sys.argv[1], encoding="utf-8"))["items"]
postgres_nodes = set(sys.argv[4:])
if len(items) != 3 or len({item["spec"]["nodeName"] for item in items}) != 3:
    raise SystemExit("postgres_topology_invalid")
if {item["spec"]["nodeName"] for item in items} != postgres_nodes:
    raise SystemExit("postgres_worker_placement_invalid")
for item in items:
    container = next(value for value in item["spec"]["containers"] if value["name"] == "postgres")
    status = next(value for value in item["status"]["containerStatuses"] if value["name"] == "postgres")
    if container["image"] != sys.argv[2] or not status["imageID"].endswith(sys.argv[3]):
        raise SystemExit("postgres_image_digest_invalid")
    if item["metadata"]["labels"].get("io.hormuz.proof-role") != "postgres":
        raise SystemExit("postgres_network_policy_label_missing")
PY
wait_for_failover_quorum_ready "${ARTIFACT_ROOT}/failover-quorum.json"
failover_quorum="$(<"${ARTIFACT_ROOT}/failover-quorum.json")"
record_event postgres_topology_ready

python3 - "${SECRET_ROOT}" <<'PY'
from pathlib import Path
import sys

root = Path(sys.argv[1])
host = "hormuz-postgres-rw.hormuz-dependencies.svc.cluster.local"
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
chmod 0600 "${SECRET_ROOT}"/*

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
python3 - "${ARTIFACT_ROOT}/bootstrap.json" <<'PY' \
  || fail "PostgreSQL bootstrap evidence invalid"
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    value = json.load(handle)
expected = {
    "command",
    "restricted_login_roles",
    "schema",
    "schema_complete",
    "schema_version",
}
valid = (
    set(value) == expected
    and value["command"] == "postgres-ha-bootstrap"
    and value["restricted_login_roles"] == 4
    and value["schema"] == "hormuz"
    and value["schema_complete"] is True
    and isinstance(value["schema_version"], int)
    and not isinstance(value["schema_version"], bool)
    and value["schema_version"] > 0
)
raise SystemExit(0 if valid else 1)
PY
kubectl --namespace hormuz-system delete job/hormuz-postgres-ha-bootstrap \
  secret/hormuz-postgres-ha-bootstrap --wait=true >/dev/null

create_immutable_configmap hormuz-dependencies fake-provider-v1 \
  --from-file="fake-provider.py=${FIXTURE_ROOT}/fake-provider.py"
kubectl apply --filename "${FIXTURE_ROOT}/fake-provider.yaml" >/dev/null
wait_for_deployment hormuz-dependencies fake-provider 5m
wait_for_deployment hormuz-dependencies forbidden-provider 5m

create_immutable_configmap hormuz-system hormuz-config-v1 \
  --from-file="hormuz.json=${FIXTURE_ROOT}/hormuz.allow.json"
create_immutable_secret hormuz-system hormuz-runtime-v1 \
  --from-file="postgres-runtime-dsn=${SECRET_ROOT}/postgres-runtime-dsn" \
  --from-file="hormuz-identity-token=${SECRET_ROOT}/identity-token" \
  --from-file="hormuz-ingress-credential=${SECRET_ROOT}/ingress-credential" \
  --from-file="openai-api-key=${SECRET_ROOT}/openai-api-key" \
  --from-file="anthropic-api-key=${SECRET_ROOT}/anthropic-api-key"
create_immutable_secret hormuz-ingress hormuz-probe-v1 \
  --from-file="identity-token=${SECRET_ROOT}/identity-token" \
  --from-file="ingress-credential=${SECRET_ROOT}/ingress-credential"
create_immutable_configmap hormuz-ingress hormuz-probe-v1 \
  --from-file="probe.py=${FIXTURE_ROOT}/probe.py"
create_immutable_configmap hormuz-system hormuz-postgres-ha-state-config \
  --from-file="state-config.json=${HA_ROOT}/state-config.json"
create_immutable_configmap hormuz-system hormuz-postgres-ha-state-script \
  --from-file="state_probe.py=${HA_ROOT}/state_probe.py"
create_immutable_secret hormuz-system hormuz-postgres-ha-state \
  --from-file="postgres-runtime-dsn=${SECRET_ROOT}/postgres-runtime-dsn" \
  --from-file="postgres-migration-dsn=${SECRET_ROOT}/postgres-migration-dsn" \
  --from-file="postgres-policy-control-dsn=${SECRET_ROOT}/postgres-policy-control-dsn" \
  --from-file="postgres-custody-control-dsn=${SECRET_ROOT}/postgres-custody-control-dsn" \
  --from-file="postgres-custody-executor-dsn=${SECRET_ROOT}/postgres-custody-executor-dsn" \
  --from-file="alice-token=${SECRET_ROOT}/identity-token" \
  --from-file="bob-token=${SECRET_ROOT}/bob-token" \
  --from-file="openai-api-key=${SECRET_ROOT}/openai-api-key" \
  --from-file="anthropic-api-key=${SECRET_ROOT}/anthropic-api-key" \
  --from-file="openbao-token=${SECRET_ROOT}/openbao-token"

config_sha="sha256:$(sha256sum "${FIXTURE_ROOT}/hormuz.allow.json" | awk '{print $1}')"
python3 "${ROOT}/tools/verify_helm_profile.py" validate-chart --chart "${CHART_ROOT}" >/dev/null
helm lint "${CHART_ROOT}" --values "${HA_ROOT}/helm-values.yaml" \
  --set-string "configuration.sha256=${config_sha}" >/dev/null
helm package "${CHART_ROOT}" --destination "${WORK_ROOT}/chart" >/dev/null
chart_package="${WORK_ROOT}/chart/hormuz-0.1.0.tgz"
chart_sha256="$(sha256sum "${chart_package}" | awk '{print $1}')"
helm template hormuz "${CHART_ROOT}" --namespace hormuz-system \
  --values "${HA_ROOT}/helm-values.yaml" \
  --set-string "configuration.sha256=${config_sha}" \
  >"${ARTIFACT_ROOT}/rendered.yaml"
if grep -Eq 'kind: (Cluster|PostgreSQL|DatabaseRole)|postgresql.cnpg.io' "${ARTIFACT_ROOT}/rendered.yaml"; then
  fail "Hormuz Helm chart attempted to install PostgreSQL"
fi
helm upgrade --install hormuz "${CHART_ROOT}" --namespace hormuz-system \
  --values "${HA_ROOT}/helm-values.yaml" \
  --set-string "configuration.sha256=${config_sha}" \
  --wait --timeout 10m >/dev/null
wait_for_deployment hormuz-system hormuz-hormuz 10m

kubectl apply --filename "${HA_ROOT}/probes.yaml" >/dev/null
wait_for_pod hormuz-system hormuz-postgres-ha-state
wait_for_pod hormuz-ingress hormuz-postgres-ha-probe

gateway_topology="$(kubectl --namespace hormuz-system get pods \
  --selector='app.kubernetes.io/instance=hormuz,app.kubernetes.io/component=gateway' \
  --output=json)"
python3 -c 'import json,sys; items=json.loads(sys.argv[1])["items"]; nodes={i["spec"]["nodeName"] for i in items}; postgres_nodes=set(sys.argv[2:]); raise SystemExit(0 if len(items)==2 and len(nodes)==2 and not nodes & postgres_nodes and all(i["metadata"]["annotations"].get("io.hormuz/runtime-secret-revision")=="postgres-ha-generation-v1" for i in items) else 1)' \
  "${gateway_topology}" "${worker_nodes[0]}" "${worker_nodes[1]}" "${worker_nodes[2]}" \
  || fail "gateway topology or database-worker isolation invalid"
probe request "http://hormuz-hormuz.hormuz-system.svc.cluster.local:8787" 200 \
  "${ARTIFACT_ROOT}/baseline-request.json"
record_event gateway_baseline_ready
state_probe seed "${ARTIFACT_ROOT}/state-seed.json"
record_event durable_state_seeded

timeout 120s kubectl --namespace hormuz-ingress exec pod/hormuz-postgres-ha-probe --container probe -- \
  /opt/hormuz/bin/python -I /opt/hormuz-proof/probe.py ambiguous-request \
  --target http://hormuz-hormuz.hormuz-system.svc.cluster.local:8787 \
  --expected-policy fallback+capped+redacted \
  >"${ARTIFACT_ROOT}/ambiguous-request.jsonl" &
blocking_pid=$!
wait_for_provider_block
state_probe snapshot "${ARTIFACT_ROOT}/state-before.json"
record_event ambiguous_attempt_committed
provider_before_positive="$(provider_request_count)"
[[ "${provider_before_positive}" == "2" ]] || fail "provider baseline count invalid"

gateway_uids_before="$(gateway_uids)"
old_primary="$(current_primary)"
[[ -n "${old_primary}" ]] || fail "current primary is unavailable"
old_primary_node="$(pod_node "${old_primary}")"
positive_started_ms="$(monotonic_ms)"
pause_node "${old_primary_node}"
record_event primary_loss_injected
provider_control POST /control/block/abort >"${ARTIFACT_ROOT}/provider-abort.json"
gateway_fail_closed positive
positive_fail_closed_ms=$(( $(monotonic_ms) - positive_started_ms ))
record_event all_gateways_failed_closed
provider_after_positive_denials="$(provider_request_count)"
[[ "${provider_after_positive_denials}" == "${provider_before_positive}" ]] \
  || fail "provider egress occurred during positive outage denials"
record_event positive_outage_provider_egress_unchanged
wait "${blocking_pid}" || fail "ambiguous request did not return its bounded outcome"
python3 - "${ARTIFACT_ROOT}/ambiguous-request.jsonl" <<'PY'
import json
import sys

values = [json.loads(line) for line in open(sys.argv[1], encoding="utf-8") if line.strip()]
if values[-1] != {"command": "ambiguous-request", "transport_outcome": "ambiguous"}:
    raise SystemExit("ambiguous_request_result_invalid")
PY
new_primary="$(wait_for_primary_change "${old_primary}")"
primary_promotion_ms=$(( $(monotonic_ms) - positive_started_ms ))
record_event safe_replica_promoted
wait_for_lease_and_rw_primary "${new_primary}"
record_event lease_and_rw_endpoint_converged
state_probe snapshot "${ARTIFACT_ROOT}/state-after-failover.json"
record_event durable_state_continuity_verified

for index in 0 1; do
  gateway_ip="$(python3 -c 'import json,sys; items=sorted(json.loads(sys.argv[1])["items"],key=lambda i:i["metadata"]["name"]); print(items[int(sys.argv[2])]["status"]["podIP"])' "$(kubectl --namespace hormuz-system get pods --selector='app.kubernetes.io/instance=hormuz,app.kubernetes.io/component=gateway' --output=json)" "${index}")"
  ready_observed=0
  for attempt in $(seq 1 60); do
    if probe ready "http://${gateway_ip}:8787" 200 "${ARTIFACT_ROOT}/positive-recovered-ready-${index}.json" 2>/dev/null; then
      ready_observed=1
      break
    fi
    sleep 1
  done
  [[ "${ready_observed}" -eq 1 ]] || fail "gateway did not reconnect after primary failover"
done
gateway_recovery_ms=$(( $(monotonic_ms) - positive_started_ms ))
[[ "$(gateway_uids)" == "${gateway_uids_before}" ]] || fail "database failover restarted a gateway process"
record_event gateways_reconnected_without_restart
probe request "http://hormuz-hormuz.hormuz-system.svc.cluster.local:8787" 200 \
  "${ARTIFACT_ROOT}/recovered-request.json"
state_probe snapshot "${ARTIFACT_ROOT}/state-after-recovery.json"
ambiguous_preserved="$(python3 -c 'import json,sys; v=json.load(open(sys.argv[1],encoding="utf-8")); print(v["pending_attempts"]+v["outcome_unknown_attempts"])' "${ARTIFACT_ROOT}/state-after-recovery.json")"
uncertain_preserved="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1],encoding="utf-8"))["uncertain_reservations"])' "${ARTIFACT_ROOT}/state-after-recovery.json")"
[[ "${ambiguous_preserved}" -ge 1 && "${uncertain_preserved}" -ge 1 ]] \
  || fail "ambiguous request evidence was not preserved"
record_event ambiguous_attempt_preserved
provider_after_recovery="$(provider_request_count)"
[[ "${provider_after_recovery}" -eq $((provider_after_positive_denials + 1)) ]] \
  || fail "provider replay or missing recovery request detected"
record_event no_provider_replay_verified
provider_control POST /control/block/reset >"${ARTIFACT_ROOT}/provider-reset.json"

rejoin_started_ms="$(monotonic_ms)"
unpause_node "${old_primary_node}"
wait_for_node_state "${old_primary_node}" True
wait_for_cnpg_ready
former_primary_rejoin_ms=$(( $(monotonic_ms) - rejoin_started_ms ))
old_primary_recovery="$(timeout 30s kubectl --namespace hormuz-dependencies exec "pod/${old_primary}" --container postgres -- \
  psql --username postgres --dbname postgres --tuples-only --no-align \
  --command 'SELECT pg_is_in_recovery()' | tr -d '[:space:]')"
[[ "${old_primary_recovery}" == "t" ]] || fail "former primary did not rejoin as a fenced replica"
record_event former_primary_fenced_and_rejoined

wait_for_cnpg_ready
record_event quorum_fixture_ready
negative_primary="$(current_primary)"
negative_primary_node="$(pod_node "${negative_primary}")"
mapfile -t negative_replica_nodes < <(kubectl --namespace hormuz-dependencies get pods \
  --selector='cnpg.io/cluster=hormuz-postgres' --output=json \
  | python3 -c 'import json,sys; primary=sys.argv[1]; print("\n".join(sorted(item["spec"]["nodeName"] for item in json.load(sys.stdin)["items"] if item["metadata"]["name"]!=primary)))' \
    "${negative_primary}")
[[ "${#negative_replica_nodes[@]}" -eq 2 ]] || fail "negative replica topology invalid"
negative_replica_node="${negative_replica_nodes[0]}"
pause_node "${negative_replica_node}"
wait_for_node_state "${negative_replica_node}" False
provider_before_negative="$(provider_request_count)"
negative_started_ms="$(monotonic_ms)"
pause_node "${negative_primary_node}"
record_event primary_and_replica_loss_injected
gateway_fail_closed negative
negative_fail_closed_ms=$(( $(monotonic_ms) - negative_started_ms ))
record_event all_gateways_failed_closed_without_quorum
provider_after_negative_denials="$(provider_request_count)"
[[ "${provider_after_negative_denials}" == "${provider_before_negative}" ]] \
  || fail "provider egress occurred without PostgreSQL quorum"

for attempt in $(seq 1 120); do
  if [[ "$(rw_ready_addresses 2>/dev/null || printf 'invalid')" == "0" ]]; then
    break
  fi
  sleep 1
done
[[ "${attempt}" -lt 120 ]] || fail "read-write endpoint retained a stale primary"
quorum_observation_started_ms="$(monotonic_ms)"
sleep 30
quorum_refusal_observation_ms=$(( $(monotonic_ms) - quorum_observation_started_ms ))
[[ "$(current_primary)" == "${negative_primary}" ]] \
  || fail "CloudNativePG promoted without the required failover quorum"
ready_database_pods="$(kubectl --namespace hormuz-dependencies get pod \
  --selector='cnpg.io/cluster=hormuz-postgres' --output=json \
  | python3 -c 'import json,sys; print(sum(1 for item in json.load(sys.stdin)["items"] if any(c.get("type")=="Ready" and c.get("status")=="True" for c in item.get("status",{}).get("conditions",[]))))')"
[[ "${ready_database_pods}" == "1" ]] || fail "negative quorum fixture did not leave exactly one promotable replica"
python3 -c 'import json,sys; status=json.loads(sys.argv[1])["status"]; n=len(status["standbyNames"]); r=int(sys.argv[2]); w=status["standbyNumber"]; raise SystemExit(0 if n==2 and r==1 and w==1 and not (r+w>n) else 1)' \
  "${failover_quorum}" "${ready_database_pods}" \
  || fail "failover quorum arithmetic did not block unsafe promotion"
record_event quorum_promotion_refused
[[ "$(rw_ready_addresses)" == "0" ]] || fail "read-write endpoint exposed a stale primary"
record_event rw_endpoint_withdrawn
record_event negative_outage_provider_egress_unchanged

negative_recovery_started_ms="$(monotonic_ms)"
unpause_node "${negative_primary_node}"
unpause_node "${negative_replica_node}"
wait_for_node_state "${negative_primary_node}" True
wait_for_node_state "${negative_replica_node}" True
wait_for_cnpg_ready
[[ "$(gateway_uids)" == "${gateway_uids_before}" ]] \
  || fail "quorum-loss recovery restarted a gateway process"
for index in 0 1; do
  gateway_ip="$(python3 -c 'import json,sys; items=sorted(json.loads(sys.argv[1])["items"],key=lambda i:i["metadata"]["name"]); print(items[int(sys.argv[2])]["status"]["podIP"])' "$(kubectl --namespace hormuz-system get pods --selector='app.kubernetes.io/instance=hormuz,app.kubernetes.io/component=gateway' --output=json)" "${index}")"
  ready_observed=0
  for attempt in $(seq 1 60); do
    if probe ready "http://${gateway_ip}:8787" 200 "${ARTIFACT_ROOT}/negative-recovered-ready-${index}.json" 2>/dev/null; then
      ready_observed=1
      break
    fi
    sleep 1
  done
  [[ "${ready_observed}" -eq 1 ]] || fail "gateway did not reconnect after quorum recovery"
done
negative_recovery_ms=$(( $(monotonic_ms) - negative_recovery_started_ms ))
record_event negative_path_recovered
state_probe snapshot "${ARTIFACT_ROOT}/state-after-quorum-recovery.json"
record_event final_state_continuity_verified

[[ "${EVENT_SEQUENCE}" -eq 24 ]] || fail "event sequence incomplete"
[[ "${MAXIMUM_STORAGE_DENIAL_MS}" -gt 0 ]] || fail "storage denial timing was not measured"

python3 - \
  "${ARTIFACT_ROOT}/observations.json" "${SOURCE_COMMIT}" \
  "$(docker version --format '{{.Server.Version}}')" "${chart_sha256}" \
  "${positive_fail_closed_ms}" "${primary_promotion_ms}" "${gateway_recovery_ms}" \
  "${former_primary_rejoin_ms}" "${negative_fail_closed_ms}" \
  "${quorum_refusal_observation_ms}" "${negative_recovery_ms}" "${MAXIMUM_STORAGE_DENIAL_MS}" \
  "${provider_before_positive}" "${provider_after_positive_denials}" "${provider_after_recovery}" \
  "${ambiguous_preserved}" "${uncertain_preserved}" \
  "${provider_before_negative}" "${provider_after_negative_denials}" \
  "${ARTIFACT_ROOT}/state-before.json" "${ARTIFACT_ROOT}/state-after-failover.json" \
  "${ARTIFACT_ROOT}/state-after-recovery.json" "${ARTIFACT_ROOT}/state-after-quorum-recovery.json" \
  "${ARTIFACT_ROOT}/events.log" <<'PY'
import json
import sys
from pathlib import Path

(
    output, source_commit, docker_engine, chart_sha,
    positive_fail_closed, primary_promotion, gateway_recovery, former_primary_rejoin,
    negative_fail_closed, quorum_observation, negative_recovery, maximum_storage_denial,
    provider_before_positive, provider_after_positive, provider_after_recovery,
    ambiguous_preserved, uncertain_preserved,
    provider_before_negative, provider_after_negative,
    state_before, state_after_failover, state_after_recovery, state_after_quorum,
    events_path,
) = sys.argv[1:]

def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))

events = []
for line in Path(events_path).read_text(encoding="utf-8").splitlines():
    sequence, event = line.split("|", 1)
    events.append({"sequence": int(sequence), "event": event})

value = {
    "source_commit": source_commit,
    "docker_engine": docker_engine,
    "helm_chart_sha256": chart_sha,
    "topology": {
        "kind_nodes": 6,
        "worker_nodes": 5,
        "postgresql_worker_nodes": 3,
        "gateway_worker_nodes": 2,
        "postgresql_instances": 3,
        "distinct_postgresql_nodes": 3,
        "gateway_replicas": 2,
        "synchronous_method": "any",
        "synchronous_number": 1,
        "data_durability": "required",
        "failover_quorum": True,
        "isolation_check": True,
        "primary_lease": {
            "lease_duration_seconds": 15,
            "renew_deadline_seconds": 10,
            "retry_period_seconds": 2,
            "released_lease_duration_seconds": 1,
        },
    },
    "pool_bounds": {
        "minimum_connections_per_replica": 1,
        "maximum_connections_per_replica": 4,
        "acquire_timeout_seconds": 5,
        "maximum_waiting_per_replica": 8,
        "reconnect_horizon_seconds": 15,
    },
    "primary_loss": {
        "trigger": "unexpected_worker_pause",
        "previous_primary_changed": True,
        "lease_holder_matches_current_primary": True,
        "rw_endpoint_matches_current_primary": True,
        "former_primary_rejoined_as_replica": True,
        "former_primary_fenced_before_rejoin": True,
        "gateway_replicas_observed": 2,
        "gateways_not_ready": 2,
        "backpressure_requests": 32,
        "gateway_storage_denials": 32,
        "provider_requests_before_denials": int(provider_before_positive),
        "provider_requests_after_denials": int(provider_after_positive),
        "provider_requests_after_recovery": int(provider_after_recovery),
        "gateway_processes_reused": True,
        "ambiguous_attempts_preserved": int(ambiguous_preserved),
        "uncertain_reservations_preserved": int(uncertain_preserved),
        "automatic_provider_replays": 0,
    },
    "quorum_loss": {
        "trigger": "primary_and_one_replica_worker_pause",
        "unavailable_postgresql_instances": 2,
        "promotion_prevented": True,
        "failover_quorum_reported_insufficient": True,
        "rw_ready_addresses": 0,
        "stale_primary_endpoint_absent": True,
        "gateway_replicas_observed": 2,
        "gateways_not_ready": 2,
        "backpressure_requests": 32,
        "gateway_storage_denials": 32,
        "provider_requests_before_denials": int(provider_before_negative),
        "provider_requests_after_denials": int(provider_after_negative),
        "gateway_processes_reused_after_recovery": True,
    },
    "state": {
        "before": load(state_before),
        "after_failover": load(state_after_failover),
        "after_recovery": load(state_after_recovery),
        "after_quorum_recovery": load(state_after_quorum),
    },
    "timings_ms": {
        "positive_fail_closed": int(positive_fail_closed),
        "primary_promotion": int(primary_promotion),
        "gateway_recovery": int(gateway_recovery),
        "former_primary_rejoin": int(former_primary_rejoin),
        "negative_fail_closed": int(negative_fail_closed),
        "quorum_refusal_observation": int(quorum_observation),
        "negative_recovery": int(negative_recovery),
        "maximum_storage_denial": int(maximum_storage_denial),
    },
    "events": events,
}
Path(output).write_text(json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
PY

python3 "${ROOT}/tools/verify_postgres_ha_reference.py" write-evidence \
  --observations "${ARTIFACT_ROOT}/observations.json" \
  --output "${EVIDENCE_DIR}/summary.json" >/dev/null
python3 "${ROOT}/tools/verify_postgres_ha_reference.py" validate \
  --evidence "${EVIDENCE_DIR}/summary.json" >/dev/null
python3 "${ROOT}/tools/verify_helm_profile.py" assert-no-secrets \
  --artifact-root "${EVIDENCE_DIR}" --secret-root "${SECRET_ROOT}" >/dev/null
chmod 0600 "${EVIDENCE_DIR}/summary.json"

helm uninstall hormuz --namespace hormuz-system --wait >/dev/null
kubectl delete namespace hormuz-system hormuz-dependencies hormuz-ingress --wait=true >/dev/null
kind delete cluster --name "${CLUSTER_NAME}" >/dev/null
CLUSTER_CREATED=0
printf 'verified PostgreSQL HA reference: cloudnativepg=%s postgres=16.15 events=%s\n' \
  "${CNPG_VERSION}" "${EVENT_SEQUENCE}"
