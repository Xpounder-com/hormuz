# Incident response drills

Hormuz binds the seven incident scenarios required by issue #9 to exact repository-local regressions. Run them with:

```bash
python scripts/incident_drill.py \
  --catalog operations/incident-drills.json \
  --project-root . \
  --output /tmp/hormuz-incident-drill-evidence.json
```

The strict catalog rejects unknown or duplicate fields, missing scenarios, broad or missing test identifiers, unresolved runbook anchors, and any claim that a repository-local drill establishes production readiness. The output is metadata-only: catalog version and digest, aggregate scenario and test counts, execution counts, and explicit false production-readiness flags. It is created mode `0600` and never overwrites an existing path.

These are executable control regressions, not live disaster-recovery exercises. They do not prove production on-call coverage, response or recovery targets, customer communications, real provider or IdP behavior, shared persistence, cross-region failover, legal compliance, or independent security review. Named roles below are functional placeholders. Hormuz does not assign people, paging schedules, incident severities, or deadlines until the owner approves those operating commitments.

## Common operating sequence

For every incident, the functional lead and incident commander should preserve metadata-only evidence and follow four phases:

1. Detect and classify from authenticated health, policy, usage, security, or billing signals. Do not copy prompts, responses, secrets, or provider-bound customer content into the incident ledger.
2. Contain with the narrowest fail-closed control that prevents new harm while preserving auditability. Never relax tenant, DLP, identity, or budget policy to restore availability.
3. Recover only after the affected credential, provider path, tenant boundary, policy version, reservation, or record set is verified against its source of truth.
4. Verify the relevant exact regression, reconcile retained metadata, record the unresolved production gaps, and obtain authorized approval before closing or relaxing containment.

## Provider outage

- Detect: bounded upstream timeouts, provider failures, health changes, and rising unpriced failures.
- Contain: stop new affected upstream work; do not forward caller credentials or expose upstream bodies. A future fallback must remain policy-authorized and budget-bound.
- Recover: confirm the configured provider origin and credential custody, then restore admission conservatively.
- Verify: run the exact pre-header timeout regression and reconcile usage events. Live vendor and regional exercises remain open.

## Identity provider outage

- Detect: discovery, authorization callback, token exchange, or signing-key validation failures.
- Contain: issue no new Hormuz session after an incomplete or invalid OIDC flow. Existing-session policy and emergency access require a separately approved production decision.
- Recover: verify issuer, audience, endpoint origin, keys, and subject mapping before reopening enrollment.
- Verify: run the exact token-endpoint outage regression. A real-IdP profile and workforce exercise remain open.

## Credential compromise

- Detect: refresh replay, impossible session behavior, unauthorized provider use, or signing-key exposure evidence.
- Contain: revoke the affected session family or authorized scope; rotate provider and signing credentials through their approved custody systems when those systems exist.
- Recover: re-authenticate identities, confirm old credentials fail, and review tenant-scoped security metadata.
- Verify: run the refresh-replay regression. Shared revocation, KMS rotation, and notification exercises remain open.

## Tenant isolation incident

- Detect: any result, cache entry, administrative view, job, or audit record outside the authenticated organization scope.
- Contain: disable the affected path, preserve metadata-only evidence, and do not use another tenant's data to diagnose the incident.
- Recover: verify every application predicate, database policy, cache key, worker binding, and reused connection before restoring the path.
- Verify: run the tenant-scoped session administration regression. Production PostgreSQL RLS and independent adversarial evidence remain open.

## Policy rollout incident

- Detect: a desired policy fingerprint differs from the shared projection, an unauthorized version is staged or activated, or replicas report different active versions.
- Contain: fail closed before serving provider traffic and preserve metadata-only policy version and actor evidence.
- Recover: reconcile the desired and projected policy, require authorized activation, and restore replicas to one approved version.
- Verify: run both the stale-policy startup regression and the digest-pinned
  schema-v5 PostgreSQL activation/rollback integration. Public authenticated
  policy administration, request-time active-policy evaluation, and production
  replica rollout remain open in issue #21.

## Cost spike

- Detect: budget burn, request or token anomalies, unpriced usage, reconciliation variance, or reservation saturation.
- Contain: deny new work at the narrowest policy scope before provider egress. Do not represent provider aggregate billing as final person-level cost.
- Recover: reconcile provider totals and Hormuz request estimates, clear only verified abandoned reservations, and restore approved limits.
- Verify: run the conservative reservation regression. Shared atomic budgets, alert delivery, billing lag, and finance escalation remain open.

## Data deletion request

- Detect: an authenticated request with verified tenant scope and an explicit inventory of usage, audit, session, policy, approval, backup, and provider-side data.
- Contain: prevent new writes in the approved deletion scope while preserving records subject to an authorized legal hold.
- Recover: delete or anonymize only according to the approved retention contract, verify referential integrity, and create metadata-only attestation evidence.
- Verify: run the tenant isolation regression before any deletion workflow. Complete tenant erasure, backups, legal holds, provider systems, residency, and attestation remain open.
