# Bounded local coding probes

This experimental outcome-collection path for [#537](https://github.com/Xpounder-com/hormuz/issues/537)
uses the shared connected-client owner at connect → request → inspect → disconnect.
It does not install coding evidence or admit ordinary unqualified coding.
The [qualification criteria](LOCAL_CODING_QUALIFICATION.md) still govern readiness.

## Operator workflow

1. Use existing verified engine/model files and an explicit private Codex home.
   Generate a local-only setup with AI Work enabled and its answer cache off.
   The route must declare `code_change`; it may remain unqualified. Never use
   `configured` mode, invented outcomes or a disguised noncoding task.
2. Before inference, capture the actual native request without forwarding to a
   model. Freeze source/wheel, client/runtime/model/hardware, instructions and
   inline tools, full case inventory, task/checker hashes and bounds. Preserve
   earlier failures; keep independent checks outside the writable model project.
3. Create a content-free version-1 plan (see the [fixture](../tests/fixtures/coding-probe-plan-v1.json)).
   `plan_sha256` refers to the full preregistered campaign plan;
   `first_input_sha256` binds the actual first Responses `input`. Canonical JSON
   uses sorted keys, compact separators, UTF-8 and finite numbers.
4. Explicitly authorize that case over the existing private owner channel:

   ```sh
   hormuz client qualify codex --client-home /absolute/private/codex-home \
     --plan /absolute/private/probe-plan.json
   ```

   Attach the returned `probe_id` as `X-Hormuz-Qualification-Probe` only to this
   native episode's requests. Codex 0.160.0 supports a per-process HTTP header
   override on the generated provider without editing installed settings.
   The generated credential helper still authenticates the client; no manual
   token export is needed.
5. Run one bounded episode; inspect public results, file diff and independent
   checks separately. Inspect permission with `client qualify codex
   --client-home ...` and ordinary readiness with `client status codex
   --client-home ... --routing`. HTTP success alone is not coding success.
6. Revoke future dispatch with `client qualify codex --client-home ... --revoke`,
   then disconnect with the observed connection generation. Revoke does not
   claim in-flight transport finished. Cancellation and disconnect retain
   existing owned-process cleanup and conditional restoration.

The skills-only plugin can call the same explicit CLI operation. Installation
does not arm a probe. No MCP interception, second agent loop, download service,
automatic quality retry or new outcome store is introduced.

## Admission boundaries

Plans are at most 3,072 UTF-8 bytes and name one managed local route and actor.
The subject must match the supported client version, OS family/architecture,
`managed-llama.v1` archive digest, model revision/alias, protocol/capabilities,
byte/output/time bounds and instruction/tool profile hash. Budgets allow 1–32
client submissions and 1–90 seconds from authorization, with the existing
single local inference slot and the smaller remaining deadline. There are no
additional retries or alternate destinations.

The first request must have the frozen input/profile and a recognized native
conversation. Later submissions keep the work and destination generation,
preserve the entire last submitted input prefix, and append only assistant
messages or inline function call/results. New user turns, replays, branches
from earlier input, helpers, unsupported state, drift, expired/revoked markers
are denied. Existing policy, privacy, envelope, capability, readiness and final
dispatch checks remain. A consumed submission remains consumed after an unknown
or canceled outcome; its count does not prove upstream success.

Ordinary requests without the marker use the unchanged evidence gate, even
while a probe is armed. Shadow advice has no execution authority. Probes skip
extra shadow classifier inference so the frozen budget describes the repair.
Ordinary noncoding work retains shadow observation. Probe status always reports
`automatic_admission_effect: "none"`; coding readiness and automatic task lists
do not change.

Plans and headers are operator assertions, not cryptographic proof of quality or
executable identity. The private owner authorizes an existing authenticated
actor; the marker is neither a cloud credential nor an HTTP authorization
endpoint. The operator still owns honest preregistration, checking and retaining
all outcomes. A subject/profile change requires a new campaign version and fresh
held-out tasks. One development repair cannot qualify a model.

## Contracts, migration and rollback

The manifest discovers these new version-1 contracts:

| Contract | Meaning |
| --- | --- |
| `hormuz.coding-probe-plan` | Frozen campaign/case references, subject, input hash and budget |
| `hormuz.coding-probe-status` | Inactive, armed, running, exhausted, expired or revoked permission |
| `hormuz.coding-probe-error` | Invalid plan, subject mismatch, already active, or missing local-only/work/uncached prerequisite |
| `hormuz.coding-probe-relay` | Provider-owned response with run and destination/work headers |

Relay headers use `X-Hormuz-Contract: hormuz.coding-probe-relay;v=1`,
`X-Hormuz-Qualification-Probe`, `X-Hormuz-Routing-Mode: local_only` and initial
`X-Hormuz-Routing-Reason: qualification_probe`; continuations use
`session_affinity`. No ordinary routing-qualification header is asserted for a
probe. Provider bodies and existing routing status/receipt/relay contracts stay
unchanged. The [status fixture](../tests/fixtures/coding-probe-status-v1.json)
defines the public shape.

Permission lives only in the connected owner's memory. An active/exhausted grant
cannot be replaced until revoked or expired. Restart, disconnect or recovery
loses it. Reauthorization is explicit and does not erase separately retained
outcomes. There is no durable campaign ledger, journal migration or config flag.
Invalid plans do not change settings. Older binaries lack this command. Revoke
and disconnect before downgrade; retain all evidence. Downgrading past the coding
evidence gate has the separate risks documented in the qualification guide.

Controlled external workflow fixtures establish isolation, affinity, no replay
and finite lifecycle behavior, not native coding quality.
