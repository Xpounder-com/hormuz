# Context optimizer qualification

This separate, on-demand Rust helper implements the existing `structural-v1`
lossless transformations. It does **not** replace the shipping Python helper,
change the optimization preference, integrate with a client launcher, or change
the server. Missing resources, unsupported input, failed counts and the work
deadline return pass-through. The relay remains responsible for authentication,
credential cancellation and forwarding exactly once.

## Contract

The library supports canonical JSON tables, repeated line runs, search lines and
path lists, including the existing framed Codex output forms. Every candidate
must reconstruct the original text exactly and satisfy the Python byte/token
savings guards for both `cl100k_base` and `o200k_base`. JSON order, arbitrary-sized
integers, float spelling, Unicode, newlines, duplicate results and opaque
Responses history have regression coverage. Search-line decoding matches the
packaged Python 3.12 Unicode 15.0 digit semantics.

The existing limits remain: 64 KiB blocks, 1 MiB requests, 4,096 rows, 64 columns,
32 JSON levels, 50,000 nodes, 64 selections and 256-byte minimum candidates.
Malformed envelopes cannot expand beyond the block limit. Malformed governing
JSON or duplicate keys are refused. Serialized Codex tool arguments retain
Python's last-key-wins behavior; these strings are never executed.

`tiktoken-rs` 0.12.1 supplies the maintained BPE implementation. The helper uses
the existing external rank files and exact pinned SHA256 values; it never
downloads resources or embeds vocabularies. Tokenizer construction is explicit:
hash-only readiness, offline transformations and optimization-Off do not load a
tokenizer. The gateway probe is an unauthenticated, bounded `/health` GET with no
redirect or proxy; it is not a provider request.

Supported executable commands are:

- `qualify [--tokenizer-cache DIRECTORY]`: one bounded synthetic JSON job on stdin.
- `resources --check DIRECTORY`: hash-only readiness without tokenizer setup.
- `relay-bridge --client codex|claude-code --path PATH`: the existing length-prefixed
  gateway/request stdin protocol, with byte `0` for pass-through or byte `1` plus
  transformed bytes. The supervising relay still bounds the helper lifetime.

The bridge finds tokenizers in `Contents/Resources/ContextTokenizers` when placed
in an app's `Contents/Helpers`, or from `HORMUZ_CONTEXT_TOKENIZER_CACHE`. Standalone
Python state-directory cache inference is not implemented yet.

## Reproduce qualification

Use the workspace-pinned Rust toolchain and the packaged-reference Python 3.12
environment with tiktoken 0.14.0 and both verified rank files. From `clients/rust`:

```sh
cargo test --workspace --locked
cargo clippy --workspace --all-targets --locked -- -D warnings
cargo build --release --locked -p hormuz-context-optimizer
```

From the repository root:

```sh
python -m tools.verify_rust_context_optimizer \
  --helper clients/rust/target/release/hormuz-context-optimizer \
  --tokenizer-cache /absolute/verified-tokenizers \
  --output /absolute/new-parity-report.json
python -m tools.measure_context_helper_footprint \
  --rust-helper clients/rust/target/release/hormuz-context-optimizer \
  --python-helper /absolute/original/Hormuz.app/Contents/Resources/ContextHelper/hormuz-context \
  --python-backend /absolute/original/Hormuz.app/Contents/Helpers/hormuz-context-arm64 \
  --tokenizer-cache /absolute/verified-tokenizers \
  --repetitions 5 --output /absolute/new-footprint-report.json
```

The differential harness reuses the Python evaluation's 12 fixtures in all three
protocols, exact token resources, adversarial shell mappings and decoder bounds.
It additionally compares 4,108 deterministic float values, Unicode digit edge
cases, token counts and canonical request outputs. Reports identify binary and
resource hashes without publishing request content. No provider money or
credentials are involved.

Footprint qualification launches both **actual** bridge processes on identical
synthetic eligible input and checks byte-identical output/reconstruction. Five
alternating repetitions record wall/CPU time, `time -l` max RSS and sampled RSS
sums for every helper descendant (including PyInstaller's worker). Surviving
observed processes fail the test. This is helper-process evidence, not a resident
UI, power, physical footprint or production claim. RSS sums can double-count
shared pages and miss short transients. The Python backend is the original
distribution; the Rust binary is a local development release build. Signing
differences especially limit startup comparisons. Identically compressed helper
components are not whole-app archives.

## Shipping migration and rollback

Do not remove Python or change the launcher based on these tests alone. The full
packaged helper interface is not yet equivalent: macOS calls `context status
--profile ... --state-directory ... --readiness-only`; packaging checks `context
--help`. The Python CLI also advertises `context settings`, `compact`, `run` and
`resources install`. Those commands are not implemented here. macOS preference
toggles currently write the private settings file directly, not through this
new binary.

The crate enables `serde_json` insertion-order and arbitrary-precision features;
Cargo unifies them with other workspace members. Its bounded JSON parser builds
objects explicitly so serde's private number-tag key remains an ordinary object.
The full workspace tests and lint checks must accompany changes to those shared
features; focused optimizer tests alone are insufficient.

Before a backend migration:

1. Implement or deliberately retain the advertised commands, profile/state
   resolution, hash-only status output and error codes. Verify actual app callers,
   not just this helper's transform command.
2. Integrate on one exact accepted source, build/sign the new nested helper and
   verify the original resulting app archive. Run actual installed Codex and
   Claude checks with On/Off, resource failures, cancellation and no replay.
3. Compare same-source integrated whole-app archives and helper process trees;
   retain the original archives and report any platform/qualification limits.
4. Preserve a verified Python-backed app as the explicit rollback artifact. Keep
   Off on the governed relay route; failures must pass through once, never retry
   a provider request or copy credentials to the optimizer.

Until that evidence exists, the production Python backend and its resource
installer remain intact. This crate provides a measurable migration candidate,
not completed release/distribution acceptance for issue #342.
