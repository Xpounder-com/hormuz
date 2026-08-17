# Audit export

Hormuz can export the local usage and security-egress ledgers as deterministic, metadata-only JSON Lines. The export is intended for inspection, incident evidence, or ingestion into an organization-controlled SIEM or immutable archive. It does not contain prompts, responses, matched values, source files, or provider credentials.

This content-free boundary also applies to the built-in HTTP access log and public gateway errors. Access records contain a canonical route label, method, response status, response-size metadata when available, and whether a query was present. They do not contain the raw path, query, body, headers, OAuth callback values, or internal provider exception. An unrecognized or dynamic path is logged as `unknown` or a fixed route template rather than copied from the request. This guarantee does not extend automatically to a reverse proxy, load balancer, service mesh, crash reporter, or packet capture placed in front of Hormuz; operators must apply the same policy there.

## Export

The default lower bound is the start of the current UTC month:

```bash
python3 -m hormuz --config hormuz.json audit-export \
  --kind all \
  --output hormuz-audit.jsonl
```

Select one ledger or an explicit lower bound when needed:

```bash
python3 -m hormuz --config hormuz.json audit-export \
  --kind security \
  --since 2026-08-01T00:00:00Z \
  --output hormuz-security-audit.jsonl
```

`--kind` accepts `all`, `usage`, or `security`. `--output -` writes the events to standard output. A file export is created with owner-only permissions (`0600`) and refuses to replace an existing path unless the operator explicitly passes `--force`.

Hormuz writes the event count and the SHA-256 checksum of the exact JSONL bytes to standard error. Preserve that checksum separately if the export will be used as evidence. You can verify it later with:

```bash
shasum -a 256 hormuz-audit.jsonl
```

## Chained export and anchored verification

Raw JSONL remains the compatibility default. For evidence that must expose an
altered, deleted, inserted, reordered, or duplicated exported record, select the
versioned chain format:

```bash
python3 -m hormuz --config hormuz.json audit-export \
  --kind all \
  --chain \
  --output hormuz-audit-chain.jsonl
```

Each line is a canonical `hormuz.audit-chain.v1` wrapper containing the original
metadata-only event, a one-based sequence, the preceding chain digest, a
domain-separated event digest, and the resulting chain digest. Sequence one
starts from the all-zero SHA-256 genesis value. The command writes four anchor
values to standard error: the schema, event count, final chain SHA-256, and
SHA-256 of the exact JSONL bytes.

Retain at least the event count and final chain digest in a different trusted
system, such as an access-controlled deployment log, SIEM, signed release
record, or immutable archive. Retaining the file digest as well binds the exact
serialization. Verify a later copy without loading the gateway configuration:

```bash
python3 -m hormuz audit-verify \
  --input hormuz-audit-chain.jsonl \
  --expected-head LOWERCASE_CHAIN_SHA256 \
  --expected-count EVENT_COUNT \
  --expected-sha256 LOWERCASE_FILE_SHA256
```

Successful verification emits only schema, status, event count, chain head, and
file digest. The verifier requires canonical strict UTF-8 JSONL, rejects
duplicate or unknown wrapper members, non-standard constants, missing terminal
newlines, sequence/predecessor/hash mismatches, symlink inputs, records over 2
MiB, more than 1,000,000 records, and files over 256 MiB. File exports are
assembled in a private same-directory temporary file, synchronized, and then
published without exposing partial bytes; replacement requires `--force`.

Governed-context audit supports the identical format:

```bash
python3 -m hormuz --config hormuz.json context-audit-export \
  --actor alice \
  --since 2026-08-01T00:00:00Z \
  --chain \
  --output hormuz-context-audit-chain.jsonl
```

The chain is not a signature or MAC. Anyone who can replace both the export and
its external anchor can recompute a valid chain. It is built at export time, so
it cannot detect a database record deleted before the export, prove gateway
coverage, create one continuous sequence across separate exports/stores, or
make local SQLite append-only. KMS-backed signing, durable sequence allocation,
externally immutable streaming/retention, legal hold, and restore verification
remain production work under issue #17.

Governed context uses a deliberately separate database and export command:

```bash
python3 -m hormuz --config hormuz.json context-audit-export \
  --actor alice \
  --since 2026-08-01T00:00:00Z \
  --output hormuz-context-audit.jsonl
```

Context event types are `context.mutation`, `context.read`, `context.lifecycle`, `context.evidence`, and `context.revalidation`. Evidence events include only the record ID/version, signal family, actor, and policy metadata; the submitted evidence reference and its fingerprint are omitted. Revalidation events expose job, batch status, scope, actor, and policy metadata. The ordinary mutation event records each verification flip, while the current job JSON reports bounded aggregate promotion, invalidation, unchanged, deferred, and processed counts. Neither surface includes record content, source locators, dependency identities, query text, or raw evidence references.

The governed-context repository itself is intentionally content-bearing. Its private content export is therefore outside the content-free telemetry contract even though its mutation, read, lifecycle, evidence, and revalidation audits remain metadata-only.

## Content-free storage manifest

`hormuz.content-free-schema.v1` is the structural allowlist for routine observability. It names the exact permitted columns in the usage, secret/DLP, approval, budget, administrative-access, provider-cost, session-security, and governed-context audit tables. Each local store validates its applicable tables after creation or supported migration and refuses startup with the fixed `content_free_schema_incompatible` error when a column is missing or added. A schema migration therefore cannot add a content-bearing telemetry field without an explicit manifest and privacy-contract change.

The manifest is a structural backstop, not a claim that every Hormuz table is content-free. Canonical governed-context records and lifecycle snapshots are intentionally content-bearing. Session enrollments, human sessions, and consumed-credential state belong to the separate authentication-secret contract. Those tables are excluded rather than mislabeled as telemetry. The audit-export field allowlist remains an independent defense: unknown physical columns are not exported automatically, including before a restarted process detects drift.

## Schema versions

Every line is one JSON object containing:

- `schema_version`: usage events are `2`; security and administrative events remain `1`.
- `event_type`: `usage`, `security.secret`, `security.dlp`, `security.dlp.approval`, or `security.admin.usage_read`.
- `id` and `occurred_at`: the event identifier and UTC occurrence time.
- event-time organization, actor, team, client, protocol, requested/resolved/routed/actual model, policy, status, normalized token, cost-basis, currency, rate-card-version, provider-request, and redaction metadata when applicable. Pre-organization usage rows retain a null organization rather than receiving a guessed tenant.
- `provider_usage`: a provider-specific allowlisted object containing only documented usage counters and bounded categorical metadata. Unknown fields and content-bearing provider data are removed before the event is written.

Usage schema version 2 adds automatic-context lineage: injection mode, outcome and bounded reason; pack and selected record IDs; context policy, retrieval and render versions; trusted repository revision; estimated rendered tokens; assembly time; and authoritative reuse status. It does not include the retrieval query, prompt, response, rendered pack, record content, title, source locator/hash, provider credential, or raw repository/branch/revision selector headers. Exact authorized repository and branch scope IDs may appear only in the separate metadata-only context-read audit.

Array fields such as `redaction_rules`, `rules`, and DLP `findings` are emitted as JSON arrays. A finding is restricted to rule ID, category, confidence, action, and count. The `opaque_media` finding therefore records only the `unsupported_media` category and number of provider content blocks; URLs, file IDs, filenames, media types, encoded bytes, and surrounding content are excluded. DLP events also carry the exact routed upstream model and the effective policy version. For an identity with team/person overlays, that version is a deterministic digest of safe layer versions and rule metadata; it contains no dictionary values and binds later approval matching. Approval events record `requested`, `approved`, `consumed`, or post-egress `model_mismatch` transitions with the opaque request ID, event-time employee/team, separate decision actor, provider/model/policy, and rule IDs. They exclude the keyed payload fingerprint as well as all content.

Administrative usage-read events record the viewing actor, organization, report grouping, frozen UTC window, returned row count, and SHA-256 digests of any exact actor/team filters. A successful report page is not disclosed when its mandatory audit write fails. These events contain no prompt, response, model output, provider credential, or raw filter value.

Events are ordered by occurrence time and ID, and object keys are serialized deterministically. Rate-card versions and estimates are immutable event snapshots, not a lookup through the current configuration. The store snapshots actor and team names on each request, so administrators should treat audit files as access-controlled employee metadata even though request content is absent.

## Security boundary

The raw checksum and the optional chain detect changes only when a trusted copy of the applicable external anchor is retained elsewhere. Hormuz does not yet sign exports, make the local SQLite database append-only, send events to WORM storage, record who ran an export, or enforce audit-reader RBAC. Production deployments should ship events to an organization-controlled append-only destination and apply retention, legal-hold, access-review, and deletion policies there.

The export covers only requests that passed through Hormuz. The separate offline provider-cost reconciliation kernel can expose aggregate unexplained variance, but it cannot prove report scope or gateway bypass and does not convert this audit file into complete organization-wide usage. Authenticated provider polling, final invoice reconciliation, and externally immutable audit remain enterprise milestones.
