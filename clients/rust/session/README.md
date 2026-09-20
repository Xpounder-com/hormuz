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
native/gateway validators. The shared scheduler below adds age-based staleness,
refresh coalescing and lifecycle inputs without synthesizing accounting.

## Central dashboard scheduler (#337 source checkpoint)

The controller owns one constant-size scheduler, one outstanding dashboard job
and the existing one-slot snapshot-change delivery. Construction still creates
no timer, event subscription, thread, async runtime, relay or render loop.
All screens share this controller. Do not call the immediate `refresh_snapshot`
transaction from individual views; route their dashboard polling through this
scheduler. The Python gateway and existing native apps are unchanged.

| Policy | Initial hypothesis |
| --- | --- |
| Visible summary refresh | 45 seconds |
| Visible detail refresh | 7 seconds |
| Current reading becomes stale | 60 seconds after last whole valid response |
| Local-request completion debounce | 2 seconds quiet, at most 10 seconds during a continuous burst |
| Failed-request retry | 5, 10, 20, 40, 80, 160, then at most 300 seconds |

`RefreshPolicy::new` accepts tunable second-based intervals from 1 to 86400,
rejecting zero, inverted summary/detail and inverted retry ranges. These defaults
start within #337's 30–60/5–10-second measurement ranges. They are not a measured
power saving or release budget. Failures retain exponential backoff across view
changes and relay-completion events; a coalesced network-change hint can bring
forward one retry. Only successful refresh resets the failure count.

The future native adapter must follow this event-driven ownership contract:

1. Restore/verify the session on a bounded worker, then call
   `set_dashboard_profile(Some(profile))`. Call it again after explicit session
   recovery; authentication failures disarm automatic refresh. Profile changes
   clear the display and invalidate old work immediately. `sign_out` also
   deactivates the scheduler before waiting for custody.
2. Feed the aggregate `Hidden`, `Summary` or `Detail` visibility through
   `set_dashboard_visibility`. Feed OS lifecycle events through
   `dashboard_lifecycle`. Sleep and session lock are independent gates; both
   must clear before polling resumes. Duplicate events have no polling effect.
3. After each event, alarm, and worker completion, call
   `take_dashboard_refresh()`. Move a returned non-cloneable job to the existing
   bounded worker pool and call `run_dashboard_refresh(job)`. Until that job
   completes or is dropped, no other dashboard job can be taken. A queued job
   that reaches its worker after suspension does no network work.
4. Calculate `next_dashboard_wakeup()`, then drain `take_snapshot_change()` on
   the UI thread and redraw only for an emitted change. Replace the shell's
   **single** alarm with that delay. `None` means disarm; do not poll the API
   repeatedly. A zero delay means work is due. An alarm or deadline calculation
   can only age the snapshot, without creating a network request; always drain
   changes and rearm after processing.
5. Feed `local_request_completed()` as a content-free signal. It retains one
   coalesced pending refresh, including completions during an outstanding job.
   No relay count, cost, token value, prompt or response enters the snapshot.

Hidden, locked, sleeping and quit states return no wake-up deadline and dispatch
no new polls. They do not cancel a transaction already running: it may have saved
a credential-refresh intent and must finish its bounded commit or recovery.
An explicit `job.cancellation()` handle belongs only to that dashboard operation.
Cancellation after intent still requires session recovery; no automatic replay.
Relay operations keep separate cancellation handles and lifetimes. Dropping a
job before dispatch releases its slot with a retry delay; a worker must always
report completion so the shell can rearm its alarm. Quit is terminal for that
controller; helper shutdown remains the lifecycle owner's responsibility.

Reopening or resuming with current data does not itself fetch; it schedules the
next visible interval. Stale/missing data can fetch immediately, subject to any
existing failure backoff. A pending relay-completion signal is an independent
refresh reason. While suspended, age is reconciled on the next visible event
without timer wake-ups. A current reading ages using the greater of monotonic
and wall-clock elapsed time. Backward/invalid wall time or backward monotonic
time expires it conservatively. It never becomes current again without a valid
response, and age/failure never changes its successful timestamp or scope.

Run `cargo test -p hormuz-client-session tests::scheduler --locked` for the
deterministic scheduling traces and synthetic concurrency tests, in addition to
the workspace verification above. Coverage includes duplicate views/events,
overlapping sleep/lock gates, stale-only reopening, bounded debounce, capped
backoff, network recovery, wall/monotonic clock changes, dropped/foreign jobs,
profile/sign-out invalidation, persisted-refresh completion under suspension,
and explicit cancellation after intent. A real loopback request proves separate
operation cancellation; it is a buffered synthetic request, not a streaming
relay or native-shell acceptance test.

**#337 remains open.** The shipping Mac app and draft Windows shell do not yet
consume this Rust scheduler or its lifecycle inputs. Before closure, link native
shell wiring and measured release-build visible-idle, hidden, locked, sleeping,
resume/network and active-relay traces, with exact commit, binary hash, OS,
duration, request/wake-up/redraw counts and platform measurement commands. Verify
one native alarm, no per-screen timers, no lock/sleep polling, stale-only reopen,
and uninterrupted AI traffic on each claimed shell. Injected events and portable
test passes do not establish OS event delivery, actual sleep behavior, native
power use, UI responsiveness or an improved footprint. Connected Windows
integration/acceptance remains #339–340; the Mac Rust bridge/integration remains
#344–345. The product stays v1.2.0 and this library stays unpublished
`1.5.0-dev.1`.
