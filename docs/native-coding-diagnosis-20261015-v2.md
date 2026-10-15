# Native coding diagnosis and shadow calibration, version 2

The pinned local combination produced **one real repair edit that passed the
independent frozen checks**, but that diagnostic episode hit its 90-second
client deadline. The four-task held-out pilot produced **zero passing repairs**.
Its three normally completed episodes all received false-local advice; the
fourth timed out and remains unevaluable. Keep advice shadow-only.

This is negative qualification evidence, not a production routing change. The
[original version-one receipt](shadow-routing-evaluation-20261015.json) remains
byte-for-byte unchanged: zero of three passing repairs, three false-local
recommendations, and unchanged implementation files. The [version-two
receipt](native-coding-evaluation-20261015-v2.json) retains every declared
coding probe, including invalid catalog treatments and setup denials. It does
not replace the original result or count the successful diagnostic edit as a
held-out success.

## Boundary and pins

Every native coding episode used the approved public workflow: shared CLI setup,
a real Codex request through Hormuz, public work/attempt inspection, and
owned-generation disconnect. Inputs were synthetic. The runtime came from the
verified CI wheel and all 113 runtime Python/JSON files matched source head
`b7aa34791bd3112ae03bf45a38b1aabfba4a193a`. PR535 subsequently merged unchanged
at `811cfd22f87cced671142aca709bb13ee33bfee0`.

The client was Codex 0.147.0, the engine llama.cpp b11617, and the model
Qwen3-4B-Q4_K_M on macOS arm64. Exact asset hashes are in the receipt. Existing
verified files were reused; no model/runtime download, cloud inference or
subscription comparison occurred. Each native episode had a 90-second deadline,
a 512-token per-generation output cap and a 65,536-byte request cap. Sampling
used the existing temperature 0.7 with no fixed model seed. The coding oracle
is deterministic for a given implementation; model behavior is stochastic.

## Diagnosis

The minimized first case is `ceil_div(a, b)`: floor division is wrong for mixed
signs and nonintegral quotients. The frozen read-only oracle checks six signed
examples plus division by zero. Its SHA256 is
`2d047de10eaa09b8bcca4e535f5d2b79bdec9e54a5822a39bbde8097765d3b15`.
The broken implementation failed with `AssertionError: (5, 2)`. Model episodes
could inspect the repository and use the host's tools; the evaluator supplied
no solution code or implementation-writing command.

The feedback command runs the frozen oracle in the pinned client's read-only
sandbox against a disposable repair directory. Each check uses fresh bytecode;
the oracle hash is checked again after the episode. The complete bounded native
drivers, before-run manifests, raw outputs and original failed implementation
are retained privately. Their hashes are indexed by the receipt. This document
and the receipt are evidence, not a packaged portable evaluation runner.

Ranked hypotheses were stated before the corresponding probes:

| Hypothesis | Prediction | Observation |
| --- | --- | --- |
| File discovery prevents editing | Naming `solution.py` should produce an edit | The model found/read the file but still returned proposed code without a write |
| The output budget prevents completion | Increasing output would permit completion | The 2,048-token setup was denied before inference by the existing qualification boundary; hypothesis remains untested outside that boundary |
| Generic catalog metadata causes the failure | A loaded compatible catalog should fix the repair | Early catalog overrides were ignored; the corrected text-only catalog loaded and removed parse errors but still produced no edit |
| The model stops at advice instead of using edit tools | An explicit execute-before-reply instruction should produce a write | That probe still finished without a write |

The original trace contained inspection/check commands and no implementation
write. A later generic-metadata diagnostic episode did issue writes: it first
failed the check, revised the implementation, and then passed the unchanged
check. The independent post-episode check also passed. The implementation hash
and strict completion limits are retained in the [repair observation](native-repair-observation-20261015-v2.json).
It reached the native deadline, so this establishes an actual repair edit and
available write permissions, not a normally completed coding session or reliable
model competence. It is not evidence that a changed catalog caused the edit.

### Catalog correction

In this pinned invocation, placing `-c model_catalog_json=...` before `exec`
failed to apply it when `exec` had its own `-c` flags. A deliberately missing
catalog path failed at configuration loading after `exec`, whereas the earlier
placement progressed to the disabled provider. The two earlier catalog probes
therefore did not establish a treatment. Their failures and the one passing
repair observation remain in the record; they are accidental effective
replications, not evidence of an instruction improvement.

An offline model-list preflight established that the catalog format itself was
loadable. A bounded external loopback stand-in then inspected only synthetic
request metadata. The inherited entry emitted `reasoning: {effort: none}` and
`text: {verbosity: medium}`. The real router denied that unqualified shape before
inference. Removing reasoning support produced `{}`; separately removing
verbosity support removed `text`. The final preflight confirmed the 466-character
local coding instructions and ordinary function tools. The subsequent real
router episode had no catalog decode errors but finished with no edit and a
failed oracle. This is not a fix for the original coding failure.

The catalog shape follows the [official pinned Codex
source](https://github.com/openai/codex/blob/rust-v0.147.0/codex-rs/models-manager/models.json).
[OpenAI's catalog guidance](https://learn.chatgpt.com/docs/enterprise/roll-out-a-gateway#use-a-model-catalog-for-custom-names)
explains that catalog entries provide model instructions and capabilities. Such
metadata is an integration input; it does not establish a model's ability to
complete the task.

No production defect that explains the original missing edit was established.
The remaining uncertainty concerns task completion under this model, instruction,
output and deadline envelope. Do not broaden the output qualification or claim
that catalog parsing alone caused the failed repairs.

## TDD and CI repair

Two independently diagnosed CI failures were fixture races, fixed in PR535
using the same approved workflow seam. One cancellation fixture reset the client
before the classifier necessarily started; another inspected public attempt
history before asynchronous settlement finished. The corrected external client
waits for provider entry before reset. The observer makes at most four public
history reads, with finite backoff, before retaining the exact expected terminal
state assertion. Provider/stream release remains in `finally` blocks.

The original CI failures are the red evidence. Focused regressions passed after
the test-only changes. The [exact-head CI run](https://github.com/Xpounder-com/hormuz/actions/runs/38814783876)
then passed all six checks: four source Python versions and the installed wheel
each completed 147 tests without skips. Standards and Spec each found zero
remaining issues at the final head. No production admission, local-only,
affinity, cancellation or credential behavior was changed to obtain green CI.

## Held-out pilot

The [four-task fixture](../tests/fixtures/shadow-coding-cases-v2.json) was frozen
before any of its tasks reached the model. SHA256:
`26dbfe67992cb9c110681199f0ead4c54d1f5f4c45f0f55b60eea2b8505700d5`.
Independent preflight checks rejected every broken baseline and accepted a
known-correct reference; references were removed before the coding episodes.
Tasks used newline normalization, equality-preserving de-duplication, strict
port parsing and TTL expiry. No task was retried, dropped or used to retune the
classifier. The running batch retained its frozen invocation after the catalog
scope mistake was discovered; its effective metadata was the generic fallback,
with two catalog decode errors per case. It is not a held-out test of the final
corrected catalog.

| Case | Native episode | Implementation changed | Independent checks | Advice |
| --- | --- | --- | --- | --- |
| normalize-newlines | finished, exit 0 | no | fail | local |
| stable-unique | finished, exit 0 | no | fail | local |
| strict-port | 90-second deadline | yes | fail | local |
| ttl-cache | finished, exit 0 | yes | fail | local |

A passing implementation repair requires an actual changed implementation and
the unchanged independent checks to pass. Full native acceptance additionally
requires exit 0 and normal termination. The receipt's `actual_edit_passed` is
this stricter full-native predicate; `implementation_repair_passed` is the
independent repair predicate. The held-out pilot passed neither predicate.

False-local rate is **3/3 among evaluable normally completed local-advised
episodes**. The timeout is one unevaluable coding outcome out of four, not a
fourth scored false-local result. Advice coverage was four local recommendations,
zero unknown/abstentions, and zero missing advice: all denominators include the
timeout. This exposes poor abstention behavior as well as false-local errors.
Four synthetic tasks are too few for a general accuracy or confidence claim.

Fresh classifier overhead was **429.784 ms mean**, **428.772 ms median**, with a
**390.103–471.491 ms** range and nearest-rank p95 of **471.491 ms**. It consumed
1,465 input and eight output tokens across the four classifications. These are
direct helper measurements, not a causal estimate of whole-session slowdown;
there was no classifier-off control. All 25 observed coding requests used the
configured local model; all 21 continuations retained affinity. All advice had
`routing_effect: none`.

Disconnect stopped each owned engine and removed the installed provider block.
A final task-process inspection found no remaining native clients, engines or
probe workers. The failed 2,048-token setup had no coding inference; its cleanup
reporter's missing-config error is retained and does not establish an engine
leak. No unrelated process was stopped.

## Decision boundary

Retain opt-in shadow advice and hard local-only admission. These outcomes do
not support execution authority. A successful diagnostic edit does not cancel
the frozen failures or qualify general coding capability. Before another native
qualification, the owner must define acceptable completion, failure/unknown and
latency criteria, then freeze a separately versioned client/catalog/output
profile. Select development tasks independently of a future untouched holdout.
Cloud entitlement, distribution and additional-platform gates remain separate.
