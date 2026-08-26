# Kubernetes enterprise-reference disaster recovery

Hormuz's v1 recovery contract is an operator-controlled procedure around the
signed OCI application, a customer-operated PostgreSQL endpoint, and
customer-owned configuration, secrets, keys, and immutable audit anchors. The
Helm chart deploys only Hormuz. It does not create a database, backup system,
KMS, Object Lock store, ingress, or recovery authority.

The exact disposable rehearsal is implemented by
`tools/verify_disaster_recovery_reference.sh`. A passing artifact proves only
the pinned, account-free reference combination recorded in that artifact. It
does not establish a customer SLA or certify a customer's infrastructure.

## Frozen objectives and clocks

The acceptance criteria from ADR 0009 are:

- RPO: at most 300 seconds between failure injection and the latest recovered
  committed marker for every #105 state class.
- Internal RTO: at most 3,600 seconds from authorized recovery execution start
  until the isolated recovered application passes all admission checks and is
  ready for controlled promotion.
- Complete recovery time: always publish the elapsed time from failure
  injection through detection, declaration, authorization, restore, admission,
  promotion, and the first successful governed request.

The internal RTO is not presented as the full outage duration. The eight
contract-required clock events plus recovered-database readiness, successful
admission, and completion of the required failure paths are retained. Every
reported phase duration is derived from adjacent timestamps, so the phases
span the complete end-to-end clock without hidden intervals.

## Reference backup and retention policy

The v1 reference policy is deliberately explicit:

| Control | Reference requirement |
| --- | --- |
| Physical base backup | At least daily; the executable rehearsal takes one before failure injection. |
| WAL archive | Continuous; archive lag and missing segments alert before expiry. |
| Base-backup retention | 35 days. |
| WAL retention | 35 days and never shorter than the retained base backup's recovery window. |
| Encryption | Customer-controlled encryption at rest and in transit is required for backup, archive, configuration, secret-envelope, and anchor stores. |
| Backup writer | May write backup and WAL objects; cannot restore, promote traffic, change retention, or use the Hormuz runtime role. |
| Restore operator | Separately authorized human or recovery service may create an isolated target and run validation; it cannot silently promote traffic. |
| Hormuz runtime | Cannot create backups, restore PostgreSQL, change recovery targets, or promote traffic. |
| Monitoring | Alert on base-backup age, WAL archival lag, failed verification, failed restore rehearsals, storage expiry, and evidence-anchor age. |
| Expiry | Expire only after the complete base-backup/WAL recovery window. Never shorten custody-event or immutable audit-anchor retention. |

Production schedules may be stricter, but changing them does not change the
reference claim. Customers remain responsible for capacity, encryption keys,
retention locks, monitoring delivery, and restore authorization.
The disposable job verifies recovery behavior; it does not certify that its
temporary runner storage enforces a production customer's encryption or
retention controls.

## Recovery roles

- The incident commander declares the disaster, freezes the source, owns the
  timeline, and authorizes recovery execution.
- The database recovery operator obtains the approved backup set, verifies its
  manifest, restores it into an isolated endpoint, and has no provider or
  Hormuz policy entitlement.
- The custody recovery operator verifies that the customer KMS can decrypt a
  protected canary using a non-administrative key capability. A KMS root or
  key-administration token is never delivered to the gateway.
- The Hormuz admission operator runs the content-free state comparison. It
  cannot rewrite source snapshots, audit history, custody lifecycle events, or
  checkpoint objects.
- The traffic owner promotes the already admitted application. Promotion is a
  distinct act after review; it is not available to the application runtime.

Two-person approval should protect irreversible customer-side key retirement,
recovery resolution, retention changes, and destructive post-incident cleanup.

## Rehearsal procedure

1. Confirm the exact signed Hormuz digest, chart digest, PostgreSQL image,
   CloudNativePG manifest, Kind, Kubernetes, Cilium, Helm, and OpenBao canary
   versions.
2. Verify the latest base backup, continuous WAL archive, configuration
   generation, secret-envelope generation, custody canary, and external audit
   checkpoint. Preserve their immutable identifiers and fingerprints.
3. Record `failure_injection_at`, detect and declare the event, identify the
   recovery organization, and obtain explicit restore authorization.
4. Start the internal RTO clock only when the authorized operator begins with
   required inputs available.
5. Restore the pre-disaster physical backup and WAL into a new, isolated
   PostgreSQL target. The runtime credential is never used by `pg_basebackup`,
   `pg_receivewal`, `pg_verifybackup`, recovery configuration, or promotion.
   Recovery-critical PostgreSQL settings must be at least the values recorded
   by the source primary; the pinned CloudNativePG reference requires
   `max_worker_processes = 32`.
6. Run the Hormuz state probe through restricted PostgreSQL roles. It verifies
   the migration ledger, event-time identity snapshots, usage/security events,
   budgets and uncertain reservations, request-attempt history, policy
   authority and active version, audit chain and checkpoints, custody authority
   and retained lifecycle history, derived projection state, coordination, and
   tenant isolation.
7. Compare the source and recovered fingerprints with the exact external
   configuration, secret-envelope, custody-canary, and latest checkpoint
   artifacts. Do not install or expose Hormuz if any check fails.
8. Exercise the required negative paths. Missing WAL, a corrupted backup,
   unavailable custody key, stale checkpoint, partial restore, failed
   coordination, and cross-tenant scope must all deny admission with no
   provider request.
9. Install the digest-pinned Helm release with two replicas only after
   admission succeeds. Keep the Service internal and confirm two distinct
   Kubernetes nodes, ready probes, the exact configuration fingerprint, and
   the existing Secret DSN.
10. Record readiness for promotion. The traffic owner then promotes the
    isolated Service and sends one governed request. Verify exactly one provider
    request and zero automatic replay of the restored ambiguous attempt.
11. Preserve only the strict content-free `summary.json`, then record review,
    anomalies, corrective actions, and the next rehearsal date.

## Failure and rollback

Before traffic promotion, rollback means deleting the isolated application and
database while retaining the original backup, WAL, configuration, secrets,
custody canary, and checkpoint. After promotion, stop new ingress, preserve any
new request attempts as terminal or `outcome_unknown`, and return traffic only
to an independently validated prior environment. Hormuz never retries provider
work automatically.

Do not repair a mismatch by editing the restored database, changing a source
fingerprint, replacing the checkpoint, relaxing tenant scope, skipping custody
verification, or running migrations through the runtime role. Declare the
recovery attempt failed, preserve evidence, correct the upstream backup or
procedure, and begin a new authorized attempt.

## Running the disposable proof

The command is intentionally Linux AMD64-only and destroys only uniquely named
Kind clusters and labelled containers that it creates:

```bash
HORMUZ_DISASTER_RECOVERY_PROOF_ACK=I_UNDERSTAND_THIS_IS_A_DISPOSABLE_DISASTER_RECOVERY_REFERENCE_PROOF \
HORMUZ_DISASTER_RECOVERY_EVIDENCE_DIR=/secure/new/evidence-directory \
HORMUZ_SOURCE_COMMIT="$(git rev-parse HEAD)" \
./tools/verify_disaster_recovery_reference.sh
```

The output directory must not already exist. The only retained artifact is an
owner-readable, content-free `summary.json`.
