# Retained v6 development outcome

The one authorized `dev-clamp` episode **failed**. Codex exited zero and
completed one turn in 22,888 ms, with five local model submissions in one work.
The only shell command shown by its public events read the function declaration
using `cat solution.py | rg 'def clamp'`. It then narrated attempts to edit,
but made no implementation change. The retained patch is empty and the
unchanged independent checker returned assertion exit 1 before and after.

The public native events do not expose every function-call argument or tool
error. They establish no observed edit command and no filesystem change; they
do not establish the exact contents of every remaining model/tool exchange.
Do not infer that there were no other attempted function calls. No second model
episode or quality retry was run to obtain a more favorable result.

The [manifest](frozen-manifest.json) was frozen before inference at SHA-256
`0a8b985534bcd2e399f2b9733d1b0ddd16418ff619242b6928fef44341630ecc`.
It binds 160 artifacts, including all 136 v5-matching Hormuz runtime files.
The [plan](coding-plan.json), [probe permission](probe-plan.json),
[treatment](treatment.json), [candidate](candidate.json), [pins](pins.json),
[actual native profile](request-profile.json) and [first input](first-input.json)
remain unchanged. The bound preparation source is local commit
`19b1008`; the executed Hormuz runtime source remains
`4e63d49389922df4079fe22c49d57f191756b910`, deliberately retaining v5's runtime.
All 160 bound artifacts still matched their frozen hashes after this no-edit run.

The [zero-inference preflight](preflight.json) captured exactly one unforwarded
native request and restored settings/closed owned listeners. The actual run's
[public result](development-result.json) records ordinary unqualified coding
denied with HTTP 503 before probe authorization. All five model submissions
used the same local route/work; the four later attempts retained session
affinity. There were zero transport retries, failovers, failed transport attempts
or unknown transport outcomes. Successful transport does not make the repair pass.

Probe permission was revoked, configuration was restored exactly, and both
owned listeners closed. Coding readiness remained `unknown`, its automatic
task list stayed empty, and no coding receipt was emitted or installed.
The native configuration had no cloud destination and no shadow classifier;
the unchanged workflow checks preserve shadow advice's lack of execution
authority. No claim of native classifier calibration is made here.

This is zero passing observations from one development case, with fifteen
planned cases missing, including all twelve fresh held-out cases. It is not a
statistical success-rate estimate, a general model verdict, or qualification.
Explicit shell-patch guidance alone did not produce a repair in this observed
episode. The original v5 failure is unchanged. Do not rerun or modify the frozen
v6 treatment; a further changed treatment requires a new version, fresh held-out
inventory and its own finite native budget.

The workspace and retained paths are those of this observed host. Only the
project and private temporary directory were writable; no filesystem read
isolation from the oracle or corpus is claimed. Independent checks ran under
a separate two-second no-network/no-write sandbox. The unchanged implementation
is retained as [implementation-after.py](implementation-after.py), with its empty
[patch](implementation.patch). Neither the supplied-patch transport controls nor
the offline reference implementations count toward native model quality.
