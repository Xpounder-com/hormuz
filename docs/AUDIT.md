# Audit export

Hormuz can export the local usage and secret-egress ledgers as deterministic, metadata-only JSON Lines. The export is intended for inspection, incident evidence, or ingestion into an organization-controlled SIEM or immutable archive. It does not contain prompts, responses, matched secret values, source files, or provider credentials.

## Export

The default lower bound is the start of the current UTC month:

```bash
python3 -m hormuz --config hormuz.json audit export \
  --kind all \
  --output hormuz-audit.jsonl
```

Select one ledger or an explicit lower bound when needed:

```bash
python3 -m hormuz --config hormuz.json audit export \
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

## Commit-time audit chain and external checkpoints

The `audit export` and `audit anchor` commands remain useful inspection and
snapshot tools, but they are not the commit-time evidence contract. New
metadata-only usage and secret-egress audit events are also appended in the
same storage transaction to a versioned chain for their organization. Each
digest binds the complete canonical event, organization, chain version, epoch,
sequence, and previous digest. There is no global cross-tenant chain.

The core commands are intentionally operational rather than request-path work:

```bash
# Local chain state only; this never contacts Object Lock.
hormuz --config /etc/hormuz/hormuz.json audit chain status

# Write a canonical checkpoint file, anchor the tuple to Object Lock, then
# record the successful receipt locally.
hormuz --config /etc/hormuz/hormuz.json audit chain anchor \
  --output /var/lib/hormuz/audit-checkpoint.json

# Verify current event correspondence, ordering, and a trusted checkpoint.
hormuz --config /etc/hormuz/hormuz.json audit chain verify \
  --checkpoint /var/lib/hormuz/audit-checkpoint.json
```

The checkpoint is a small `hormuz.audit-chain-checkpoint` v1 object containing
only the organization, chain version/epoch/sequence, head digest, timestamp,
its own schema ID/version, and random checkpoint identifier. The retained Object Lock object is the
external evidence; the local receipt supports freshness monitoring but is not a
substitute for the protected object.

Run `hormuz audit chain anchor` from a customer-controlled scheduler (for
example a systemd timer or Kubernetes CronJob). The scheduled job anchors the
tenant's organization, chain epoch, sequence, head digest, chain version, and
checkpoint schema version. `audit_chain.maximum_anchor_age_seconds` turns an
overdue receipt into a readiness failure/alert without putting Object Lock on
the request path; the job itself is the only component that contacts the anchor
store.

Commit-time entry v1 remains untouched for historical usage and secret-egress
evidence. Entry v2 is an explicit, finite custody-source union: custody control
events, executor attempts/events, lifecycle events, envelope attestations, and
deletion-block events. It has an exact metadata-only schema and binds its source
schema ID/version/event ID into the canonical digest. Verifiers reject unknown
entry/source versions and arbitrary source JSON rather than treating it as a
forward-compatible extension.

After an intentional restore or migration, an operator must start a new epoch
from a trusted canonical checkpoint. Hormuz never silently resets sequence
numbers. The command requires an explicit confirmation and has no HTTP route:

```bash
hormuz --config /etc/hormuz/hormuz.json audit chain epoch \
  --reason restore \
  --checkpoint /secure/recovery/trusted-checkpoint.json \
  --confirm START_NEW_AUDIT_CHAIN_EPOCH
```

If retained local history does not contain the predecessor referenced by the
new epoch, verification requires that checkpoint file. That makes the recovery
gap explicit rather than turning an older backup into an apparently continuous
chain.

The `epoch` command validates the canonical checkpoint and its tenant/chain
binding, but a local file alone is not evidence of external retention. During
recovery, operators must use the exact checkpoint recovered from the protected
Object Lock version and retain its independent receipt/version evidence.

The precise claim is:

> Once a chain checkpoint is externally anchored, Hormuz can detect
> modification, deletion, reordering, or truncation of any committed event in
> that anchored per-organization history.

Hormuz cannot prove traffic that bypassed it. Events created after the newest
external checkpoint remain inside an anchor-delay risk window. The chain is
tamper-evident evidence, not a claim that a database, storage administrator,
or host root account is physically unable to alter data.

## Security boundary

The checksum detects accidental or deliberate changes only when a trusted copy of the checksum is retained elsewhere. Hormuz does not yet sign exports, make the local SQLite database append-only, send the export to WORM storage, record who ran an export, or enforce audit-reader RBAC. Production deployments should ship events to an organization-controlled append-only destination and apply retention, legal-hold, access-review, and deletion policies there.

The legacy `audit anchor` command creates an export-time snapshot. It remains
separate from the commit-time chain above and does not retroactively protect
events removed before it runs. The configured Object Lock sink can also retain
commit-time checkpoint artifacts; see [CUSTODY.md](CUSTODY.md). Neither mode
makes the source database physically append-only or proves source completeness
before the relevant commit or anchor.

The export covers only requests that passed through Hormuz. Provider-invoice reconciliation and gateway-bypass detection are separate enterprise milestones; this file must not be represented as complete organization-wide usage until those controls exist.
