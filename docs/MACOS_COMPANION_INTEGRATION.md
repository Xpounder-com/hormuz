# Hormuz macOS companion integration

Status: implemented locally on `mehrdad/hormuz-companion-ui` from repository
`main` at `5815081`.

## Product behavior

Hormuz now launches as a menu-bar accessory with a compact right-edge surface.
The companion is the default product face. It renders three gateway-backed values:

- estimated cost from the team's configured rate card;
- input plus output tokens recorded this month;
- gateway-captured requests, with denied and rate-limited outcomes in details.

Hover selects a detail card, click pins it, outside click dismisses it, and Fold
when idle reduces the widget to a narrow edge handle. The lower gear opens the
compact edge controls. A menu-bar item can show or hide the widget, open any metric,
refresh status, open client setup, sign out, or quit.

The edge card has Connection, Client, and Appearance pages. It includes browser
sign-in, advanced connection fields, session/Keychain details, refresh, reconnect,
sign-out retry, and inline launcher preview/save/copy. Editing keeps the card open;
Back/Escape preserves connection fields. The expanded control center remains an
optional view, reached with the bottom-right expand button. First launch without
a stored session opens the edge Connection page.

Reconnect uses the existing revocation path before enrollment. If revocation
fails, the retry record stays in Keychain and no second session is created.

## Live sizing

`SettledPanelUpdate` coalesces pre-change Combine notifications onto the next main
queue turn, so AppKit reads the same scale that SwiftUI renders. Window updates
use the resulting native frame for host sizing, including pixel rounding. Obsolete
resize animations can no longer restore a previous frame. Scale changes resize
the entire edge surface and keep it anchored to the selected display's right edge.

The controls card has a 336 × 480 point panel at 100%, including its outer padding;
it scales with the widget and its height is bounded by the display. Longer forms
scroll inside it. The resting three-ring body remains approximately 70 points wide.

## Runtime ownership

`ConnectionModel` remains the sole owner of the live client state. It delegates to
the existing `SessionController`, `PrivateDirectory`, `KeychainSessionStore`,
`HTTPGatewayTransport`, and `ConnectorPlan` implementations. The companion reads a
derived `CompanionSnapshot`; it does not access Keychain or the gateway itself.

The `Hormuz` executable still supports the credential-helper command path before
starting AppKit. Arguments beginning with `--companion-` are the only nonempty
arguments routed to the GUI. Connector launchers and protected pilot commands keep
their existing command grammar and implementation.

## Honest metric behavior

`GET /v1/gateway/usage` provides exact current-month counts and estimated cost but
no authorized cap denominator. The live companion uses a neutral circular track
and a small semantic status marker. A colored progress sweep is rendered only when
`usedFraction` is backed by a future verified contract. The control center explains
this boundary directly.

Session state takes precedence over cached dashboard data. An expired or
refresh-pending session changes the markers and card message to authentication
required while retaining the last checked raw usage as historical context. A
failed refresh cannot leave a current green state.

## Local verification

```sh
./clients/macos/script/build_and_run.sh --verify
```

The integrated Swift package has 33 tests: the existing authentication, secure
storage, transport, and connector checks plus companion geometry, threshold,
clamping, pinning, hover-dismissal, navigation, and settled-scale regression coverage. The opt-in isolated Keychain
round-trip remains skipped unless `HORMUZ_TEST_KEYCHAIN=1` is set.

Visual evidence and normalized source comparisons are in
`docs/evidence/companion-hub/` (the previous integration is in
`docs/evidence/companion-live/`). The source visual remains the pinned Codenotch
frame and MIT attribution under `docs/design/companion-reference/`.
