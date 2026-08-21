# Context experiment

This deprecated experiment builds an explicit, provider-neutral context pack from content-bearing JSONL records. It is intentionally separate from the Hormuz core gateway request path: it does not automatically inject context, store context in the metadata-only usage database, call an embedding model, or send record content to OpenAI or Anthropic.

## Build a pack

The actor must exist in `hormuz.json`; Hormuz derives the actor and team scope from that configured identity.

```bash
hormuz-context-experiment --config hormuz.json context-pack \
  --records examples/context-records.jsonl \
  --query "How should API retries work?" \
  --organization xpounder \
  --actor alice \
  --repository Xpounder-com/hormuz \
  --branch main \
  --clearance internal \
  --token-budget 2000 \
  --policy-version engineering-v1 \
  --as-of 2026-08-15T12:00:00Z
```

The command writes a content-bearing JSON object to stdout. Treat it as company data. Redirect it only to an appropriately protected destination.

## Record contract

Each non-empty JSONL line is one object:

```json
{
  "id": "adr-0017",
  "title": "Retry standard",
  "content": "Use bounded exponential backoff with jitter for transient failures.",
  "organization_id": "xpounder",
  "visibility": "team",
  "scope_id": "engineering",
  "classification": "internal",
  "source": {
    "uri": "https://github.com/Xpounder-com/hormuz/blob/main/docs/adr/0017.md",
    "revision": "git:abc123"
  },
  "repository_id": "Xpounder-com/hormuz",
  "branch": null,
  "verification": "verified",
  "verified_at": "2026-08-14T18:00:00Z",
  "expires_at": null,
  "supersedes_id": null,
  "tags": ["reliability", "api"]
}
```

Unknown fields and malformed timestamps fail closed. Supported values are:

- `visibility`: `organization`, `team`, or `actor`. `scope_id` must identify that exact scope.
- `classification`: `public`, `internal`, `confidential`, or `restricted`.
- `verification`: `verified` or `provisional`. Verified records require a timezone-aware `verified_at`; provisional records cannot claim one.
- `repository_id` and `branch`: optional narrowing scopes. A branch requires a repository.

## Selection contract

Hormuz applies these stages in order:

1. Match the exact organization.
2. Match organization, team, or actor visibility against the configured actor.
3. Enforce classification clearance.
4. Match repository and branch scopes. Repository-scoped records are excluded when the request has no repository.
5. Exclude provisional records unless `--include-provisional` is explicit.
6. Exclude records verified in the future or expired at the requested `--as-of` time.
7. Apply active, authorized supersession chains.
8. Rank remaining records lexically and deterministically.
9. Select complete records within the estimated token budget and item cap.

The SHA-256 pack identity and `manifest_sha256` cover the query, actor/team/org/repository/branch scope, clearance, policy version, token budget, selected record metadata, source revisions, content hashes, scores, and token estimates. Reordering the input file cannot change the pack. Evaluation time remains in the output for audit, but it does not change the pack identity when authorization, freshness, selection, and content are unchanged; crossing a verification or expiry boundary changes the selected manifest and therefore the identity. Diagnostic counts and `as_of` are not part of the manifest checksum.

## Current boundary

This is a retained experimental kernel, not a Hormuz core capability or enterprise context service:

- retrieval is lexical; there is no embedding provider or vector index;
- the JSONL file is caller-managed; Hormuz has not selected SQLite, Postgres, or customer object storage for governed content;
- `--organization` and `--clearance` are caller-supplied until enterprise identity supplies signed claims;
- the conservative byte-based token estimate is deterministic but not a provider tokenizer guarantee;
- there is no automatic prompt injection, context-pack cache, encrypted content store, approval workflow, or outcome writeback yet;
- if a pack is later sent through Hormuz, context injection must happen before the existing secret-egress boundary so the resulting provider request is inspected.

Those remaining choices are outside the core Hormuz release path. See the core [migration note](../../../docs/CONTEXT_EXPERIMENT_MIGRATION.md).
