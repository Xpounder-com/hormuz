# Client-side context optimization

Context optimization is an optional Hormuz client feature for repetitive,
structured tool results. It is **Off by default** and scoped to one saved
connection profile on one device. When enabled, the dedicated Hormuz launcher
runs a short-lived loopback helper, which may replace an eligible tool result
with a smaller, lossless structural representation before sending the request
to the configured Hormuz gateway.

This feature is part of the normal Hormuz product and release line. It is
separate from the historical `context-pack` experiment: it does not retrieve
memories, summarize prose, or maintain a content cache.

## Data path and trust boundary

```text
Codex or Claude Code
        |
        | ordinary request, authenticated with a per-launch local credential
        v
Hormuz helper on 127.0.0.1
        |  selects, validates, reconstructs, and counts locally
        |  sends one request representation
        v
configured Hormuz gateway
        |  authenticates the employee, applies policy and budgets,
        |  reconstructs compact blocks in bounded memory for secret checks
        v
configured model provider
```

The source request, transform choice, reconstruction check, and tokenizer
estimates stay on the user's machine. Hormuz does not upload an original and a
compact copy, and this feature adds no prompt log, response log, content hash,
central optimization telemetry, or database migration. The only persistent
client setting is a private boolean preference.

The launcher passes the user's existing process environment to the official
Codex or Claude Code child process so client-owned tools keep their normal local
configuration. Before launch it removes that client's direct provider
credentials and endpoint selectors, then supplies only a fresh loopback relay
credential for model traffic. The relay does not attach or serialize the
inherited environment in requests to the Hormuz gateway. This transient read is
declared explicitly in Hormuz's content-free secret custody inventory.

Compaction is an encoding, not encryption. The gateway still receives readable
model traffic: all unselected text remains plaintext, and compact blocks are
reversible. The gateway may reconstruct those blocks in memory so an encoding
cannot hide a secret from existing deny or redaction controls. The provider
receives the final request after normal Hormuz governance. Existing usage and
audit stores remain metadata-only.

When the setting is Off, the helper does not load tokenizers, inspect JSON, or
select content. It forwards request-body bytes unchanged, replacing only the
local transport credential with the employee's Hormuz session credential. The
gateway therefore sees the same plaintext it saw before this feature.

## Toggle and status

Use **Client → Context optimization** in Hormuz Mac. The setting is saved only
after the private file write succeeds. A missing or invalid setting behaves as
Off. Changes apply to the next request; an in-flight request keeps the setting
it read at its start. Starting a new chat after changing the toggle gives the
most stable provider prefix-cache behavior.

The same setting is available from the CLI for an existing saved Mac profile:

```sh
hormuz context settings --profile <profile-uuid> --enabled on
hormuz context status --profile <profile-uuid>
hormuz context settings --profile <profile-uuid> --enabled off
```

The local statuses are `off`, `ready`, `unsupported_client`,
`unsupported_history`, `resources_unavailable`, `gateway_incompatible`, and
`settings_invalid`. On means Hormuz may try an eligible transform; it does not
promise that every request changes or saves tokens.

Connectors created before this feature point directly to the gateway. Review
and save the connector once in the updated Mac app. Updated launchers always use
the local helper for both On and Off requests, so future toggle changes need no
gateway restart, reconnection, or launcher rewrite.

## Supported structures

The `structural-v1` representation supports four exact, bounded transforms:

| Transform | Eligible structure | Preserved information |
| --- | --- | --- |
| JSON table | Canonical arrays of objects with identical ordered keys | Every row, key, value, order, and JSON type |
| Line runs | Consecutive identical lines | Every line and occurrence count |
| Search lines | Results from one path in `path:line:text` form | Path, line spelling, text, order, final newline, and verified wrapper text before or after the result run |
| Path list | Paths with one shared directory prefix | Every suffix, duplicate, order, final newline, and verified wrapper text before or after the result run |

Automatic selection is deliberately narrow and versioned in
`hormuz/context-tool-mappings-v1.json`. The first client mapping covers simple,
uncomposed `rg --files` and `rg -n` commands from Codex `exec_command` and
Claude Code `Bash`, plus Claude Code `Glob` path results. Shell composition,
unknown tools, ambiguous call IDs, unsupported output containers, opaque
Responses history, code, diffs, schemas, images, system/user text, and anything
that cannot reconstruct exactly pass through unchanged.

Codex `exec_command` returns a framed status block around command output. For
the pinned client, Hormuz compacts only one eligible contiguous result run and
stores the exact surrounding bytes as `before` and `after`; reconstruction must
equal the complete original tool string. The unframed form remains valid for
clients whose mapped result is only the line data.

Internal limits protect memory and correctness; they are not organization
policy or user-tunable aggression controls. An eligible request is at most 1
MiB, a block is at most 64 KiB, and tables/runs are capped at 4,096 rows and 64
columns. A candidate must save at least 32 tokens and 5 percent under both
`cl100k_base` and `o200k_base`, and cannot grow in bytes. Ordinary requests above
the optimization limit stream through the helper without unbounded buffering.

## Installation and offline inspection

The gateway does not need tokenizer dependencies. A source-installed client can
install the matching optional extra and verified vocabulary files explicitly:

```sh
python -m pip install '.[context]'
hormuz context resources install
```

The command downloads two pinned public tiktoken vocabularies and verifies their
SHA-256 digests. Request handling never downloads resources. The signed Mac
artifact must contain the version-matched standalone helper and both verified
vocabularies; customers do not install Python separately.

To inspect an explicit request without gateway configuration or credentials:

```sh
hormuz context compact \
  --protocol responses \
  --input request.json \
  --selection selection.json \
  --output compacted-request.json \
  --metadata compaction-metadata.json
```

The selection file has the closed shape below. Missing `enabled` means false.

```json
{"enabled":true,"version":"structural-v1","selections":[{"result_id":"call-1","format":"json_table"}]}
```

The output is content-bearing. Metadata contains only transform status and
numeric byte/token measurements. The command refuses output overwrites and path
collisions, writes new files privately, and blocks network access while parsing,
transforming, and counting.

## Evidence and remaining release gates

The deterministic 12-case fixture suite covers duplicate records, signed
amounts, rare errors, JSON type distinctions, quoted Unicode, nested values,
line counts, exact search locations, duplicated paths, prompt injection inside
tool data, and literal format-marker text. The current
[native evidence](evidence/context-compaction/native-v1-results.json) reports
exact reconstruction and a guarded reduction under both encodings for all 12
selected synthetic cases.

That result is not model-quality, billed-cost, external usability, deployment,
or release-candidate evidence. `tools/evaluate_context_compaction.py` creates
five-or-more paired repetitions and scores answers without an LLM judge. A live
run requires an explicit authorization flag, exact loopback-helper origin,
request cap, total spend ceiling, and a provider/account-derived maximum cost per
request whose full request envelope fits under that ceiling. Redirects are
disabled. No paid-provider evaluation runs in normal tests.

After an evaluation budget and account have been authorized, the complete live
command must declare both the total ceiling and a provider/account-derived
worst-case cost for every request:

```sh
python tools/evaluate_context_compaction.py run \
  --manifest evaluation-manifest.json \
  --local-helper-endpoint http://127.0.0.1:<relay-port> \
  --credential-env HORMUZ_EVAL_LOCAL_TOKEN \
  --request-cap 120 \
  --spend-ceiling-usd 12.00 \
  --maximum-cost-per-request-usd 0.10 \
  --authorized-live-run \
  --output evaluation-outcomes.json
```
