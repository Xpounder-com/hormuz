# Exact PostgreSQL HA conformance fixture

This directory is verification infrastructure for Hormuz issue #104. It is
not rendered or installed by the Hormuz Helm chart and is not part of the
product contract. The chart continues to consume only a generic PostgreSQL DSN
from an existing Secret.

The account-free fixture pins CloudNativePG `1.30.0`, PostgreSQL `16.15`, Kind
`v0.32.0` / Kubernetes `v1.36.1`, Helm `v3.21.4`, Cilium `1.20.1`, and the
published Hormuz `linux/amd64` digest. The operator manifest is accepted only
at its exact SHA-256 and is rewritten to the exact Linux AMD64 operator image
manifest before installation. PostgreSQL uses and verifies the exact published
Linux AMD64 image-manifest digest.

The disposable topology contains three PostgreSQL instances on three tainted
database workers and two Hormuz replicas on two different workers. It enables
synchronous `ANY 1`, required durability, failover quorum, primary Lease
coordination, and isolation fencing.

The live verifier proves two bounded scenarios:

1. After an unexpected primary-worker pause, both gateways withdraw readiness
   and deny concurrent governed requests before provider egress. CloudNativePG
   promotes a safe replica, the Lease and read/write endpoint converge, the
   same gateway processes reconnect, durable governed state remains intact,
   and no provider request is replayed. Immediately after the pause, the fake
   provider records an in-flight request and closes its connection without a
   response. The client outcome remains ambiguous while the pre-egress attempt
   and reservation remain durably uncertain.
2. After the primary and one replica worker are paused, failover quorum blocks
   promotion, the read/write endpoint has no ready address, both gateways stay
   unready and deny provider egress, and the surviving replica is not exposed
   as a writable primary. Normal operation returns only after quorum is
   restored.

Run it only on native Linux AMD64 with a disposable Docker daemon:

```bash
HORMUZ_POSTGRES_HA_PROOF_ACK=I_UNDERSTAND_THIS_IS_A_DISPOSABLE_POSTGRESQL_HA_REFERENCE_PROOF \
HORMUZ_POSTGRES_HA_EVIDENCE_DIR=/protected/new/output-directory \
HORMUZ_SOURCE_COMMIT="$EXACT_SOURCE_COMMIT" \
  ./tools/verify_postgres_ha_reference.sh
```

The output directory must not already exist. The verifier retains only a
strict mode-`0600`, metadata-only `hormuz.postgresql-ha-reference-proof` v1
summary. Generated credentials, DSNs, state snapshots, raw observations,
rendered manifests, and the disposable cluster are deleted.

This verifies only the exact pinned single-host Kind combination. It does not
certify customer PostgreSQL operations, managed PostgreSQL services, broad
Kubernetes/CNI portability, hardware or zone failure, production storage,
backup/retention, RPO/RTO, disaster recovery, or a customer SLA.
