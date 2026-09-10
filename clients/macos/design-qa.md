# Hormuz integrated companion design QA

## Controls, brand, and landing-page follow-up

Outside-click dismissal: control and usage cards now close on clicks elsewhere,
including other apps, without hiding or immediately folding the widget. The
temporary hold clears on the next pointer visit or explicit visibility command;
the saved visibility preference is unchanged. Picker-menu clicks are exempt and
outside clicks are passed through to their intended destination. Live native QA
opened the hub, clicked the menu bar outside it, and confirmed that the hub was
gone while all three ring buttons remained visible after the fold delay. The
matching browser check confirmed `cardOpen=false` and `widgetFolded=false`.
Both builds passed.

Hover refinement: the gear artwork rests at 58% of its full size (about 27 points)
inside the unchanged 57-point approach/click target. It eases to full size over
280 ms on entry and returns over 360 ms on exit, without spring overshoot. Reduce
Motion skips the scaling animation. The website preview uses matching timing and
also expands on keyboard focus. Native and website builds passed after this edit.

- Replaced the idle quarter-circle with a visible gear within the existing 57-point
  hit zone. The entire rectangle accepts clicks. Its panel sits above the body's
  transparent curl so body reordering cannot intercept the overlapping target.
- Primary edge actions now retain an explicit high-contrast fill when the panel
  is inactive. Live UI clicking Connect to Hormuz opened the setup form; no sign-in
  or session revocation was submitted during this verification.
- Expired/revocation-pending states use attention indicators. Interrupted opacity
  transitions resume from their current alpha instead of jumping to full visibility.
- Header and menu artwork match the website's approved passage-H PNG bytes. The
  app icon uses its forest variant. `script/sync_brand_assets.py` reproduces these
  assets; local and release bundles both include them.
- Native tests: 32 passed, one opt-in isolated Keychain test skipped. Final build
  and ad-hoc signature verified. Layout audit: all eight checks passed, including
  100/125/150% sizes, folding, hiding, and reopening. Motion audit passed with the
  expired fixture (318-point body). Evidence: `docs/evidence/companion-controls/`.
- Native accessibility automation exposes one overlay window at a time; it did
  not complete a physical click on the separate gear window. The visible gear,
  target bounds, panel ordering, and shared control-center action were inspected.

The matching landing-page implementation is an isolated interactive illustration,
not a connected session. See `website/companion-qa.md`. Website source is updated
locally; no GitHub Pages publication was performed in this pass.

## Compact placeholders and visibility follow-up

Unavailable headline dashes and their reserved label rows are removed. At 100%,
the three-ring unavailable state is now 318 points tall versus 399 previously
(81 points, about 20%, saved). Supported live totals still appear beneath rings.
Tooltip anchors use the same per-reading geometry as the compact body.

Fold/unfold now fades the existing surface out over 80 ms before changing geometry,
then fades the new surface in over 200 ms. Detail cards, the controls card and gear
also fade in/out. The animation tasks cancel on reversal; scale updates retain
atomic native frame/host sizing. Reduce Motion shortens the opacity-only fades.

`docs/evidence/companion-motion/motion-audit.json` records actual window opacity
samples through the transition and verifies compact height and rapid fold/hide
reversal recovery. All passed. The compact rendered body is in
`compact-no-placeholders-body.png` beside that report. The existing test suite
remains at 32 passed, one opt-in Keychain test skipped, zero failures.

## Edge controls and live-scale follow-up — September 7, 2026

This section supersedes the earlier acceptance record below. Fixed-scale images
from the first pass did not prove that changing the setting live worked.

### Fixed findings

- P1: Combine's pre-change notifications resized panels from the previous scale.
  Updates now coalesce after state settles; native host bounds use the resulting
  AppKit frame, including pixel rounding. Old animation completions cannot restore
  an obsolete frame.
- P1: Routine connection and client actions required a separate large window.
  The lower gear and folded handle now open one compact, keyboard-capable edge
  card with Connection, Client, and Appearance pages. The window is optional.
- P2: Native segmented size controls stayed disproportionately small at 150%.
  The selector now uses proportional, accessible selected-state buttons.
- P2: Hover could replace a pinned detail, and fold timing could leave the body
  expanded. Pinning now changes on click; folding reschedules after detail dismissal.

### Current evidence

- `docs/evidence/companion-hub/live-layout-audit.json`: all eight checks passed.
  Live sequence: 100 → 125 → 150 → 125 → 100 percent, then fold, hide, reopen.
  Native body widths were 70, 88, 105, 88, 70 points. Host and panel bounds matched,
  the right edge stayed anchored, and the controls stayed inside the visible frame.
- `docs/evidence/companion-hub/live-scale-*-composite.png`: actual native frames
  during preference changes, inspected at 100% and 150%.
- `docs/evidence/companion-hub/hub-home-composite.png`: final home card capture.
- `docs/evidence/companion-hub/reference-vs-hormuz-overall.png`: opened side-by-side
  source and final hub. The resting notch retains the source silhouette, rings,
  black surface and green accent. The user-approved controls card adds labeled
  navigation and internal scrolling; it is intentionally larger than a usage tooltip
  only while open. No extra ring or resting-state surface was added.
- Native accessibility interactions verified: live scale selection, folded-handle
  activation, home/Connection/Appearance navigation, expired-session Reconnect and
  Sign out controls, text entry, advanced fields, internal scrolling, Escape/Back,
  and preservation of entered fields. Preview runs skip real session restoration.
- Final package: 33 tests executed, 32 passed, one opt-in Keychain test skipped,
  zero failures. Ad-hoc signing and `git diff --check` passed.

### Limits of this pass

The saved session was expired. Its state and controls were inspected, but a fresh
browser login, real revocation/reconnection, and client connector save were not
executed against the hosted gateway during this UI pass. Those actions reuse the
existing tested session and connector layers. Reconnect retires the old session
before enrollment and stops if revocation fails. External display disconnect,
1× hardware, and full VoiceOver navigation remain unexercised. This is local UI
acceptance, not hosted-pilot or distribution qualification.

## Previous integration record (historical)

**Findings**

- No actionable P0, P1, or P2 findings remain in the integrated native build.
- The source uses progress arcs backed by provider limits. Hormuz's current usage
  contract has no cap denominator, so the implementation intentionally uses neutral
  tracks with small green connection-status markers and exact raw values. This is
  a product-data constraint rather than unfinished styling.
- The Hormuz card is taller than the source because it includes identity,
  gateway-coverage disclosure, and the action that opens the control center. Width,
  radius, spacing rhythm, tail geometry, and right-edge silhouette retain the
  normalized source proportions.

**Comparison Target**

- Source visual truth:
  `docs/design/companion-reference/docs/design/frame-124-hover-tooltip.png`
- Rendered implementation:
  `docs/evidence/companion-live/connected-cost-final-v2-composite.png`
- State: connected synthetic presentation fixture, Cost selected and pinned,
  100% UI scale, dark appearance. The fixture changes presentation only; the
  normal launch path restores `ConnectionModel` and its Keychain-backed session.
- Native viewport: built-in 3024 × 1964 Retina display at 2× backing scale.
- Source pixels: 2000 × 2000. Implementation composite: 822 × 1022 pixels.
  There is no CSS viewport or browser device-scale factor for this native app.
- Density normalization: the comparison script scales the source crop by 0.752 so
  its 117-pixel ring and the implementation's 88-pixel @2x ring represent the same
  44-point target.

**Comparison Evidence**

- Full view:
  `docs/evidence/companion-live/reference-vs-hormuz-overall.png`.
- Focused card, tail, and first-ring region:
  `docs/evidence/companion-live/reference-vs-hormuz-detail.png`.
- Both files place the actual source and final integrated capture in the same image.
  Source wallpaper and provider logos are excluded from fidelity scoring because
  Hormuz has its own system symbols and must not imply provider-owned limits.

**Required Fidelity Surfaces**

- Fonts and typography: SF system type, semibold title/value hierarchy,
  monospaced numeric values, secondary gray labels, and line-height behavior match
  the native target. The final card has no clipped or unintended wrapped text.
- Spacing and layout: the continuous right-edge silhouette, 44-point rings,
  roughly 70-point body depth, 225.64-point card width, cell pitch, inverse joins,
  card radius, tail, and internal block rhythm match the pinned geometry.
- Colors and tokens: black surfaces, `#303030` neutral tracks, `#2D2D2D` dividers,
  white/gray type, and `#00FF88` verified-status markers preserve the source's
  contrast while separating status from utilization.
- Image and asset quality: the widget uses sharp native SF Symbols. The source
  wallpaper and provider logos are not product assets and are intentionally absent.
- Copy and content: Cost, Tokens, Requests, identity, gateway-only coverage,
  estimate qualification, and Open Hormuz action describe the real client contract.
- Icons and states: connected, no-session, expired, offline, selected, pinned,
  folded, hidden, and display-scale paths exist. The exact built bundle exposed
  Cost, Tokens, and Requests with status-aware accessibility values.
- Accessibility and resilience: every ring has a label, status, value, and pinning
  hint; the gear has an explicit control-center label; reduced motion removes
  springs and numeric rolling; tooltip placement clamps to the visible display.

**Primary Interactions Verified**

- Opened the exact `clients/macos/dist/Hormuz.app` bundle and inspected its native
  accessibility tree rather than another local copy with the same bundle ID.
- Verified the edge metrics in authentication-required and connected fixture states.
- Opened the control center, switched from Connection to Appearance, and observed
  working Visibility, UI scale, Display, and Show widget controls.
- Selected Fold when idle, verified the compact edge rendering, then activated its
  native `Show Hormuz usage` button and observed the three metric buttons reappear.
- Verified that Connection still exposes the original gateway, organization,
  client, model, browser sign-in, refresh, connector preview, and sign-out paths.
- Verified the menu and tooltip action wiring in the compiled app; session,
  connector, secure-store, and hover behavior are covered by the Swift test suite.

**Comparison History**

1. First integrated comparison found a P1 semantic mismatch: fixed green 88%
   sweeps looked like utilization despite the absence of verified denominators.
2. The ring renderer was changed to keep a neutral track and use a small semantic
   status marker when `usedFraction` is absent. True progress rendering remains for
   future verified limits.
3. The final source-versus-implementation comparison above confirms that the
   silhouette, scale, typography, tail, card hierarchy, and interaction affordance
   remain visually coherent after the fix.
4. A subsequent P1 accessibility pass found that gesture-only ring and folded
   controls exposed generic accessibility roles. They now use plain-styled native
   buttons. The exact packaged app exposed `Show Hormuz usage` as a button; invoking
   it expanded to actionable Cost, Tokens, and Requests buttons. The final-v2
   capture and normalized comparisons were produced after this change.

**Residual Test Gaps**

- Only the built-in 2× display was available for physical inspection. Pure tests
  cover scaling and visible-frame clamping, but 1× hardware and display disconnect
  reanchoring were not physically exercised.
- The isolated Keychain round-trip is opt-in and was skipped in this run. The
  broader in-memory session, revocation, refresh, and credential-file tests passed.

**Implementation Checklist**

- [x] Use the companion as the primary application surface.
- [x] Reuse the existing session, Keychain, gateway, and connector implementation.
- [x] Preserve the full connection workflow in an integrated control center.
- [x] Show exact supported metrics without invented percentages.
- [x] Compare source and final implementation in the same images.
- [x] Build, sign, launch, inspect, and run the full Swift test suite.

final result: passed
