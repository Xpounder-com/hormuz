# Evidence-driven context lifecycle

Hormuz can promote provisional organizational context only after configured evidence is present, and return verified context to provisional when newer negative evidence or trusted source state invalidates it. The lifecycle repository and worker remain local, but trusted CI jobs and internal connectors can now reach them through a capability- and tenant-scoped remote API/CLI. This proves the lifecycle contract, durable recovery, authorization split, and provider-neutral connector transport without claiming a hosted scheduler or source-specific connector.

## Enable the automation boundary

The sample configuration contains a complete lifecycle policy but leaves it disabled for backward-compatible local setup. To enable it:

1. set `context_service.lifecycle.enabled` to `true`;
2. give at least one identity in every configured organization the `context_promoter` capability; and
3. review the configured promotion paths as organization policy, not as example data.

```json
{
  "context_service": {
    "lifecycle": {
      "enabled": true,
      "policy_version": "engineering-lifecycle-v1",
      "job_batch_size": 100,
      "lease_seconds": 30,
      "promotion_paths": [
        {
          "id": "merged-and-green",
          "record_kinds": ["claim"],
          "required_signals": ["commit_merged", "ci_passed"]
        }
      ]
    }
  },
  "identities": [
    {
      "actor_id": "alice",
      "capabilities": ["context_promoter"]
    }
  ]
}
```

The abbreviated identity above is not a complete Hormuz identity object; preserve the other required fields from `config.example.json`.

When automation is enabled, new CLI imports must be `provisional`. This prevents a writer from self-declaring a new managed record verified. An exact idempotent retry of a verified record that already exists is still accepted. Records that were already verified before this feature are treated as legacy trusted bootstrap and are not silently demoted merely because they have no Hormuz-managed evidence. That compatibility rule is deliberate and auditable.

Context reads do not require `context_promoter`. Evidence import, trusted snapshot replacement, and revalidation do. The capability therefore separates consumption from lifecycle promotion and invalidation.

## Run the local workflow

Import the provisional sample, record trusted repository state, import two evidence signals, and run one bounded revalidation batch:

```bash
hormuz --config hormuz.json context-import \
  --records examples/context-lifecycle-records.jsonl \
  --actor alice

hormuz --config hormuz.json context-snapshot-import \
  --snapshot examples/context-lifecycle-snapshot.json \
  --actor alice

hormuz --config hormuz.json context-evidence-import \
  --evidence examples/context-evidence-commit-merged.json \
  --actor alice

hormuz --config hormuz.json context-evidence-import \
  --evidence examples/context-evidence-ci-passed.json \
  --actor alice

hormuz --config hormuz.json context-revalidate \
  --actor alice \
  --repository Xpounder-com/hormuz \
  --branch main
```

`context-revalidate` runs or resumes one batch. If the returned status is `pending`, run the same command again. `completed` means the frozen record and evidence sets for that job have been evaluated. `superseded` means the trusted snapshot, record set, or evidence set changed before the batch committed; start the command again to create a job bound to the new inputs.

For a CI job or connector that cannot access the server's configuration or database, use `hormuz lifecycle snapshot`, `hormuz lifecycle evidence`, and `hormuz lifecycle revalidate` against the authenticated gateway. These commands support bootstrap, workload OIDC, and Hormuz session credentials already accepted by the gateway. See [CONTEXT_LIFECYCLE_API.md](CONTEXT_LIFECYCLE_API.md) for the exact schemas, status codes, OIDC boundary, and retry behavior.

## Evidence contract

An input envelope uses `hormuz.context-evidence.v1`:

```json
{
  "schema_version": "hormuz.context-evidence.v1",
  "organization_id": "xpounder",
  "record_id": "retry-ci-observation",
  "record_version": 1,
  "signal": "ci_passed",
  "evidence_ref": "github-actions:organization/repository:run:12345",
  "observed_at": "2026-08-16T00:06:00Z"
}
```

The raw `evidence_ref` is hashed at the import boundary and is never retained in the context database or metadata-only audit export. The deterministic evidence ID covers organization, record, record version, signal, reference fingerprint, and observation time. Exact retries are idempotent. References more than five minutes in the future are rejected to limit clock-poisoning attacks.

Evidence is also bound to a semantic subject hash of the exact record. Lifecycle-only flips such as `provisional` to `verified` do not change that subject, so valid evidence can recover a false invalidation. Content, source, scope, dependency, or policy-relevant record changes do change it, so evidence for stale content is not reused.

Hormuz currently recognizes these signal families:

| Family | Positive | Negative |
| --- | --- | --- |
| commit | `commit_merged` | `commit_reverted` |
| CI | `ci_passed` | `ci_failed` |
| review | `review_accepted` | `review_rejected` |
| ADR | `adr_approved` | `adr_superseded` |
| incident | `incident_resolved` | `incident_reopened` |
| human | `human_confirmed` | `human_withdrawn` |
| failed attempt | `failed_attempt_validated` | `failed_attempt_rejected` |

Only positive paths explicitly present in configuration can promote a record. A `negative_knowledge` record can use only a path that also requires the `negative_knowledge` tag, preventing an ordinary human confirmation from legitimizing an unvalidated failed attempt. Newer negative evidence invalidates. Opposing signals in the same family with the same latest timestamp produce an explicit conflict and remain provisional rather than being ordered arbitrarily.

The reference fingerprint proves stable linkage inside Hormuz; it does not independently prove that the external event occurred. Until signed connector attestations exist, the `context_promoter` operator or connector remains part of the trust boundary.

## Source invalidation and recovery

Revalidation compares each managed record with the exact trusted organization, repository, and branch snapshot:

- a changed `git:` source revision returns a record to provisional when `source_revision_changed` is configured;
- a mismatched dependency revision or hash returns it to provisional;
- a missing dependency observation defers the decision and preserves the current state; and
- returning to a matching trusted snapshot can promote the record again from its still-valid subject-bound evidence.

Policy changes are hash-bound. A worker cannot resume a job with different policy content under the same human-readable version. A changed policy creates a separate job.

## Durable job behavior

Each job is tenant-, repository-, and branch-scoped and binds to:

- the trusted snapshot hash and monotonically increasing snapshot version;
- the complete lifecycle policy hash;
- a semantic fingerprint of the complete scoped record set;
- a fingerprint of every current-subject evidence event in that scope; and
- the policy version shown to operators.

The record-set fingerprint excludes lifecycle-only verification flips but covers each record's semantic subject. A later import, deletion, content/source/scope change, or new applicable evidence event supersedes an uncompleted job and creates a new identity on the next invocation; a successful verification flip does not. This also ensures that evidence arriving after an unchanged completed run creates a new job instead of reusing the completed result. Batches use a bounded durable lease. A live lease rejects a second worker, an expired lease can be resumed after a crash, and every record mutation plus job cursor/count update commits in one transaction. An optimistic record-version check prevents a concurrent writer from being overwritten.

Lifecycle changes preserve record content, source, dependencies, supersession, classification, and scope. Only verification state, managed evidence IDs, verification time, storage version, and update time change. Metadata-only evidence, job, batch, and ordinary mutation events make the transition observable without retaining content or raw evidence references.

## Current boundary

This slice does not collect GitHub, GitLab, CI, ADR, incident, or review events automatically; a trusted operator or source connector must validate its upstream protocol and produce the evidence envelope and repository snapshot. The authenticated remote boundary proves delivery and authorization, not the truth of the external event. It does not implement probabilistic confidence scoring, time-based decay, cross-node leases, PostgreSQL tenancy, signed attestations, or a hosted scheduler. A separate disabled-by-default generation path can inject verified unscoped or exact administrator-granted repository context, but it does not make this lifecycle connector source-trusting or automatic. The existing assertion-level contradiction and pack-safety behavior remains separate from evidence-family conflicts. Issue #12 therefore remains open until source-specific verification, remaining decay policy, hosted operations, and release evidence are complete.

[Proposed ADR 0005](decisions/0005-github-lifecycle-event-trust.md) defines the owner decision required before the first GitHub source collector is implemented. It recommends signed GitHub App webhooks for a narrow source-event plane while preserving GitHub Actions OIDC as a separately labeled delegated-attestation plane; the proposal is not shipped behavior.
