# Audit export

Hormuz can export the local usage and secret-egress ledgers as deterministic, metadata-only JSON Lines. The export is intended for inspection, incident evidence, or ingestion into an organization-controlled SIEM or immutable archive. It does not contain prompts, responses, matched secret values, source files, or provider credentials.

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

## Schema version 2

Every newly exported line is a `hormuz.audit-event` version `2` JSON object containing:

- `schema_id`: `hormuz.audit-event`.
- `schema_version`: currently `2`.
- `event_type`: `usage` or `security.secret`.
- `id` and `occurred_at`: the event identifier and UTC occurrence time.
- event-time organization, actor, team, identity type, authentication source, client, protocol, requested/routed/provider-reported model, policy version and outcome, status, token, cost basis, allocation basis, coverage, provider-request, and redaction metadata when applicable.

Array fields such as `redaction_rules` and `rules` are emitted as JSON arrays. Events are ordered by occurrence time and ID, and object keys are serialized deterministically. The store snapshots actor and team names on each request, so administrators should treat audit files as access-controlled employee metadata even though request content is absent.

Version 1 files remain readable by the contract validator for historical compatibility, but Hormuz no longer emits them. In v2, the old `upstream_model` field is named `routed_model`; v2 also makes the identity, policy-version, provider-reported-model, cost/allocation, and coverage boundaries explicit. See [CONTRACTS.md](CONTRACTS.md) for the manifest, strict validation rules, and migration boundary.

## Security boundary

The checksum detects accidental or deliberate changes only when a trusted copy of the checksum is retained elsewhere. Hormuz does not yet sign exports, make the local SQLite database append-only, send the export to WORM storage, record who ran an export, or enforce audit-reader RBAC. Production deployments should ship events to an organization-controlled append-only destination and apply retention, legal-hold, access-review, and deletion policies there.

An explicit AWS Object Lock anchor is now available for a configured deployment;
see [CUSTODY.md](CUSTODY.md). It creates and verifies a strict hash-chained,
metadata-only snapshot before writing a compliance-retained SSE-KMS object.
It does not make the source database append-only or prove source completeness
before the snapshot is created.

The export covers only requests that passed through Hormuz. Provider-invoice reconciliation and gateway-bypass detection are separate enterprise milestones; this file must not be represented as complete organization-wide usage until those controls exist.
