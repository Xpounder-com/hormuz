# Managed llama.cpp lifetime

The [connected-client lifetime](CONNECTED_CLIENT_LIFETIME.md) can start one
verified llama.cpp engine before connecting Codex and stop that engine during
disconnect. The CLI and the skills-only setup workflow use the same owner. Existing
coding clients retain their tools, permissions and agent loop. This implements
the managed-runtime slice of [#523](https://github.com/Xpounder-com/hormuz/issues/523)
and [ADR 0020](decisions/0020-managed-llama-lifetime.md).

The files must already exist. This slice does not acquire, extract, convert or
select models or runtime archives. A healthy externally started engine remains
external; Hormuz neither adopts it nor stops it.

## Profile and activation

Add a `managed_llama` object to the existing gateway configuration:

```json
{
  "managed_llama": {
    "schema_version": 1,
    "route": "local-code",
    "executable": "/absolute/runtime/llama-b11617/llama-server",
    "runtime_archive": "/absolute/archives/llama-b11617-bin-macos-arm64.tar.gz",
    "runtime_sha256": "051bb0b55b3e5e72081aa819131ba30c74d910acc915b28bab298757472e39d9",
    "model_file": "/absolute/models/Qwen3-4B-Q4_K_M.gguf",
    "model_sha256": "7485fe6f11af29433bc51cab58009521f205840f5b4ae3a32fa7f92e8534fdf5",
    "startup_timeout_seconds": 60
  }
}
```

This is an additional object, not a complete gateway configuration. Its version
is exactly `1`; unknown fields and versions fail. Every shown field is required.
Paths must be absolute and bounded to 4,096 characters; digests are 64 lowercase
hexadecimal characters. Startup time is an integer from 1 through 60 seconds.
The [version-1 profile fixture](../tests/fixtures/managed-llama-profile-v1.json)
freezes this shape with synthetic external files rather than redistributing
model weights or executable binaries.

The route must name an existing qualified local OpenAI destination using HTTP,
host `127.0.0.1`, an explicit port and no authentication. Its path is empty, `/`
or `/v1`; credentials, query strings and fragments are rejected. Its model
revision must be `sha256:` followed by `model_sha256`. The qualification's output
limit must be at most 512 tokens. Admission must be `auto` or `local_only`;
`configured` mode cannot activate a managed engine. The selected actor's policy
must permit the managed route. Existing qualification, task-envelope admission,
secret inspection, tool compatibility and capacity limits still govern every
request. Starting an engine does not qualify a route or authorize cloud use.

Use the existing lifecycle commands with an explicit disposable client home
while evaluating a profile:

```sh
hormuz --config /absolute/hormuz.json client connect codex \
  --executable /absolute/codex --actor developer \
  --client-home /absolute/disposable-codex-home --dry-run

hormuz --config /absolute/hormuz.json client connect codex \
  --executable /absolute/codex --actor developer \
  --client-home /absolute/disposable-codex-home

hormuz client status codex --client-home /absolute/disposable-codex-home
hormuz client disconnect codex --client-home /absolute/disposable-codex-home
```

Preview verifies the files and requires the target engine endpoint to be free.
It starts neither engine nor router and does not activate routing. Connect
repeats the checks, stages the existing owned Codex settings and starts the
managed owner. An identical active connection reuses its owner. Changed
configuration requires disconnect/reconnect; repeated connect does not start a
second engine. An occupied engine endpoint fails without borrowing the listener
or terminating its process.

## Verified inputs and bounded readiness

Before activation, the owner verifies the runtime archive's SHA-256, the model's
SHA-256 and every regular file and symbolic link in the extracted runtime against
the archive. The executable must be a unique regular archive member and remain
executable. Comparing only the launcher is insufficient because the macOS bundle
loads adjacent libraries. Verification performs no extraction. Archive names
cannot escape the runtime root; links must match the archive and resolve inside
that root. Hashed files must be regular files owned by the invoking user, with
no group/other write permission, and cannot be symbolic links. File size,
modification time and inode must remain stable while hashing.

Verification has a 30-second budget, at most 256 archive members, a 256 MiB
archive limit, 1 GiB total archived regular-file content and a 32 GiB model-file
limit. The guardian verifies the same inputs again immediately before execution.
Validation or verification errors fail before successful activation, using
content-free errors rather than returning private paths or file contents.

The fixed engine invocation uses the qualified llama.cpp b11617 envelope:

```text
-m MODEL --offline --host 127.0.0.1 --port PORT --alias UPSTREAM_MODEL
--parallel 1 --threads 4 --threads-batch 4 --threads-http 2
--ctx-size 32768 --n-predict QUALIFIED_OUTPUT_LIMIT --jinja
--chat-template-kwargs {"enable_thinking":false}
--no-webui --no-agent --no-slots
--temp 0.7 --top-p 0.8 --top-k 20 --min-p 0 --presence-penalty 1.5
```

Arguments are passed directly, without a shell. Caller-supplied runtime flags
and model-download selectors are not part of this profile. The guardian receives
only allowlisted, non-secret process-context environment variables.
Provider credentials and arbitrary `LLAMA_ARG_*` environment overrides are not
forwarded. The engine inherits that restricted environment.

Readiness requires the owned child to remain alive, its own bounded output to
contain `llama_server: listening on http://127.0.0.1:PORT`, and a successful
prompt-free `GET /health`. The health response must be HTTP 200, at most 4 KiB
and decode to exactly `{"status":"ok"}`. The guardian permits at most 30 health
observations, spaced two seconds apart, within the configured startup deadline;
each uses a 0.5-second HTTP timeout and closes its connection. Engine output is
drained continuously with a bounded 4 KiB tail and is not retained as a public
log. A health response alone cannot establish engine identity or ownership.
Keep the verified bundle and single-file model unchanged while connected;
verification is a startup integrity check, not an OS sandbox or continuing
attestation of files modified by the same user.

The controller allows startup plus 35 seconds for verification and process
coordination. A failed start restores only unchanged owned configuration values
and cleans up its owned child. Status reports unavailable readiness if the
managed engine is unavailable. No automatic engine restart or inference replay
occurs; reconnect creates a new router epoch and requires fresh conversation
state under [automatic binding](CONVERSATION_WORK_BINDING.md).

## Ownership, shutdown and status

One guardian owns the actual engine child through its process handle. A private
parent pipe signals owner loss, and an inherited runtime lease remains held
through child cleanup. Closing the pipe or losing the connected router causes
the guardian to stop its child. Shutdown first requests termination and waits
two seconds, then may kill that same child and wait two more seconds. No PID is
stored in the journal, discovered from a port, or used to adopt another process.

Disconnect keeps the existing generation guard and conditional setting
restoration. It requests shutdown and waits up to 12 seconds for the runtime
lease. If cleanup is still pending, phase remains `disconnecting`; it does not
report completed shutdown or release another lifetime into that owned lease.
Preserve the ownership record and rerun disconnect. User-edited settings remain
reported through `retained_settings`. The archive, extracted runtime, model
files, external engines and coding client are left in place.

Managed status uses `schema_id: "hormuz.connected-client-status"` with
`schema_version: 2`, retaining the existing status fields and adding:

```json
{"local_runtime": {"ownership": "owned", "state": "ready"}}
```

`state` equals the existing `router` field and is one of `ready`, `unavailable`,
`stopping` or `stopped`. It expresses the owned lifetime's current readiness or
cleanup observation. `ready` requires router and engine health; `stopped`
requires completed owned cleanup. Neither means a coding task succeeded.
Request receipts and the existing public router/work endpoints retain their
separate outcome and affinity authority.

Managed preview uses `schema_id: "hormuz.connected-client-preview"`, version
`2`, and `local_runtime: {"ownership":"planned","state":"not_started"}`.
An unchanged active owner's preview instead reports `owned`/`ready` and no
configuration changes. Public [status](../tests/fixtures/connected-client-status-v2.json)
and [preview](../tests/fixtures/connected-client-preview-v2.json) fixtures freeze
these shapes. Contract discovery includes both versions, and validation rejects
unknown fields and inconsistent ownership/state pairs. Errors retain the
version-1 connected-client error envelope, including `runtime_invalid`,
`runtime_occupied` and `runtime_start_failed` lifecycle failures.

## Migration and rollback

Unmanaged connections retain their public status/preview version 1 and private
journal version 1. Exact earlier identifier-less v1 journals remain readable
under the existing normalization rule. Managed connections use the same private
`hormuz.connected-client-journal` identifier at version 2, with exactly the
existing fields plus `managed_llama: true`. No PID, credential, transcript or
second runtime journal is added. Version 2 without that true marker, a v1 record
with the marker, unknown versions and malformed records fail explicitly.

Gateway configuration bytes remain pinned by the existing journal digest.
Changing profiles, files or routing policy requires an explicit lifecycle
transition; startup does not silently rewrite qualification facts. The AI Work
ledger schema, historical work receipts and relay versions are unchanged.

Before removing `managed_llama` or downgrading to a binary without journal v2
support, disconnect with the current binary and verify `disconnected`, `stopped`
and restored settings. Resolve retained user edits separately. Do not delete a
journal while its lease or restoration is pending. Keep the current binary
available for recovery; an older binary cannot resume the managed record.
Disconnect/reconnect does not preserve prior conversation affinity or replay
unfinished inference.

## Evidence boundaries and existing pins

The approved workflow seam remains connect → coding-client request through the
router → result/status → disconnect. External synthetic runtime/provider
fixtures exercise process lifetime and public behavior without model downloads,
private-ledger assertions or internal routing mocks. A synthetic answer does not
establish model quality or support for an installed platform.

The existing [Codex 0.147.0 / llama.cpp b11617 / Qwen3-4B qualification](qualifications/codex-0147-llama-b11617-qwen3-4b.md)
retains the immutable artifact pins and its original manual-lifetime results:

| Existing input | Pin | Bytes | SHA-256 |
| --- | --- | ---: | --- |
| llama.cpp macOS arm64 archive | b11617; source `14310190cd0a0c831fe949ac8cbedd8abdc90695` | 12,157,609 | `051bb0b55b3e5e72081aa819131ba30c74d910acc915b28bab298757472e39d9` |
| Qwen3-4B-Q4_K_M.gguf | HF revision `bc640142c66e1fdd12af0bd68f40445458f3869b` | 2,497,280,256 | `7485fe6f11af29433bc51cab58009521f205840f5b4ae3a32fa7f92e8534fdf5` |

That archive contains 42 regular files and 18 symbolic links under
`llama-b11617/`; the launcher depends on the adjacent runtime. Its observed
version is `0.6.0-dev (build 11617, commit 14310190c)`, Darwin arm64. The historical
record declares llama.cpp MIT and the model Apache-2.0; preserve applicable
licenses if distributing artifacts. This implementation distributes neither.

The historical real-model cases and the automatic-binding deterministic native
case remain distinct evidence. Their retained quality failures are unchanged.
This slice does not qualify Codex desktop, Claude, another operating system or
model, plugin installation, subscription authentication or paid cloud fallback.
