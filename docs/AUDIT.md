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

## Schema version 1

Every line is one JSON object containing:

- `schema_version`: currently `1`.
- `event_type`: `usage` or `security.secret`.
- `id` and `occurred_at`: the event identifier and UTC occurrence time.
- event-time actor, team, client, protocol, requested/resolved/routed/actual model, policy, status, normalized token, cost-basis, currency, rate-card-version, provider-request, and redaction metadata when applicable.
- `provider_usage`: a provider-specific allowlisted object containing only documented usage counters and bounded categorical metadata. Unknown fields and content-bearing provider data are removed before the event is written.

Array fields such as `redaction_rules` and `rules` are emitted as JSON arrays. Events are ordered by occurrence time and ID, and object keys are serialized deterministically. Rate-card versions and estimates are immutable event snapshots, not a lookup through the current configuration. The store snapshots actor and team names on each request, so administrators should treat audit files as access-controlled employee metadata even though request content is absent.

## Security boundary

The checksum detects accidental or deliberate changes only when a trusted copy of the checksum is retained elsewhere. Hormuz does not yet sign exports, make the local SQLite database append-only, send the export to WORM storage, record who ran an export, or enforce audit-reader RBAC. Production deployments should ship events to an organization-controlled append-only destination and apply retention, legal-hold, access-review, and deletion policies there.

The export covers only requests that passed through Hormuz. Provider-invoice reconciliation and gateway-bypass detection are separate enterprise milestones; this file must not be represented as complete organization-wide usage until those controls exist.
