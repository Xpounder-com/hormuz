# Governed context records and packs

Hormuz has a local persistent repository for provider-neutral, governed context records. It is intentionally separate from both the gateway request path and the metadata-only usage database: importing or retrieving context does not inject it into an employee prompt, call an embedding model, or send content to OpenAI or Anthropic.

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

Import validates the whole file against the selected configured identity before writing. Records are then ingested idempotently in supersession order inside one SQLite transaction; any late conflict rolls back every new record and mutation event in that batch. The local CLI treats control of the configuration file and machine as the operator authorization boundary; enterprise mutation RBAC and signed user sessions are not implemented yet.

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

For compatibility with the earlier JSONL format, an omitted owner becomes the importing actor, an omitted source item key becomes the record ID, an omitted effective time becomes the verification time, and an omitted source hash becomes the imported record-content hash. Production connectors should always provide the actual immutable source-artifact hash and stable item key.

The tuple `(organization, source URI, source revision, source item key)` is idempotent. Reusing it for different data fails closed. Changing content through the repository update API requires a new source identity or revision, and every update/delete uses an expected storage version for optimistic concurrency. Metadata-only mutation events retain the actor, action, record/version references, policy version, classification, visibility, and repository ID without title, content, source URI, or source hashes.

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

Export the organization-scoped, metadata-only mutation ledger separately:

```bash
python3 -m hormuz --config hormuz.json context-audit-export \
  --actor alice \
  --since 2026-08-01T00:00:00Z \
  --output hormuz-context-audit.jsonl
```

The configured actor determines the organization boundary. As with the other local mutation commands, access to the local configuration is the current operator authorization boundary—not proof of enterprise reader RBAC.

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

`--records examples/context-records.jsonl` remains available as a no-persistence compatibility and migration check. The command writes a content-bearing JSON object to stdout; treat it as company data.

## Selection contract

Hormuz applies these stages in order:

1. Match the exact organization.
2. Match organization, team, or actor visibility against the configured actor.
3. Enforce classification clearance.
4. Match repository and branch scopes.
5. Exclude provisional records unless `--include-provisional` is explicit.
6. Exclude records not yet effective, verified in the future, or expired at `--as-of`.
7. Apply active, authorized supersession chains.
8. Rank remaining records lexically and deterministically.
9. Select complete records within the estimated token budget and item cap.

Steps 1–6 execute in SQLite before stored content is decoded. The pack builder repeats those checks as a defense-in-depth boundary before ranking.

The SHA-256 pack identity covers the query, actor/team/org/repository/branch scope, clearance, policy version, token budget, selected record metadata, provenance, content hashes, scores, and token estimates. Input ordering cannot change the pack. Evaluation time remains in the output but does not change the identity when authorization, freshness, selection, and content are unchanged.

## Current boundary

This is a durable local governance kernel, not the final enterprise context service:

- retrieval is lexical; there is no embedding provider, symbol index, or vector index;
- the SQLite implementation is single-node and plaintext, with no accepted enterprise tenancy, KMS, backup, restore, legal-hold, or HA contract;
- mutation commands trust the local configuration operator rather than a dedicated context-writer RBAC permission;
- active authorized supersession means an expired successor can leave its prior record eligible; automatic invalidation and contradiction policy remain open lifecycle work;
- the token estimate is deterministic but not a provider tokenizer guarantee;
- there is no automatic prompt injection, context-pack cache, approval workflow, connector, or outcome writeback;
- if a pack is later injected, injection must happen before the existing secret-egress boundary so all added content is inspected.

No context caching has been enabled. Cache privacy remains blocked on proposed ADR 0003, and the hosted persistence topology remains blocked on proposed ADR 0002.
