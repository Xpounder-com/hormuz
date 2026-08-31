#!/usr/bin/env bash

set -euo pipefail
umask 077

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CHART_ROOT="${ROOT}/deploy/helm/hormuz"
FIXTURE_ROOT="${ROOT}/deploy/kubernetes/conformance"
EVIDENCE_DIR="${HORMUZ_KUBERNETES_EVIDENCE_DIR:-}"
STATE_EVIDENCE="${HORMUZ_MULTI_REPLICA_STATE_EVIDENCE:-}"
SOURCE_COMMIT="${HORMUZ_SOURCE_COMMIT:-}"
CLUSTER_NAME="${HORMUZ_KUBERNETES_CLUSTER_NAME:-hormuz-reference}"
PROOF_ACK="I_UNDERSTAND_THIS_IS_A_DISPOSABLE_KUBERNETES_REFERENCE_PROOF"
HORMUZ_IMAGE="ghcr.io/xpounder-com/hormuz@sha256:8ac24f5c7afb8ce09ec133616de06702f568a2e70594d8034146a131d86e5b67"
POSTGRES_IMAGE="postgres@sha256:7a396fd264a2067788b6551122b50f162bf6136312c7fc9d74381cb92c648382"
KIND_VERSION="v0.32.0"
KIND_SHA256="50030de23cf40a18505f20426f6a8506bedf13c6e509244bd1fa9463721b0f54"
KUBECTL_VERSION="v1.36.1"
KUBECTL_SHA256="629d3f410e09bf49b64ae7079f7f0bda1191efed311f7d37fdbab0ad5b0ec2b7"
HELM_VERSION="v3.21.4"
HELM_SHA256="61f88ab166748cb19604d7884cb100ae9ccb13804ddeb98e08af167eacbb6a14"
CILIUM_VERSION="1.20.1"
CILIUM_CHART_SHA256="06210eef7c23d15f7699c79e2fe3a1ec9c389024c5c5c006ea04022d322449a2"
CILIUM_AGENT_IMAGE="quay.io/cilium/cilium:v1.20.1@sha256:ae9ea21f7427fe24bc6ea7247eb552157a1b0a431744045d3f641545ca71d11b"
CILIUM_OPERATOR_IMAGE="quay.io/cilium/operator-generic:v1.20.1@sha256:6c3885fc7b629099fdbe2a5c87869c86feb825fa18fae299eac0f61918d16ecf"
UPSTREAM_TIMEOUT_SECONDS=45
# Gateway reservations deliberately outlive the provider timeout by 60 seconds
# so an ambiguous attempt cannot stop counting while its outcome is unknown.
REQUEST_ATTEMPT_STALE_SECONDS=$((UPSTREAM_TIMEOUT_SECONDS + 60))
WORK_ROOT=""
SECRET_ROOT=""
ARTIFACT_ROOT=""
KUBECONFIG=""
CLUSTER_CREATED=0
PROBE_SEQUENCE=0
SUCCESSFUL_REQUESTS=0
POLICY_DENIALS=0
OPERATION_EVENT_SEQUENCE=0
OPERATION_EVENT_LOG=""
RESTRICTIVE_ROLLOUT_CONVERGENCE_MS=0
ROLLBACK_CONVERGENCE_MS=0
GRACEFUL_READINESS_WITHDRAWAL_MS=0
GRACEFUL_INFLIGHT_DRAIN_MS=0
ABRUPT_REPLACEMENT_CONVERGENCE_MS=0
OUTCOME_UNKNOWN_ATTEMPTS=0
UNCERTAIN_RESERVATIONS=0
OPERATION_PROOF_ENABLED=0

monotonic_ms() {
  python3 -c 'import time; print(time.monotonic_ns() // 1_000_000)'
}

record_operation_event() {
  local event=$1
  [[ "${OPERATION_PROOF_ENABLED}" -eq 1 ]] || return
  OPERATION_EVENT_SEQUENCE=$((OPERATION_EVENT_SEQUENCE + 1))
  printf '%s|%s\n' "${OPERATION_EVENT_SEQUENCE}" "${event}" >>"${OPERATION_EVENT_LOG}"
}

fail() {
  printf 'Kubernetes reference proof failed: %s\n' "$1" >&2
  exit 1
}

cleanup() {
  local status=$?
  set +e
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

write_random_hex_secret() {
  local output=$1
  local value
  value="$(openssl rand -hex 32)"
  [[ "${#value}" -eq 64 ]] || fail "generated secret length invalid"
  # Secret-backed environment variables are byte-for-byte values. Do not add
  # a line ending that would make credentials invalid as HTTP header values.
  printf '%s' "${value}" >"${output}"
}

wait_for_deployment() {
  local namespace=$1
  local deployment=$2
  local timeout=$3
  if kubectl --namespace "${namespace}" rollout status "deployment/${deployment}" \
    --timeout="${timeout}" >/dev/null; then
    return
  fi

  emit_deployment_diagnostics "${namespace}" "${deployment}"
  fail "deployment did not become ready: ${namespace}/${deployment}"
}

wait_for_serving_generation() {
  local namespace=$1
  local deployment=$2
  local checkpoint=$3
  local configuration=$4
  local configuration_sha256=$5
  local runtime_secret=$6
  local runtime_secret_revision=$7
  [[ "${checkpoint}" =~ ^[a-z0-9-]+$ ]] || fail "rollout checkpoint invalid"

  # Helm --wait may return while old ready replicas are still participating in
  # a rolling Deployment. Wait for Kubernetes' complete-rollout condition, then
  # independently prove that every ready, non-terminating replica carries the
  # exact immutable configuration and runtime Secret generation under test.
  if ! kubectl --namespace "${namespace}" rollout status "deployment/${deployment}" \
    --timeout=10m >/dev/null; then
    emit_deployment_diagnostics "${namespace}" "${deployment}"
    fail "serving generation did not finish rolling out: ${checkpoint}"
  fi

  local output="${ARTIFACT_ROOT}/serving-generation-${checkpoint}.json"
  kubectl --namespace "${namespace}" get deployment,pod \
    --selector='app.kubernetes.io/instance=hormuz,app.kubernetes.io/component=gateway' \
    --output=json >"${output}"
  python3 "${ROOT}/tools/verify_helm_profile.py" validate-serving-generation \
    --manifest "${output}" \
    --configuration "${configuration}" \
    --configuration-sha256 "${configuration_sha256}" \
    --runtime-secret "${runtime_secret}" \
    --runtime-secret-revision "${runtime_secret_revision}" >/dev/null \
    || fail "serving replicas do not share the expected input generation: ${checkpoint}"
}

emit_deployment_diagnostics() {
  local namespace=$1
  local deployment=$2

  local diagnostic="${ARTIFACT_ROOT}/failure-${namespace}-${deployment}.log"
  {
    printf 'deployment rollout failed: namespace=%s deployment=%s\n' \
      "${namespace}" "${deployment}"
    kubectl --namespace "${namespace}" get deployment "${deployment}" --output=wide
    kubectl --namespace "${namespace}" get pods --output=wide
    kubectl --namespace "${namespace}" get events --sort-by=.metadata.creationTimestamp
    kubectl --namespace "${namespace}" logs "deployment/${deployment}" \
      --all-pods=true --all-containers=true --prefix=true
    kubectl --namespace "${namespace}" describe deployment "${deployment}"
    kubectl --namespace "${namespace}" describe pods \
      --selector='app.kubernetes.io/instance=hormuz,app.kubernetes.io/component=gateway'
  } >"${diagnostic}" 2>&1 || true
  if python3 "${ROOT}/tools/verify_helm_profile.py" assert-no-secrets \
    --artifact "${diagnostic}" --secret-root "${SECRET_ROOT}" >/dev/null; then
    sed -n '1,480p' "${diagnostic}" >&2
  else
    printf 'deployment diagnostic withheld because secret scanning did not pass\n' >&2
  fi
}

emit_job_diagnostics() {
  local namespace=$1
  local job=$2
  local diagnostic="${ARTIFACT_ROOT}/failure-${namespace}-${job}.log"
  {
    printf 'probe job failed: namespace=%s job=%s\n' "${namespace}" "${job}"
    kubectl --namespace "${namespace}" get "job/${job}" --output=wide
    kubectl --namespace "${namespace}" get pods \
      --selector="job-name=${job}" --output=wide
    kubectl --namespace "${namespace}" logs "job/${job}"
    kubectl --namespace "${namespace}" get events --sort-by=.metadata.creationTimestamp
    kubectl --namespace "${namespace}" describe "job/${job}"
  } >"${diagnostic}" 2>&1 || true
  if python3 "${ROOT}/tools/verify_helm_profile.py" assert-no-secrets \
    --artifact "${diagnostic}" --secret-root "${SECRET_ROOT}" >/dev/null; then
    sed -n '1,320p' "${diagnostic}" >&2
  else
    printf 'probe diagnostic withheld because secret scanning did not pass\n' >&2
  fi
}

capture_gateway_logs() {
  local checkpoint=$1
  [[ "${checkpoint}" =~ ^[a-z0-9-]+$ ]] || fail "gateway log checkpoint invalid"
  local output="${ARTIFACT_ROOT}/gateway-${checkpoint}.log"
  kubectl --namespace hormuz-system logs \
    --selector='app.kubernetes.io/instance=hormuz,app.kubernetes.io/component=gateway' \
    --all-containers=true --prefix=true --tail=-1 >"${output}"
  python3 "${ROOT}/tools/verify_helm_profile.py" assert-no-secrets \
    --artifact "${output}" --secret-root "${SECRET_ROOT}" >/dev/null \
    || fail "gateway logs failed secret non-disclosure: ${checkpoint}"
}

wait_for_job_log_marker() {
  local namespace=$1
  local job=$2
  local marker=$3
  local attempt
  for attempt in $(seq 1 30); do
    if kubectl --namespace "${namespace}" logs "job/${job}" 2>/dev/null \
      | grep -Fxq "${marker}"; then
      return
    fi
    if [[ "$(kubectl --namespace "${namespace}" get "job/${job}" \
      --output=jsonpath='{.status.failed}' 2>/dev/null)" =~ ^[1-9][0-9]*$ ]]; then
      emit_job_diagnostics "${namespace}" "${job}"
      fail "probe job failed before its synchronization marker: ${namespace}/${job}"
    fi
    sleep 1
  done
  emit_job_diagnostics "${namespace}" "${job}"
  fail "probe job synchronization marker missing: ${namespace}/${job}"
}

run_probe() {
  local namespace=$1
  local template=$2
  local expected_status=$3
  PROBE_SEQUENCE=$((PROBE_SEQUENCE + 1))
  local job="${template}-${PROBE_SEQUENCE}"
  local output="${ARTIFACT_ROOT}/${job}.json"
  kubectl --namespace "${namespace}" create job "${job}" --from="cronjob/${template}" >/dev/null
  if ! kubectl --namespace "${namespace}" wait --for=condition=complete \
    "job/${job}" --timeout=30s >/dev/null; then
    emit_job_diagnostics "${namespace}" "${job}"
    fail "probe job did not complete: ${namespace}/${job}"
  fi
  kubectl --namespace "${namespace}" logs "job/${job}" >"${output}"
  python3 - "${output}" "${expected_status}" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as stream:
    value = json.load(stream)
expected = sys.argv[2]
if expected == "network-denied":
    if value != {"command": "network-denied", "network_denied": True}:
        raise SystemExit("network_denial_observation_invalid")
elif value.get("status") != int(expected):
    raise SystemExit("probe_status_invalid")
PY
  kubectl --namespace "${namespace}" delete "job/${job}" --wait=true >/dev/null
}

provider_stats() {
  local output=$1
  kubectl --namespace hormuz-dependencies exec deployment/fake-provider -- \
    /opt/hormuz/bin/python -I -c \
    'from urllib.request import urlopen; print(urlopen("http://127.0.0.1:8090/stats", timeout=3).read().decode("utf-8"))' \
    >"${output}"
}

assert_provider_stats() {
  local input=$1
  local expected=$2
  local expected_blocking=${3:-0}
  python3 - "${input}" "${expected}" "${expected_blocking}" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as stream:
    value = json.load(stream)
expected = int(sys.argv[2])
expected_blocking = int(sys.argv[3])
if value != {
    "requests": expected,
    "redaction_marker_seen": True,
    "unredacted_secret_seen": False,
    "provider_authorization_seen": True,
    "capped_output_seen": True,
    "routed_model_seen": True,
    "blocking_requests": expected_blocking,
}:
    raise SystemExit("fake_provider_observation_invalid")
PY
}

provider_control() {
  local method=$1
  local path=$2
  local output=$3
  kubectl --namespace hormuz-dependencies exec deployment/fake-provider -- \
    /opt/hormuz/bin/python -I -c \
    'from urllib.request import Request,urlopen; import sys; request=Request("http://127.0.0.1:8090"+sys.argv[2],method=sys.argv[1]); print(urlopen(request,timeout=3).read().decode("utf-8"))' \
    "${method}" "${path}" >"${output}"
}

wait_for_provider_block() {
  local output=$1
  local attempt
  for attempt in $(seq 1 30); do
    provider_control GET /control/block/status "${output}"
    if python3 - "${output}" <<'PY' >/dev/null
import json
import sys

with open(sys.argv[1], encoding="utf-8") as stream:
    value = json.load(stream)
raise SystemExit(0 if value.get("started") is True and value.get("gateway_ip") else 1)
PY
    then
      python3 - "${output}" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as stream:
    print(json.load(stream)["gateway_ip"])
PY
      return
    fi
    sleep 1
  done
  fail "blocking provider request did not start"
}

provider_release_block() {
  provider_control POST /control/block/release "${ARTIFACT_ROOT}/provider-block-release.json"
}

provider_reset_block() {
  provider_control POST /control/block/reset "${ARTIFACT_ROOT}/provider-block-reset.json"
}

wait_for_provider_disconnect() {
  local output=$1
  local attempt
  for attempt in $(seq 1 30); do
    provider_control GET /control/block/status "${output}"
    if python3 - "${output}" <<'PY' >/dev/null
import json
import sys

with open(sys.argv[1], encoding="utf-8") as stream:
    value = json.load(stream)
raise SystemExit(0 if value.get("gateway_disconnected") is True else 1)
PY
    then
      return
    fi
    sleep 1
  done
  fail "force-deleted gateway connection remained open at the provider"
}

gateway_pod_for_ip() {
  local pod_ip=$1
  kubectl --namespace hormuz-system get pods \
    --selector='app.kubernetes.io/instance=hormuz,app.kubernetes.io/component=gateway' \
    --output=json \
    | python3 -c 'import json,sys; ip=sys.argv[1]; matches=[item["metadata"]["name"] for item in json.load(sys.stdin)["items"] if item.get("status",{}).get("podIP")==ip]; sys.exit("gateway_pod_for_ip_invalid") if len(matches)!=1 else print(matches[0])' \
      "${pod_ip}"
}

wait_for_service_exclusion() {
  local pod=$1
  local pod_ip=$2
  local started_ms=$3
  local attempt
  for attempt in $(seq 1 30); do
    local pod_ready="False"
    if kubectl --namespace hormuz-system get pod "${pod}" >/dev/null 2>&1; then
      pod_ready="$(kubectl --namespace hormuz-system get pod "${pod}" \
        --output=jsonpath='{.status.conditions[?(@.type=="Ready")].status}')"
    fi
    kubectl --namespace hormuz-system get endpointslices \
      --selector='kubernetes.io/service-name=hormuz-hormuz' --output=json \
      >"${ARTIFACT_ROOT}/service-endpoint-slices.json"
    if [[ "${pod_ready}" != "True" ]] && python3 - "${ARTIFACT_ROOT}/service-endpoint-slices.json" "${pod_ip}" <<'PY' >/dev/null
import json
import sys

with open(sys.argv[1], encoding="utf-8") as stream:
    value = json.load(stream)
addresses = {
    address
    for item in value.get("items", [])
    for endpoint in item.get("endpoints", [])
    if endpoint.get("conditions", {}).get("ready") is True
    for address in endpoint.get("addresses", [])
}
raise SystemExit(1 if sys.argv[2] in addresses else 0)
PY
    then
      local elapsed=$(( $(monotonic_ms) - started_ms ))
      [[ "${elapsed}" -gt 0 ]] || elapsed=1
      printf '%s\n' "${elapsed}"
      return
    fi
    sleep 1
  done
  fail "terminating replica remained ready or service-addressable"
}

request_attempt_uncertainty() {
  kubectl --namespace hormuz-dependencies exec deployment/postgres -- \
    psql --username postgres --dbname hormuz --tuples-only --no-align --field-separator='|' \
      --command "WITH latest AS (SELECT root.attempt_id, root.organization_id, (SELECT event.state FROM hormuz.gateway_request_attempt_events AS event WHERE event.attempt_id = root.attempt_id ORDER BY event.sequence DESC LIMIT 1) AS state FROM hormuz.gateway_request_attempts AS root WHERE root.organization_id = 'kubernetes-proof-organization') SELECT COUNT(*) FILTER (WHERE state = 'outcome_unknown'), (SELECT COUNT(*) FROM hormuz.gateway_budget_reservations AS reservation JOIN latest ON latest.attempt_id = reservation.attempt_id WHERE latest.state = 'outcome_unknown' AND reservation.reserved_tokens > 0 AND reservation.reserved_cost_microusd > 0) FROM latest;" \
    | tr -d '[:space:]'
}

usage_events() {
  kubectl --namespace hormuz-dependencies exec deployment/postgres -- \
    psql --username postgres --dbname hormuz --tuples-only --no-align \
      --command 'SELECT COUNT(*) FROM hormuz.gateway_usage_events' | tr -d '[:space:]'
}

gateway_topology() {
  local output=$1
  kubectl --namespace hormuz-system get pods \
    --selector='app.kubernetes.io/instance=hormuz,app.kubernetes.io/component=gateway' \
    --output=json >"${output}"
  python3 - "${output}" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as stream:
    value = json.load(stream)
items = value.get("items", [])
if len(items) != 2:
    raise SystemExit("gateway_replica_count_invalid")
nodes = set()
for item in items:
    conditions = {entry["type"]: entry["status"] for entry in item.get("status", {}).get("conditions", [])}
    if conditions.get("Ready") != "True" or item.get("status", {}).get("phase") != "Running":
        raise SystemExit("gateway_replica_not_ready")
    node = item.get("spec", {}).get("nodeName")
    if not node:
        raise SystemExit("gateway_node_missing")
    nodes.add(node)
if len(nodes) != 2:
    raise SystemExit("gateway_topology_not_spread")
print(len(nodes))
PY
}

wait_for_gateway_replacement() {
  local old_uid=$1
  local output=$2
  local attempt
  local topology
  for attempt in $(seq 1 120); do
    if topology="$(gateway_topology "${output}" 2>/dev/null)" \
      && kubectl --namespace hormuz-system get pods \
        --selector='app.kubernetes.io/instance=hormuz,app.kubernetes.io/component=gateway' \
        --output=json \
      | python3 -c 'import json,sys; old=sys.argv[1]; raise SystemExit(1 if any(i["metadata"]["uid"]==old for i in json.load(sys.stdin)["items"]) else 0)' "${old_uid}"; then
      printf '%s\n' "${topology}"
      return
    fi
    sleep 1
  done
  emit_deployment_diagnostics hormuz-system hormuz-hormuz
  fail "deleted gateway replica did not reach a distinct ready replacement"
}

[[ "${HORMUZ_KUBERNETES_PROOF_ACK:-}" == "${PROOF_ACK}" ]] \
  || fail "set HORMUZ_KUBERNETES_PROOF_ACK=${PROOF_ACK}"
[[ -n "${EVIDENCE_DIR}" ]] || fail "HORMUZ_KUBERNETES_EVIDENCE_DIR is required"
if [[ -n "${STATE_EVIDENCE}" || -n "${SOURCE_COMMIT}" ]]; then
  [[ -n "${STATE_EVIDENCE}" && -f "${STATE_EVIDENCE}" && ! -L "${STATE_EVIDENCE}" ]] \
    || fail "HORMUZ_MULTI_REPLICA_STATE_EVIDENCE must name the completed state proof"
  [[ "${SOURCE_COMMIT}" =~ ^[0-9a-f]{40}$ ]] \
    || fail "HORMUZ_SOURCE_COMMIT must be the exact 40-character source commit"
  OPERATION_PROOF_ENABLED=1
fi
[[ ! -e "${EVIDENCE_DIR}" ]] || fail "evidence output already exists"
[[ "$(uname -s)" == "Linux" ]] || fail "the v1 proof requires Linux"
host_arch="$(uname -m)"
[[ "${host_arch}" == "x86_64" || "${host_arch}" == "amd64" ]] \
  || fail "the v1 proof requires native AMD64"
command -v docker >/dev/null 2>&1 || fail "Docker is unavailable"
command -v curl >/dev/null 2>&1 || fail "curl is unavailable"
command -v openssl >/dev/null 2>&1 || fail "OpenSSL is unavailable"
command -v python3 >/dev/null 2>&1 || fail "Python 3 is unavailable"
command -v sha256sum >/dev/null 2>&1 || fail "sha256sum is unavailable"
docker_platform="$(docker info --format '{{.OSType}}/{{.Architecture}}')"
[[ "${docker_platform}" == "linux/x86_64" || "${docker_platform}" == "linux/amd64" ]] \
  || fail "the Docker daemon is not native linux/amd64"

WORK_ROOT="$(mktemp -d "${RUNNER_TEMP:-/tmp}/hormuz-kubernetes-proof.XXXXXX")"
SECRET_ROOT="${WORK_ROOT}/secrets"
ARTIFACT_ROOT="${WORK_ROOT}/artifacts"
KUBECONFIG="${WORK_ROOT}/kubeconfig"
mkdir -p "${WORK_ROOT}/bin" "${SECRET_ROOT}" "${ARTIFACT_ROOT}" "${WORK_ROOT}/chart"
chmod 0700 "${WORK_ROOT}" "${SECRET_ROOT}" "${ARTIFACT_ROOT}"
mkdir -p "${EVIDENCE_DIR}"
chmod 0700 "${EVIDENCE_DIR}"
OPERATION_EVENT_LOG="${ARTIFACT_ROOT}/multi-replica-events.log"
: >"${OPERATION_EVENT_LOG}"
chmod 0600 "${OPERATION_EVENT_LOG}"
if [[ "${OPERATION_PROOF_ENABLED}" -eq 1 ]]; then
  python3 "${ROOT}/tools/verify_multi_replica_operation.py" validate \
    --evidence "${STATE_EVIDENCE}" >/dev/null \
    || fail "shared-state evidence is invalid"
  install -m 0600 "${STATE_EVIDENCE}" "${EVIDENCE_DIR}/state-summary.json"
fi
python3 - "${FIXTURE_ROOT}/hormuz.allow.json" "${FIXTURE_ROOT}/hormuz.deny.json" \
  "${UPSTREAM_TIMEOUT_SECONDS}" <<'PY'
import json
import sys

expected = int(sys.argv[3])
for path in sys.argv[1:3]:
    with open(path, encoding="utf-8") as stream:
        if json.load(stream).get("upstream_timeout_seconds") != expected:
            raise SystemExit("upstream_timeout_fixture_mismatch")
PY
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
chmod 0755 "${WORK_ROOT}/bin/kind" "${WORK_ROOT}/bin/kubectl"
tar -xzf "${WORK_ROOT}/helm.tgz" -C "${WORK_ROOT}"
install -m 0755 "${WORK_ROOT}/linux-amd64/helm" "${WORK_ROOT}/bin/helm"

[[ "$(kind version)" == *"${KIND_VERSION}"* ]] || fail "Kind version mismatch"
[[ "$(helm version --short)" == "${HELM_VERSION}"* ]] || fail "Helm version mismatch"
kubectl_client_version="$(kubectl version --client --output=json | python3 -c 'import json,sys; print(json.load(sys.stdin)["clientVersion"]["gitVersion"])')"
[[ "${kubectl_client_version}" == "${KUBECTL_VERSION}" ]] || fail "kubectl version mismatch"
if kind get clusters | grep -Fxq "${CLUSTER_NAME}"; then
  fail "the disposable cluster name already exists"
fi

kind create cluster --name "${CLUSTER_NAME}" --config "${FIXTURE_ROOT}/kind.yaml" \
  --kubeconfig "${KUBECONFIG}" >/dev/null
CLUSTER_CREATED=1
helm upgrade --install cilium "${WORK_ROOT}/cilium.tgz" \
  --namespace kube-system \
  --values "${FIXTURE_ROOT}/cilium-values.yaml" \
  --wait --timeout 8m >/dev/null
kubectl wait --for=condition=Ready nodes --all --timeout=8m >/dev/null
kubectl get nodes --output=json >"${ARTIFACT_ROOT}/cluster-nodes.json"
python3 - "${ARTIFACT_ROOT}/cluster-nodes.json" "${KUBECTL_VERSION}" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as stream:
    items = json.load(stream).get("items", [])
if len(items) != 3:
    raise SystemExit("kind_node_count_invalid")
control_planes = 0
workers = 0
for item in items:
    labels = item.get("metadata", {}).get("labels", {})
    if "node-role.kubernetes.io/control-plane" in labels:
        control_planes += 1
    else:
        workers += 1
    info = item.get("status", {}).get("nodeInfo", {})
    if info.get("architecture") != "amd64" or info.get("operatingSystem") != "linux":
        raise SystemExit("kind_node_platform_invalid")
    if info.get("kubeletVersion") != sys.argv[2]:
        raise SystemExit("kind_kubernetes_version_invalid")
if (control_planes, workers) != (1, 2):
    raise SystemExit("kind_topology_invalid")
PY
[[ -z "$(kubectl --namespace kube-system get daemonset kindnet --ignore-not-found --output=name)" ]] \
  || fail "Kind default CNI was not disabled"
cilium_agent_image="$(kubectl --namespace kube-system get daemonset cilium --output=jsonpath='{.spec.template.spec.containers[?(@.name=="cilium-agent")].image}')"
cilium_operator_image="$(kubectl --namespace kube-system get deployment cilium-operator --output=jsonpath='{.spec.template.spec.containers[0].image}')"
[[ "${cilium_agent_image}" == "${CILIUM_AGENT_IMAGE}" ]] || fail "Cilium agent image mismatch"
[[ "${cilium_operator_image}" == "${CILIUM_OPERATOR_IMAGE}" ]] || fail "Cilium operator image mismatch"

# Let kubelet pull the exact public digest references. Kind v0.32.0 host-side
# image import loses the repository name for digest-only references and leaves
# containerd with unusable import-<date> entries (kind issue #4184). Pulling by
# digest preserves the immutable input without substituting a mutable tag.

for namespace in hormuz-system hormuz-dependencies hormuz-ingress hormuz-denied; do
  kubectl create namespace "${namespace}" >/dev/null
  [[ "$(kubectl get namespace "${namespace}" --output=jsonpath='{.metadata.labels.kubernetes\.io/metadata\.name}')" == "${namespace}" ]] \
    || fail "namespace identity label missing"
done

write_random_hex_secret "${SECRET_ROOT}/postgres-superuser-password"
write_random_hex_secret "${SECRET_ROOT}/postgres-runtime-password"
write_random_hex_secret "${SECRET_ROOT}/ingress-credential"
write_random_hex_secret "${SECRET_ROOT}/identity-token"
write_random_hex_secret "${SECRET_ROOT}/openai-api-key"
write_random_hex_secret "${SECRET_ROOT}/anthropic-api-key"
python3 - "${SECRET_ROOT}" <<'PY'
from pathlib import Path
import sys

root = Path(sys.argv[1])
superuser = (root / "postgres-superuser-password").read_text(encoding="utf-8").strip()
runtime = (root / "postgres-runtime-password").read_text(encoding="utf-8").strip()
host = "postgres.hormuz-dependencies.svc.cluster.local"
(root / "postgres-migration-dsn").write_text(
    f"postgresql://postgres:{superuser}@{host}:5432/hormuz", encoding="utf-8"
)
(root / "postgres-runtime-dsn").write_text(
    f"postgresql://hormuz_runtime:{runtime}@{host}:5432/hormuz", encoding="utf-8"
)
PY
chmod 0600 "${SECRET_ROOT}"/*

create_immutable_secret hormuz-dependencies postgres-bootstrap-v1 \
  --from-file="postgres-superuser-password=${SECRET_ROOT}/postgres-superuser-password" \
  --from-file="postgres-runtime-password=${SECRET_ROOT}/postgres-runtime-password"
create_immutable_configmap hormuz-dependencies postgres-init-v1 \
  --from-file="postgres-init.sh=${ROOT}/deploy/compose/scripts/postgres-init.sh"
create_immutable_configmap hormuz-dependencies fake-provider-v1 \
  --from-file="fake-provider.py=${FIXTURE_ROOT}/fake-provider.py"
kubectl apply --filename "${FIXTURE_ROOT}/postgres.yaml" >/dev/null
kubectl apply --filename "${FIXTURE_ROOT}/fake-provider.yaml" >/dev/null
wait_for_deployment hormuz-dependencies postgres 5m
wait_for_deployment hormuz-dependencies fake-provider 5m
wait_for_deployment hormuz-dependencies forbidden-provider 5m

create_immutable_configmap hormuz-system hormuz-config-v1 \
  --from-file="hormuz.json=${FIXTURE_ROOT}/hormuz.allow.json"
create_immutable_secret hormuz-system hormuz-migration-v1 \
  --from-file="postgres-runtime-dsn=${SECRET_ROOT}/postgres-runtime-dsn" \
  --from-file="postgres-migration-dsn=${SECRET_ROOT}/postgres-migration-dsn"
kubectl apply --filename "${FIXTURE_ROOT}/migration-job.yaml" >/dev/null
kubectl --namespace hormuz-system wait --for=condition=complete job/hormuz-migration --timeout=5m >/dev/null
kubectl --namespace hormuz-system logs job/hormuz-migration >"${ARTIFACT_ROOT}/migration.log"
kubectl --namespace hormuz-system delete job/hormuz-migration secret/hormuz-migration-v1 --wait=true >/dev/null

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
create_immutable_configmap hormuz-denied hormuz-probe-v1 \
  --from-file="probe.py=${FIXTURE_ROOT}/probe.py"
kubectl apply --filename "${FIXTURE_ROOT}/probes.yaml" >/dev/null

config_v1_sha="sha256:$(sha256sum "${FIXTURE_ROOT}/hormuz.allow.json" | awk '{print $1}')"
python3 "${ROOT}/tools/verify_helm_profile.py" validate-chart --chart "${CHART_ROOT}" >/dev/null
helm lint "${CHART_ROOT}" --values "${FIXTURE_ROOT}/helm-values.yaml" \
  --set-string "configuration.sha256=${config_v1_sha}" >/dev/null
helm package "${CHART_ROOT}" --destination "${WORK_ROOT}/chart" >/dev/null
chart_package="${WORK_ROOT}/chart/hormuz-0.1.1.tgz"
chart_package_sha256="$(sha256sum "${chart_package}" | awk '{print $1}')"
helm template hormuz "${CHART_ROOT}" --namespace hormuz-system \
  --values "${FIXTURE_ROOT}/helm-values.yaml" \
  --set-string "configuration.sha256=${config_v1_sha}" \
  >"${ARTIFACT_ROOT}/rendered-v1.yaml"
kubectl apply --dry-run=server --filename "${ARTIFACT_ROOT}/rendered-v1.yaml" >/dev/null
python3 "${ROOT}/tools/verify_helm_profile.py" assert-no-secrets \
  --artifact-root "${CHART_ROOT}" \
  --artifact "${ARTIFACT_ROOT}/rendered-v1.yaml" \
  --secret-root "${SECRET_ROOT}" >/dev/null
helm template yaml-keywords "${CHART_ROOT}" --namespace hormuz-system \
  --values "${FIXTURE_ROOT}/helm-values.yaml" \
  --set-string "configuration.name=on" \
  --set-string "configuration.key=true" \
  --set-string "configuration.sha256=${config_v1_sha}" \
  --set-string "runtimeSecret.name=on" \
  --set-string "runtimeSecret.env.NO=true" \
  --set-string "imagePullSecrets[0].name=on" \
  >"${ARTIFACT_ROOT}/rendered-yaml-keywords.yaml"
kubectl apply --dry-run=server \
  --filename "${ARTIFACT_ROOT}/rendered-yaml-keywords.yaml" >/dev/null

# Keep a failed first installation observable until its diagnostics have been
# secret-scanned. The EXIT trap deletes the entire disposable cluster, while
# later upgrades still prove Helm's atomic rollback behavior.
if ! helm upgrade --install hormuz "${CHART_ROOT}" \
  --namespace hormuz-system \
  --values "${FIXTURE_ROOT}/helm-values.yaml" \
  --set-string "configuration.sha256=${config_v1_sha}" \
  --wait --timeout 10m >/dev/null; then
  emit_deployment_diagnostics hormuz-system hormuz-hormuz
  fail "initial Hormuz release did not become ready"
fi
wait_for_serving_generation \
  hormuz-system hormuz-hormuz initial-v1 \
  hormuz-config-v1 "${config_v1_sha}" \
  hormuz-runtime-v1 conformance-generation-v1
kubectl --namespace hormuz-system get deployment,service,poddisruptionbudget,networkpolicy \
  --selector='app.kubernetes.io/instance=hormuz' --output=json \
  >"${ARTIFACT_ROOT}/installed-v1.json"
python3 "${ROOT}/tools/verify_helm_profile.py" validate-manifest \
  --manifest "${ARTIFACT_ROOT}/installed-v1.json" \
  --configuration hormuz-config-v1 \
  --configuration-sha256 "${config_v1_sha}" \
  --runtime-secret hormuz-runtime-v1 \
  --runtime-secret-revision conformance-generation-v1 >/dev/null
[[ "$(kubectl --namespace hormuz-system get configmap hormuz-config-v1 --output=jsonpath='{.immutable}')" == "true" ]] \
  || fail "configuration object is mutable"
[[ "$(kubectl --namespace hormuz-system get secret hormuz-runtime-v1 --output=jsonpath='{.immutable}')" == "true" ]] \
  || fail "runtime Secret is mutable"
distinct_gateway_nodes="$(gateway_topology "${ARTIFACT_ROOT}/gateway-topology-v1.json")"

run_probe hormuz-ingress unauthenticated-health 401
run_probe hormuz-denied denied-network network-denied
run_probe hormuz-ingress allowed-request 200
SUCCESSFUL_REQUESTS=$((SUCCESSFUL_REQUESTS + 1))
provider_stats "${ARTIFACT_ROOT}/provider-v1.json"
assert_provider_stats "${ARTIFACT_ROOT}/provider-v1.json" "${SUCCESSFUL_REQUESTS}"
usage_before_replacement="$(usage_events)"
[[ "${usage_before_replacement}" -ge 1 ]] || fail "metadata-only usage evidence was not committed"
record_operation_event service_baseline_verified

gateway_pod="$(kubectl --namespace hormuz-system get pods \
  --selector='app.kubernetes.io/instance=hormuz,app.kubernetes.io/component=gateway' \
  --output=jsonpath='{.items[0].metadata.name}')"
kubectl --namespace hormuz-system exec "${gateway_pod}" -- \
  /opt/hormuz/bin/python -I -c \
  'from urllib.request import urlopen; import sys; url="http://forbidden-provider.hormuz-dependencies.svc.cluster.local:8090/health";
try: urlopen(url, timeout=3); sys.exit(1)
except Exception: sys.exit(0)' >/dev/null \
  || fail "gateway egress reached an unapproved destination"

create_immutable_configmap hormuz-system hormuz-config-v2 \
  --from-file="hormuz.json=${FIXTURE_ROOT}/hormuz.deny.json"
create_immutable_secret hormuz-system hormuz-runtime-v2 \
  --from-file="postgres-runtime-dsn=${SECRET_ROOT}/postgres-runtime-dsn" \
  --from-file="hormuz-identity-token=${SECRET_ROOT}/identity-token" \
  --from-file="hormuz-ingress-credential=${SECRET_ROOT}/ingress-credential" \
  --from-file="openai-api-key=${SECRET_ROOT}/openai-api-key" \
  --from-file="anthropic-api-key=${SECRET_ROOT}/anthropic-api-key"
config_v2_sha="sha256:$(sha256sum "${FIXTURE_ROOT}/hormuz.deny.json" | awk '{print $1}')"
[[ "${config_v2_sha}" != "${config_v1_sha}" ]] || fail "replacement configuration was not distinct"
capture_gateway_logs v1-before-upgrade
record_operation_event restrictive_rollout_started
restrictive_rollout_started_ms="$(monotonic_ms)"
helm upgrade hormuz "${CHART_ROOT}" \
  --namespace hormuz-system \
  --values "${FIXTURE_ROOT}/helm-values.yaml" \
  --set-string "configuration.name=hormuz-config-v2" \
  --set-string "configuration.sha256=${config_v2_sha}" \
  --set-string "runtimeSecret.name=hormuz-runtime-v2" \
  --set-string "runtimeSecret.revision=conformance-generation-v2" \
  --atomic --wait --timeout 10m >/dev/null
wait_for_serving_generation \
  hormuz-system hormuz-hormuz replacement-v2 \
  hormuz-config-v2 "${config_v2_sha}" \
  hormuz-runtime-v2 conformance-generation-v2
RESTRICTIVE_ROLLOUT_CONVERGENCE_MS=$(( $(monotonic_ms) - restrictive_rollout_started_ms ))
[[ "${RESTRICTIVE_ROLLOUT_CONVERGENCE_MS}" -gt 0 ]] || RESTRICTIVE_ROLLOUT_CONVERGENCE_MS=1
record_operation_event restrictive_generation_converged
run_probe hormuz-ingress denied-request 403
POLICY_DENIALS=$((POLICY_DENIALS + 1))
provider_stats "${ARTIFACT_ROOT}/provider-after-deny.json"
assert_provider_stats "${ARTIFACT_ROOT}/provider-after-deny.json" "${SUCCESSFUL_REQUESTS}"

capture_gateway_logs v2-before-rollback
record_operation_event rollback_started
rollback_started_ms="$(monotonic_ms)"
helm rollback hormuz 1 --namespace hormuz-system --wait --timeout 10m --cleanup-on-fail >/dev/null
wait_for_serving_generation \
  hormuz-system hormuz-hormuz rollback-v1 \
  hormuz-config-v1 "${config_v1_sha}" \
  hormuz-runtime-v1 conformance-generation-v1
ROLLBACK_CONVERGENCE_MS=$(( $(monotonic_ms) - rollback_started_ms ))
[[ "${ROLLBACK_CONVERGENCE_MS}" -gt 0 ]] || ROLLBACK_CONVERGENCE_MS=1
record_operation_event permissive_generation_converged
run_probe hormuz-ingress allowed-request 200
SUCCESSFUL_REQUESTS=$((SUCCESSFUL_REQUESTS + 1))
provider_stats "${ARTIFACT_ROOT}/provider-after-rollback.json"
assert_provider_stats "${ARTIFACT_ROOT}/provider-after-rollback.json" "${SUCCESSFUL_REQUESTS}"

old_pod="$(kubectl --namespace hormuz-system get pods \
  --selector='app.kubernetes.io/instance=hormuz,app.kubernetes.io/component=gateway' \
  --sort-by=.metadata.name --output=jsonpath='{.items[0].metadata.name}')"
old_uid="$(kubectl --namespace hormuz-system get pod "${old_pod}" --output=jsonpath='{.metadata.uid}')"
PROBE_SEQUENCE=$((PROBE_SEQUENCE + 1))
replacement_job="replacement-traffic-${PROBE_SEQUENCE}"
kubectl --namespace hormuz-ingress create job "${replacement_job}" \
  --from=cronjob/replacement-traffic >/dev/null
wait_for_job_log_marker hormuz-ingress "${replacement_job}" '{"event":"traffic_started"}'
capture_gateway_logs v1-before-replica-deletion
kubectl --namespace hormuz-system delete pod "${old_pod}" --wait=false >/dev/null
kubectl --namespace hormuz-system wait --for=delete "pod/${old_pod}" --timeout=5m >/dev/null
distinct_gateway_nodes="$(wait_for_gateway_replacement \
  "${old_uid}" "${ARTIFACT_ROOT}/gateway-topology-replacement.json")"
kubectl --namespace hormuz-system rollout status deployment/hormuz-hormuz --timeout=10m >/dev/null
if [[ "$(kubectl --namespace hormuz-ingress get "job/${replacement_job}" \
  --output=jsonpath='{.status.active}')" != "1" ]]; then
  emit_job_diagnostics hormuz-ingress "${replacement_job}"
  fail "synthetic traffic did not remain active through replica replacement"
fi
if ! kubectl --namespace hormuz-ingress wait --for=condition=complete \
  "job/${replacement_job}" --timeout=90s >/dev/null; then
  emit_job_diagnostics hormuz-ingress "${replacement_job}"
  fail "replacement traffic did not complete"
fi
kubectl --namespace hormuz-ingress logs "job/${replacement_job}" \
  >"${ARTIFACT_ROOT}/${replacement_job}.jsonl"
replacement_successes="$(python3 - "${ARTIFACT_ROOT}/${replacement_job}.jsonl" <<'PY'
import json
import sys
values = []
with open(sys.argv[1], encoding="utf-8") as stream:
    for line in stream:
        if line.strip():
            values.append(json.loads(line))
if not values or values[0] != {"event": "traffic_started"}:
    raise SystemExit("replacement_traffic_start_invalid")
summary = values[-1]
if (
    summary.get("command") != "replacement-traffic"
    or summary.get("failed_requests") != 0
    or not isinstance(summary.get("successful_requests"), int)
    or summary["successful_requests"] < 2
):
    raise SystemExit("replacement_traffic_summary_invalid")
print(summary["successful_requests"])
PY
)"
SUCCESSFUL_REQUESTS=$((SUCCESSFUL_REQUESTS + replacement_successes))
capture_gateway_logs v1-after-replica-replacement
kubectl --namespace hormuz-ingress delete "job/${replacement_job}" --wait=true >/dev/null

provider_stats "${ARTIFACT_ROOT}/provider-final.json"
assert_provider_stats "${ARTIFACT_ROOT}/provider-final.json" "${SUCCESSFUL_REQUESTS}"
usage_event_count="$(usage_events)"
[[ "${usage_event_count}" -ge "${SUCCESSFUL_REQUESTS}" ]] \
  || fail "metadata-only usage evidence did not survive rolling changes"

# Preserve the already-closed #108 summary at its exact v1 boundary. The
# stronger #103 proof below writes a separate schema and may intentionally
# create one ambiguous provider outcome that is not a successful request.
reference_successful_requests="${SUCCESSFUL_REQUESTS}"
reference_policy_denials="${POLICY_DENIALS}"
reference_usage_events="${usage_event_count}"

if [[ "${OPERATION_PROOF_ENABLED}" -eq 1 ]]; then
# Graceful drain: pin one provider request to the Service-selected replica,
# terminate that exact Pod, prove it leaves readiness and the Service before a
# sibling accepts new work, then release the provider response and require the
# pinned handler to finish its evidence write before the old Pod disappears.
PROBE_SEQUENCE=$((PROBE_SEQUENCE + 1))
graceful_job="graceful-drain-${PROBE_SEQUENCE}"
kubectl --namespace hormuz-ingress create job "${graceful_job}" \
  --from=cronjob/blocking-request >/dev/null
wait_for_job_log_marker \
  hormuz-ingress "${graceful_job}" '{"event":"blocking_request_started"}'
graceful_gateway_ip="$(wait_for_provider_block "${ARTIFACT_ROOT}/graceful-block-status.json")"
graceful_pod="$(gateway_pod_for_ip "${graceful_gateway_ip}")"
graceful_uid="$(kubectl --namespace hormuz-system get pod "${graceful_pod}" \
  --output=jsonpath='{.metadata.uid}')"
record_operation_event graceful_inflight_started
graceful_started_ms="$(monotonic_ms)"
kubectl --namespace hormuz-system delete pod "${graceful_pod}" --wait=false >/dev/null
GRACEFUL_READINESS_WITHDRAWAL_MS="$(wait_for_service_exclusion \
  "${graceful_pod}" "${graceful_gateway_ip}" "${graceful_started_ms}")"
record_operation_event graceful_replica_withdrew_readiness
run_probe hormuz-ingress allowed-request 200
SUCCESSFUL_REQUESTS=$((SUCCESSFUL_REQUESTS + 1))
record_operation_event sibling_service_request_succeeded
provider_release_block
if ! kubectl --namespace hormuz-ingress wait --for=condition=complete \
  "job/${graceful_job}" --timeout=90s >/dev/null; then
  emit_job_diagnostics hormuz-ingress "${graceful_job}"
  fail "gracefully drained request did not complete"
fi
kubectl --namespace hormuz-ingress logs "job/${graceful_job}" \
  >"${ARTIFACT_ROOT}/${graceful_job}.jsonl"
python3 - "${ARTIFACT_ROOT}/${graceful_job}.jsonl" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as stream:
    values = [json.loads(line) for line in stream if line.strip()]
if values[0] != {"event": "blocking_request_started"}:
    raise SystemExit("graceful_drain_start_invalid")
if values[-1].get("command") != "blocking-request" or values[-1].get("status") != 200:
    raise SystemExit("graceful_drain_result_invalid")
PY
SUCCESSFUL_REQUESTS=$((SUCCESSFUL_REQUESTS + 1))
record_operation_event graceful_inflight_finalized
GRACEFUL_INFLIGHT_DRAIN_MS=$(( $(monotonic_ms) - graceful_started_ms ))
[[ "${GRACEFUL_INFLIGHT_DRAIN_MS}" -gt 0 ]] || GRACEFUL_INFLIGHT_DRAIN_MS=1
distinct_gateway_nodes="$(wait_for_gateway_replacement \
  "${graceful_uid}" "${ARTIFACT_ROOT}/gateway-topology-graceful-drain.json")"
record_operation_event graceful_replacement_ready
kubectl --namespace hormuz-ingress delete "job/${graceful_job}" --wait=true >/dev/null
provider_reset_block

# Abrupt loss: switch the same synthetic client to expect an ambiguous
# transport, force-delete the exact Pod that reached provider egress, and
# require a distinct ready replacement. After the reservation stale boundary,
# one new Service request invokes the durable sweeper; the original attempt must
# remain outcome_unknown with its uncertain reservation and no provider replay.
kubectl --namespace hormuz-ingress patch cronjob blocking-request \
  --type=json \
  --patch='[{"op":"replace","path":"/spec/jobTemplate/spec/template/spec/containers/0/args/2","value":"ambiguous-request"}]' \
  >/dev/null
PROBE_SEQUENCE=$((PROBE_SEQUENCE + 1))
abrupt_job="abrupt-loss-${PROBE_SEQUENCE}"
kubectl --namespace hormuz-ingress create job "${abrupt_job}" \
  --from=cronjob/blocking-request >/dev/null
wait_for_job_log_marker \
  hormuz-ingress "${abrupt_job}" '{"event":"blocking_request_started"}'
abrupt_gateway_ip="$(wait_for_provider_block "${ARTIFACT_ROOT}/abrupt-block-status.json")"
abrupt_pod="$(gateway_pod_for_ip "${abrupt_gateway_ip}")"
abrupt_uid="$(kubectl --namespace hormuz-system get pod "${abrupt_pod}" \
  --output=jsonpath='{.metadata.uid}')"
record_operation_event ambiguous_inflight_started
abrupt_started_ms="$(monotonic_ms)"
kubectl --namespace hormuz-system delete pod "${abrupt_pod}" \
  --grace-period=0 --force --wait=false >/dev/null
record_operation_event owning_replica_force_deletion_requested
wait_for_provider_disconnect "${ARTIFACT_ROOT}/abrupt-disconnect-status.json"
record_operation_event abrupt_gateway_connection_closed
provider_release_block
if ! kubectl --namespace hormuz-ingress wait --for=condition=complete \
  "job/${abrupt_job}" --timeout=90s >/dev/null; then
  emit_job_diagnostics hormuz-ingress "${abrupt_job}"
  fail "force-killed request did not report an ambiguous transport"
fi
kubectl --namespace hormuz-ingress logs "job/${abrupt_job}" \
  >"${ARTIFACT_ROOT}/${abrupt_job}.jsonl"
python3 - "${ARTIFACT_ROOT}/${abrupt_job}.jsonl" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as stream:
    values = [json.loads(line) for line in stream if line.strip()]
if values[0] != {"event": "blocking_request_started"}:
    raise SystemExit("abrupt_loss_start_invalid")
if values[-1] != {"command": "ambiguous-request", "transport_outcome": "ambiguous"}:
    raise SystemExit("abrupt_loss_result_invalid")
PY
distinct_gateway_nodes="$(wait_for_gateway_replacement \
  "${abrupt_uid}" "${ARTIFACT_ROOT}/gateway-topology-abrupt-loss.json")"
ABRUPT_REPLACEMENT_CONVERGENCE_MS=$(( $(monotonic_ms) - abrupt_started_ms ))
[[ "${ABRUPT_REPLACEMENT_CONVERGENCE_MS}" -gt 0 ]] || ABRUPT_REPLACEMENT_CONVERGENCE_MS=1
record_operation_event abrupt_replacement_ready
provider_reset_block
sleep "$((REQUEST_ATTEMPT_STALE_SECONDS + 2))"
run_probe hormuz-ingress allowed-request 200
SUCCESSFUL_REQUESTS=$((SUCCESSFUL_REQUESTS + 1))
uncertainty="$(request_attempt_uncertainty)"
OUTCOME_UNKNOWN_ATTEMPTS="${uncertainty%%|*}"
UNCERTAIN_RESERVATIONS="${uncertainty#*|}"
[[ "${OUTCOME_UNKNOWN_ATTEMPTS}" == "1" ]] \
  || fail "abrupt provider attempt state invalid: outcome_unknown=${OUTCOME_UNKNOWN_ATTEMPTS}"
[[ "${UNCERTAIN_RESERVATIONS}" == "1" ]] \
  || fail "ambiguous estimated consumption invalid: reservations=${UNCERTAIN_RESERVATIONS}"
record_operation_event ambiguous_attempt_preserved_unknown
kubectl --namespace hormuz-ingress delete "job/${abrupt_job}" --wait=true >/dev/null

operation_provider_requests=$((SUCCESSFUL_REQUESTS + OUTCOME_UNKNOWN_ATTEMPTS))
provider_stats "${ARTIFACT_ROOT}/provider-operation-final.json"
assert_provider_stats \
  "${ARTIFACT_ROOT}/provider-operation-final.json" \
  "${operation_provider_requests}" 2
operation_usage_event_count="$(usage_events)"
[[ "${operation_usage_event_count}" -ge "${SUCCESSFUL_REQUESTS}" ]] \
  || fail "multi-replica usage evidence count is incomplete"
record_operation_event final_service_and_evidence_verified
capture_gateway_logs operation-final
fi

kubectl --namespace hormuz-dependencies logs deployment/postgres >"${ARTIFACT_ROOT}/postgres.log"
kubectl --namespace hormuz-dependencies logs deployment/fake-provider >"${ARTIFACT_ROOT}/fake-provider.log"
helm get values hormuz --namespace hormuz-system --output=json >"${ARTIFACT_ROOT}/installed-values.json"
python3 "${ROOT}/tools/verify_helm_profile.py" assert-no-secrets \
  --artifact-root "${ARTIFACT_ROOT}" \
  --secret-root "${SECRET_ROOT}" >/dev/null

helm uninstall hormuz --namespace hormuz-system --wait >/dev/null
remaining_chart_objects="$(kubectl --namespace hormuz-system get deployment,service,poddisruptionbudget,networkpolicy \
  --selector='app.kubernetes.io/instance=hormuz' --output=name)"
[[ -z "${remaining_chart_objects}" ]] || fail "chart-owned resources remained after uninstall"
kubectl delete namespace hormuz-system hormuz-dependencies hormuz-ingress hormuz-denied --wait=true >/dev/null
kind delete cluster --name "${CLUSTER_NAME}" >/dev/null
CLUSTER_CREATED=0

docker_engine_version="$(docker version --format '{{.Server.Version}}')"
python3 "${ROOT}/tools/verify_helm_profile.py" write-evidence \
  --output "${EVIDENCE_DIR}/summary.json" \
  --docker-engine "${docker_engine_version}" \
  --chart-package-sha256 "${chart_package_sha256}" \
  --gateway-replicas 2 \
  --distinct-gateway-nodes "${distinct_gateway_nodes}" \
  --successful-requests "${reference_successful_requests}" \
  --policy-denials "${reference_policy_denials}" \
  --provider-requests "${reference_successful_requests}" \
  --usage-events "${reference_usage_events}" >/dev/null
python3 "${ROOT}/tools/verify_helm_profile.py" validate-evidence \
  --evidence "${EVIDENCE_DIR}/summary.json" >/dev/null
if [[ "${OPERATION_PROOF_ENABLED}" -eq 1 ]]; then
  python3 "${ROOT}/tools/verify_multi_replica_operation.py" write-operation-proof \
    --output "${EVIDENCE_DIR}/multi-replica-summary.json" \
    --source-commit "${SOURCE_COMMIT}" \
    --kubernetes-evidence "${EVIDENCE_DIR}/summary.json" \
    --state-evidence "${EVIDENCE_DIR}/state-summary.json" \
    --event-log "${OPERATION_EVENT_LOG}" \
    --restrictive-rollout-convergence-ms "${RESTRICTIVE_ROLLOUT_CONVERGENCE_MS}" \
    --rollback-convergence-ms "${ROLLBACK_CONVERGENCE_MS}" \
    --graceful-readiness-withdrawal-ms "${GRACEFUL_READINESS_WITHDRAWAL_MS}" \
    --graceful-inflight-drain-ms "${GRACEFUL_INFLIGHT_DRAIN_MS}" \
    --abrupt-replacement-convergence-ms "${ABRUPT_REPLACEMENT_CONVERGENCE_MS}" \
    --successful-requests "${SUCCESSFUL_REQUESTS}" \
    --policy-denials "${POLICY_DENIALS}" \
    --provider-requests "${operation_provider_requests}" \
    --usage-events "${operation_usage_event_count}" \
    --outcome-unknown-attempts "${OUTCOME_UNKNOWN_ATTEMPTS}" \
    --uncertain-reservations "${UNCERTAIN_RESERVATIONS}" >/dev/null
  python3 "${ROOT}/tools/verify_multi_replica_operation.py" validate \
    --evidence "${EVIDENCE_DIR}/multi-replica-summary.json" >/dev/null
fi
python3 "${ROOT}/tools/verify_helm_profile.py" assert-no-secrets \
  --artifact-root "${EVIDENCE_DIR}" \
  --secret-root "${SECRET_ROOT}" >/dev/null
chmod 0600 "${EVIDENCE_DIR}"/*.json
printf 'verified disposable Kubernetes reference: chart=%s replicas=2 cni=cilium-%s coordinated-events=%s\n' \
  "${chart_package_sha256}" "${CILIUM_VERSION}" "${OPERATION_EVENT_SEQUENCE}"
