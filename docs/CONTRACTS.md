# Policy and evidence contract

Hormuz freezes the gateway-control and metadata-only evidence surface before it adds new operational integrations. This document describes the contract implemented in the `0.2` release line. It does not turn Hormuz into a content store, an accounting system, or a provider-protocol fork.

Print the machine-readable manifest from any installed core package:

```bash
hormuz contract manifest
```

The manifest is the canonical inventory of current schema IDs, versions, error codes, enforcement meanings, and compatibility rules. The fixtures in `tests/fixtures/contracts/` are the executable examples for those contracts.

## Ownership and wire convention

Hormuz-owned JSON objects include both fields below and are strictly validated before they are sent or persisted:

```json
{
  "schema_id": "hormuz.gateway-usage-summary",
  "schema_version": 1
}
```

The current Hormuz-owned JSON schemas are:

| Surface | Schema |
| --- | --- |
| `GET /health` | `hormuz.gateway-health` v1 |
| `GET /ready` | `hormuz.gateway-readiness` v1 |
| `GET /v1/gateway/whoami` | `hormuz.gateway-identity` v1 |
| `GET /v1/gateway/usage` | `hormuz.gateway-usage-summary` v1 |
| Hormuz-generated HTTP errors | `hormuz.gateway-error` v3 |
| `hormuz policy check` output | `hormuz.policy-decision` v1 |
| `hormuz policy status --json` | `hormuz.policy-control-status` v1 |
| `hormuz policy history --json` | `hormuz.policy-history` v1 |
| `hormuz policy compare --json` | `hormuz.policy-comparison` v1 |
| `hormuz policy preview --json` | `hormuz.policy-preview` v1 |
| `hormuz custody status --json` | `hormuz.custody-control-status` v3 |
| `hormuz status --json` | `hormuz.usage-report` v1 |
| audit JSONL events | `hormuz.audit-event` v2 |
| immutable audit-anchor artifact | `hormuz.audit-anchor` v1 |
| commit-time audit-chain entry | `hormuz.commit-audit-chain-entry` v1 (legacy gateway evidence) / v2 (strict custody source evidence) |
| externally retainable chain checkpoint | `hormuz.audit-chain-checkpoint` v1 |
| immutable staged policy document | `hormuz.policy-document` v1 |
| PostgreSQL policy-control event row | `hormuz.policy-control-event` v1 |
| PostgreSQL custody-control event row | `hormuz.custody-control-event` v1 |
| PostgreSQL custody-execution attempt row | `hormuz.custody-execution-attempt` v2 |
| PostgreSQL custody-execution event row | `hormuz.custody-execution-event` v1 |
| PostgreSQL custody-lifecycle event row | `hormuz.custody-lifecycle-event` v1 |
| PostgreSQL custody-envelope attestation row | `hormuz.custody-envelope-attestation` v1 |
| PostgreSQL custody deletion-denial row | `hormuz.custody-deletion-event` v1 |
| `hormuz custody evidence export` output | `hormuz.custody-evidence-export` v1 |

OpenAI and Anthropic response bodies remain provider-owned. Hormuz does not add a schema wrapper or fields to those bodies, because doing so would break Codex and Claude Code compatibility. Instead, a relayed provider response carries:

```text
X-Hormuz-Contract: hormuz.relay-metadata;v=1
```

The same header names the separately versioned Hormuz relay-metadata contract. Hormuz may also send metadata-only relay headers such as `X-Hormuz-Policy-Decision`, `X-Hormuz-Requested-Model`, `X-Hormuz-Routed-Model`, `X-Hormuz-Redactions`, and, for a Hormuz-classified protocol error, `X-Hormuz-Error-Code`. Provider-native response and error bodies remain unchanged.

## Commit-time audit-chain contract

`hormuz.commit-audit-chain-entry` v1 is durable metadata-only gateway evidence,
not a provider response. One entry is written atomically with each current v2
usage or secret-egress audit event. Its digest is SHA-256 over a canonical
object containing the organization ID, chain version, epoch, sequence, prior
digest, and complete canonical audit event. The entry is tenant-qualified;
Hormuz does not maintain a global chain.

Version 2 is a separate strict custody-evidence entry shape. It additionally
binds `source_schema_id`, `source_schema_version`, and `source_event_id` plus a
complete source record drawn from an allowlisted union. New source families are
breaking contract work: a validator, manifest entry, compatibility fixture,
migration, and verifier update are required. Version 1 stays readable and is
never rewritten; old verifiers fail closed on an unsupported entry version.

`hormuz.audit-chain-checkpoint` v1 is the compact artifact sent to external
Object Lock. It contains `checkpoint_id`, `organization_id`, `chain_version`,
`chain_epoch`, `sequence`, `head_digest`, and `created_at`. The checkpoint has
no prompt, response, secret, model-input, or credential field. It is valid
only for a nonempty chain head.

Schema v4 adds the entry, head, epoch, and checkpoint-receipt storage without
rewriting historical v1/v2 audit events. Existing pre-v4 evidence remains
readable but is not retroactively covered by the commit-time chain. A
restore/migration creates a new explicit epoch tied to a trusted checkpoint;
it never silently restarts a sequence. See [AUDIT.md](AUDIT.md) and
[STORAGE.md](STORAGE.md) for the verification and operational rules.

## Policy and identity semantics

Every durable v2 event snapshots the authenticated identity at request time:

- `organization_id`, `actor_id`, `actor_name`, `team_id`, and `team_name`;
- `identity_type` (`human`, `service_account`, `ci`, or `connector`);
- `authentication_source` (for example, static bootstrap or OIDC);
- `policy_version`.

In local mode, `policy_version` is a deterministic content-free fingerprint of the policy-relevant configuration, prefixed `local-config-`. In managed PostgreSQL mode it is the exact immutable staged-policy digest, prefixed `sha256:`. A gateway reads and pins the active managed version when a request begins; activation cannot rewrite an in-flight request or its durable evidence. Neither form contains a credential value or request content.

Managed policy control has five administrator-facing strict contracts in
addition to its durable control events. `hormuz.policy-document` v1 accepts
only allowlisted routing, cap, budget, and egress-control fields.
`hormuz.policy-control-status` v1 returns administration metadata for a current
policy administrator: the active digest/generation, immutable version
metadata, structural redacted change summaries, and stable administrator
keys. The additive `hormuz.policy-history` v1 CLI contract is a bounded,
newest-first lifecycle timeline containing only stage, activation, and rollback
events. Each event binds the immutable version ID to its digest, timestamp,
opaque actor key, nullable activation generation, and structural change
summary. Its requested limit is explicit, the fixed maximum is 100, and
`has_more` reports truncation.

`hormuz.policy-comparison` v1 is an administrator-only, value-bearing semantic
diff. It identifies the baseline and candidate by immutable version ID and
digest and reports sorted normalized policy paths with `before`, `after`, and
an `added`, `removed`, or `changed` classification. Object order and allowlist
order are not policy changes. `hormuz.policy-preview` v1 is also
administrator-only and value-bearing. It records the evaluation timestamp,
current UTC usage period and basis, explicit request dimensions, and complete
versioned decisions for one pinned active baseline and one candidate. These
two CLI outputs may contain model aliases, limits, budgets, policy paths, and
decision reasons. They are not durable metadata-only audit evidence and must
not be copied into policy-control event rows.

PostgreSQL `hormuz.policy-control-event` v1 records bootstrap, administrator,
stage, activation, rollback, and break-glass events. It stores both an
explicit durable schema ID/version and opaque actor identity keys plus
structural metadata only; Hormuz validates the exact event shape before it
inserts the row, and the compatibility fixture exercises that durable schema.
See [POLICY_CONTROL.md](POLICY_CONTROL.md) for authorization and lifecycle
semantics.

Managed custody control adds `hormuz.custody-control-status` v3 and
`hormuz.custody-control-event` v1. Status contains tenant-qualified
administrator facts, at most the latest 100 content-free operation intents,
and at most the latest 100 metadata-only custody execution attempts;
`operation_count` and `execution_attempt_count` retain the complete totals.
Each new execution root uses `hormuz.custody-execution-attempt` v2 and its
append-only transitions use `hormuz.custody-execution-event` v1. The new
metadata-only `hormuz.custody-lifecycle-event` v1 records a hash-linked
destructive lifecycle effect by asset ID, generation, and binding fingerprint;
it never contains a path, key reference, credential, target descriptor, or
parameter descriptor. Historical status v1/v2 and routine-only execution root
v1 remain validator-compatible. Unknown fields and invalid cross-field
combinations fail closed. Internal replica leases, prepared barriers, and
acknowledgements are content-free coordination state rather than a new public
evidence schema. They ensure an affected asset is locally blocked before a
replica acknowledges and before the immutable lifecycle event activates. See
[CUSTODY_CONTROL.md](CUSTODY_CONTROL.md).

PostgreSQL schema v8 adds persisted tenant retention policy, immutable
per-record `retain_until`, and legal-hold fields to new custody evidence. The
database clock is authoritative for source timestamps. `hormuz.custody-envelope-attestation`
v1, `hormuz.custody-deletion-event` v1, and the tenant-scoped
`hormuz.custody-evidence-export` v1 have exact fields only. A deletion event
can only declare `decision: deletion_blocked`; it never authorizes a deletion.
The export includes only custody evidence and its global chain positions, not a
claim of complete organization-wide audit history.

Model fields have distinct meanings:

- `requested_model`: the client-supplied model identifier;
- `resolved_alias`: the configured route matched before a fallback, when any;
- `routed_model`: the provider model Hormuz actually selected;
- `provider_reported_model`: the model identifier returned by the provider, when the provider reports one.

The stable `policy_action` values describe enforcement rather than employee behavior. `fallback` means rerouted; its historical wire spelling is retained for compatibility. `capped` means Hormuz lowered the output limit. A `+redacted` suffix means protected material was replaced before provider serialization. `denied`, `provider_policy_denied`, `secret_denied`, and `budget_reservation_denied` mean that Hormuz stopped egress before the provider call.

`status=rate_limited` means the provider returned HTTP 429 after Hormuz had allowed and forwarded the request. It is evidence of a provider response, not an additional Hormuz denial mode. Budget enforcement remains based on actual usage and active reservations, never on a projected report.

## Cost, allocation, coverage, and content boundary

The current core has one explicit economics contract:

```text
cost_basis: configured_rate_card_estimate
allocation_basis: direct_gateway_request
coverage: gateway_captured_requests_only
```

This is a gateway-recorded estimate from the active model-rate configuration. It is not invoiced spend, financial guidance, provider-total reconciliation, or a productivity measure. The event-time person and team are retained so later identity-directory changes do not rewrite historical usage.

No contract in this release permits prompts, responses, secret values, matched detector values, filenames, source files, or other source content. Audit JSONL is intentionally an allowlisted export, so an accidental future content-bearing database column cannot become public evidence automatically.

## Immutable audit-anchor artifact

`hormuz audit anchor` can package current v2 audit events for one tenant into
`hormuz.audit-anchor` v1. The durable artifact has a random `artifact_id`, its
creation time, the ordered events, and a SHA-256 predecessor chain ending in a
`head_digest`. Strict contract validation rejects unknown fields, legacy audit
events, mixed tenants, duplicate event identifiers, and malformed chain
structure. The custody verifier additionally recomputes every chain digest
before the artifact is retained by its configured immutable sink.

The artifact is metadata-only evidence, not a local database journal or a
statement of complete organization-wide activity. An external Object Lock
anchor protects the artifact after it is made; it does not prove that a mutable
source store contained every event before that point. See [CUSTODY.md](CUSTODY.md)
for the retention boundary.

## Errors

Hormuz-generated JSON errors use `hormuz.gateway-error` v3 and a stable
`error.code`. Version 2 added the content-free
`hormuz_storage_unavailable` classification; version 3 adds
`hormuz_custody_restricted` for a lifecycle restriction that blocks a new
provider selection. Historical v1/v2 objects remain validator-compatible and
do not silently accept later codes. The current public-code inventory is
available in `hormuz contract manifest`; it includes authentication,
request-shape, policy, secret, budget, configuration, upstream, custody, and
durable-storage categories. A caller should switch on `error.code`, not an
English error message.

Where an OpenAI- or Anthropic-compatible endpoint must preserve a provider-native body, Hormuz supplies the equivalent stable classification through `X-Hormuz-Error-Code`. The provider body is not rewritten.

## Migration and compatibility

The release line has these intentional pre-stability changes:

1. Audit exports now emit `hormuz.audit-event` v2. The prior v1 audit shapes remain validator-compatible for historical export fixtures, but new events use v2. `upstream_model` is renamed to `routed_model`, and v2 adds identity source/type, organization, policy version, provider-reported model, cost basis, allocation basis, and coverage.
2. `hormuz status --json` changes from an unversioned bare array to `hormuz.usage-report` v1 with report metadata and a `rows` array. `hormuz policy check` uses `routed_model` in place of its former `upstream_model` field and includes `policy_version`.
3. Gateway-owned errors now emit `hormuz.gateway-error` v2 so storage interruptions have a stable, content-free classification without widening the strict v1 error-code set. Historical v1 error objects remain validator-compatible.
4. PostgreSQL schema v2 adds the governed policy-control tables. Every staged policy stores `hormuz.policy-document` v1 in immutable canonical form; every policy-control event stores `hormuz.policy-control-event` v1. There is no down-migration. An older binary fails closed on the newer schema rather than reinterpreting versioned policy state.
5. Immutable audit anchors use `hormuz.audit-anchor` v1. The schema is added to the manifest with a compatibility fixture; its cryptographic chain verifier is separate from structural JSON validation so an operator can verify a retained artifact before trusting it.
6. `GET /ready` adds `hormuz.gateway-readiness` v1 without changing the existing dependency-free `GET /health` liveness contract. A ready response is HTTP 200; an unavailable dependency or an in-progress drain is HTTP 503 with a content-free reason in the same strict readiness schema. See [OPERATIONS.md](OPERATIONS.md).
7. Gateway configuration is now a bounded, duplicate-free, schema-strict deployment input. Existing configurations must remove unsupported or misspelled fields rather than rely on ignored values; malformed and unknown-field input fails before environment-backed secret resolution. See [OPERATIONS.md](OPERATIONS.md#configuration-input).
8. PostgreSQL schema v3 and SQLite schema v3 add the append-only `hormuz.request-attempt` v1 and `hormuz.request-attempt-event` v1 durable-evidence contracts. Immediately before provider egress, Hormuz commits an immutable content-free attempt root, its `pending` event, and its conservative budget reservation together. A reliable provider result appends exactly one `succeeded`, `failed`, or `rate_limited` event and atomically materializes the linked usage audit event. An ambiguous transport or interrupted successful stream appends `outcome_unknown` without releasing its estimate. Stale pending attempts become `outcome_unknown` through the recovery sweeper. There is no automatic provider replay, and the provider-owned relay body remains unbuffered and unchanged.
9. PostgreSQL schema v4 and SQLite schema v4 add `hormuz.commit-audit-chain-entry` v1 and `hormuz.audit-chain-checkpoint` v1. Every new current usage or secret-egress audit event commits with one tenant-qualified chain entry and an updated tenant head. The old event schemas remain unchanged and readable; pre-v4 events are not retroactively represented as commit-time chained evidence. Recovery or migration starts a new explicit epoch linked to a trusted checkpoint, never a silent sequence restart. See [AUDIT.md](AUDIT.md) and [STORAGE.md](STORAGE.md).
10. PostgreSQL schema v5 adds tenant-scoped custody administrators,
    content-free operation intents, append-only approvals, and immutable
    `hormuz.custody-control-event` v1 rows. The dedicated custody-control role
    cannot access usage or policy-control state; runtime and policy-control
    roles cannot access custody state. SQLite remains schema v4 because managed
    custody authority is PostgreSQL-only. An older binary fails closed on the
    v5 ledger. See [CUSTODY_CONTROL.md](CUSTODY_CONTROL.md) and
    [STORAGE.md](STORAGE.md).
11. PostgreSQL schema v6 adds the isolated routine-custody executor's
    `hormuz.custody-execution-attempt` v1 root and
    `hormuz.custody-execution-event` v1 append-only state history. The
    executor writes `pending` before an external effect and may append exactly
    one `succeeded`, `failed`, or `outcome_unknown` event; roots and prior
    events are never rewritten. Current `hormuz.custody-control-status` v2
    exposes metadata-only attempt history while the former v1 status remains
    readable. The restricted executor role cannot mutate custody authority,
    policy, usage evidence, or customer KMS/IAM configuration. SQLite remains
    schema v4 because shared custody authority is PostgreSQL-only. An older
    binary fails closed on the v6 ledger. See [CUSTODY_CONTROL.md](CUSTODY_CONTROL.md)
    and [STORAGE.md](STORAGE.md).
12. PostgreSQL schema v7 adds immutable custody asset identities, a
    per-organization hash-linked `hormuz.custody-lifecycle-event` v1 ledger,
    a derived runtime restriction projection, replica leases, prepared
    admission barriers, acknowledgements, and rewrap/restore attestations.
    A destructive execution root uses v2 and commits its terminal event plus
    lifecycle event, barrier activation, and projection atomically after every
    active replica acknowledges. The fixed five-second lease fences a replica
    that loses coordination; it is not the normal invalidation path. The new
    current custody-status v3
    accepts v2 attempts; v1/v2 status and v1 routine-only attempts remain
    historical compatibility shapes. The runtime role can read only the
    fingerprint registry and projection; it cannot mutate lifecycle evidence
    or restrictions. SQLite remains schema v4 because this governed shared
    custody boundary is PostgreSQL-only. An older binary fails closed on the
    v7 ledger. See [CUSTODY_CONTROL.md](CUSTODY_CONTROL.md) and
    [STORAGE.md](STORAGE.md).
13. PostgreSQL schema v8 keeps v1 audit-chain entries unchanged and adds the
    strict custody-source `hormuz.commit-audit-chain-entry` v2 shape. Every new
    custody control, execution, lifecycle, attestation, or deletion-denial
    source record commits with its v2 entry and updated tenant chain head in the
    same transaction; a missing source/entry pair rolls back. Managed custody
    now requires a bootstrap-only `custody_retention` policy. PostgreSQL derives
    each durable source timestamp and immutable deadline from its own clock and
    persisted policy; later configuration changes cannot shorten a record. The
    current binary rejects unsupported v2 source schemas, while version-1
    gateway entries remain readable. SQLite stays schema v4 because custody
    authority and retention enforcement are PostgreSQL-only. An older binary
    fails closed on the v8 ledger. See [CUSTODY_CONTROL.md](CUSTODY_CONTROL.md)
    and [AUDIT.md](AUDIT.md).

The SQLite migrations add the metadata columns required to emit v2 while retaining existing usage rows, add tenant scope to active budget reservations, add the versioned append-only request-attempt ledger, and add the per-organization commit-time evidence chain. Each persisted usage, secret-evidence, request-attempt, or audit-chain object carries an explicit schema identifier and version where it is a public/durable evidence format, so later code cannot silently reinterpret its evidence shape. Historical rows receive explicit legacy defaults where the old database could not know a value. Earlier applications will not understand newer schemas; rollback therefore requires retaining or restoring the earlier application/database pair. The corresponding PostgreSQL adapter is migration-led and uses a distinct operator migration credential and restricted runtime credential. See [STORAGE.md](STORAGE.md) for the upgrade, rollback, recovery, and remaining-operational-gates boundary.

After this contract is released, any new optional field needs a new documented schema version before release. Removed fields, changed types, changed meanings, and newly required fields also require a new version plus migration guidance.

## Verification

```bash
hormuz contract manifest
python3 -m unittest -v tests.test_contracts tests.test_cli tests.test_gateway tests.test_store
HORMUZ_TEST_POSTGRES_DSN='postgresql://operator@host:5432/hormuz_test' \
  python3 -m unittest discover -s tests -p 'test_postgres_*.py' -v
```

The contract tests validate current and legacy audit fixtures, reject unknown fields, verify the gateway preserves provider bodies, validate strict policy documents, and validate the migration-generated audit evidence. The PostgreSQL suite additionally proves the same normalized repository outcomes, forced tenant isolation, policy-admin bootstrap/activation/rollback, custody-admin bootstrap and approval thresholds, role separation, migration idempotency, partial/newer-schema failure, and content-free malformed-evidence handling against a disposable database.
