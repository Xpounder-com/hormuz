# Owned native UI boundary (#344)

`hormuz-client-ui` statically links the existing shared desktop worker; it does
not add a session state machine or a second network runtime. ABI 1 is declared
in `clients/macos/Sources/CHormuzRust/include/HormuzRust.h` and consumed by
`HormuzRustBridge`. The gateway stays server-side. Opening the UI initializes
neither a relay, optimizer nor tokenizer.

## Ownership and threading

- `Ui`, `Bytes`, `Secret` and `Subscription` are distinct opaque owned types.
  Each allocation has exactly one matching free operation. Null free is safe;
  wrong-type pointers, double free, invalid buffers and racing free are caller
  errors. No borrowed byte pointer is exported.
- All Swift calls are serialized on one owned queue. Custody/browser callbacks
  run synchronously on the Rust worker and must be bounded, thread-safe and
  non-reentrant. A native wake callback must only enqueue; it must not call back
  into Rust or perform UI/custody work. JSON display delivery is marshalled to
  the main actor and emitted only when the complete projection changes.
- One subscription covers the app, not each view. Repeated subscribers are
  rejected. Unsubscribe waits for an in-progress wake and suppresses queued
  Swift main-actor delivery. A subscription can outlive the closed Rust core
  without retaining its worker or custody host.
- Cancellation invalidates only the UI operation generation. It cannot cancel
  a governed relay or undo server egress. Accepted sign-out still drains its
  durable revocation intent. Free closes subscriptions, cancels local work and
  joins the existing bounded worker before releasing the custody context. Swift
  performs that join off the main thread, including during deinitialization.

## Explicit Mac custody and compatibility

Rust uses the host callbacks supplied by the Swift executable, **not**
`NativeCredentialStore`. That host keeps the existing `KeychainSessionStore`,
record validation, private directory and `connection.lock`. Display JSON and
launch profiles contain no credential. Privileged custody copies use `Secret`,
never display `Bytes`; Rust-owned secret copies zeroize on drop. Swift resets
temporary `Data` copies where possible, but Swift strings, serializers and OS
copies are not covered by that erasure guarantee.

The original Foundation date epoch and manual profile defaults are retained,
as are `active`, `refreshPending` and `revocationPending`. Hosted records retain
`desktopManaged` and `desktopProfileVersion` through reads and rotations. Before
hosted dashboard access or credential handoff, the shared controller validates
the origin/client/organization/model/version-bound desktop profile. Changed or
future server policy fails closed. The C `connect` command exposes only shared
manual enrollment; it rejects hosted input rather than substituting the wrong
enrollment endpoint. The Mac app keeps its established Swift manual/hosted
sign-in, team join and revocation flows during this bounded migration.

Downgrading a hosted record to a pre-hosted app version is unsupported: older
decoders can discard its policy binding. Do not remove fields or pending state
to make a downgrade appear compatible. A supported current app must finish
revocation before a user deliberately reconfigures an older manual connection.

## Mac shell integration and limits

The app consumes shared dashboard snapshots, freshness and scheduling through
one bridge owner. Its two existing native surfaces provide aggregate visibility;
AppKit still owns screen selection, geometry, hover and focus. Public session,
display sleep/wake and event-driven network inputs gate polling. Screen-lock
dictionary/notification names are advisory undocumented macOS inputs; unknown
initial state blocks polling. Their actual lock/unlock delivery and native power
behavior still need platform qualification. Enabling optimization shows
"Checked on next client request" without starting an idle helper or tokenizer.

Launch and setting commands use the non-secret C boundary, while launcher
execution and atomic private settings persistence stay shell-owned. Panel close
does not affect launched clients. Quit freezes new admissions and offers Wait,
Cancel, or explicit Stop and Quit when leases remain. Only a pending user quit
has a bounded wait loop. Manual app replacement must finish the old app's quit
first; no resident updater or automatic active-client replacement is installed.

Build and packaging scripts always build this archive from their checked-out
source and verify the embedded ABI symbol; they cannot silently package a
legacy Swift-only executable. The protected signing job consumes the unsigned
source-matched executable and does not compile code with signing credentials.

## Local verification

From `clients/rust`:

```sh
cargo test --workspace --locked
cargo clippy --workspace --all-targets --locked -- -D warnings
cargo fmt --all -- --check
cargo build --locked --package hormuz-client-ui
```

From the repository root:

```sh
HORMUZ_RUST_UI_LIBRARY_DIR="$PWD/clients/rust/target/debug" swift test --package-path clients/macos
HORMUZ_RUST_UI_LIBRARY_DIR="$PWD/clients/rust/target/debug" swift test --package-path clients/macos --sanitize address --filter OwnershipTests
```

These fixtures actually link Rust through C into Swift, exercise repeated
subscription/cancellation/teardown and pending/hosted custody compatibility, and
avoid real Keychain/accounts. The address sanitizer instruments Swift/C, not
the ordinary Rust archive. Portable fixtures are not signed-app Keychain,
native interaction/multi-display/Spaces, footprint, clean-machine upgrade,
Windows/Linux support or release qualification.
