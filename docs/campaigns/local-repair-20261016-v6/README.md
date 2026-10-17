# Local repair version 6: explicit shell-patch guidance

This separately versioned treatment follows the retained
[v5 failure](../local-repair-20261016-v5/README.md). Its one intentional change
is explicit developer guidance for invoking the existing shell patch helper.
The task, checker, client, host, model, engine, generation controls and budgets
remain fixed. Native paths and identifiers necessarily change. Twelve new
held-out tasks replace the previous held-out inventory before inference.

**Retained outcome: the one development repair failed.** See
[the outcome](OUTCOME.md), [observation](observation.json) and
[native events](development-native.jsonl). No implementation edit occurred;
the candidate remains unqualified. The sections below record preregistration.

The [diagnosis](../../diagnostics/native-patch-20261016/README.md) distinguishes
the observed empty patch commands from an integration defect. Native controls
through Hormuz retain streamed command arguments exactly: an empty call leaves
the implementation broken; a supplied complete patch edits it and passes the
unchanged checker. That supplied patch is a transport control, never model
quality evidence. No production integration defect was demonstrated, so no
production change or speculative regression test is introduced.

The [guidance](developer-instructions.txt) supplies no repair implementation.
It is part of the native developer input and is bound by the request profile.
Codex 0.160.0 supports only the freeform dedicated patch tool, which this
llama.cpp profile does not support. The experiment keeps the existing inline
function shell tool; it does not advertise an invented function patch tool.

The runtime source remains `4e63d49389922df4079fe22c49d57f191756b910`.
All 136 imported package files were reconstructed from that source and checked
against v5's frozen installed-file hashes. The original wheel container was
unavailable; no claim is made to reuse its ZIP bytes. This preserves the runtime
while main's later cloud and benchmark changes stay outside this comparison.
The model/runtime files were reacquired from the existing pinned catalog after
their former temporary paths disappeared; their bytes and executable hashes,
and the client and host pins, match v5.

The preparation inventory contains four development cases and twelve fresh
held-out cases across the four required families. All broken baselines fail
with assertion exit 1 and independent references pass. References stay outside
the native workspace. This validates checker sensitivity, not model quality.
The workspace-write launch limits writable roots; it does not establish read
isolation from the oracle, corpus or retained records.

After zero-inference preflight captures the actual native profile, freeze the
plan, first input, tool/instruction profile, assets, runner and runtime inventory.
Only one `dev-clamp` model episode is authorized: at most eight submissions and
ninety seconds, with zero transport or quality retries. The other fifteen cases
remain unrun. No coding receipt is emitted or installed; ordinary unqualified
coding stays blocked, local-only and affinity stay authoritative, and routing
advice retains no execution authority.

An actual implementation edit, unchanged passing independent checks and normal
completion are required for a passing development observation. A single pass
cannot qualify the candidate. The consumed marker prevents another run under
this treatment; changing any treatment after inference requires a new campaign
and fresh held-out inventory. Outcomes are retained separately from the frozen
inputs.
