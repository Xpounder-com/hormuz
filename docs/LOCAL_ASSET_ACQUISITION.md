# Verified local asset acquisition

`koto client acquire codex` obtains the runtime and model files consumed by
[shared setup](SHARED_LOCAL_SETUP.md). It advances the acquisition gap in
[#523](https://github.com/Xpounder-com/hormuz/issues/523) and
[#542](https://github.com/Xpounder-com/hormuz/issues/542). The existing Codex CLI
and installed Koto remain prerequisites. CLI and skills-only plugin callers
invoke this same operation; acquisition does not install a second connection
owner or activate routing.

## Preview, download and reuse

Choose a private directory with an existing parent. The default invocation is
an offline preview, and `--dry-run` is an equivalent explicit spelling:

```sh
koto client acquire codex --directory /absolute/private/hormuz-assets
```

The receipt shows the exact source URLs, SHA-256, byte sizes, license identifiers
and links, target generation, remaining download bytes and required/available
disk space. Preview creates no directory, lock, configuration or network request.
If the shown files and licenses match the user's request, explicitly download:

```sh
koto client acquire codex --directory /absolute/private/hormuz-assets --download
```

A ready receipt's `setup_arguments` is an argument array for the existing
`koto --config /absolute/private/koto.json client setup codex` command.
Pass its entries as separate arguments, along with the selected Codex executable
and client home. Preview setup first with `--dry-run`, then apply the same
authorized setup. Treat the array as data; do not evaluate it as shell code.
Normal status, request and disconnect behavior stays with the connection owner.

To require a completed installation while offline:

```sh
koto client acquire codex --directory /absolute/private/hormuz-assets --offline
```

Completed installations are reverified on every reuse, including archive/model
digests, the complete runtime inventory and internal symbolic links. `--download` on a
completed, unchanged generation also reuses it without any network request.
Missing offline assets fail explicitly. Damaged assets, unexpected files, unsafe
links or changed metadata are preserved and block reuse; select a new directory
or inspect the existing files before taking any cleanup action.

## Experimental catalog and scope

The bundled [version-1 catalog](../hormuz/local-acquisition-codex-0147-llama-b11617-qwen3-4b-v1.json)
retains the [existing experimental row](qualifications/codex-0147-llama-b11617-qwen3-4b.md):
Codex 0.147.0, Darwin arm64, llama.cpp b11617 and Qwen3-4B-Q4_K_M at its exact
Hugging Face revision. Its sources, sizes and digests were checked against the
[llama.cpp release](https://github.com/ggml-org/llama.cpp/releases/tag/b11617) and
[pinned Qwen file](https://huggingface.co/Qwen/Qwen3-4B-GGUF/blob/bc640142c66e1fdd12af0bd68f40445458f3869b/Qwen3-4B-Q4_K_M.gguf)
on October 15, 2026. The catalog links the runtime's MIT license and the model
repository's Apache-2.0 declaration; redistribution must retain upstream notices.
No additional account, token, license acceptance on behalf of the user, or
moving `latest`/`main` download is introduced.

The pinned model's HTTPS redirect to `us.aws.cdn.hf.co` was confirmed with a
single HEAD request on October 15, 2026; no model body was downloaded. That exact
host is declared in the catalog, consistent with the upstream
[download host documentation](https://huggingface.co/docs/hub/models-downloading).
This host-list correction changes the canonical catalog digest and selects a
new generation directory even though artifact revisions, sizes and hashes are
unchanged. Existing generations are preserved. To reuse an earlier generation,
explicitly select its original reviewed catalog; markers are never rewritten.

The fresh download is 2,509,437,865 bytes. Preflight reserves remaining bytes,
1 GiB of maximum extracted runtime data and 64 MiB of margin. The profile's
platform row is checked before any download. This does not establish RAM
suitability, model competence, a production support envelope or cloud access.
The original qualification used 32 GiB RAM; model/task suitability and the
owner's first-release envelope remain separate decisions. Existing failed
autonomous coding results remain unchanged.

An operator can explicitly select `--catalog /absolute/reviewed-catalog.json`.
That is trust in a reviewed source declaration. It is not signed certification,
model discovery or permission to expand capabilities. The strict version-1
catalog embeds a versioned setup profile and two artifacts whose digests must
match it. Unknown keys/versions, unsupported platform rows and unsafe URLs
fail before acquisition. A synthetic loopback HTTP catalog is supported for
deterministic tests; ordinary sources and redirects use HTTPS.

## Bounds, recovery and ownership

Only one artifact request and one owned download worker are active at a time.
The two artifacts download sequentially; each worker reuses its connections,
allows at most four redirects and enforces declared host restrictions,
ten-second connection timeouts and bounded read waits. The parent enforces a
per-artifact absolute deadline that includes DNS, address attempts, TLS, headers
and data. The default is 1,800 seconds; `--timeout-seconds` accepts 1–3,600.
On timeout or cancellation, the parent terminates its owned worker, allows
250 ms for graceful cleanup, then kills and reaps it within another bounded
250 ms. Workers close their connections and flush retained progress on graceful
exit. Isolated Python receives an allowlisted environment. No ambient bearer credentials, cookies or
proxy authentication are imported or forwarded. Redirect query strings and
server error bodies never enter receipts.

SIGTERM follows the CLI's interruption cleanup and returns the fixed
`interrupted` error. A private ownership pipe also stops the download worker if
its parent dies without cleanup, including SIGKILL. The worker inherits the
generation lock, keeping another acquisition out until its transport and file
descriptor close. No worker is adopted by PID or detached into a background job.

There are no automatic retries. A 429 or unavailable source stops the operation;
respect its availability/Retry-After guidance before a new explicit request.
No source failure starts another model, a paid provider or a background job.

The selected parent is private and user-owned. Each catalog has its own canonical
SHA-256 generation directory. A nonblocking exclusive lock prevents concurrent
inspection and acquisition of that generation. Preview and offline reuse open
an existing lock without creating one; an active writer returns `busy`, including
during extraction. A fresh lockless preview is a missing-state snapshot and
does not inspect a generation created concurrently. An existing generation
with a missing or unsafe lock fails closed for inspection.
Owned `.part` files retain bounded progress after
an interrupted transfer. A later explicit download resumes at that byte offset
only with an exact validated Content-Range and final size/digest. A source that
ignores Range restarts that owned partial file; mismatching bytes never publish
a ready installation. The next preview reports retained progress without
claiming it is already verified.

Each runtime/model checksum read has a finite budget of 60 seconds plus one
second per started 8 MiB. Exceeding that budget returns `download_unavailable`
and preserves the owned partial for a later explicit attempt. A completed
size/digest mismatch still returns `download_invalid` and resets only that
owned partial. Published artifacts are never truncated. Managed-runtime checks
use the same checksum policy, and startup owners include verification time in
their bounded wait. Fresh ready receipts report zero remaining download/required
space and current free space; earlier version-1 receipts remain readable without
rewriting their stored preflight figures.

Archive extraction allows at most 256 entries and 1 GiB of expanded data within
30 seconds, including a separate decompression bound. It rejects traversal,
duplicate entries, hard links, special files, escaping or cyclic symlinks and
unsafe path layouts, including paths deeper than sixteen components. Links are created after file writes. The existing managed
runtime verifier checks the result before a ready receipt is atomically published.
No downloaded executable runs during acquisition.
The strict acquired-runtime inventory includes object types, owner, link counts
and normalized permissions, including implicit parent directories. Replacing an
expected empty directory with a file or escaping symlink blocks offline reuse.

An interrupted transfer with a valid catalog marker is resumable. Unknown state
or interruption around local publication can instead fail closed for inspection;
the command does not delete files to guess ownership. An interrupted extraction
cleans its own temporary directory during normal exception/cancellation cleanup.
A process killed before cleanup can leave preserved state requiring inspection.

Updates select a separately reviewed catalog and create another digest-bound
generation. Existing generations are never replaced, auto-updated or pruned.
Disconnect before changing or removing files used by an active connection.
Disconnect and plugin removal retain acquired assets. Automatic removal and
shared-cache discovery are outside this slice; no deletion command is added.

## Contracts and rollback

The public catalog (`hormuz.local-acquisition-catalog`), receipt
(`hormuz.local-acquisition`) and fixed error (`hormuz.local-acquisition-error`)
start at version 1, with [catalog](../tests/fixtures/local-acquisition-catalog-v1.json),
[receipt](../tests/fixtures/local-acquisition-v1.json) and
[error](../tests/fixtures/local-acquisition-error-v1.json) compatibility fixtures.
They appear in `koto contract manifest`. Artifact URLs and installation paths
are operational metadata; receipts contain no provider response body, credential,
prompt or retained history text.

This is additive. Existing setup/profile, connection journal, configuration,
admission and policy versions remain supported. The new directory has its own
catalog and receipt; existing stores are not migrated. Older binaries can use
the verified paths through the existing setup flags but cannot interpret the
new acquisition contracts. Disconnect with the binary owning the connection
before downgrade. Preserve acquired files, user edits and existing history.

Workflow tests use synthetic artifact servers and the real setup/router path.
They establish integrity, bounds, recovery and restoration behavior. They do
not download real model weights or establish marketplace acceptance, native
plugin activation, cloud eligibility or model quality. Parallel #538 owns
evidence-backed coding qualification; PR #545 owns plugin capability workflows.
