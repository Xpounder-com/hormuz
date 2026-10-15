# Shadow routing with a local model

The owner requested a small local routing model, initially in shadow mode, with
TDD at the approved connect → coding request → inspect result/status → disconnect
workflow. Hard local-only admission and conversation affinity remain authoritative.
Recommendations are evaluated against actual coding checks before any proposal
to let a learned model select routes.

## Connect

Use [shared local setup](SHARED_LOCAL_SETUP.md) with a new configuration path and
add `--shadow-router` to both preview and apply. It uses the same verified model
and owned llama.cpp process for classification and coding, sequentially. No
second model, download, token export or cloud account is required. CLI and
skills-only plugin callers use this same command.

The version-3 `hormuz.local-setup` receipt adds
`configuration.shadow_router: shadow`. Without this explicit option, existing
version-1 environment-backed and version-2 managed-credential setup is unchanged.
Repeated identical setup reuses the active connection. Existing configurations
are preserved; changing the opt-in requires a separately selected new configuration
and the existing disconnect/reconnect procedure. Setup never rewrites an active
configuration to enable classification.

Advanced existing configurations can declare:

```json
{
  "shadow_router": {
    "schema_version": 1,
    "mode": "shadow",
    "model": "local-router",
    "local_model": "local-code",
    "timeout_seconds": 2,
    "max_input_bytes": 8192
  }
}
```

Both aliases must explicitly declare local, unauthenticated text Responses
execution and exact model revisions. The target needs a local qualification;
the classifier is an explicit experimental model declaration. A loopback address
alone does not prove that an externally operated endpoint performs local inference.
Shared setup additionally verifies and owns the pinned runtime/model files.
AI Work and `auto` or `local_only` admission must be enabled. Active learned
routing and cloud classifiers are rejected.

## Observe without changing execution

1. Existing policy, secret inspection, admission and work reservation run first.
2. A fresh request's latest sanitized user text is sent to the declared local
   classifier. Its instruction predicts fit for the specific local target and
   requests exactly `LOCAL`, `CLOUD` or `UNKNOWN`. These are uncalibrated model
   recommendations, not measured probabilities or permissions.
3. The existing selected route executes. Neither recommendation nor classifier
   failure can replace it. Final policy/readiness checks still run before dispatch.
4. Inspect authenticated `/v1/work/jobs/{work_id}/attempts` or the ordinary work
   receipt. A version-1 `hormuz.shadow-routing` object records the recommendation,
   classifier/target revisions, actual route, bounded timing and observed usage.

Continuing provider/tool conversations record `affinity_preserved` and make no
classifier call. Unknown or unsupported local-only requests remain blocked
before classification. A cloud recommendation never authorizes cloud execution;
a local recommendation never qualifies an otherwise ineligible model.

The helper shares the router's single local inference lease. Defaults allow
eight output tokens, 8 KiB of user text and a two-second absolute deadline;
configuration permits at most five seconds and 16 KiB. There is no retry or
background queue. Oversized/missing text, invalid answers, unavailable or busy
models and deadlines yield unknown advice. Classification consumes measured
latency; it is not a free or zero-overhead operation. A canceled client closes
the owned classifier transport and prevents the unstarted coding request.

Classifier inference requires the caller's client/model permissions and output
cap. Configured monthly token or monetary limits currently make classification
`budget_unqualified`; the helper does not bypass those limits to obtain advice.
Classifier token observations are separate from task usage, with monetary cost
explicitly unknown. Classification never establishes coding success.

## Evidence and compatibility

Request text, model reply text, credentials and conversation identifiers are
absent from shadow records. Invalid model output is replaced with fixed labels.
The existing private AI Work ledger gains one nullable metadata column, not a
second learning or transcript store. Pages containing shadow records use
`hormuz.ai-work-attempt-history` version 2; legacy pages remain version 1.
[Advice](../tests/fixtures/shadow-routing-v1.json) and
[setup](../tests/fixtures/local-setup-v3.json) fixtures freeze their public shapes.

Migration is additive on opening the existing ledger; old attempts retain no
shadow evidence and pending work retains conservative recovery. Disconnect with
the current binary before rollback, retain the ledger/evidence, and use a
configuration without `shadow_router` for an older binary. Read/export shadow
records with a current reader; an older reader cannot interpret this extension.
Disconnect still restores only owned settings and stops only owned resources.

## Coding-outcome evaluation

The frozen [coding cases](../tests/fixtures/shadow-coding-cases-v1.json) specify
initial files, goals and independent Python checks. Each starts with failing
checks. The real pinned Codex CLI attempts each repair autonomously through the
installed Hormuz package and existing pinned llama.cpp/Qwen3-4B files. One bounded
episode per case is retained, including failed or timed-out repairs; no solution
script is supplied to the model and there are no model-quality retries.

Match the first fresh recommendation to the final independent check result.
Keep classification failures, incomplete client execution and unevaluable cases
visible. Report completed coding checks separately from HTTP success, recommendations
and the work ledger's unknown outcome. Only actual local execution evaluates local
fit; unobserved cloud results cannot establish comparative cloud quality or savings.
This small case set is an experiment, not general coding-model qualification.

Promotion requires additional held-out evidence for the actual model/client
combination, including false-local recommendations, unknown coverage, overhead
and continuity. No setting in this slice enables live learned routing. Codex
subscription execution and desktop/plugin activation remain separate qualification.

## Pinned experiment: 2026-10-15

The installed runtime at `8c0d6a0` was evaluated once with Codex CLI 0.147.0
(Darwin arm64), llama.cpp b11617 and the verified Qwen3-4B-Q4_K_M files. The
same model served as classifier and coding target. The independent checks
rejected every broken baseline and accepted every known correct reference
before the cases were frozen. Each native episode had a 90-second deadline
and no quality retry.

| Coding repair | Shadow advice | Independent check | Native exit | Classifier time |
| --- | --- | --- | --- | --- |
| Signed ceiling division | local | failed | 0 | 478 ms |
| Quoted CSV parsing | local | failed | 0 | 474 ms |
| Atomic, idempotent transfers | local | failed | 0 | 537 ms |

The result was **zero passing repairs and three false-local recommendations**.
All implementation files remained unchanged: the first two episodes searched
for incorrect filenames, and the third described intended changes without
executing tools. A normal client exit established neither a repair nor success.
There were no missing recommendations, unknown recommendations, timed-out
episodes or omitted cases. No cloud comparison was performed.

All 16 actual coding requests used the pinned local model. The 13 observed
continuations preserved affinity; the final case had no continuation. Disconnect
restored configuration and stopped the owned engine. The source and installed
wheel each passed 147 workflow tests with no skips. These workflow results
establish guards and integration behavior; the failed repair checks establish
only the observed coding outcomes of this small experiment.

Keep this classifier **shadow-only**. This run supplies no evidence for promoting
its recommendations into execution decisions. The content-free
[evaluation receipt](shadow-routing-evaluation-20261015.json) records pins,
fixture and private evidence digests, latency, coverage, outcomes and cleanup.
