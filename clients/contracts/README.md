# Native client compatibility fixtures (v1, initial slice)

This is the first implementation slice of [#330](https://github.com/Xpounder-com/hormuz/issues/330),
targeting v1.4.0. The shared file is
[`tests/fixtures/native_client/v1/gateway.json`](../../tests/fixtures/native_client/v1/gateway.json).
It contains synthetic input and expected results, not captured credentials or traffic.

The source revision is recorded in the fixture. Reference implementations are
Swift `Dashboard.swift`, `TestSupport.swift`, the existing gateway contract fixture
`tests/fixtures/contracts/valid-v1.json`, and Python `hormuz.contracts.validate_contract`.
The new Rust crate lives in [`clients/rust`](../rust).

## What the first slice proves

- Rust and the existing Swift model consume the same 41 identity/usage vectors
  and agree on acceptance and selected display values.
- The existing Python gateway validator consumes those same vectors against its
  independently recorded `gateway_valid` expectation.
- Ten additional raw-JSON vectors in `raw-numbers.json` preserve integer,
  decimal and exponent spellings through all three consumers. Expected counts
  are decimal strings so the fixture reader cannot round the expected value.
- Organization and supported-client binding, human/session identity, UTF-8 name
  bounds, current-month usage, nonnegative counts/cost, and original cost/coverage
  labels retain the current native meaning.
- Missing data cannot construct a zero snapshot. Rust responses are bounded at
  128 KiB and parse errors expose fixed messages only. Unknown fields are dropped
  from the display projection; there are no credential members.

These models do not call the server, start a runtime, access secure storage,
launch helpers, load tokenizers, aggregate organization totals or implement a UI.
Cost remains the existing floating-point display estimate. Never use it for a
new accounting or billing calculation.

## Different responsibilities are explicit

The complete gateway v1 schema uses exact keys and includes additional accounting
and security counters. The Swift client reads a smaller projection and ignores
unknown fields. Its existing minimal test response is therefore accepted by the
client but is not a valid complete gateway response.

Conversely, the gateway schema supports identities and months that this native
session client cannot use. The client requires its saved organization/client,
a human identity, a `session:` authentication source and the current month.
Swift also has native display-name bounds and signed 64-bit counts. Python
integers have no fixed width. Swift accepts integral JSON numbers such as `3.0`;
the Python server schema requires an integer. Every vector where the client and
gateway differ has a note. No existing validator is relaxed or changed here.

The common vectors characterize the normal unique snake_case gateway keys.
Duplicate keys and alternative key spellings are not yet a cross-language
compatibility claim. Counts are decoded from decimal digits with checked signed
64-bit arithmetic, including exact integral decimal/exponent spellings. Rust
rejects actual fractions and nonzero underflow instead of rounding; Foundation
can round some such inputs and reject some boundary decimal forms. Those inputs
are not emitted by the gateway's integer contract, and reproducing Foundation's
lossy behavior is deliberately outside this port. Rust independently
rejects malformed/nonfinite JSON and oversized responses. Transport must also
bound bytes while reading; a parser size check is not streaming transport.

## Version and rollback rules

`hormuz.native-client-fixtures` version 1 versions the test corpus, not an HTTP
API, persisted credential, or released application. The existing identity and
usage HTTP schema IDs and versions stay unchanged. The unpublished Rust package
is `1.4.0-dev.1` (`publish = false`); the shipped gateway/app remain v1.2.0.

Additive cases may be added to this corpus with all consumers updated. Changing
an existing expectation requires an explicit compatibility decision and a
successor fixture version; never silently rewrite reference behavior to make a
port pass. New response fields, durable state, or adapters need their own schema
and migration review. The initial crate is not linked into the existing app, so
rollback removes development/test files without a data migration.

Profiles, session transitions, freshness/scheduling, context settings, client
status and the complete safe-error catalog remain open in #330 and its dependent
issues. The three current Rust errors cover only this parser/identity slice.

## Run the reference checks

From the repository root:

```sh
python3 -m unittest -v tests.test_native_client_contracts tests.test_contracts
swift test --package-path clients/macos --filter SharedContractTests
```

For Rust, with rustup installed:

```sh
cd clients/rust
cargo fmt --all -- --check
cargo test --workspace --locked
cargo clippy --workspace --all-targets --locked -- -D warnings
```

`rust-toolchain.toml` pins the compiler/components and `Cargo.lock` pins crate
resolution. The native-contract CI workflow executes the Rust and Python checks
on Windows, Linux and macOS and the Swift reference on macOS. These checks do
not qualify a Windows/Linux shell, a native sign-in path or a release artifact.
