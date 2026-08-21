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
| Hormuz-generated HTTP errors | `hormuz.gateway-error` v1 |
| `hormuz policy-check` output | `hormuz.policy-decision` v1 |
| `hormuz status --json` | `hormuz.usage-report` v1 |
| audit JSONL events | `hormuz.audit-event` v2 |

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

The currently emitted `policy_version` is a deterministic, content-free fingerprint of the local policy-relevant configuration, prefixed `local-config-`. It deliberately contains no credential value or request content. Issue [#21](https://github.com/Xpounder-com/hormuz/issues/21) will replace this local baseline with immutable policy-administration versions; consumers must not mistake the current fingerprint for that future control-plane capability.

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

## Errors

Hormuz-generated JSON errors use `hormuz.gateway-error` v1 and a stable `error.code`. The current public-code inventory is available in `hormuz contract-manifest`; it includes authentication, request-shape, policy, secret, budget, configuration, and upstream categories. A caller should switch on `error.code`, not an English error message.

Where an OpenAI- or Anthropic-compatible endpoint must preserve a provider-native body, Hormuz supplies the equivalent stable classification through `X-Hormuz-Error-Code`. The provider body is not rewritten.

## Migration and compatibility

The release makes two intentional pre-stability changes:

1. Audit exports now emit `hormuz.audit-event` v2. The prior v1 audit shapes remain validator-compatible for historical export fixtures, but new events use v2. `upstream_model` is renamed to `routed_model`, and v2 adds identity source/type, organization, policy version, provider-reported model, cost basis, allocation basis, and coverage.
2. `hormuz status --json` changes from an unversioned bare array to `hormuz.usage-report` v1 with report metadata and a `rows` array. `hormuz policy-check` uses `routed_model` in place of its former `upstream_model` field and includes `policy_version`.

The SQLite migration adds the metadata columns required to emit v2 while retaining existing usage rows. Each persisted usage or secret-evidence row now also carries `evidence_schema_id` and `evidence_schema_version`, so later code cannot silently reinterpret its evidence shape. Historical rows receive explicit legacy defaults where the old database could not know a value. Earlier applications will not understand the v2 export shape; rollback therefore requires retaining or restoring the earlier application/database pair. Issue [#26](https://github.com/Xpounder-com/hormuz/issues/26) is the separate gate for automated upgrade, rollback, SQLite/PostgreSQL parity, and failure-path proof. This issue does not claim those proofs are complete.

After this contract is released, any new optional field needs a new documented schema version before release. Removed fields, changed types, changed meanings, and newly required fields also require a new version plus migration guidance.

## Verification

```bash
hormuz contract-manifest
python3 -m unittest -v tests.test_contracts tests.test_cli tests.test_gateway tests.test_store
```

The contract tests validate current and legacy audit fixtures, reject unknown fields, verify the gateway preserves provider bodies, and validate the migration-generated audit evidence.
