# Plugin capability contract, version 1

The 0.2.0 skills-only plugin adds focused diagnosis, explanation, qualification,
recommendation, privacy/budget, insights and maintenance workflows. All hosts
call the same CLI; the persistent routing adapter remains pinned Codex CLI
0.147.0. Installing the plugin does not connect a client or install assets.

The [capability workflow](../skills/hormuz/references/capabilities.md) documents
commands, bounds, activation, output privacy and unsupported scope.

## Public receipts

New CLI JSON receipts have a schema identifier and integer version 1:

- `hormuz.client-diagnosis`: connection receipt, compatibility, scope, routing
  mode, qualification count, unobserved model/task state and recovery action.
- `hormuz.request-explanation`: retained metadata, observed policy/fallback,
  explicitly unobserved optimization and retained-opt-in coverage.
- `hormuz.policy-proposal`: changed public fields, inactive/reconnect state,
  original-source preservation, consent separation and activation next action.
- `hormuz.model-qualification`: setup fingerprints, suite, bounded check results,
  narrow task scope, expiry and explicit absence of route authorization.
- `hormuz.model-recommendation`: current/stale evidence, advisory recommendation,
  hardware facts, unverified inference fit, report trust and no downloads.
- `hormuz.usage-insights`: existing spending coverage plus observed tokens,
  period, metadata-only observations and no measured savings.
- `hormuz.capability-error`: content-free fixed error code, exit 2.

Compatibility examples live in the plugin-capabilities fixture. New receipt
versions must be handled explicitly; the commands do not consume arbitrary
future qualification-report versions. Unknown/missing evidence is not success.

## History compatibility

Consent/settings and outer management receipts remain version 1. Newly captured
history entries use entry version 2, adding `routing_evidence` with its own version
1: policy action, routing mode/reason and observed policy fallback. The cipher
and key layout are unchanged. Readers accept retained entry v1 without that
field and entry v2 with it, without rewriting legacy records. Unknown versions
and mismatched version/field combinations fail closed. Deletion, retention and
exports apply to both formats. Exported entries retain their entry version.

Before downgrade, export/delete version 2 entries using this version. Older
readers reject them without rewriting or misinterpreting them. Disabling capture
alone does not remove v2 entries. No migration from external histories occurs.

## Implementation scope

The user approved the ten implementation slices. Conservative defaults avoid
MCP/UI expansion, implicit downloads, cloud probes, automatic remediation,
unsigned report enrollment and automatic runtime updates. The tiny qualification
suite is a useful starting envelope, not broader coding qualification. Historical
quality failures in existing profiles remain valid. Wider evaluation thresholds
and richer interaction designs remain future product decisions.

Verification uses the agreed public CLI/connection seam and synthetic client and
provider boundaries. Live macOS/model quality is not established by synthetic
workflow tests. A separately scoped smoke test is required for a live claim.
