# Local repair version 5: frozen corrected development probe

This preregistration follows the retained [v4 pre-inference denial](../local-repair-20261016-v4/README.md).
That native client attempt never reached a model. Version 5 uses version-2
probe/profile semantics to ignore only recognized native client message IDs.
It preserves the task goals, existing model/runtime, budgets and untouched
sixteen-case inventory. No model result or held-out observation tuned this version.

The [complete plan](coding-plan.json), [corpus](corpus.json), [setup profile](setup-profile.json),
[actual native profile](request-profile.json), [first input](first-input.json),
[probe plan](probe-plan.json), [candidate](candidate.json) and [runner](development-runner.py)
are frozen before inference. The [manifest](frozen-manifest.json) binds 152
artifacts, including all 136 installed package files. Source runtime is commit
`4e63d49389922df4079fe22c49d57f191756b910`; the later validation commit changes
only test fixtures. The wheel SHA-256 is
`8515fcff6884052b35ecaaeedb1e6e625003979edb0bcff7cfeda9a587173c06`.
Manifest SHA-256 is
`0caa83eca1a8e5db839b7319b71f66fdd60c18aaa946d37298cc68bd7d1e9482`.
Absolute paths describe this one observed host.

Only `dev-clamp` is authorized now: one actual model episode, at most eight
native submissions and ninety seconds, with no transport or quality retries.
Twelve held-out cases and the other three development cases remain unrun.
A passing first case cannot qualify this model; all sixteen outcomes remain
required. Original v1/v2 failures, v3 preparation and v4 invalid treatment are
preserved independently.

[Zero-inference preflight](preflight.json) captured one native POST without
forwarding, observed zero router attempts, and restored settings/closed owned
listeners. The launch configuration and emitted permission context identify
only the project and private temporary directory as writable. The oracle,
corpus and frozen records lie outside them. This is not a filesystem read
isolation claim. Independent checks execute under a separate no-network,
no-write macOS sandbox; the oracle hash must remain unchanged.

At preregistration the actual model outcome was pending. The one-shot runner checks
all frozen hashes before authorization, records a consumed marker, retains
native events and implementation diff, evaluates unchanged independent checks,
revokes permission, disconnects and verifies restoration/closed listeners.
Its failure finalizer preserves missing, invalid and timeout outcomes.
It emits no coding receipt and never changes ordinary automatic admission.
Results are retained as separate files without rewriting this manifest.

## Retained development outcome

The [observation](observation.json), [full public result](development-result.json)
and [native events](development-native.jsonl) record one completed local model
episode in 25,626 ms. Codex exited zero with one completed turn. Six inference
submissions used one work; public totals report no retry, failover, unknown or
failed transport attempts. The retained five most recent attempt summaries
show the same model/generation and conversation affinity; the state endpoint's
per-work default page is five, while its total includes all six requests.

**The repair failed.** The model read only the function declaration using
`cat solution.py | rg 'def clamp'`, proposed a plausible implementation in text,
then twice ran `apply_patch` without a patch. Both commands exited 2 with usage
output. It never edited [solution.py](implementation-after.py). The unchanged
independent oracle still returned assertion exit 1; the empty
[patch](implementation.patch) is retained. A normal turn and successful model
transport do not satisfy the coding criteria.

This is zero passing episodes out of the one observed development case, with
fifteen planned cases missing. No held-out case ran and no quality retry occurred.
This does not establish general model incompetence or a statistically estimated
failure rate. It demonstrates the observed tool-use failure in this exact
512-token/request native profile. Do not change the prompt/model or run another
episode under this frozen version.

Probe permission was revoked; disconnect restored settings and closed both owned
listeners. Public coding readiness stayed `unknown`, with no automatic coding
tasks and no passing receipt. The normal evidence gate remains authoritative.
The candidate remains experimental and unqualified.

The independent failure check is the frozen corpus's `dev-clamp` checker against
[the unchanged implementation](implementation-after.py), using the runner's
`independent_check` procedure under a two-second no-network/no-write sandbox.
The consumed marker deliberately prevents another native run. A future changed
model/instruction/tool treatment requires a new campaign and fresh held-out
inventory before inference, while retaining this failure.
