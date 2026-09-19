# Native session transactions

This unpublished `1.5.0-dev.1` library implements the shared session work in
[#335](https://github.com/Xpounder-com/hormuz/issues/335). It is not linked into
the shipping Mac app or the synthetic Windows shell. The gateway/app remains
v1.2.0; connected UI acceptance and distribution remain #340 and #344–346.

## Worker and cancellation contract

Construct a `SessionController` with the platform `PrivateDirectory` and
`NativeCredentialStore`, `NativeTransport`, and `SystemClock`. All operations
are synchronous **worker** operations, including native custody and browser
dispatch. A shell must schedule them off its UI thread and marshal the bounded
`BrowserOpener` call to its native browser-opening interface. No browser command
line is constructed by this crate. Construction starts no worker, timer,
network runtime, relay, optimizer, or tokenizer. `NativeTransport` waits on the
existing process-wide transport runtime; it does not create another one.

Give each operation its own `Operation` and retain a clone for cancellation.
Cancellation aborts an active transport task and is checked at lock/poll waits
and credential commits. Do not detach/terminate the worker to cancel a UI flow:
let it finish bounded cleanup. Lock waiting is limited to 10 seconds; individual
requests retain the transport's 15-second deadline. Enrollment polling uses a
monotonic deadline capped at 15 minutes (600-second server enrollments remain
valid). Browser dispatch itself must return promptly rather than waiting for
the person to finish sign-in. A request already received cannot be undone by
cancellation. Cleanup gets one independent uncancelled request, never a refresh
retry. The library starts no permanent task or polling loop after the operation.

## Custody, compatibility and failure behavior

- Hold the fixed `connection.lock` through read, intent, request and commit.
  Every cooperating app/helper must use the same application-data root.
- Preserve the Swift `SessionRecord` keys, profile defaults, Foundation date
  epoch, and `active`, `refreshPending`, `revocationPending` spellings. There is
  no new record envelope or migration. Gateway replies separately decode
  snake-case keys and RFC3339 dates. Existing Swift can read Rust records; Rust
  validates records before use. Swift's permissive noncanonical URL forms may
  still be rejected by the existing shared Rust profile validation contract.
- Preflight the complete encoded profile plus fixed-length credential fields,
  the longest state and conservative date space against the native record limit
  before enrollment/refresh. Oversize Windows records fail before server
  mutation; no truncation, split store, or plaintext fallback is permitted.
- Validate the exact origin-bound enrollment URL, ID, interval and expiry
  before browser dispatch. Preserve HTTP 409 as a pending redemption response.
  Redeemed credentials are first quarantined as `revocationPending`; only a
  validated human/session/organization/client identity permits an active
  commit. This deliberately strengthens the Swift crash window during identity
  verification without changing its credential schema. Restart can only revoke
  the quarantined record, never use it.
- Persist `refreshPending` before refresh. Both tokens must rotate and absolute
  session expiry must remain unchanged. Any uncertain/failed response or failed
  replacement leaves recovery state and forbids replay after restart. Even a
  transport `NotSent` failure is conservatively not retried automatically.
- Sign-out disables new credential handoffs in that controller immediately,
  then saves `revocationPending` before contacting the record's trusted origin.
  Removed/edited non-secret profiles cannot redirect logout. Failed revocation
  or deletion retains a non-usable record for idempotent logout retry. If the
  store rejects the pending write, no network request is made and that controller
  stays disabled; durable suspension across other processes cannot be promised
  when the OS refuses persistence. Report the storage failure. Previously handed
  out credential copies cannot be recalled locally; remote revocation and the
  launch/lifecycle integration retain their own responsibilities.
- Credential handoff is an explicit redacted `AccessCredential`, never a display
  model. Owned secret strings and record/request/reply buffers are zeroized on
  drop. Transport, serializer and OS copies are outside that erasure guarantee.
  Errors contain fixed codes only, never raw server/OS messages or response data.

The Python CLI has a separate snake-case v1 store and no native pending-state
field. Shared fixtures verify its token codec and 60-second refresh/absolute
expiry decisions, and explicitly exclude native pending-state parity. Neither
Python's store nor any shipping runtime is migrated by this implementation.

## Verification

From `clients/rust`, run `cargo test --workspace --locked`,
`cargo clippy --workspace --all-targets --locked -- -D warnings`, and
`cargo fmt --all -- --check`. The existing three-OS workflow includes this crate.
Run `python -m unittest -v tests.test_native_client_contracts` and, on Mac,
`swift test --package-path clients/macos --filter SharedContractTests`.

Tests drive the real controller through shared transition vectors, HTTP 409,
identity denial, browser URL rejection, expiry/size bounds, interrupted refresh,
crash injection after a saved intent, save/delete failures, cancellation after
redemption/commit, and logout recovery. A native kernel lock serializes competing
refresh workers. A real loopback server verifies the transport adapter's status
and active cancellation behavior. The platform workspace retains native
two-process lock/crash and isolated Keychain/Credential Manager tests. Session
crash vectors use an in-memory synthetic store; they are not signed-app,
power-loss, native browser, or connected-companion acceptance.

## Authoritative usage snapshots (#336)

`refresh_snapshot(profile, operation)` holds the same session transaction while
fetching identity and personal usage with one validated credential. The snapshot
contains immutable identity, `UsageReading`, an explicit `current_actor` scope,
and the last successful check time. Both responses and the current saved profile
must pass validation before that time advances. Gateway vocabulary remains
`configured_rate_card_estimate`, `direct_gateway_request` and
`gateway_captured_requests_only`; no local
relay traffic or invented organization-wide total enters the reading. The exact
native `u64` token sum handles two valid i64 counters without signed overflow.

`snapshot()` returns an immutable `Arc<UsageSnapshot>`. A native adapter drains
`take_snapshot_change()` after worker operations and local sign-out. Its single
pending slot coalesces intermediate changes, and equivalent states do not emit
again. There is no callback, timer, permanent subscriber worker or redraw loop.
An updated successful timestamp is a real state change even when counts are the
same. Existing holders of an older immutable snapshot retain that value; the UI
must process the next change to update what it displays.

| Result | Display state and retained data |
| --- | --- |
| First load/offline with no successful response | `offline`, absent identity/usage/time |
| Valid measured zero | `current`, real zero values and successful time |
| Connection failure after success | `offline`, last valid same-session data/time |
| Malformed, redirected or oversized response after success | `stale`, last valid same-session data/time |
| Authentication loss, unsafe storage or identity mismatch | `needsAuthentication`, cleared display identity/usage/time |
| Credential handoff cancelled before changing the session | Prior snapshot and state retained; no sign-in prompt |
| Sign-out or different profile | Immediately cleared; late earlier work is discarded |

An internal connection/attempt ticket rejects completions from another profile,
sign-out, older refresh or another controller. A non-display, zeroizing copy of
the current refresh token binds the cache to the authoritative credential
record. A replacement by another helper clears cached identity before the first
fallible request; a known local rotation updates that binding. Actor, team,
organization or session changes under the same credential reject the response
and remain rejected until the connection or credential changes. Tokens and the
saved profile are never members of the serialized display snapshot.
Cancellation after a durable refresh intent still clears the snapshot because
the interrupted session requires recovery, even when the error is cancellation.

Shared `snapshots.json` vectors distinguish zero/missing, offline/stale retention,
repeated equivalent errors, authentication loss and recovery. Additional tests
cover late completions, credential/identity changes, exact integer limits,
credential exclusion and a real concurrent sign-out during a blocked usage
response. Swift and Python verify the shared inputs against their existing
native/gateway validators. Age-based staleness, refresh coalescing, sleep/network
events and relay-completion scheduling remain #337; callers may request a fresh
snapshot without synthesizing accounting or creating another polling loop.
