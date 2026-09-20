# Native client compatibility fixtures (v1)

These are the contract foundations for [#330](https://github.com/Xpounder-com/hormuz/issues/330),
targeting v1.4.0. The first shared file is
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

## Profiles, preferences and display state

The second slice records source revision `68b0b978fabac90f0bc7c33228a5bd3c68c681ab`
and adds these portable corpora. Fixtures contain synthetic values only.

| File | Ownership and reference | Expected behavior |
| --- | --- | --- |
| `profiles.json` (49 cases) | Swift `ConnectionProfile`; Python `validate_session_gateway` owns only its CLI gateway string | Native validation, normalization, legacy defaults, round trips, explicit CLI/native differences |
| `context-settings.json` (20 cases) | Swift `ContextOptimizationSettings`; Python `ContextPreferenceStore` | Missing means Off; only the two canonical schema-v1 byte strings are valid |
| `status.json` | Swift `SessionState`, `ConnectionStatus`, `CompanionReadingStatus`, `ContextOptimizationStatus` | Pending sessions remain present; freshness and optimization codes retain their display meaning |
| `errors.json` (22 cases) | Swift `ClientError` | Fixed messages and codes; Rust uses a platform-neutral credential-store name instead of Keychain |

Python has no native profile or UI status model. Its consumer checks the shared
gateway inputs against the existing CLI expectations, the setting bytes against
the relay parser, and provenance/error-catalog drift. It does not impersonate a
Swift state implementation. `parse_context_preference` is extracted from the
existing Python file reader so format checks can run on every CI OS. The file
reader retains its ownership, mode, regular-file and symlink checks.

### Field ownership and bounds

- A connection profile owns a UUID, gateway origin, organization, optional opaque
  issuer identifier, supported AI client, approved model alias, explicit loopback
  opt-in and setup kind. There are no credential members. UUID keys are lowercase;
  persisted UUID text follows Swift's uppercase form. Organization is nonempty
  and at most 200 UTF-8 bytes; issuer/gateway are at most 2048 bytes. Text rejects
  control/format characters and outer whitespace. Model aliases are 1–128 ASCII
  bytes matching `[A-Za-z0-9][A-Za-z0-9._:-]*`.
- `issuer` may be missing, null or empty; all normalize to absent. Only a missing
  `setup` defaults to `custom`; null/unknown setups and clients fail. The other
  profile members are required. Unknown members are dropped, never copied into
  output. Duplicate keys are outside the cross-language compatibility claim;
  Rust rejects duplicate known fields. Profiles are bounded by 128 KiB.
- Gateways require an HTTP(S) origin with no userinfo, query, fragment or path
  beyond one optional trailing slash. An explicit port is 1–65535 and is retained,
  including a default port. HTTP requires opt-in and the literal `127.0.0.1`,
  `localhost` or `[::1]` host. Pilot setup is Codex over HTTPS with
  `openai-primary`/`openai-secondary` and no loopback opt-in.
- Rust uses the maintained [`url` parser](https://docs.rs/url/2.5.8/url/) and rejects
  any rewrite of the input host beyond ASCII case. This intentionally narrows
  Foundation: shorthand numeric addresses, percent-encoded hostnames, noncanonical
  IPv6 and Unicode host spellings must be entered in canonical ASCII/IDNA form.
  A parser rewrite can never turn an unapproved HTTP host into allowed loopback.
  Unicode-category validation uses a pinned library; the corpus does not claim
  identical classification of every newly assigned character across OS versions.
- Context preferences are non-secret and at most 4096 bytes. The complete valid
  contents are `{"enabled":false,"schema_version":1}` and
  `{"enabled":true,"schema_version":1}`. Invalid bytes, duplicate/extra keys,
  coercions, unsupported versions and noncanonical encodings fail with the fixed
  settings error. Invalid is distinct from a missing file; no reader silently
  repairs a file or treats invalid content as enabled. Storage adapters must
  bound reads too; this byte parser does not establish filesystem safety.
- `ConnectionStatus` is a credential-free display projection. Its optional
  `SessionState` retains `active`, `refreshPending` and `revocationPending`.
  `has_session` means a record exists, **not authorization to use credentials**.
  Rust requires a profile for a session and disallows an expiry without a
  session. Optional expiry/check times are finite, nonnegative Unix seconds;
  native bridges own conversion to their platform date types.
- `UsageReading` pairs an optional already-validated personal usage value with
  its original check time. Usage and time must appear together; `current` requires
  both. `stale`, `offline` and `needsAuthentication` can retain that pair or have
  neither. Absent usage serializes as null, never fabricated zero totals. The
  new constructor checks are Rust invariants; they do not change current Swift
  runtime behavior. Display projections serialize for inspection but cannot be
  deserialized around the validated constructors.
- Session, reading, optimization and error codes reject unknown variants. Error
  variants have no arbitrary string payload; public diagnostics cannot echo an
  input body. Identity/usage retain the gateway-owned meaning and limits above.

The Mac profile format has no schema-version field today. This slice preserves
that legacy shape; it does not silently add a version to existing app files.
The setting's existing schema version remains 1. The fixture corpus version is
not a new HTTP or durable-state schema. Future durable Rust state needs an
explicit versioned envelope and migration plan before an adapter writes it.

Session transitions/credential custody (#333/#335), transport (#334), freshness
thresholds and response ordering (#336), scheduling (#337), interaction reducers
(#338) and OS integration remain in their dependent issues. These contracts do
not start sign-in, refresh, polling, relay, tokenizers or a native shell. The Rust
library is still unlinked from the shipping Mac app. Rollback needs no user-data
migration; the Python parser extraction preserves the existing file contract.

## Run the reference checks

From the repository root:

```sh
python3 -m unittest -v tests.test_native_client_contracts tests.test_contracts tests.test_compaction_runtime
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

The separate unpublished [platform services library](../rust/platform/README.md)
implements #333's native storage and coordination primitives for the planned
v1.5.0 milestone. It uses the existing Mac credential identity and supplies
native Windows credential/ACL adapters; session orchestration remains #335.
## Session compatibility

`sessions.json` adds the existing Swift credential record codec and executable
credential-use transitions for #335. Swift and Rust round-trip Foundation dates
and all three pending-state spellings. Python consumes only the explicitly
marked active-session expiry/refresh vectors: its separate CLI record has no
native pending-state field. The session controller's failure/crash tests live in
`clients/rust/session`; this fixture does not migrate the shipping app.

`snapshots.json` supplies gateway-valid identity/usage examples and ordered
freshness/change-delivery vectors for #336. Rust executes the snapshot transitions;
Swift/Python verify the inputs through their existing validators. New immutable
native snapshots are in-memory display projections, not a new gateway wire or
durable settings schema. Zero remains a measured value and missing remains absent.

## Companion interaction traces

`interactions.json` adds 15 synthetic event traces (122 steps) for #338. The
unpublished [interaction library](../rust/interaction/README.md) checks every
snapshot and timer/focus effect. Six marked traces also compare the existing
Swift `HoverCoordinator` and `EdgeHubNavigation` projections without changing
shipping Swift behavior. That comparison covers selected/pinned metric,
pointer-inside-tooltip and settings page; it does not establish native window,
visibility, OS focus, keyboard or screen-reader acceptance. Callback ordering
and the broader focus policy are Rust foundation checks. #331 integration and
#338 native acceptance remain open; the fixture is not a new durable or IPC schema.
