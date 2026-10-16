# Plugin capability contract, version 1

The 0.2.1 skills-only plugin adds focused diagnosis, explanation, qualification,
recommendation, privacy/budget, insights and maintenance workflows. All hosts
call the same CLI; the persistent routing adapter supports exact Codex CLI
0.147.0 and [the 0.160.0 single-agent HTTP profile](CODEX_0160.md). Installing
the plugin does not connect a client or install assets.

The [capability workflow](../skills/hormuz/references/capabilities.md) documents
commands, bounds, activation, output privacy and unsupported scope.

## Public receipts

CLI JSON receipts have a schema identifier and integer version. These receipts
use version 1 unless stated otherwise:

- `hormuz.client-diagnosis`: connection receipt, compatibility, scope, routing
  mode, qualification count, unobserved model/task state and recovery action.
  Its additive `override_diagnosis` contains `hormuz.client-overrides` v1:
  pinned client version, selected-context scope, applied/inactive/ignored layers,
  conflict source/key names, effective-routing comparisons and explicit
  unobserved running-session state. Inspection failures retain connection state
  and return unknown routing with a fixed error code. Values, prompts and
  credentials are omitted. Existing readers may ignore this nested receipt.
- `hormuz.request-explanation`: retained metadata, observed policy/fallback,
  explicitly unobserved optimization and retained-opt-in coverage.
- `hormuz.policy-proposal`: changed public fields, inactive/reconnect state,
  original-source preservation, consent separation and activation next action.
- `hormuz.model-qualification` **v2**: setup fingerprints, suite, bounded check
  results, narrow task scope, expiry, explicit absence of route authorization
  and authenticated execution identity (requested alias, destination, generation,
  upstream model and revision). Each probe and the final state must match.
  Shadow-enabled configurations are rejected before model requests or report
  publication with `qualification_shadow_router_unsupported`, preserving the
  fixed four-request model envelope without disabling configured classification.
  Authenticated work-state v3 adds the boolean
  `connection.shadow_router_enabled`. Qualification checks the active router
  before probes and after the suite; copying or editing the supplied file cannot
  hide an active classifier. A live-state rejection records a failed report with
  zero model probes. Older state readers may ignore this additive metadata;
  missing/nonboolean metadata fails closed for qualification. Upgrade the router
  before using the new qualifier against an older running server. No stored
  work state is rewritten; the work-state compatibility fixture covers rollback.
- `hormuz.model-recommendation`: current/stale evidence, advisory recommendation,
  hardware facts, unverified inference fit, report trust and no downloads.
- `hormuz.usage-insights`: existing spending coverage plus observed tokens,
  period, metadata-only observations and no measured savings.
- `hormuz.capability-error`: content-free fixed error code, exit 2.

Required-field compatibility fixtures live in the plugin-capabilities fixture. New receipt
versions must be handled explicitly; the commands do not consume arbitrary
future qualification-report versions. Unknown/missing evidence is not success.
Qualification readers validate required fields, exact types, check consistency,
limits, scope, expiry and execution identity. Complete legacy v1 reports remain
readable but require requalification because they lack execution binding;
malformed reports return `qualification_invalid`. Router generation changes
also require requalification. Older readers reject v2 reports, so downgrade
requires fresh qualification with the older version; reports grant no routing
authority and are never rewritten automatically.

## History compatibility

Consent/settings and outer management receipts remain version 1. Newly captured
history entries use entry version 2, adding `routing_evidence` with its own version
1: policy action, routing mode/reason and observed policy or provider-capacity
fallback. The first failed attempt remains separate from the fallback attempt.
The cipher
and key layout are unchanged. Readers accept retained entry v1 without that
field and entry v2 with it, without rewriting legacy records. Unknown versions
and mismatched version/field combinations fail closed. Deletion, retention and
exports apply to both formats. Exported entries retain their entry version.

Before downgrade, disconnect or disable capture, then export/delete version 2 entries using this version. Older
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

Override inspection follows the selected supported Codex configuration loader:
user defaults, selected profile file, trusted project layers, generic `-c`
overrides, then dedicated launch flags. Project provider/profile settings are
ignored by that client. Selected files are bounded and checked for safe local
ownership; missing profiles, malformed files and unsupported supplied flags
remain explicit inspection failures. See the pinned
[loader](https://github.com/openai/codex/blob/rust-v0.147.0/codex-rs/config/src/loader/mod.rs)
and [CLI precedence](https://github.com/openai/codex/blob/rust-v0.147.0/codex-rs/tui/src/lib.rs).
The 0.160.0 version is selected from the observed executable or owned journal;
its `--ignore-user-config` bypass is attributed to launch settings. Supplied
unsupported versions retain the connection receipt with unobserved routing.
