# Codex 0.160.0 routing profile

Hormuz supports exact CLI versions 0.147.0 and 0.160.0. The connection owner
detects the actual executable and records that version in previews, status and
the private journal. Keep an installed 0.160.0 executable; a downgrade is not
required. Other versions remain blocked. This terminal-CLI qualification does
not cover a desktop app's separately bundled engine.

Use the shared [setup](SHARED_LOCAL_SETUP.md) with existing verified files.
Setup selects the matching bundled profile; an explicit qualification document
must also match the actual executable. Changing supported versions while
connected requires disconnect/reconnect and a new generation. Setup rechecks
the declared version at the connection transaction before changing settings.

## Qualified launch

The 0.160.0 llama.cpp profile covers one agent using Responses HTTP, inline
function tools and streaming. Its selected launch settings are:

```sh
codex exec -c agents.enabled=false --disable multi_agent_v2 \
  -c 'model_reasoning_summary="none"' -c 'model_reasoning_effort="none"' \
  --sandbox read-only \
  "Review this Python function using the supplied verification script."
```

The same settings can accompany an interactive Codex invocation. Keep the
project, task and permissions selected by the user. The adapter preserves the
coding client's agent loop; it does not execute tools itself. Namespaced and
freeform tools remain unsupported by this runtime profile. In particular,
llama.cpp b11617 skips non-function Responses tool declarations, so Hormuz
blocks those requests instead of silently losing available tools. Reasoning
execution controls remain blocked. The qualified empty object or explicit
`{"effort":"none"}` disables request reasoning. The launch override takes
precedence over saved effort preferences without rewriting them.

`--ignore-user-config` skips both base and named profile files, bypasses the
owned user-default provider and is reported
by diagnosis as a launch conflict. Supplied flags are inspected without being
executed. Unknown versions cannot receive a clean effective-routing diagnosis.
The 0.160.0 project denylist also identifies `responses_api_metadata` as ignored.

## Wire and catalog compatibility

The opt-in `codex_http_0160` capability admits the bounded `root_turn_id`
metadata tag observed from the real client. The older `codex_http` capability
does not admit this additional shape. Each other native field retains the
existing printable-byte and optional-encrypted-output bounds. The new capability
also admits explicit `reasoning.effort="none"`; the old capability still permits
only empty reasoning. Higher effort, summaries and additional controls block.
This marker does not admit provider-owned encrypted input or grant a route
qualification. Existing continuation and conversation-affinity checks apply.

The 0.160.0 provider table explicitly uses Responses HTTP, disables automatic
request/stream replay, and points model discovery at the authenticated local
`/v1/models`. That endpoint retains the OpenAI `object`/`data` representation
and adds a `models` catalog for actor-visible qualified local streaming routes.
Unqualified routes and policy-hidden aliases gain no Codex catalog entry.
Catalog metadata advertises text input and the tested tool/summary limits,
without claiming a tokenizer context window or coding quality.

The catalog embeds the exact upstream 0.160.0 fallback instructions so that
valid metadata does not replace Codex's tool/permission guidance with a new
Hormuz prompt. The bundled JSON records its origin and SHA-256; the upstream
Apache-2.0 license is distributed beside it. Native custom credentials remain
generation-bound local commands; personal authentication caches are unused.

The additive catalog contract is version 1, frozen by
[`codex-model-catalog-0.160-v1.json`](../tests/fixtures/codex-model-catalog-0.160-v1.json).
Earlier OpenAI consumers may ignore the extra `models` member. No stored
ledger or credential is migrated. Existing 0.147.0 provider blocks and journals
remain readable. Before running an older Hormuz binary, disconnect a 0.160.0
lifetime with this binary: older versions do not understand the new provider
block. Preserve the current binary until owned cleanup succeeds.

## Native verification

The [0.160.0 setup row](../hormuz/local-setup-codex-0160-llama-b11617-qwen3-4b-v1.json)
uses the existing macOS arm64 llama.cpp b11617 archive and Qwen3-4B Q4_K_M
digests. Their provenance and historical quality limitations remain in the
[0.147.0 qualification](qualifications/codex-0147-llama-b11617-qwen3-4b.md).
Matching files are reused without model downloads or cloud credentials.

The opt-in [native verifier](../tools/verify_codex_0160.py) creates a private
disposable client home, previews and applies setup, executes one supplied-script
task with the existing 0.160.0 binary, inspects public attempts/status, then
disconnects and verifies restoration plus listener shutdown:

```sh
python tools/verify_codex_0160.py --codex /absolute/current/codex \
  --python /absolute/hormuz-venv/bin/python \
  --runtime-archive /absolute/llama-b11617-bin-macos-arm64.tar.gz \
  --llama-executable /absolute/runtime/llama-server \
  --model-file /absolute/Qwen3-4B-Q4_K_M.gguf
```

`--site` optionally selects an independently installed wheel import root.
The native process has a 120-second deadline and zero transport retries. One
local model is loaded by the shared lifetime owner. The driver prints only the
result/evidence path; generated caller credentials stay outside output.

Observed on macOS arm64: Codex 0.160.0 completed a real `exec_command` running
the supplied Python script, received `ROUTING_OK`, and completed its turn. Two
model requests settled successfully in one public work record; the second
used session affinity. There were zero retries/failovers, valid catalog
metadata, and no retained settings after disconnect. A saved `xhigh` preference
was overridden for this launch and preserved after disconnect. Both owned listeners
stopped. The work record remains active until explicit workflow completion;
successful model attempts alone do not mark a job complete.

The read-only native shell emitted macOS temporary-cache warnings, while the
script exited 0. No sandbox, firewall or kernel settings were weakened. This
single supplied-script case does not establish autonomous repair, general
coding accuracy, another platform or subscription eligibility.

Pinned primary sources:

- [Provider/authentication fields](https://github.com/openai/codex/blob/79b1b666f2e8551f8abbbca34957227f67f3f553/codex-rs/model-provider-info/src/lib.rs).
- [Exec flags](https://github.com/openai/codex/blob/79b1b666f2e8551f8abbbca34957227f67f3f553/codex-rs/exec/src/cli.rs) and [config loader](https://github.com/openai/codex/blob/79b1b666f2e8551f8abbbca34957227f67f3f553/codex-rs/config/src/loader/mod.rs).
- [Catalog schema](https://github.com/openai/codex/blob/79b1b666f2e8551f8abbbca34957227f67f3f553/codex-rs/protocol/src/openai_models.rs) and [native fallback instructions](https://github.com/openai/codex/blob/79b1b666f2e8551f8abbbca34957227f67f3f553/codex-rs/models-manager/prompt.md).
- [llama.cpp Responses tool conversion](https://github.com/ggml-org/llama.cpp/blob/14310190cd0a0c831fe949ac8cbedd8abdc90695/tools/server/server-chat.cpp).
