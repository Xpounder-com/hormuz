# Policy and evidence contract

Hormuz freezes the gateway-control and metadata-only evidence surface before it adds new operational integrations. This document describes the contract implemented in the `0.2` release line. It does not turn Hormuz into a content store, an accounting system, or a provider-protocol fork.

Print the machine-readable manifest from any installed core package:

```bash
hormuz contract-manifest
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
| `GET /v1/gateway/whoami` | `hormuz.gateway-identity` v1 |
| `GET /v1/gateway/usage` | `hormuz.gateway-usage-summary` v1 |
| Hormuz-generated HTTP errors | `hormuz.gateway-error` v2 |
| `hormuz policy-check` output | `hormuz.policy-decision` v1 |
| `hormuz policy status --json` | `hormuz.policy-control-status` v1 |
| `hormuz status --json` | `hormuz.usage-report` v1 |
| audit JSONL events | `hormuz.audit-event` v2 |
| immutable audit-anchor artifact | `hormuz.audit-anchor` v1 |
| immutable staged policy document | `hormuz.policy-document` v1 |
| PostgreSQL policy-control event row | `hormuz.policy-control-event` v1 |

OpenAI and Anthropic response bodies remain provider-owned. Hormuz does not add a schema wrapper or fields to those bodies, because doing so would break Codex and Claude Code compatibility. Instead, a relayed provider response carries:

```text
X-Hormuz-Contract: hormuz.relay-metadata;v=1
```

The same header names the separately versioned Hormuz relay-metadata contract. Hormuz may also send metadata-only relay headers such as `X-Hormuz-Policy-Decision`, `X-Hormuz-Requested-Model`, `X-Hormuz-Routed-Model`, `X-Hormuz-Redactions`, and, for a Hormuz-classified protocol error, `X-Hormuz-Error-Code`. Provider-native response and error bodies remain unchanged.

## Policy and identity semantics

Every durable v2 event snapshots the authenticated identity at request time:

- `organization_id`, `actor_id`, `actor_name`, `team_id`, and `team_name`;
- `identity_type` (`human`, `service_account`, `ci`, or `connector`);
- `authentication_source` (for example, static bootstrap or OIDC);
- `policy_version`.

In local mode, `policy_version` is a deterministic content-free fingerprint of the policy-relevant configuration, prefixed `local-config-`. In managed PostgreSQL mode it is the exact immutable staged-policy digest, prefixed `sha256:`. A gateway reads and pins the active managed version when a request begins; activation cannot rewrite an in-flight request or its durable evidence. Neither form contains a credential value or request content.

Managed policy control has two additional strict contracts. `hormuz.policy-document` v1 accepts only allowlisted routing, cap, budget, and egress-control fields. `hormuz.policy-control-status` v1 returns administration metadata for a current policy administrator: the active digest/generation, immutable version metadata, structural redacted change summaries, and stable administrator keys. PostgreSQL `hormuz.policy-control-event` v1 records bootstrap, administrator, stage, activation, rollback, and break-glass events. It stores both an explicit durable schema ID/version and opaque actor identity keys plus structural metadata only; Hormuz validates the exact event shape before it inserts the row, and the compatibility fixture exercises that durable schema. See [POLICY_CONTROL.md](POLICY_CONTROL.md) for authorization and lifecycle semantics.

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

`hormuz audit-anchor` can package current v2 audit events for one tenant into
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

Hormuz-generated JSON errors use `hormuz.gateway-error` v2 and a stable `error.code`. Version 2 adds the content-free `hormuz_storage_unavailable` classification; v1 remains validator-compatible for historical clients and does not silently accept that new code. The current public-code inventory is available in `hormuz contract-manifest`; it includes authentication, request-shape, policy, secret, budget, configuration, upstream, and durable-storage categories. A caller should switch on `error.code`, not an English error message.

Where an OpenAI- or Anthropic-compatible endpoint must preserve a provider-native body, Hormuz supplies the equivalent stable classification through `X-Hormuz-Error-Code`. The provider body is not rewritten.

## Migration and compatibility

The release makes two intentional pre-stability changes:

1. Audit exports now emit `hormuz.audit-event` v2. The prior v1 audit shapes remain validator-compatible for historical export fixtures, but new events use v2. `upstream_model` is renamed to `routed_model`, and v2 adds identity source/type, organization, policy version, provider-reported model, cost basis, allocation basis, and coverage.
2. `hormuz status --json` changes from an unversioned bare array to `hormuz.usage-report` v1 with report metadata and a `rows` array. `hormuz policy-check` uses `routed_model` in place of its former `upstream_model` field and includes `policy_version`.
3. Gateway-owned errors now emit `hormuz.gateway-error` v2 so storage interruptions have a stable, content-free classification without widening the strict v1 error-code set. Historical v1 error objects remain validator-compatible.
4. PostgreSQL schema v2 adds the governed policy-control tables. Every staged policy stores `hormuz.policy-document` v1 in immutable canonical form; every policy-control event stores `hormuz.policy-control-event` v1. There is no down-migration. An older binary fails closed on the newer schema rather than reinterpreting versioned policy state.
5. Immutable audit anchors use `hormuz.audit-anchor` v1. The schema is added to the manifest with a compatibility fixture; its cryptographic chain verifier is separate from structural JSON validation so an operator can verify a retained artifact before trusting it.

The SQLite migration adds the metadata columns required to emit v2 while retaining existing usage rows, then adds tenant scope to active budget reservations. Each persisted usage or secret-evidence row now also carries `evidence_schema_id` and `evidence_schema_version`, so later code cannot silently reinterpret its evidence shape. Historical rows receive explicit legacy defaults where the old database could not know a value. Earlier applications will not understand the v2 export shape; rollback therefore requires retaining or restoring the earlier application/database pair. The corresponding PostgreSQL adapter is migration-led and uses a distinct operator migration credential and restricted runtime credential. See [STORAGE.md](STORAGE.md) for the upgrade, rollback, recovery, and remaining-operational-gates boundary.

After this contract is released, any new optional field needs a new documented schema version before release. Removed fields, changed types, changed meanings, and newly required fields also require a new version plus migration guidance.

## Verification

```bash
hormuz contract-manifest
python3 -m unittest -v tests.test_contracts tests.test_cli tests.test_gateway tests.test_store
HORMUZ_TEST_POSTGRES_DSN='postgresql://operator@host:5432/hormuz_test' \
  python3 -m unittest -v tests.test_postgres
```

The contract tests validate current and legacy audit fixtures, reject unknown fields, verify the gateway preserves provider bodies, validate strict policy documents, and validate the migration-generated audit evidence. The PostgreSQL suite additionally proves the same normalized repository outcomes, forced tenant isolation, policy-admin bootstrap/activation/rollback, role separation, migration idempotency, partial/newer-schema failure, and content-free malformed-evidence handling against a disposable database.
