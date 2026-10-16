# Local coding campaign version 3: prepared, not executed

This prepares [#537](https://github.com/Xpounder-com/hormuz/issues/537) for the
[selected candidate](../../CANDIDATE_SETUP.md). The exact inputs are in
[manifest.json](manifest.json), [candidate.json](candidate.json),
[setup-profile.json](setup-profile.json), and the
[corpus](../../../tests/fixtures/local-coding-campaign-v3.json).

The corpus and asset records are frozen preparation inputs. A complete native
plan is **not yet preregistered**: the exact observed instruction/tool/generation
profile must be captured and bound before inference. No native episodes or
passing coding receipts have been produced. The default setup must continue
to deny this unqualified coding workload.

## Inventory and independent checks

| Family | Development | Held-out |
| --- | --- | --- |
| Arithmetic | Inclusive clamp | Ceiling multiple, interval overlap, weighted total |
| Text | Leading prefix removal | ASCII-space collapse, suffix preservation, first split |
| Collections | List rotation | Adjacent pairs, merged counts, final matching index |
| Validation/state | Positive counter | Strict percentage, LIFO stack, atomic withdrawal |

All tasks are newly authored, with distinct task hashes. Each task digest binds
the goal and broken file bytes without an identifier salt. Each checker digest
binds the full standalone checker source. Expected results are worked literals
and specified errors; they are not computed from the broken implementation.
The four development cases are available for diagnosis. Held-out cases are
used once only after the whole native plan is frozen; they must not tune the
model, instructions, router or checkers.

Run offline preparation from a source checkout, using a new private output
directory:

```sh
python tools/prepare_local_coding_campaign.py --output /absolute/new-private-preparation
```

This verifies input/checker hashes, the exact family/partition inventory,
historical evidence, source syntax and assertion-failing baselines. It runs
only trusted local checker subprocesses, sequentially, with two-second
deadlines. It does not execute Codex or llama.cpp, access providers, download
assets or create an admission receipt. It refuses to overwrite an existing
preparation directory. Its successful exit means preparation succeeded.

For independent checker validation, a reviewer can provide separately authored
reference files named `<case_id>.py` in an external directory:

```sh
python tools/prepare_local_coding_campaign.py \
  --output /absolute/another-new-private-preparation \
  --reference-directory /absolute/private-reference-directory
```

Keep these implementations outside both the corpus and model workspaces. This
option checks that the oracle accepts known-good behavior; it is not a repair
episode, model result or supplied solution for the coding client. The retained
preparation report records all sixteen baseline failures and sixteen reference
passes separately from zero native episodes.

## Setup preview

Use only the verified asset paths corresponding to candidate.json. This is a
preview of the existing shared setup, with a separate private client home:

```sh
hormuz --config /absolute/private/gateway.json client setup codex \
  --client-home /absolute/private/codex-home \
  --executable /absolute/verified/codex-native \
  --runtime-archive /absolute/verified/llama-b11617-bin-macos-arm64.tar.gz \
  --llama-executable /absolute/verified/llama-server \
  --model-file /absolute/verified/Qwen3-4B-Q4_K_M.gguf \
  --qualification /absolute/source/docs/campaigns/local-repair-20261016-v3/setup-profile.json \
  --dry-run
```

The native binary hash matters independently of a launcher script's hash.
Reject asset or version drift before activating anything. Preview requires
temporary loopback port allocation; an execution sandbox denying `bind` can
report `connected_client_setup_unavailable` without establishing a product
defect. The isolated successful preparation preview preserved the original
client settings and created no gateway configuration.

## Native execution protocol to review before starting

1. Retain the original v1/v2 receipts and manifests. Use the exact pinned wheel,
   native client, runtime/model and observed hardware for this version. Set a
   private client home, disable paid/API/provider fallback, and expose only
   local destinations. Keep the existing local-only and affinity checks.
2. Capture a synthetic native request without model inference, using one fixed
   isolated campaign working directory. Review the actual catalog, inline
   tools, instructions and generation controls. Freeze their exact
   `request_profile_sha256`, the subject, all sixteen case/checker digests and
   `plan_sha256` before any native episode. Synthetic compatibility fixtures
   cannot substitute for this observed profile. Reusing one workspace path
   avoids silently varying environment instructions between cases; reset all
   task files and use a fresh conversation for each case.
3. Establish an explicitly reviewed local outcome-collection path at connect →
   submit through the real router → inspect public result/status → disconnect.
   Today the null-evidence profile intentionally denies fresh coding. Do not
   obtain a first receipt by weakening local-only, switching to `configured`,
   inserting synthetic passing outcomes, or disguising the task. If collection
   needs a new explicit interface, its implementation and approved-seam TDD
   precede native execution. This preparation does not add that interface.
4. Diagnose a minimal development repair using ranked, falsifiable hypotheses.
   Distinguish failure to write, a wrong edit, failed feedback, termination and
   integration denial. Use TDD for a demonstrated product defect; a policy
   denial or an inadequate model is not by itself such a defect. Any changed
   subject or controls invalidate this version before held-out execution.
5. Run one episode per case, sequentially, with a 90-second whole-client
   deadline, 512 tokens per generation, 65,536 bytes per request, at most
   60 seconds for owned startup and 20 seconds for disconnect. Stop after one
   hour overall. Quality and transport retries are zero. Count all declared
   cases, including missing, canceled, unknown and timeout outcomes.
6. Copy only the broken solution into the isolated task workspace. Keep frozen
   checkers outside its writable boundary. Never give the client a reference
   implementation or implementation-writing command. Observe an actual edit,
   retain the patch digest, recheck the unchanged oracle hash, and require
   independent checks and normal client completion with exit zero within the
   declared deadline. Baseline assertion failure must have exit 1.
7. Inspect public work/attempt status, destinations and any continuations. Keep
   every continuation pinned; reject incompatible opaque state and avoid
   ambiguous replay. Optional shadow advice remains `routing_effect: none`.
   This campaign does not establish routing-classifier calibration or grant
   learned execution authority. Stop only this campaign's owned resources in
   finally-equivalent cleanup and verify conditional settings restoration.
8. Retain every outcome before calculating readiness. Only an entirely passing
   campaign can produce an operator-attested receipt for review under
   [local-repair.v1](../../LOCAL_CODING_QUALIFICATION.md). Incomplete or failed
   campaigns remain unqualified. A passing campaign would still cover only its
   exact envelope, not broad coding accuracy, another host or cloud eligibility.

## Current result

Preparation passed. Native request-profile binding and a reviewed qualification
collection path remain prerequisites. Native outcomes: none. Coding receipt:
none. Automatic admission effect: none. The six historical evidence files in
manifest.json are unchanged. See [preparation-evidence.json](preparation-evidence.json)
for content-free validation facts and their limits.
