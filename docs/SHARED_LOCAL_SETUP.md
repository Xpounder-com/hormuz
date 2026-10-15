# Shared local setup

`client setup codex` generates a local-only router configuration from existing
verified llama.cpp and model files, then connects through the existing
[connected-client lifetime](CONNECTED_CLIENT_LIFETIME.md). The terminal and
[Hormuz skill](../skills/hormuz/references/workflow.md) call this same command.
This implements the next setup slice of [#523](https://github.com/Xpounder-com/hormuz/issues/523).

## Use

Use an installed Hormuz development CLI and a supported Codex CLI executable.
Setup creates or reuses a private local caller credential and configures a
generation-bound Codex auth helper. No manual token export or cloud account is
needed. See [credential ownership and recovery](LOCAL_CREDENTIAL_SETUP.md).
Select a new output configuration path and the existing archive, extracted
launcher and GGUF:

Check `hormuz client setup --help` when updating a development installation;
the development version string alone does not establish command availability.

```sh
hormuz --config /absolute/private/hormuz.json client setup codex \
  --client-home /absolute/codex-home --executable /absolute/codex \
  --runtime-archive /absolute/llama-b11617-bin-macos-arm64.tar.gz \
  --llama-executable /absolute/runtime/llama-server \
  --model-file /absolute/Qwen3-4B-Q4_K_M.gguf --dry-run
```

Review the configuration's operation, local-only mode, model, qualification,
managed authentication and proposed loopback ports, plus the nested connection
preview. Run the identical command without `--dry-run` to create and connect.
Use `--credential-env NAME` to explicitly retain environment-backed authentication.
That mode still requires the variable in both Hormuz and Codex. Existing
environment-backed configurations are preserved; reconnect with `client connect`
or use their original `--credential-env` rather than implicitly migrating them.
Generated configurations use actor `local-user`; other configured identities
remain available through the existing `client connect` workflow.

```sh
hormuz client status codex --client-home /absolute/codex-home
hormuz client disconnect codex --client-home /absolute/codex-home
```

Setup success requires the nested connection receipt to report `connected`,
`ready`, `installed` and an owned ready local runtime. A preview reports
proposed changes and starts neither router nor engine. It may create private
lock directories/files and missing output parents, but leaves the final router
configuration contents and Codex settings unchanged. It may complete the
matching staging-link cleanup described below.

## Qualification and files

The bundled [version-2 profile](../hormuz/local-setup-codex-0147-llama-b11617-qwen3-4b-v2.json)
contains the exact digests, client/platform row and bounded request envelope
from the [Codex 0.147.0 / llama.cpp b11617 / Qwen3-4B macOS arm64 experiment](qualifications/codex-0147-llama-b11617-qwen3-4b.md).
It retains that record's limitations: supplied-script execution passed;
autonomous repair and strict final formatting failed. Matching artifact hashes
does not establish general coding quality or another client/platform row.
Its nullable `coding_evidence` is empty, so automatic coding remains blocked even
when the engine is healthy. [Coding qualification](LOCAL_CODING_QUALIFICATION.md)
defines the outcome gate, status, explicit trust and migration rules. Version-1
profiles remain readable with unknown coding readiness.

The existing [managed lifetime verifier](MANAGED_LLAMA_LIFETIME.md) checks the
archive and GGUF digests and every archived runtime file and symbolic link,
including adjacent libraries. Missing, corrupt or unsafe files block before
configuration publication or model startup. Files are reused at the supplied
locations; setup neither copies nor extracts them, discovers installations,
selects another model, or downloads anything. Unsupported platform/client
declarations block. Actual target version detection remains in the shared
connection owner.

Advanced operators may pass `--qualification /absolute/reviewed-profile.json`
for their own pre-existing qualification declaration. This is explicit trust
in an operator-selected document, equivalent to configuring a local
qualification manually. It is not a new certificate or a way to infer task
quality from filenames. Use existing evidence and independently trusted
digests; agents must preserve the declared capabilities and limits. The
[version-2 synthetic fixture](../tests/fixtures/local-setup-profile-v2.json) documents
the exact input shape; it does not identify a real runtime or model.

## Creation, reuse and recovery

Setup creates a mode-0600 configuration and a sibling `<config>.state`
directory with mode 0700 for local usage/work SQLite files. An exclusive,
bounded setup lock serializes callers targeting the same configuration.
Configuration publication is atomic and create-only. Preview validates the
candidate with the same connection owner before publishing it.

Staging names use `.hormuz-setup-v1-<identity>-<nonce>`, where identity is the
first 16 hexadecimal SHA-256 characters of the canonical absolute output path.
After interruption between linking the final file and removing its staging
name, setup can remove that duplicate staging link under its lock. Recovery
requires exactly two links to the same private, user-owned regular inode, the
matching versioned namespace, unchanged metadata, and a match within at most
256 directory entries. Final contents remain intact. Other hard links stay
untouched and unsafe configurations block. An orphan left before publication
does not prevent a fresh attempt; it remains available for inspection.

Output paths are canonicalized before the reserved-path check. Codex settings
and its private lifecycle directory are excluded, including ancestor aliases
and `..`; final symbolic links remain subject to the no-follow file check.

Repeated identical setup uses the existing configuration, ports and active
connection generation. After disconnect, it can reconnect with a new
generation. A changed or unrelated existing configuration is preserved and
reported as `connected_client_setup_changed`; inspect it and select a new
output path or deliberately reconcile it. Setup never rewrites existing
gateway policy. Unsafe files, links or replaced state directories block.

An engine startup failure restores owned Codex settings using the existing
journal and shutdown authority. The generated configuration remains available
for inspection and a subsequent identical setup. A successful preview or a
configuration file alone is not a ready connection. Follow public recovery
actions for interrupted ownership; preserve the journal and user edits.

Disconnect restores only owned client settings and stops only owned processes.
It keeps the generated configuration, SQLite evidence and supplied assets for
reuse. Removal of the plugin removes instructions; disconnect first.

## Contracts and rollback

- `hormuz.local-setup-profile` version 1 is the strict, bounded input declaration.
  It includes exact digests, client/platform, model, capabilities, task types
  and request/output/startup/upstream limits. Unknown fields/versions fail.
- `hormuz.local-setup` version 1 is the environment-backed, content-free receipt containing
  `configuration` and `connection`. Configuration has operation `create` or
  `reuse`, mode `local_only`, qualification/model identifiers, credential
  variable name and two distinct loopback ports. The connection is the existing
  version-2 preview/status contract. Errors retain the existing version-1
  connected-client error envelope and fixed recovery vocabulary.
  Its [version-1 receipt fixture](../tests/fixtures/local-setup-v1.json) freezes
  the shape independently of generated ports and connection generations.
- Version 2 replaces `configuration.credential_env` with
  `configuration.authentication: managed_local`. Its
  [fixture](../tests/fixtures/local-setup-v2.json) retains the nested version-2
  connection contract. Managed local authentication uses private journal
  version 3 and a separate private credential file version 1; complete
  disconnect before downgrading to a binary that cannot read those formats.
- Existing environment-backed gateway configuration, managed profile version 1,
  journal versions 1/2, work storage and connect/status/disconnect contracts
  remain supported. The new `token_file` configuration reference and journal
  version 3 require the current binary. Complete disconnect before downgrade.

Routine tests use external synthetic runtime/client fixtures through the
approved connect → request → result/status → disconnect seam. They do not prove
real plugin activation, desktop routing, subscription access, other platforms
or autonomous model quality. See [the setup decision](decisions/0021-shared-local-setup.md).

## Native setup confirmation — October 15, 2026

One frozen episode ran against clean runtime source
`4042819304048ab81997c618f43f7fba51053a1f`, using the existing pinned
Codex 0.147.0 executable, llama.cpp b11617 bundle and Qwen3-4B GGUF. Inputs,
driver, runtime Python/JSON hashes and supplied-script acceptance were frozen
before execution. There were no model-quality retries or downloads.

In a disposable private home, the shared command's preview left final router
configuration and client settings unchanged. Its apply command generated the
mode-0600 configuration with only a local destination, verified and reused the
existing artifacts, then reported the owned engine ready. The driver wrote no
router JSON and never started or signalled an engine itself.

The real Codex supplied-script episode exited zero in **19.679 seconds**,
executed exactly one requested Python command, persisted the expected fix and
passed both assertions. It created one automatically bound work item with two
successful attempts on the same local destination; the second retained session
affinity. Public work outcome remained `unknown`, and local capacity returned
to zero. All twelve existing behavioral predicates passed.

Disconnect restored owned preferences and removed its provider while preserving
the native client's observed project-trust addition. Engine/router endpoints
closed, the native process was reaped and the independent loopback cloud trap
stopped with zero calls. That trap was not a configured destination: this case
qualifies generated local setup, not hybrid cloud routing. Frozen inputs and
all cleanup predicates passed. Subsequent documentation-only changes preserve
the qualified runtime bytes.

| Private evidence receipt | SHA-256 |
| --- | --- |
| Frozen driver | `3b2e3ee8d63bfcca205a37fcdf9f0a41addea4831d5e69845f0f98de091528f4` |
| Supplied-script acceptance | `a68e717b3cc8ae755d6ba8394e18a12fe13af7a155541cbed5aec9308b28550a` |
| Frozen input manifest | `7b6c4ac2d0a9bb056561394eb8602fd811b41f7391b4e92db2fd458e319219ee` |
| Final summary | `d779b6a3a81dea7fedb082e24e4773a78ece3c058dfa2fbc6bdd91ba4e40f5de` |

Raw evidence remains private. This pass preserves the earlier model-quality
failures and leaves plugin activation, desktop routing, later turns/resume/fork,
other client/platform rows and subscription eligibility unqualified.
