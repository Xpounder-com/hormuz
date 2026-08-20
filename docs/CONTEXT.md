# Governed context records and packs

> **Deprecated experimental compatibility surface.** Hormuz's supported product boundary is the AI gateway/control plane defined in [ADR 0008](decisions/0008-gateway-product-boundary.md). This repository is opened lazily only when legacy context behavior is explicitly used; do not adopt it for new Hormuz deployments.

Hormuz has a local persistent repository for provider-neutral, governed context records. It is intentionally separate from the metadata-only usage database. Importing, listing, exporting, or explicitly retrieving context never calls an embedding model or provider. When an administrator separately enables automatic context policy, supported generation requests can retrieve and inject a verified pack through the gateway path described in [CONTEXT_INJECTION.md](CONTEXT_INJECTION.md).

The repository is a reversible local implementation behind a dedicated storage boundary. It proves the record lifecycle and authorization contract without selecting the pending enterprise PostgreSQL tenancy design.

## Configure the local repository

`context_database` must not be the same path as `database`, which remains the usage ledger:

```json
{
  "database": "./hormuz.sqlite3",
  "context_database": "./hormuz-context.sqlite3"
}
```

Hormuz creates the local database and SQLite sidecar files with mode `0600`. Record content is integrity-checked but stored with the explicit `plaintext-local-v1` codec. That codec is **not encryption** and is not suitable for an enterprise deployment; KMS/BYOK storage remains an open release gate.

## Import records

```bash
python3 -m hormuz --config hormuz.json context-import \
  --records examples/context-records.jsonl \
  --actor alice \
  --policy-version engineering-v1
```

Import validates the whole file against the selected configured identity before writing. Records are then ingested idempotently in supersession order inside one SQLite transaction; any late conflict rolls back every new record and mutation event in that batch. The local mutation CLI treats control of the configuration file and machine as the operator authorization boundary; enterprise mutation RBAC is not implemented. Human sessions authenticate the read-only Context Pack HTTP route, not local mutation commands.

Each non-empty JSONL line is one object:

```json
{
  "id": "adr-0017",
  "kind": "decision",
  "title": "Retry standard",
  "content": "Use bounded exponential backoff with jitter for transient failures.",
  "owner_id": "alice",
  "organization_id": "xpounder",
  "visibility": "team",
  "scope_id": "engineering",
  "classification": "internal",
  "source": {
    "uri": "https://github.com/Xpounder-com/hormuz/blob/main/examples/sources/adr-0017.md",
    "revision": "example:v1",
    "sha256": "760dda407b1899616d3c962db7c734b7814f9060e35ec5291a7f25a4d5300c17",
    "item_key": "adr-0017"
  },
  "repository_id": "Xpounder-com/hormuz",
  "branch": null,
  "verification": "verified",
  "verification_evidence": ["source:checked-in", "review:approved"],
  "effective_at": "2026-08-14T18:00:00Z",
  "verified_at": "2026-08-14T18:00:00Z",
  "expires_at": null,
  "supersedes_id": null,
  "invalidation_rules": ["source_revision_changed"],
  "dependencies": [
    {
      "uri": "repo://Xpounder-com/hormuz/config/retry-policy.json",
      "revision": "git:abc123",
      "sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    }
  ],
  "assertion": {"key": "retry.transient.enabled", "value": "true"},
  "tags": ["reliability", "api"]
}
```

Unknown fields, malformed timestamps, bad hashes, and content/hash mismatches fail closed. Supported values are:

- `kind`: `claim` or `decision`.
- `visibility`: `organization`, `team`, or `actor`. `scope_id` must identify that exact scope.
- `classification`: `public`, `internal`, `confidential`, or `restricted`.
- `verification`: `verified` or `provisional`. The persistent repository requires verified records to have a timezone-aware `verified_at` and at least one evidence identifier.
- `repository_id` and `branch`: optional narrowing scopes. A branch requires a repository.
- `effective_at`, `expires_at`, `supersedes_id`, and `invalidation_rules`: explicit lifecycle controls.
- `dependencies`: immutable artifact identities. A dependency-bearing record is excluded unless the exact URI has a trusted observation whose revision and optional SHA-256 still match.
- `assertion`: an optional structured key/value claim used to detect active disagreement. Both fields are required together; multiline values are rejected.

For compatibility with the earlier JSONL format, an omitted owner becomes the importing actor, an omitted source item key becomes the record ID, an omitted effective time becomes the verification time, and an omitted source hash becomes the imported record-content hash. Production connectors should always provide the actual immutable source-artifact hash and stable item key.

The tuple `(organization, source URI, source revision, source item key)` is idempotent. Reusing it for different data fails closed. Changing content through the repository update API requires a new source identity or revision, and every update/delete uses an expected storage version for optimistic concurrency. Metadata-only mutation events retain the actor, action, record/version references, policy version, classification, visibility, and repository ID without title, content, source URI, or source hashes. Successful repository-backed pack reads record a separate metadata-only event containing the trusted organization/team/actor scope, pack ID, policy version, repository/branch, clearance, provisional flag, and aggregate record/token counts. It excludes query text, titles, content, source locators and hashes, and selected record IDs.

## Record trusted lifecycle state

A connector or trusted operator records the current repository and dependency state in an exact organization/repository/branch envelope:

```bash
python3 -m hormuz --config hormuz.json context-snapshot-import \
  --snapshot examples/context-lifecycle-snapshot.json \
  --actor alice \
  --policy-version engineering-lifecycle-v1

python3 -m hormuz --config hormuz.json context-snapshot-show \
  --actor alice \
  --repository Xpounder-com/hormuz \
  --branch main
```

Snapshot creation is idempotent. Replacing a different snapshot requires `--expected-version`; concurrent writers using the same version produce exactly one winner. Snapshot audit events retain scope, actor, policy, version, hash, and artifact count, but not artifact URIs or revisions. The local snapshot row does retain those artifact identities because pack evaluation needs them; it does not contain source content.

When lifecycle automation is enabled, snapshot import requires the explicit `context_promoter` capability. The same capability governs immutable evidence import and resumable revalidation, while ordinary context reads remain available under their existing identity, classification, and repository scope. See [CONTEXT_LIFECYCLE.md](CONTEXT_LIFECYCLE.md) for configuration, evidence signals, promotion/invalidation rules, job recovery, and the legacy verified-record compatibility boundary.

Trusted CI jobs and internal connectors can submit the same lifecycle state and evidence through the remote `hormuz lifecycle` CLI and authenticated gateway without local database access. The gateway authenticates and authorizes the connector before mutation, derives its organization server-side, and never calls a model provider for these operations. See [CONTEXT_LIFECYCLE_API.md](CONTEXT_LIFECYCLE_API.md).

The CLI envelope is `hormuz.context-lifecycle-envelope.v1`, and its nested trusted state is `hormuz.context-lifecycle-snapshot.v1`. The HTTP Context Pack API never accepts a caller-supplied snapshot: it loads only the latest server-side snapshot for the authenticated organization and exact requested repository/branch.

## Inspect and export

List authorized metadata; add `--include-content` only when content is required:

```bash
python3 -m hormuz --config hormuz.json context-list \
  --actor alice \
  --repository Xpounder-com/hormuz \
  --branch main
```

Create an import-compatible, content-bearing export. A file is created with mode `0600`, refuses an accidental overwrite, and is accompanied by a SHA-256 checksum:

```bash
python3 -m hormuz --config hormuz.json context-export \
  --actor alice \
  --repository Xpounder-com/hormuz \
  --output hormuz-context-export.jsonl
```

Export the organization-scoped, metadata-only mutation and pack-read ledger separately:

```bash
python3 -m hormuz --config hormuz.json context-audit-export \
  --actor alice \
  --since 2026-08-01T00:00:00Z \
  --output hormuz-context-audit.jsonl
```

The configured actor determines the organization boundary. As with the other local commands, access to the local configuration is the current operator authorization boundary—not proof of enterprise reader RBAC.

Delete requires the exact record ID and current version:

```bash
python3 -m hormuz --config hormuz.json context-delete \
  --actor alice \
  --record-id adr-0017 \
  --expected-version 1 \
  --policy-version engineering-v1
```

Hormuz refuses to delete a record still referenced by a superseding record. SQLite secure deletion and WAL truncation are enabled for this local path, but device backups and filesystem snapshots can retain earlier bytes; enterprise retention and backup erasure require the approved deployment design.

## Build a pack

The actor and team come from the configured identity. With no `--records`, Hormuz reads only authorized candidates from the persistent repository before content decoding and ranking:

```bash
python3 -m hormuz --config hormuz.json context-pack \
  --query "How should API retries work?" \
  --organization xpounder \
  --actor alice \
  --repository Xpounder-com/hormuz \
  --branch main \
  --clearance internal \
  --token-budget 2000 \
  --policy-version engineering-v1
```

Before a repository-backed pack is printed, Hormuz commits its metadata-only read event. An audit failure fails the command closed, so content is not printed. `--records examples/context-records.jsonl` remains available as an explicit no-persistence compatibility and migration check and therefore does not write an access event. Either path writes a content-bearing JSON object to stdout; treat it as company data.

## Selection contract

Hormuz applies these stages in order:

1. Match the exact organization.
2. Match organization, team, or actor visibility against the configured actor.
3. Enforce classification clearance.
4. Match repository and branch scopes.
5. Mark provisional records ineligible unless `--include-provisional` is explicit.
6. Mark records not yet effective, verified in the future, or expired at `--as-of` ineligible.
7. Apply active, authorized supersession chains.
8. Identify lexical query matches without returning or scanning unrelated records.
9. Quarantine matched records whose model-visible fields match the bounded high-confidence prompt-injection indicators.
10. Evaluate matched `git:` source revisions and explicit artifact dependencies against the trusted lifecycle snapshot. Missing or mismatched dependency observations fail closed.
11. Group matched structured assertions by key. When active authorized candidates disagree, exclude every conflicting record and return `requires_resolution` with each authorized source reference and value.
12. Rank remaining records lexically and deterministically.
13. Estimate each complete emitted item, including content, provenance, lifecycle,
   classification, and audit-facing metadata, then select whole items within the
   token budget and item cap.

Steps 1–4 execute in SQLite before stored content is decoded, so an unauthorized record is never passed to the pack builder. The builder repeats those access checks, then returns explicit exclusions for authorized lexical matches that fail steps 5–6. This makes stale and provisional outcomes observable without revealing that an unauthorized record exists.

The SHA-256 pack identity covers the query, actor/team/org/repository/branch scope, clearance, policy, retrieval and render versions, token budget, selected record metadata, provenance, content hashes, scores, token estimates, lifecycle snapshot hash, exclusions, contradictions, and lifecycle outcome. Input ordering cannot change the pack. Evaluation time remains in the output but does not change the identity when authorization, freshness, lifecycle state, selection, and content are unchanged.

The additive pack-v1 lifecycle outcome is `complete`, `partial`, or `requires_resolution`. `exclusions` explain authorized lexical matches removed by verification, freshness, or lifecycle evaluation. `contradictions` deliberately include authorized source locators and structured assertion values so an employee or approval workflow can resolve the disagreement; treat the complete response as company content.

## Current boundary

This is a durable local governance kernel, not the final enterprise context service:

- retrieval is lexical; there is no embedding provider, symbol index, or vector index;
- the SQLite implementation is single-node and plaintext, with no accepted enterprise tenancy, KMS, backup, restore, legal-hold, or HA contract;
- mutation commands trust the local configuration operator rather than a dedicated context-writer RBAC permission;
- active authorized supersession means an expired successor can leave its prior record eligible;
- snapshot evaluation is immediate and deterministic on every pack read; opt-in local evidence-driven promotion, invalidation, recovery, and resumable CLI revalidation are implemented, while source connectors, hosted scheduling, and remaining time/confidence decay policy remain open lifecycle work;
- the prompt-injection quarantine uses narrow high-confidence text patterns. It lowers obvious risk but is not semantic prompt-injection prevention, and a safe record can still contain adversarial instructions that do not match those patterns;
- the token estimate covers the complete serialized item contract and is
  deterministic, but it excludes the response wrapper and is not a provider
  tokenizer guarantee;
- bounded disabled-by-default automatic injection exists for verified organization/team/actor records on OpenAI Responses, Anthropic Messages generation, and Anthropic token-count requests; repository selectors, continuation bindings, OpenAI compaction injection, and complete issue #5 evidence remain open;
- there is no context-pack cache, source-specific event collector, or outcome writeback; the current connector API accepts already-validated normalized attestations; and
- injected packs run before the existing secret-egress boundary, so added content receives the same DLP action as employee request content.

No context caching has been enabled. ADR 0003 now authorizes conservative cache
privacy tiers, but encrypted tenant storage, authorization rechecks, bounded
TTL, invalidation, deletion, and isolation evidence remain mandatory before any
pack cache is activated. ADR 0002 now authorizes the PostgreSQL topology and a
schema-v1 isolation foundation exists, but the live context repository remains
SQLite-backed until its PostgreSQL repository contract and KMS gates pass.
