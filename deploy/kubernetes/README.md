# Optional Kubernetes + Helm reference

This profile runs the exact signed Hormuz OCI digest as two gateway replicas
behind one internal `ClusterIP` Service. The chart is vendor-neutral: every
rendered object uses a standard Kubernetes API, and no CNI, cloud, ingress
controller, certificate manager, database operator, IdP, provider, or custody
backend is part of the chart.

The signed OCI digest remains the Hormuz application contract. Kubernetes and
Helm are optional deployment tooling. The first executable reference uses
Kind `v0.32.0`, Kubernetes `v1.36.1`, Helm `v3.21.4`, and Cilium `1.20.1` in a
disposable account-free cluster. Cilium is the first tested CNI, not a chart or
product dependency.

## Ownership boundary

The chart creates only these Hormuz resources:

- one `apps/v1` Deployment with at least two replicas;
- one private `ClusterIP` Service;
- one `policy/v1` PodDisruptionBudget;
- standard `networking.k8s.io/v1` NetworkPolicies.

Customer operators provide and operate:

- a PostgreSQL database that has already received the compatible Hormuz
  migration through a separate migration credential;
- one immutable, generation-scoped ConfigMap containing `hormuz.json`;
- one immutable, generation-scoped Secret containing the restricted runtime
  PostgreSQL DSN, private-hop credential, and configured identity/provider
  inputs;
- public TLS termination and the authenticated, source-restricted private hop;
- generic OIDC issuer/JWKS configuration and routing when OIDC is enabled;
- model-provider and optional custody endpoints, credentials, and operations;
- image admission, registry mirroring, monitoring, backup, recovery, and
  cluster operations.

The chart renders no Secret and no PostgreSQL resource. It never receives a
migration DSN. The customer may mirror the signed application digest to a
different registry by changing `image.repository`; this chart version still
requires the exact signed digest from the `v0.1.1` OCI contract.

## Security defaults

The gateway is numeric non-root, read-only, `RuntimeDefault` seccomp, and drops
all capabilities. It receives no Kubernetes API token. CPU and memory are
bounded. An init container runs `hormuz doctor` with the same immutable runtime
inputs before a replacement can become ready. Rolling updates require zero
unavailable replicas, the readiness contract, a disruption budget, and
`DoNotSchedule` topology spread across `kubernetes.io/hostname` by default.
The spread constraint honors node affinity and taints, so nodes on which the
gateway cannot schedule do not create a rollout deadlock by being counted as
empty topology domains.

The chart schedules only on Linux AMD64 because that is the only signed Hormuz
OCI platform currently supported. Issue
[#109](https://github.com/Xpounder-com/hormuz/issues/109) is the separate ARM64
gate; a mutable tag or a different digest is rejected by this chart version.

Network policy starts fail-closed:

- all ingress and egress to gateway Pods is denied;
- DNS to CoreDNS is allowed on TCP/UDP 53;
- customer-supplied standard NetworkPolicy ingress rules admit only the
  selected private TLS proxy or mesh workload;
- customer-supplied standard NetworkPolicy egress rules admit only the
  selected PostgreSQL, IdP, provider, and optional custody destinations.

Standard NetworkPolicy does not define hostname rules. A customer with dynamic
external endpoints must provide stable CIDRs, a controlled egress proxy, or a
separately governed CNI/network control. The chart deliberately does not emit a
CiliumNetworkPolicy or another vendor extension.

## Prepare protected inputs

Verify the exact OCI signature and attestations described in
[OCI.md](../../docs/OCI.md) before deployment. Prepare protected files without
placing values in Helm values, shell arguments, URLs, logs, or source control.
Then create generation-scoped immutable objects from file paths:

```bash
kubectl create namespace hormuz-system

kubectl --namespace hormuz-system create configmap hormuz-config-v1 \
  --from-file=hormuz.json=/protected/hormuz.json
kubectl --namespace hormuz-system patch configmap hormuz-config-v1 \
  --type=merge --patch '{"immutable":true}'

kubectl --namespace hormuz-system create secret generic hormuz-runtime-v1 \
  --from-file=postgres-runtime-dsn=/protected/postgres-runtime-dsn \
  --from-file=hormuz-ingress-credential=/protected/hormuz-ingress-credential
kubectl --namespace hormuz-system patch secret hormuz-runtime-v1 \
  --type=merge --patch '{"immutable":true}'
```

Add only the identity and provider keys that the reviewed Hormuz configuration
actually names. An OIDC-only configuration need not carry a static employee
token. Secret values never belong in a Helm values file.

Copy `deploy/helm/hormuz/values.yaml` to a protected operator workspace. Set
only object names, the SHA-256 of the exact reviewed configuration bytes, a
non-secret rollout revision, the desired image repository, and standard
NetworkPolicy rules. Keep the exact image digest unchanged.

## Install and inspect

Run the database migration separately with a restricted migration credential.
The normal gateway runtime must never receive it. Then render, inspect, and
install the chart:

```bash
helm lint deploy/helm/hormuz --values /protected/hormuz-values.yaml
helm template hormuz deploy/helm/hormuz \
  --namespace hormuz-system \
  --values /protected/hormuz-values.yaml > /protected/hormuz-rendered.yaml
helm upgrade --install hormuz deploy/helm/hormuz \
  --namespace hormuz-system \
  --values /protected/hormuz-values.yaml \
  --atomic --wait --timeout 10m
kubectl --namespace hormuz-system rollout status \
  deployment/hormuz-hormuz --timeout=10m
```

The rendered output must contain no Secret object or secret value. The Service
must remain `ClusterIP`; put a customer-controlled TLS ingress or mesh in front
of it. That ingress must strip any caller-supplied
`X-Hormuz-Ingress-Credential`, inject the protected backend value, and be the
only workload admitted by the ingress NetworkPolicy. Employee authentication
remains a separate static-token or generic OIDC bearer-JWT check.

Browser login, cookies, refresh-token custody, and a Hormuz browser session
broker are outside this profile.

## Replacement and rollback

Never mutate an in-use ConfigMap or Secret. Create a new immutable generation,
validate it, update `configuration.name`, `configuration.sha256`,
`runtimeSecret.name`, and `runtimeSecret.revision`, then run a readiness-gated
`helm upgrade --atomic --wait`. Then require `kubectl rollout status` to report
the Deployment complete before asserting or depending on the new policy. Helm's
wait condition can be satisfied while old ready replicas still participate in
a rolling replacement. Keep the prior immutable objects until the rollback
window closes.

Rollback reactivates a known Helm revision and its exact prior object names:

```bash
helm history hormuz --namespace hormuz-system
helm rollback hormuz EXACT_REVISION \
  --namespace hormuz-system --wait --timeout 10m --cleanup-on-fail
kubectl --namespace hormuz-system rollout status \
  deployment/hormuz-hormuz --timeout=10m
```

The chart's 660-second termination grace exceeds the default proof upstream
timeout and lets Hormuz withdraw readiness before draining accepted handlers.
A platform force-kill can still interrupt a stream.

## Disposable executable proof

The deployment-profile proof remains directly runnable on Linux AMD64:

```bash
HORMUZ_KUBERNETES_PROOF_ACK=I_UNDERSTAND_THIS_IS_A_DISPOSABLE_KUBERNETES_REFERENCE_PROOF \
HORMUZ_KUBERNETES_EVIDENCE_DIR=/protected/new/output-directory \
  ./tools/verify_helm_profile.sh
```

The #103 release gate adds a focused shared-state proof and binds it to that
same live cluster run. CI first runs:

```bash
HORMUZ_TEST_POSTGRES_DSN="$PROTECTED_DISPOSABLE_POSTGRES_DSN" \
  python tools/verify_multi_replica_operation.py run-state-proof \
    --postgres-image 'postgres@sha256:57c72fd2a128e416c7fcc499958864df5301e940bca0a56f58fddf30ffc07777' \
    --source-commit "$EXACT_SOURCE_COMMIT" \
    --output /protected/state-summary.json
```

It then supplies the protected summary and exact commit through
`HORMUZ_MULTI_REPLICA_STATE_EVIDENCE` and `HORMUZ_SOURCE_COMMIT` when invoking
`verify_helm_profile.sh`. Supplying only one of those inputs fails closed.

The proof verifies every downloaded binary or chart against an exact SHA-256,
creates one Kind control plane and two workers with the exact node-image
digest, installs the exact digest-pinned Cilium chart, and then installs the
Hormuz chart from clean inputs. Cluster nodes pull the public Hormuz and
PostgreSQL images directly by immutable digest; the proof never replaces those
references with mutable tags. It proves two ready replicas on distinct workers,
authenticated ingress, provider-shaped fake traffic, policy and
metadata-only evidence persistence, ingress and egress denial, readiness-gated
configuration/Secret replacement and rollback, and one graceful Pod deletion
after sustained synthetic traffic has started. At each rollout boundary it
requires the Deployment's observed generation and replica counts to be complete
and every ready, non-terminating Pod to reference the same expected immutable
configuration and runtime Secret generation. The proof requires replacement
traffic to remain active until two distinct ready replicas—including a new Pod
UID—are observed, then requires every request to have succeeded. Gateway and
preflight logs are captured and secret-scanned before each revision change or
deletion and once more after replacement. The proof then removes the chart and
cluster. It contacts no model provider or external IdP.

The coordinated-operation extension also runs eight named tests against a
disposable PostgreSQL backend. They exercise two independent gateway process
pools, atomic organization budget reservations, immutable policy activation,
the append-only request-attempt ledger, concurrent per-tenant audit-chain
sequencing, two-person custody approval, barrier acknowledgements, duplicate
notification idempotence, stale acknowledgement rejection, and five-second
partition fencing.

In the live cluster, one Service-routed request is held at the fake provider
while its exact gateway Pod receives a normal termination. The proof requires
that Pod to leave readiness and the Service, a sibling to admit new work, and
the pinned request to finish its evidence write before the old Pod disappears.
A second Service-routed request is held after provider egress and its exact Pod
is force-deleted. The provider observes one call; Hormuz never replays it. Once
the reservation reaches its stale boundary, a later request invokes the durable
sweeper and the original attempt must remain `outcome_unknown` with one
uncertain reservation.
The replacement must become ready on the two-worker topology in both cases.

The base run retains the strict mode-`0600`
`hormuz.kubernetes-reference-proof` v1 summary. The coordinated gate also
retains a strict `hormuz.multi-replica-state-proof` v1 summary and one
`hormuz.multi-replica-operation-proof` v1 summary. The final summary binds the
other two by SHA-256, the exact source commit, signed image digest, chart
digest, fixed event sequence, measured rollout/drain/replacement durations,
state counts, retry/session boundary, and nonclaims. Rendered manifests, logs,
synthetic configuration, generated credentials, and raw observations remain
temporary and are deleted.

## Exact PostgreSQL HA conformance reference

The separate `PostgreSQL HA failover reference` job runs the
[issue #104 fixture](conformance/postgres-ha/README.md). CloudNativePG is
verification infrastructure only: it is never rendered by this chart, and
Hormuz still receives a generic PostgreSQL HA DSN through the existing runtime
Secret.

The account-free proof creates one Kind control plane, three tainted database
workers, and two separate gateway workers. It pins CloudNativePG `1.30.0`,
three PostgreSQL `16.15` instances, synchronous `ANY 1`, required durability,
failover quorum, primary Lease coordination, isolation fencing, and exact OCI
digests. Both gateway replicas retain pool bounds of 1-4 connections, a
five-second acquisition timeout, eight queued waiters, and a 15-second
reconnect horizon.

The positive path abruptly pauses the active primary's worker while one
provider request is in flight. Both gateways must become unready and return the
content-free `hormuz_storage_unavailable` classification under concurrent
load without adding provider calls. A safe replica must become primary, own
the Lease and read/write endpoint, and serve the same two gateway processes.
Policy activation, budget reservations, request attempts, usage/secret
evidence, custody administrators and restrictions, audit-chain integrity, and
tenant isolation are checked before and after promotion. The ambiguous request
must remain pending or unknown with uncertain consumption, and Hormuz must not
replay it. The former primary is then allowed to return only as a replica.

The negative path pauses the primary and one replica. The remaining replica
cannot satisfy failover quorum, no ready read/write endpoint may remain, and
both gateways must stay unready and deny provider egress throughout a measured
observation window. Recovery begins only after quorum is restored.

Only the strict mode-`0600`, content-free
`hormuz.postgresql-ha-reference-proof` v1 summary is retained. It records the
exact source, images, manifest checksum, chart digest, topology, fixed event
sequence, state counts, timings, checks, and nonclaims. It contains no DSN,
credential, request content, raw state, log, or customer identifier.

## Exact nonclaims

This disposable proof does not certify Kubernetes, Helm, Cilium, Kind,
PostgreSQL, public TLS, an ingress implementation, an IdP, a model provider, a
cloud, a customer cluster, or customer operations. It makes no broad
CNI-portability, universal HA, RPO, RTO, multi-region, or zone-failure claim.
The exact #104 fixture proves only its pinned PostgreSQL promotion and
quorum-loss behaviors. It does not prove production storage durability,
autoscaling, capacity, browser sessions, provider exactly-once semantics, or
zero interruption for a force-killed in-flight stream. The complete recovery
rehearsal remains a separate release gate under
[#105](https://github.com/Xpounder-com/hormuz/issues/105).
