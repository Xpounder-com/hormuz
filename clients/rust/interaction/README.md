# Shared companion interaction state

`hormuz-client-interaction` is the unpublished `1.5.0-dev.1` source foundation
for [#338](https://github.com/Xpounder-com/hormuz/issues/338). It is a pure Rust
reducer around the existing Swift companion behavior. It does not change the
shipping gateway or app version. The Windows development shell consumes its
visibility, pointer/focus and timer policy; metric cards and settings navigation
are not yet connected there. Swift production code remains independent.
The Windows shell dependency #331 and native interaction acceptance remain open.

## State and ownership

`Interaction::new(mode, visible)` starts with no card, pointer or keyboard focus.
A visible widget starts expanded in `always` mode and folded in `fold` mode.
The read-only snapshot explicitly represents:

- hidden, folded or expanded presentation and the visibility preference;
- the selected and pinned metric, independently of the pointer's hover target;
- the settings page (`home`, `connection`, `client`, `appearance`, `setup`, `review`);
- the semantic pointer region and observed keyboard focus region;
- the hold-open state after outside-click dismissal and pending timer tokens.

Only the reducer mutates its state. Snapshots are serializable inspection
projections with no deserialization path into the reducer. State contains no
credentials, gateway data, geometry, screen identifiers or arbitrary labels.
There are at most two timer slots (detail dismissal and fold); their eligibility
rules make them mutually exclusive. No event history or background task is retained.

The native shell owns rendering, animation/reduced-motion behavior, window
geometry, screen selection, actual visibility, native focus, tab traversal,
screen-reader labels/actions and hit testing. It maps native pointer observations
to semantic regions. An outside click means outside every interactive companion
surface; native popup menus must remain inside their owning card for this purpose.
The reducer never inspects screen coordinates or installs global input hooks.

## Event ordering and timer adapter

Call `dispatch_batch(now_ms, events)` from one UI executor, with a monotonic
millisecond clock. Group queued equal-time input and callback observations into
the same UI turn. Within each batch, input/native observations run first in
their original order, then timer callbacks in their original order. Render only
the final snapshot, then apply the effects in returned order. Already committed
batches retain their delivery order; the reducer cannot reorder future input
into an earlier turn. A clock regression rejects the entire batch.

`ScheduleTimer` carries an absolute monotonic deadline and an opaque token.
The shell schedules a one-shot timer and echoes **that token** in `TimerFired`;
it must not replace it with the latest token when a callback finally runs.
`CancelTimer` asks the shell to cancel the corresponding native timer. Logical
cancellation takes effect immediately even if native cancellation races delivery.

- Detail dismissal and folding each use the existing 250 ms delay.
- Entering the card, a new metric, or applicable keyboard focus cancels detail
  dismissal. Entering the widget/settings handle cancels folding.
- A stale, cancelled, unknown or duplicate token cannot dismiss current UI.
- An early callback does not consume its timer. The native adapter must retain
  or rearm the same token for its original deadline; an early wake is not proof
  that the deadline elapsed. The reducer does not run or repair native timers.
- A completed detail dismissal starts a fresh fold delay when the pointer and
  focus are outside, avoiding a widget stuck expanded after hover travel.
- Unrelated observations do not continually reset a dismissal deadline.
- Clock arithmetic or token exhaustion rejects a batch atomically, preserving
  state, time tracking and tokens. Tokens never wrap or reset after hide/reopen.

Effects contain only timer actions and a native focus request. Focus requests
are coalesced to the last still-visible destination in the completed batch, so
opening settings and then hiding cannot leave a stale activation request.
`RequestFocus` does not claim focus was obtained: only `FocusChanged` updates the
observed focus state. A settings card stays open during child-to-child keyboard
focus transitions, including an intermediate observation of no focus.

## Behavior retained from Swift

Hover selects an unpinned metric. Hovering another metric cannot replace a pin;
explicit activation can. The pointer can leave a metric, cross the gap and enter
its card before dismissal. A late exit for the previous region cannot overwrite
the newly entered region. An outside click dismisses either card and keeps the
widget expanded until the next pointer visit or visibility command.

Unpinning cancels dismissal and keeps the selected card until a later leave of
that metric or its card, including when keyboard/menu/accessibility activation
occurs with the pointer already outside. A cancelled callback in the same batch
cannot override that activation. Entering a metric resumes ordinary hover policy.

Escape/back follows `review → client → home → closed` and
`setup → connection → home → closed`. Escape without settings dismisses details.
Closing a focused card requests native focus back to the widget. Outside click
does not request focus back from another application.

`Hide` clears details, pin, settings, pointer, observed focus, hold-open state and
pending timers. Passive events cannot reopen a hidden widget. `Reopen`,
`ShowAndPin` and `OpenSettings` are explicit activation paths and work while
hidden. `Reopen` expands the widget and replaces an old fold deadline. As with
Swift `showWidget`, it preserves an already-open card, including an unpinned
card's still-current dismissal timer; it does not silently turn a hover into a
pin. `ShowAndPin` and `OpenSettings` replace the card and cancel its old dismissal.

`ToggleFold` is the explicit native Fold/Expand action. It closes content,
cancels its timers and folds even while a visible widget button retains keyboard
focus. A content-focus transfer requests widget focus without fabricating an
observation. Expanding switches to always-visible mode. Pointer re-entry or
`Reopen` ends an explicit collapse; after reopening, the ordinary fold delay
applies only while both pointer and keyboard focus are outside. Hidden controls
cannot reopen the widget with this toggle. No preference is persisted by it.

The #339 lifecycle adapter can deliver `Reopen`/`Hide` while retaining ownership
of single-instance IPC, activation, helpers and shutdown. The reducer has no
process or session dependency. Keyboard and accessibility activation use the
same explicit actions as pointer/menu activation; each shell must make those
actions reachable and confirm native focus, rather than implementing a parallel
interaction policy.

## Shared evidence and limits

[`interactions.json`](../../../../tests/fixtures/native_client/v1/interactions.json)
contains 16 synthetic traces with 130 steps, full expected snapshots and ordered
effects. The reference revision and four Swift source paths are recorded in the
fixture. Rust executes every trace, plus focused tests for equal-time ordering,
failed-batch rollback, focus requests, stale exits and 13,824 adversarial
three-event sequences with interleaved timer delivery.

Seven traces also run through the **existing** Swift `HoverCoordinator` and
`EdgeHubNavigation`, in `SharedContractTests`. They compare selected/pinned
metric, pointer-inside-tooltip and settings page after each step. For due
dismissal, the test awaits the actual Swift publication using a shortened timer;
for cancelled callbacks it gives the existing task an opportunity to run. The
test does not inject Rust callback tokens into Swift or assert exact wall-clock
timing. No Swift production source changes are required.

The remaining focus, visibility and callback-order traces are Rust policy tests.
They do not prove AppKit/Win32 hover geometry, activation, screen-reader access,
focus transfer, native dismissal timing or visual equivalence. #338 requires
native hover/focus/outside-click/close-reopen acceptance, #331 integration and
protected-main evidence before closure. Linux support, footprint measurements
and release qualification are separate gates.

Fixture schema v1 versions the test corpus, not public IPC, an HTTP API or
persisted state. Event serde support is for local fixture interchange, not an
untrusted-command decoder. Existing expectations require an explicit
compatibility decision before changing. The Windows integration is a development
checkpoint; rollback restores the previous shell event adapter without a
user-data migration.

## Verification

From `clients/rust`:

```sh
cargo fmt --all -- --check
cargo test --workspace --locked
cargo clippy --workspace --all-targets --locked -- -D warnings
```

From the repository root on macOS:

```sh
swift test --package-path clients/macos --filter SharedContractTests
swift test --package-path clients/macos --filter CompanionTests
```

The existing native-contract workflow runs the new workspace crate on Linux,
Windows and macOS and the unchanged Swift test filter on macOS. CI and native
acceptance must be evaluated at the exact proposed commit.
