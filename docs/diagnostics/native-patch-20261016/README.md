# Native empty-patch diagnosis

Issue: [#537](https://github.com/mehrdadxzaker/hormuz/issues/537).
The original [v5 episode](../../campaigns/local-repair-20261016-v5/README.md)
completed six local submissions but made no edit. Two shell invocations were
exactly `apply_patch`, with no argument or stdin, and returned usage exit 2.
The frozen independent checker remained failing. Original evidence is unchanged.

## Feedback loop and minimization

Run from this checkout on the pinned macOS host:

```sh
/Users/mehrdadz/Downloads/memoWiki/hormuz/.venv/bin/python \
  docs/diagnostics/native-patch-20261016/replay-v5.py --minimal
```

Observed in 0.1 seconds: helper exit 2, unchanged implementation, independent
checker exit 1, unchanged oracle. The command exits 1 as the repair remains
broken. Full replay reproduces the same result. Removing the initial file read
and duplicate failed command retains the exact symptom. It runs the pinned
native helper under a no-network sandbox with no model calls.

## Ranked hypotheses and distinguishing observations

1. **Missing patch content at the client boundary.** A populated shell command
   should edit the file while an empty command should reproduce failure.
   The native routed controls show exactly that, with unchanged checkers.
2. **Router streaming or argument loss.** A populated emitted call would arrive
   empty or changed at the client or in its tool continuation. In both controls,
   fragmented SSE arguments and the continuation match exactly. No such loss
   was demonstrated. This is a bounded control, not proof of every tool shape.
3. **Instruction/tool mismatch contributed to model behavior.** The default
   instructions describe an apply_patch command array, but the available inline
   shell function requires `cmd`. Explicit shell-helper guidance is a new,
   separately frozen model treatment. Its effect remains an outcome question.
4. **The 512-token envelope forced the omission.** The retained episode reports
   503 output tokens across six requests and a normal completed turn; these
   observations do not establish truncation as the cause. The first treatment
   holds the cap fixed instead of widening it speculatively.

The [routed control](routed-control.py) uses native Codex and real shared setup,
router, probe authorization, status and disconnect with a deterministic local
runtime stand-in. Two provider responses form one affine tool conversation.
The empty control makes no edit and fails checks. A supplied complete patch
edits the file and passes. Both restore settings and close owned listeners;
readiness remains unknown. Neither control is autonomous model evidence.

The direct catalog prototypes did not use command authentication and fetched no
catalog. They are inconclusive for metadata activation. Do not enable the
proposed `function` patch-tool variant: the pinned upstream
[model schema](https://github.com/openai/codex/blob/a956835d020762cb2b570053af06f643a11c0ecc/codex-rs/protocol/src/openai_models.rs#L322)
has only `freeform`. The release name `rust-v0.160.0` resolves through annotated
tag `79b1b666f2e8551f8abbbca34957227f67f3f553` to that source commit.
The [catalog manager](https://github.com/openai/codex/blob/a956835d020762cb2b570053af06f643a11c0ecc/codex-rs/models-manager/src/manager.rs#L665)
requires the relevant discovery/authentication path; custom command auth is
eligible. These findings avoid a speculative metadata change.

## Finding and next treatment

The immediate observed failure is an empty patch command. No Hormuz integration
defect requiring a production TDD fix was demonstrated. Preserve the approved
workflow seam; add a failing regression there if a later observation identifies
a real integration defect. Do not convert a supplied synthetic patch into
model qualification or weaken admission to collect an outcome.

[Version 6](../../campaigns/local-repair-20261016-v6/README.md) varies explicit
developer guidance, keeps v5's runtime/model/client/limits, preregisters fresh
held-out tasks, and permits one development repair with independent checks.
A single stochastic observation cannot establish a general causal effect.
