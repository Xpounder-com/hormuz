# Optional personal optimization experiments

These APIs are shipped as provider-free reference experiments and are not
enabled in the first release request path. A passing fixture proves only the
typed local contract described below. It does not prove a built-in agent
integration, an avoided model call in ordinary work, live Jev behavior, or a
net product benefit.

## Cycle 3: typed tool-session history

`TypedToolHistoryCompactor` accepts only `typed-tool-session-v1` turns. It keeps
all system turns, explicit constraints, unresolved questions, recent turns, and
the latest state for every named tool. Older resolved turns may be replaced by
an extractive local summary. The full original is held only in a bounded
in-memory `RecoveryBuffer` and is removed on recovery or process exit. No model
or hosted compactor is called; auxiliary calls are reported as zero. Byte
reduction and local overhead are measured. Task-quality promotion still
requires paired live-model evidence in addition to the provider-free contract
fixture; this experiment is not integrated with an agent and is not a default
transform.

## Cycle 4: possible friction and one safe intervention

`BehaviorTracker` derives an intent signature on-device, keeps at most a bounded
number in memory, and returns only a category, count, coverage, and uncertainty.
It excludes normal history retransmission, pagination, polling, automatic
retry, and explicitly marked legitimate iteration. A repeated signature is
labelled `possible_friction_not_model_failure`; it is not evidence about model
quality or developer productivity.

`BoundedReadRetry` is the one intervention. It permits at most one retry only
when an adapter marks the operation read-only, non-side-effecting, retryable,
and not already retried. Every write, network-side effect, uncertain operation,
or excluded loop returns control instead. Detection counts as zero savings.
`InterventionComparison` reports savings only when baseline and intervention
cost/delay observations both exist, plus whether later repetition was seen.

## Cycle 5: choose an execution method

H14 currently has a no-go result for the built-in catalog. Codex, Claude Code,
and Aider expose no supported explicit execution yield through these adapters,
so Hormuz does not intercept a hidden model decision and the release runtime
does not invoke `ExecutionRouter`.

`DeterministicExecutor` is the H15 reference contract for exact JSON counting,
path lookup, or required-key validation. Given a caller-owned typed step, it
preserves the step ID, tool-call ID, permission, side-effect flag, and streaming
requirement. Ambiguous natural language and write-capable requests fall back.
The provider-free fixture demonstrates the contract but does not establish
that an actual agent model step was avoided; promotion needs an agent or SDK
that explicitly yields that step.

The optional `JevChoiceAdapter` handles only `handler_select` over caller-
supplied options. It sends the explicitly supplied state to
`https://api.typesafe.ai/v1/systemone`, returns the selected choice and
probability, discloses the state shape, and records input/output usage, network
delay, and explicit monetary cost when the provider supplies it. It is constructed only after the
developer selects TypeSafe and supplies a credential. Promotion requires
Hormuz-owned fixture success, a probability threshold, a measured token benefit
after Jev usage, and a latency ceiling; confidence alone never routes work.
Failure or inconclusive net benefit uses the ordinary model path.
The built-in transport pins the exact HTTPS endpoint and rejects every redirect
before a bearer credential can be forwarded to a different origin or downgraded
to plaintext HTTP.

The H16 Jev fixture uses an injected fake transport; no credential, external
state, or TypeSafe call is used. Live qualification and economic comparison
remain open. `ExecutionRouter` documents the proposed H17 sequence—exact code,
then an explicitly configured and qualified bounded choice, otherwise the
ordinary model path—but no two alternatives are integrated and qualified, so
H17 is not a release capability. The reference never presents Jev as a coding
model.
