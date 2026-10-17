# Local coding diagnosis resolution

This resolves the investigation in [Establish the failing local coding workflow
and its qualification boundary](https://github.com/mehrdadxzaker/hormuz/issues/537).
It does not qualify a model or complete the customer-local router release.

## Observed failure and reproducible check

The original three-case campaign remains zero passing repairs with three
false-local recommendations. The later four-case pilot remains zero passing
repairs, including one unevaluable timeout. Their original records are retained
unchanged; see the [version-two diagnosis](../../native-coding-diagnosis-20261015-v2.md).

The [patch-call diagnosis](README.md) minimizes the version-five development
failure to one empty `apply_patch` command. On October 17, 2026, both offline
replays were rerun on the pinned macOS host:

```sh
python docs/diagnostics/native-patch-20261016/replay-v5.py --minimal
python docs/diagnostics/native-patch-20261016/replay-v5.py
```

Each command returned exit 1: the native patch helper returned usage exit 2,
the implementation remained unchanged, and the independent checker returned
assertion exit 1 with its hash unchanged. These are expected failing repair
checks, not successful model episodes. The replays run without network access
or model inference and require the exact native client pinned in v5's records.

The retained routed controls use native Codex and the real shared connection,
probe, router, continuation, status and disconnect path with a deterministic
local runtime stand-in. Both preserve the streamed call arguments exactly. The
[empty call](routed-empty.json) leaves the file broken; the
[populated call](routed-populated.json) edits it and passes unchanged checks.
Both restore settings and close owned listeners. A supplied patch proves this
transport control, not autonomous model capability. No router argument-loss
defect explaining the native failure was demonstrated.

## Native outcome and remaining uncertainty

The separately frozen [version-six treatment](../../campaigns/local-repair-20261016-v6/README.md)
changed explicit developer guidance for the existing shell helper while holding
the client, runtime, model, task, checker and generation limits fixed. Its
[retained outcome](../../campaigns/local-repair-20261016-v6/OUTCOME.md) is one
failed development episode: five local submissions, normal completion in
22,888 ms, no implementation edit, and failing independent checks. Fifteen
planned cases, including all twelve fresh held-out cases, remain unrun.

The native events do not expose every function-call argument or tool error.
One stochastic observation cannot isolate the general effect of the guidance,
prove model incompetence, or establish that the 512-token limit caused the
failure. Ordinary unqualified coding was denied before the explicit probe;
coding readiness stayed unknown and no coding receipt was installed.

On October 17, 2026, all 160 original host artifacts still matched the byte
sizes and hashes in the frozen v6 manifest, whose SHA-256 remains
`0a8b985534bcd2e399f2b9733d1b0ddd16418ff619242b6928fef44341630ecc`.
The v5 campaign and production source remain unchanged relative to this
investigation's baseline. This publication integrates the retained evidence
with Koto main `75794e83fb67007db5152d9b19aab9856e132572`; it does not rerun the
native campaign against that newer runtime.

The diagnosis prerequisite is complete: the failure is reproducible, the
transport control distinguishes a populated call from an empty one, and the
candidate remains experimental and unqualified. A future changed treatment
needs a new campaign version, fresh held-out inventory and an explicit finite
native budget. All required development and held-out outcomes must pass the
[existing qualification criteria](../../LOCAL_CODING_QUALIFICATION.md) before
ordinary automatic coding can be admitted. Acquisition, actual cloud/plugin
qualification and release acceptance remain separate work under the parent
epic.
