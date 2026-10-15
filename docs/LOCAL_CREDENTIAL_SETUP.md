# Automatic local caller credentials

Default `client setup codex` uses the existing verified engine/model inputs and
creates a private local caller credential. Codex obtains it through its
command-backed provider authentication. The router and client need no manual
token exports, provider account or cloud credential. CLI and plugin callers
use this same setup operation.

Use the command and preview in [shared setup](SHARED_LOCAL_SETUP.md). The
version-2 setup receipt reports `configuration.authentication: managed_local`.
An installed Hormuz package and the qualified Codex CLI executable remain
required. Plugin activation and Codex desktop integration need their own
qualification.

## Ownership and disclosure

The generated configuration contains an absolute `identities[].token_file`
reference to `<config>.state/router-credential-v1.json`, rather than a secret or
environment variable. Static identities require exactly one of `token_file`
and the existing `token_env`. File references are absolute; no implicit search,
native login-cache import or provider-token reuse occurs.

The credential file is a private, user-owned regular file (0600) beneath a
private state directory (0700). Its exact version-1 shape is `schema_id:
hormuz.local-router-credential`, `schema_version: 1` and a `token` containing
64 cryptographically random hexadecimal characters. Reads are bounded to
1024 bytes and reject symlinks, hard links, unsafe permissions and unsupported
formats. The [synthetic fixture](../tests/fixtures/local-router-credential-v1.json)
documents the format; its value is not a credential to use.

Codex settings and the ownership journal contain only the helper command and
arguments. The private version-3 journal additionally binds a SHA-256 digest
of the generated high-entropy credential to its generation. The helper validates
that generation, connected phase, installed settings, unchanged configuration,
live owned router and credential digest before returning a token.

`hormuz auth local-token` is a machine-only helper: its successful stdout is a
credential for Codex, not a status receipt or troubleshooting command. Its
arguments, errors and ordinary setup/status receipts contain no token. Keep
helper stdout out of transcripts and logs. The secret remains accessible to
the same operating-system user, as do that user's other private files.

The pinned Codex 0.147.0 schema supports `model_providers.<id>.auth` with a
command, arguments, timeout and refresh interval. Setup uses an absolute Python
interpreter and helper script from its installed package, isolated Python
imports, a five-second helper deadline and
refresh only on authentication retry. It combines this with neither `env_key`
nor native OpenAI authentication. See [official command-backed authentication](https://learn.chatgpt.com/docs/config-file/config-advanced#custom-model-providers)
and the [pinned schema](https://github.com/openai/codex/blob/rust-v0.147.0/codex-rs/core/config.schema.json).

## Preview, repeat and recovery

Preview validates a temporary credential and candidate configuration, then
removes both. It publishes no credential or connection and starts no engine.
As with existing preview, private lock directories and missing output parents
may be created.

Apply publishes the private credential exclusively after the connection preview
succeeds, then publishes configuration and uses the existing connected-client
owner. The existing bounded publication recovery also handles an interruption
between linking the credential and removing its staging link. It removes only
an exact private matching duplicate link. Other files remain untouched.

Repeated identical setup reuses the credential and active generation. After
disconnect, reconnect creates a new generation while retaining that credential.
An engine startup failure restores owned settings and keeps generated files
available for retry. A missing, invalid, replaced or unsafe credential blocks;
an existing configuration never causes its missing credential to be silently
recreated. Preserve files, inspect the public recovery action and complete
disconnect with the current binary before deliberate repair. The helper will
not return a credential for a disconnected or superseded generation.

Disconnect restores only owned values and the exact owned provider/auth tables,
preserving later user edits. It stops only owned router/engine processes and
retains the private credential, generated configuration and evidence for reuse.
Disconnect before removing the CLI or plugin; instruction removal alone does
not stop a connection.

## Compatibility and rollback

- Environment-backed setup remains explicitly available with
  `--credential-env NAME`. It retains setup receipt version 1 and the existing
  environment requirements. Existing configurations are never converted by
  implicit credential selection.
- Managed-file setup uses receipt version 2; nested connection preview/status
  remain version 2 for managed engines. The [receipt fixture](../tests/fixtures/local-setup-v2.json)
  freezes this shape.
- Private journal version 3 adds only non-secret `credential_helper` metadata
  to the existing journal. Versions 1 and 2 remain readable without rewriting
  their authentication mode. The [journal fixture](../tests/fixtures/connected-client-journal-v3.json)
  records the new shape.
- Older binaries cannot use the new file reference or journal. Disconnect with
  the current binary before downgrading, preserve reported edits and keep it
  available for recovery. No work/accounting schema or provider authentication
  format changes.

The workflow tests prove setup, authenticated local requests, status and owned
cleanup using synthetic clients and providers. Real model quality, subscription
eligibility and additional client/platform combinations remain separate.

## Pinned native confirmation — October 15, 2026

One frozen episode used clean runtime source
`0dec498c186baabc4efc8f07efa2974f356725c8` and its verified wheel, with all
111 runtime Python/JSON file hashes matching. It reused the existing pinned
Codex CLI 0.147.0 macOS arm64 binary, llama.cpp b11617 bundle and Qwen3-4B
Q4_K_M GGUF. There were no downloads, cloud credentials, token exports or
model-quality retries. Source and installed-wheel checks each passed all
136 workflow tests without skips.

In a disposable private home, preview left no credential or final configuration.
Apply generated the private credential and local-only configuration. Codex used
the installed isolated helper; repeated setup retained the active generation.
The credential was absent from router/client settings and the ownership
journal. After disconnect, the cached helper returned no credential. All six
frozen credential checks passed.

The supplied-script episode exited zero in **18.165 seconds**, executed the
requested command and assertions, and persisted the expected fix. All twelve
existing behavioral predicates passed: one automatically bound work item, two
successful attempts at one local destination, retained session affinity and
released local capacity. Public work remained `active` with outcome `unknown`.
This is supplied-script execution; the earlier autonomous repair and answer
formatting failures in the [model qualification](qualifications/codex-0147-llama-b11617-qwen3-4b.md)
remain recorded.

Disconnect restored owned preferences/provider tables and preserved the native
project-trust addition. The owned engine/router endpoints closed, native client
was reaped and the bounded independent cloud trap stopped with zero calls.
That trap was not a configured destination; this case does not qualify hybrid
routing. Frozen inputs and cleanup predicates remained valid.

| Private evidence receipt | SHA-256 |
| --- | --- |
| Frozen driver | `831695d47e9659edb9d640868e19b0e3e80108d46fe10dae72a944214253f39c` |
| Frozen manifest | `887e44932ff90357c0019dec4a8a3a457876fbf1398da13dbe9ee4bbb981ea38` |
| Final summary | `f6db28de6af467b6a2d957092c8c6c00097aa98b44b71b56766813d904b75fc0` |

Raw evidence remains private. Plugin activation, desktop routing, later
turns/resume/fork, other platforms and subscription eligibility remain separate.
