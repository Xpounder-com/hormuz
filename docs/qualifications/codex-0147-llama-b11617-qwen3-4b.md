# Codex 0.147.0 → llama.cpp b11617 qualification

This is a bounded, synthetic terminal-client qualification on **macOS 26.2,
arm64, 32 GiB RAM**. It exercises the approved workflow: connect, submit through
Hormuz, inspect public results/status, disconnect. It does not qualify the
Codex desktop engine, plugin/skill activation, automatic model installation,
subscription authentication, other models, or general coding quality.

**Result: qualified for the bounded supplied-script, function-tool continuation,
incremental HTTP stream and cancellation cases below. Autonomous coding and
strict answer formatting failed.**
`qualification_id` is an operator declaration for this exact envelope, not a
quality certificate. Do not enable this profile elsewhere based on a running
listener or HTTP success. See [admission](../LOCAL_ROUTE_ADMISSION.md),
[connection lifetime](../CONNECTED_CLIENT_LIFETIME.md) and
[scope](../ROUTER_SCOPE.md).

## Immutable inputs

Verify archive size and SHA-256 before extraction, and the extracted runtime
against that archive. Verify the model file independently. No model conversion
or quantization was performed during this qualification.

| Input | Pin | Bytes | SHA-256 |
| --- | --- | ---: | --- |
| [Codex macOS arm64 archive](https://github.com/openai/codex/releases/download/rust-v0.147.0/codex-aarch64-apple-darwin.tar.gz) | CLI `0.147.0`; source `be6e8eac029b183056b7e4402879f15d2c85f61b` | 87,984,231 | `75984b81f92a71b0c0f4b3b5cad80e5c57177e4d8c8b4b1e13db703b20dc4358` |
| [llama.cpp macOS arm64 archive](https://github.com/ggml-org/llama.cpp/releases/download/b11617/llama-b11617-bin-macos-arm64.tar.gz) | `b11617`, source `14310190cd0a0c831fe949ac8cbedd8abdc90695` | 12,157,609 | `051bb0b55b3e5e72081aa819131ba30c74d910acc915b28bab298757472e39d9` |
| [Official Qwen3-4B-Q4_K_M.gguf](https://huggingface.co/Qwen/Qwen3-4B-GGUF/resolve/bc640142c66e1fdd12af0bd68f40445458f3869b/Qwen3-4B-Q4_K_M.gguf) | HF revision `bc640142c66e1fdd12af0bd68f40445458f3869b` | 2,497,280,256 | `7485fe6f11af29433bc51cab58009521f205840f5b4ae3a32fa7f92e8534fdf5` |

The [model repository](https://huggingface.co/Qwen/Qwen3-4B-GGUF/blob/bc640142c66e1fdd12af0bd68f40445458f3869b/README.md)
declares Apache-2.0. [Codex](https://github.com/openai/codex/blob/be6e8eac029b183056b7e4402879f15d2c85f61b/LICENSE)
is Apache-2.0 and [llama.cpp](https://github.com/ggml-org/llama.cpp/blob/14310190cd0a0c831fe949ac8cbedd8abdc90695/LICENSE)
is MIT; preserve their licenses when redistributing artifacts. Python was
CPython 3.12.12.

## Exact envelope

The route declares `responses`, `text`, `streaming`, `tools`, and the opt-in
`codex_http` capability. The last capability admits only the bounded observed
0.147.0 HTTP metadata, empty `reasoning`, and optional encrypted-output include
marker. It does not admit encrypted input state or reasoning controls.

Tools are inline functions. Namespace/freeform/hosted tools, web search, apps,
multi-agent tools and remote model authentication are outside this profile.
The actual tool round trip exercised `exec_command`; declaration of `tools`
does not establish the quality of every available tool. The pinned llama.cpp
[Responses adapter](https://github.com/ggml-org/llama.cpp/blob/14310190cd0a0c831fe949ac8cbedd8abdc90695/tools/server/server-chat.cpp)
converts full-history function calls/results and does not implement opaque
provider conversation references.

Limits are one active local request, **65,536 serialized request bytes**, **512
output tokens**, **32,768 model context tokens**, and a **90-second router
upstream deadline**. Byte admission does not estimate tokenizer context use.
The policy supplies the output cap when Codex omits it. Inference retries in
the native client are explicitly zero. Each controlled native episode has a
180-second outer deadline; cancellation has a 60-second deadline.

Zero cloud-trap calls establish no dispatch to that configured destination.
They are not an OS-wide egress audit. The verified engine is launched against
the local GGUF with `--offline`; arbitrary loopback forwarding services and
the coding client's independent tools/connections are not covered by router
locality enforcement.

## Manual reproduction

Use a disposable checkout of the recorded source and the verified executables
above. Replace the absolute paths and choose two unused loopback ports. Do not
reuse the active user's home, Codex configuration, authentication files, project
or gateway token. Keep the fixture and evidence directory private (0700).

```sh
QUAL_ROOT=/absolute/path/to/private-synthetic-experiment
QUAL_PYTHON=/absolute/path/to/qualified-venv/bin/python
QUAL_CODEX=/absolute/path/to/codex-aarch64-apple-darwin
QUAL_LLAMA=/absolute/path/to/llama-server
QUAL_MODEL=/absolute/path/to/Qwen3-4B-Q4_K_M.gguf
mkdir -m 700 "$QUAL_ROOT"
mkdir -m 700 "$QUAL_ROOT/home" "$QUAL_ROOT/home/.codex" "$QUAL_ROOT/project"
printf 'personality = "none"\n' > "$QUAL_ROOT/home/.codex/config.toml"

# Run every process with an allowlisted environment; this token is loopback-only.
isolated() {
  env -i HOME="$QUAL_ROOT/home" CODEX_HOME="$QUAL_ROOT/home/.codex" \
    PATH=/usr/bin:/bin:/usr/sbin:/sbin TMPDIR="$QUAL_ROOT" LANG=en_US.UTF-8 \
    HORMUZ_QUALIFICATION_TOKEN=synthetic-loopback-qualification-token \
    NO_COLOR=1 OTEL_SDK_DISABLED=true "$@"
}
```

Write `gateway.json` below, replacing `ABS_QUAL_ROOT` and using unused ports
instead of the example router `18437` and engine `18438`. The example has only
a local destination. The controlled
experiments additionally configured a bounded loopback cloud trap to count
forbidden dispatches; this example does not by itself reproduce that assertion.
No OpenAI API key, login or paid endpoint is required.

```json
{
  "listen": {"host": "127.0.0.1", "port": 18437},
  "database": "ABS_QUAL_ROOT/usage.sqlite3",
  "upstream_timeout_seconds": 90,
  "upstreams": {
    "local-llama": {
      "protocol": "openai", "base_url": "http://127.0.0.1:18438/v1",
      "locality": "local", "authentication": "none"
    }
  },
  "identities": [{
    "token_env": "HORMUZ_QUALIFICATION_TOKEN",
    "actor_id": "synthetic-local-developer", "actor_name": "Synthetic Local Developer",
    "team_id": "local", "team_name": "Local", "allowed_clients": ["codex"]
  }],
  "model_routes": {
    "Qwen3-4B-Q4_K_M": {
      "protocol": "openai", "destination": "local-llama",
      "upstream_model": "Qwen3-4B-Q4_K_M",
      "model_revision": "sha256:7485fe6f11af29433bc51cab58009521f205840f5b4ae3a32fa7f92e8534fdf5",
      "capabilities": ["responses", "text", "streaming", "tools", "codex_http"]
    }
  },
  "policies": {"organization": {
    "allowed_clients": ["codex"], "allowed_models": ["Qwen3-4B-Q4_K_M"],
    "max_output_tokens": 512
  }},
  "route_admission": {
    "mode": "local_only",
    "local_routes": {"Qwen3-4B-Q4_K_M": {
      "qualification_id": "codex0147-llamab11617-qwen3-4b-macos-arm64-experiment-v1",
      "model_revision": "sha256:7485fe6f11af29433bc51cab58009521f205840f5b4ae3a32fa7f92e8534fdf5",
      "max_request_bytes": 65536, "max_output_tokens": 512,
      "task_types": ["code_review", "code_change"]
    }}
  },
  "ai_work": {
    "enabled": true, "tool_capable_aliases": ["Qwen3-4B-Q4_K_M"],
    "database": "ABS_QUAL_ROOT/work.sqlite3"
  }
}
```

Run connect/status/disconnect with a 20-second controller timeout, public requests
with an 8-second timeout, no automatic retries, and bounded response reads.

1. With the engine stopped, preview and connect:

   ```sh
   isolated "$QUAL_PYTHON" -m hormuz --config "$QUAL_ROOT/gateway.json" \
     client connect codex --client-home "$QUAL_ROOT/home/.codex" \
     --executable "$QUAL_CODEX" --dry-run
   isolated "$QUAL_PYTHON" -m hormuz --config "$QUAL_ROOT/gateway.json" \
     client connect codex --client-home "$QUAL_ROOT/home/.codex" \
     --executable "$QUAL_CODEX"
   ```

   Retain the public receipt's generation. Read the generated `model_provider`
   from disposable `config.toml` using `tomllib`; call it `QUAL_PROVIDER` below.
   Do not edit its owned provider table.

2. Create a work item with an authenticated `POST /v1/work/jobs` to the router:

   ```json
   {"repository":"synthetic/local-qualification","title":"Pinned local check",
    "task_type":"review","context_revision":"fixture-v1",
    "completion_condition":"workflow.completed.v1"}
   ```

   Use `Authorization: Bearer synthetic-loopback-qualification-token` and
   `Content-Type: application/json`. Retain the returned `work_id` as
   `QUAL_WORK_ID`. **Bind one work ID per native conversation**, through the
   process-only header override below. This is presently a manual qualification
   condition; automatic conversation binding remains integration work.

3. Submit a recognized `Review …` or `Fix …` prompt with this native command.
   The unavailable-engine case must fail explicitly with HTTP 503
   `hormuz_route_local_route_unavailable`, zero admitted provider attempts and
   no cloud dispatch. Native output may present the HTTP error without its
   internal Hormuz code; inspect public router status as well.

   ```sh
   isolated "$QUAL_CODEX" exec --strict-config --skip-git-repo-check \
     --ephemeral --ignore-rules --sandbox read-only --color never --json \
     -C "$QUAL_ROOT/project" -o "$QUAL_ROOT/last-message.txt" \
     -c 'web_search="disabled"' -c 'model_context_window=32768' \
     -c "model_providers.$QUAL_PROVIDER.request_max_retries=0" \
     -c "model_providers.$QUAL_PROVIDER.stream_max_retries=0" \
     -c "model_providers.$QUAL_PROVIDER.http_headers={\"X-Hormuz-Work-Id\"=\"$QUAL_WORK_ID\"}" \
     -c 'features.apps=false' -c 'features.shell_tool=true' \
     -c 'features.multi_agent=false' -c 'agents.enabled=false' \
     -c 'model_reasoning_summary="none"' "$QUAL_PROMPT"
   ```

4. Start exactly one owned engine process using its verified local model:

   ```sh
   isolated "$QUAL_LLAMA" -m "$QUAL_MODEL" --offline --host 127.0.0.1 \
     --port 18438 --alias Qwen3-4B-Q4_K_M --parallel 1 \
     --threads 4 --threads-batch 4 --threads-http 2 --ctx-size 32768 \
     --n-predict 512 --jinja --chat-template-kwargs '{"enable_thinking":false}' \
     --no-webui --no-agent --no-slots --temp 0.7 --top-p 0.8 --top-k 20 \
     --min-p 0 --presence-penalty 1.5
   ```

   Bound startup to 60 seconds. Probe `/health` with a two-second timeout,
   at most 30 observations spaced two seconds apart, reading at most 4 KiB;
   require HTTP 200 and `{"status":"ok"}`. This proves readiness, not identity.
   Create a fresh work item and run a predefined coding case with the same
   native flags; use `--sandbox workspace-write` only for the disposable repair
   project. Keep model-generated commands inside Codex's sandbox.

5. Inspect authenticated `GET /v1/work/jobs/WORK_ID`, its `/attempts`, and
   `GET /v1/router/state`; also run `client status codex` with the explicit
   disposable `--client-home`. Require the intended local destination, one
   fingerprint across tool continuations, expected attempt outcomes and an
   eventual zero active local lease. HTTP success is not job completion.

6. In a `finally` path, interrupt and reap only the owned native process,
   disconnect using the retained generation, then terminate/reap only the owned
   engine and close all observers/HTTP clients:

   ```sh
   isolated "$QUAL_PYTHON" -m hormuz --config "$QUAL_ROOT/gateway.json" \
     client disconnect codex --client-home "$QUAL_ROOT/home/.codex" \
     --generation "$QUAL_GENERATION"
   ```

   Use process handles, terminate, wait with a finite timeout, then kill and reap
   only if the owned process failed to stop. Do not search by port or kill other
   processes. Require `disconnected`/`stopped`/`not_owned` and no retained owned
   settings. Preserve legitimate project trust entries Codex appended. Restore
   Hormuz-owned values conditionally; do not replay a whole-file backup.

## Evidence and limitations

Acceptance conditions must be recorded before each native episode. Keep raw
transcripts, temporary credentials/configuration and private homes outside the
repository. Publish bounded result metadata, immutable source/artifact pins and
content hashes instead. These observations are separate cases, not retries
until a task passes.

| Case | Source | Observation |
| --- | --- | --- |
| Engine unavailable | `1235c581a37f726196b6ff27b01afbf9c68cbf28` | Explicit unavailable-local denial; no admitted provider attempt; cloud trap count zero. |
| File read and strict final format | `1235c581a37f726196b6ff27b01afbf9c68cbf28` | Actual `exec_command` read a synthetic file; two successful local attempts retained one fingerprint; cloud trap zero; cleanup passed. **Failed** the predefined literal-only final answer because Qwen added prose/code. |
| Arithmetic repair with inline assertion instruction | `1235c581a37f726196b6ff27b01afbf9c68cbf28` | **Failed**: model used GNU-style `sed` on macOS, made six local attempts, and did not run the prescribed Python assertions. No claim of a repaired task. |
| Native project configuration restoration | `a553e228509ae50b0e543a9193f6c69ac5ae01fe` | A public-seam RED → GREEN regression fixes removal of the owned provider when native Codex appends a project table. This is synthetic regression evidence, not a new real-model pass. |
| Supplied Python script | `a553e228509ae50b0e543a9193f6c69ac5ae01fe` | **Passed**: native exit 0, actual sandboxed assertions and marker output, expected persisted fix, two succeeded local attempts with one destination fingerprint and `session_affinity`; cloud trap zero. |
| Incremental streaming/cancellation | `a553e228509ae50b0e543a9193f6c69ac5ae01fe` | **Passed**: text delta before completion, owned-client SIGINT, one unknown attempt with no replay after three seconds, released lease/observer, zero cloud calls. |
| Native disconnect/restoration | `a553e228509ae50b0e543a9193f6c69ac5ae01fe` | **Passed**: original owned values restored, generated provider removed, native project trust preserved, no retained settings; router, engine and observers stopped. |

### Final observed results — October 15, 2026

The runtime source was clean at
`a553e228509ae50b0e543a9193f6c69ac5ae01fe`, descended from the merged retirement
commit `d7015cc7f061d407d57bfcfc5bde1450b9aed543`. Subsequent documentation edits
do not change the qualified runtime. Source and installed-wheel validation each
passed all **73 workflow tests**, including the new RED → GREEN restoration case.

The new supplied-script case began with:

```python
def multiply(a, b):
    return a + b
```

The prompt requested this exact command through `exec_command`:

```sh
python3 -c "from pathlib import Path; fixture = Path('fixture.py'); fixture.write_text(fixture.read_text().replace('return a + b', 'return a * b')); from fixture import multiply; assert multiply(7,5)==35; assert multiply(-2,3)==-6; print('HORMUZ_SCRIPT_PASSED')"
```

Native command events showed exit 0 and `HORMUZ_SCRIPT_PASSED`. The persisted
function returned `a * b`. The native episode took 19.651 seconds; its two
provider attempts succeeded with the same destination fingerprint and a tool
continuation. This demonstrates supplied-script execution, not independent
problem solving. The public job remained `active` with `outcome_evidence:
unknown`; no completion event was invented from HTTP success.

An independent review prompt requested a long analysis of an inline Python
rolling-average function without tools. A transparent loopback observer before
Hormuz recorded SSE event types/times and request counts, without retaining
prompt/tool/answer contents. It forwarded the unchanged request and flushed
each SSE line. Limits were two handlers, 65,536 request bytes, 2 MiB response
bytes and a 90-second socket deadline; its process-only provider URL override
did not modify the installed table. This observer is required to reproduce the
wire-level cancellation assertion; native `--json` alone is not a token-delta
observer.

The observer saw the first `response.output_text.delta` at 0.521477 seconds
after accepting the request. The controller sent SIGINT at 0.583656 seconds
after starting native Codex, before any `response.completed` was observed.
These are distinct monotonic start points. Codex exited 1 and was reaped;
the observer closed its upstream transport and all handlers. The public local
lease returned to zero. Exactly one inference request and one public attempt
remained after a three-second grace interval: state `unknown`,
`cost_microusd: null`, no replay. The configured cloud trap received zero calls
in both cases. This is not a zero-cost claim.

Disconnect restored owned values and removed its provider while preserving
Codex's new project trust table and the original personality preference.
Its public receipt was `disconnected` / `stopped` / `not_owned`, with empty
`retained_settings`. Both native children, the observer, owned engine and cloud
trap stopped. No active user configuration or credentials were used.

Acceptance was frozen before either of these two native episodes and its hash
was unchanged afterward. Controlled raw evidence remains outside the repository;
these SHA-256 digests identify the receipts without publishing transcripts or
temporary configurations:

| Evidence | SHA-256 |
| --- | --- |
| Pre-run acceptance | `6edfcea11334e9a8290ce80c622985f87215f01751a2cfeb984261c8e75ae0c0` |
| Final two-case summary | `a8d5bcceae2cc7ac5b92958ba01eacad6bd5ba81a3a81d5bb0b18b416a7b2898` |
| Supplied-script native events | `80fccb24b71b9c18a5c71903a56cdd94c648eb3a4690d2fb2bd86563f794f0d5` |
| Cancellation native events | `f51d494b6c434d5626c99ad3a276cb1b5c13ac38708d1eb04deddea93ebf18f9` |
| Earlier literal-only failure summary | `bf3477bfc027eb156061183e7ba31fe26671e92e9907c067cbca07771c55c524` |
| Earlier autonomous-repair failure summary | `9419882b2b12957f8cf971fc4ff57404c43fd69402117888487c42a04a99bf99` |

The earlier failures retain their original acceptance and failed outcomes.
This profile does not justify general autonomous coding admission, automatic
multi-conversation setup, reasoning/namespace parity, or other client versions.

Removing this document changes no runtime behavior. To withdraw the operator
declaration, disconnect, remove this qualification/route from configuration and
reconnect only with a separately qualified profile. Retaining `local_only`
keeps unavailable work denied. Changing that privacy ceiling requires an
explicit operator choice; it is not a recovery step for a failed local task.
