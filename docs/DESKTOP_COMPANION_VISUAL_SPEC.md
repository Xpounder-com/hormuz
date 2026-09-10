# Hormuz companion: visual execution specification

Version 2 · 2026-09-07 · first handoff = native visual demo, no new backend APIs.

## Priority and scope of this handoff

The primary deliverable is the visual effect shown by Codenotch on
<https://hivinz.com>: a black shape attached to the screen's right edge, vertically
stacked colored usage rings, a compact hover card with a pointed tail, and fluid
transitions. A conventional menu-bar popover alone does not satisfy this task.

Implement the decisions below without redesigning them. Deliver a launchable
macOS app in the Hormuz repository. Use fixture data first. Backend/API limitations
must not prevent the visual demo from being completed. Do not implement the new
budget or recent-request APIs in this handoff.

This specification overrides visual scope, order, and acceptance criteria in
[DESKTOP_COMPANION_PLAN.md](DESKTOP_COMPANION_PLAN.md). That document remains the
backlog for later live integration and distribution. The prior 2–3 day estimate
is a prototype timebox, not a promise to finish this more demanding fidelity work.

## Fixed source of truth

Reference source: <https://github.com/vinzdg/codenotch>, commit
`743601acd69e701131602b88082fcaeee0c2e88b`, inspected 2026-09-07.
The MIT notice and selected design/source files are preserved in
[design/companion-reference](design/companion-reference/README.md).

Open these images before coding:

- [Primary hover frame](design/companion-reference/docs/design/frame-124-hover-tooltip.png)
- [Detail frame](design/companion-reference/docs/design/frame-125-detail.png)

These are reference images, not Hormuz mockups or shipped application assets.
The wallpaper, computer bezel, web-page card, and provider branding are reference
context; do not recreate them as the application. Reproduce the actual floating
widget over the user's desktop. Use the geometry and motion files for measurements.

Precedence: this spec's explicit Hormuz behavior/content decisions → pinned source
constants for geometry, color and motion → reference frames for appearance. The
upstream historical prose has different colors/thresholds from its code; use the
code values listed here. Do not follow a changing upstream branch for design decisions.

## Application structure

Root: `apps/macos/HormuzCompanion/` within the standalone Hormuz repository.
Keep it separate from the parent MemoWiki Swift package.

Minimum macOS 14, SwiftUI content, narrow AppKit host for borderless nonactivating
panels. Use macOS 14-compatible APIs or explicit availability guards. One SwiftPM
package with an executable target and a testable core target. No third-party
runtime dependencies. Create an actual `.app` bundling script for local execution;
a SwiftUI preview or browser facsimile does not complete this task.

Required files/components (equivalent grouping is acceptable, responsibilities are fixed):

| Component | Responsibility |
| --- | --- |
| `CompanionApp.swift` | Accessory app lifecycle, menu-bar recovery menu, demo flag. |
| `EdgePanelController.swift` | Display anchoring, transparent panel lifecycle, safe pointer hit regions. |
| `SideNotchShape.swift` | Continuous black silhouette with inverse top/bottom joins to the edge. |
| `CompanionLayout.swift`, `CompanionPalette.swift`, `CompanionMotion.swift` | All visual constants in one place; no scattered magic values. |
| `UsageRingCell.swift` | Track, arc, white metric glyph, percentage, stale/unavailable state. |
| `UsageTooltip.swift` | Card, tail, title, two metric blocks, demo/source disclosure. |
| `SettingsHandle.swift`, `SettingsView.swift` | Curved resting arc to gear transition; small working settings panel. |
| `CompanionSnapshot.swift`, `DemoSnapshotSource.swift` | Typed UI state independent of networking; deterministic scenarios. |
| `HoverCoordinator.swift` | One cancellable dismissal timer, selected metric, hover/pinned states. |
| `CompanionCoreTests` | Geometry, hover cancellation, value/state mapping. |
| `tools/macos/build-companion.sh` | Reproducible local app bundle build; no signing secret required for local demo. |

The copied reference Swift files are documentation, not a drop-in library. Some
refer to upstream types not included here. Adapt the needed right-edge geometry
and constants, preserving the MIT attribution for copied/adapted source. Do not
import upstream provider adapters, session monitors, credential discovery, updater,
or product identity.

## Geometry: use these values, do not eyeball

All values are macOS logical points. Canonical conversion from the 2000 × 2000
primary design frame: `p(x) = x * 44 / 117`. Keep exact arithmetic in code; rounded
values below are for reading. This is not the display backing scale.

| Property | Canonical value | Approximate points |
| --- | --- | --- |
| Side body depth | `p(186)` | 69.95 |
| Inverse end-flare radius | `p(103)` | 38.74 |
| Body outside corner radius | `p(78.8)` | 29.63 |
| Top / bottom body padding | `p(69.5)` / `p(50.1)` | 26.14 / 18.84 |
| Between previous label bottom and next ring top | `p(83.5)` | 31.40 |
| Ring diameter | `p(117)` | 44.00 |
| Track / foreground arc stroke | `p(15.5)` / `p(8)` | 5.83 / 3.01 |
| Center glyph | `p(46)` | 17.30 |
| Ring-to-label gap | `p(26.9)` | 10.12 |
| Hover card width / radius / padding | `p(600)` / `p(49.5)` / `p(32)` | 225.64 / 18.62 / 12.03 |
| Tail length / base height / gap from tip to body | `p(75)` / `p(87)` / `p(28)` | 28.21 / 32.72 / 10.53 |
| Progress bar height | `p(10.5)` | 3.95 |
| Header glyph-to-title / header-to-first-block | `p(17)` / `p(21)` | 6.39 / 7.90 |
| Label-to-bar / bar-to-used-text / block spacing | `p(16.8)` / `p(17.8)` / `p(20)` | 6.32 / 6.69 / 7.52 |
| Folded handle width / height / hot-zone width | `p(26)` / `p(210)` / `p(90)` | 9.78 / 78.97 / 33.85 |
| Settings orb diameter / glyph / hit-region diameter | `p(124)` / `p(56)` / `p(152)` | 46.63 / 21.06 / 57.16 |
| Settings arc stroke / inset from flare | `p(18)` / `p(27)` | 6.77 / 10.15 |

Percent font: SF system semibold, `p(27)/0.714` (~14.22 pt), monospaced digits.
Card title: SF system semibold, `p(26)/0.714` (~13.69 pt).
Card body: SF system regular, `p(18)/0.714` (~9.48 pt). Provide a settings scale
of 100%, 125%, or 150% that uniformly scales geometry and type; default 100% for
reference fidelity. Do not independently enlarge text and break the proportions.

Measure percentage line height with NSFont metrics as in the pinned layout.
`cellExtent = ringDiameter + ringLabelGap + percentLineHeight`.
`bodyHeight = padTop + N*cellExtent + max(N-1,0)*cellSpacing + padBottom`.
`shapeHeight = bodyHeight + 2*curlRadius`. Ring centers are on the body horizontal
midline. The first ring top is `curlRadius + padTop`; subsequent cells advance by
`cellExtent + cellSpacing`. The right edge is flush to the usable display edge.
Default vertical center is the midpoint of the usable display region.

Adapt the pinned `SideNotchShape` right-edge path, including its radius clamping.
An ordinary rounded rectangle, a capsule detached from the edge, or square joins
at the screen boundary fails visual acceptance. No border, body shadow, frosted
glass, gradient, glow, or standard window title bar. The open card is also black;
allow only a subtle card shadow (Hormuz default: black at 25%, blur 12 pt, y 4 pt).

Card body sits to the left. Tail points to the hovered ring center and meets the
card with no seam. Measure content at the fixed width and animate the outer mask,
not the rows. At display bounds, clamp the card to 12 pt inside visibleFrame and
move the tail attachment toward the ring while keeping it clear of the rounded
corners. Do not clip the card or let a moving tail detach during transitions.

## Palette and metric meanings

Notch and card `#000000`; ring track `#303030`; bar track `#2D2D2D`;
primary text/glyphs white; secondary text `#808080`.

Used fraction `[0,.50)` → green `#00FF88`; `[.50,.70)` → yellow `#F2FF00`;
`[.70,1)` → orange `#FF3F00`; `>=1` → full orange arc and glyph opacity .35.
Clamp rendered arc to 0…1 but retain the true value in detail text. Start at
12 o'clock, sweep clockwise, round caps. Arc indicates used, never remaining.
Stale reading opacity .45, with explicit stale text on hover. Missing denominator
means neutral track and an em dash; never a fabricated percentage.

Use three Hormuz metrics, in this fixed order for the demo: Budget (`dollarsign`),
Tokens (`number`), Requests (`arrow.up.arrow.down`). These SF Symbols replace the
reference provider logos. The rings are not Codex/Claude subscription limits.
This adaptation is a fixed product decision; do not invent provider-specific
usage from the existing aggregate API.

Every demo tooltip title ends with `· Demo`; the menu-bar item reads `Hormuz Demo`.
The settings screen also displays `Demo data — not connected to a gateway`.
No background credential reads or network access in demo mode.

## Fixed demo content and scenarios

Use frozen time `2026-09-07T12:00:00Z`; no random values or relative dates derived
from the machine clock in screenshot scenarios. Default actor Alice, organization
Acme, team Engineering. Values below are synthetic UI fixtures, not production APIs.

| Ring | Label | First tooltip block | Second tooltip block |
| --- | --- | --- | --- |
| Budget | 73% | `Monthly budget`; `Resets Oct 1 UTC`; 73% bar; `$73 of $100 used` | `Monthly tokens`; same reset; 21% bar; `210K of 1M used` |
| Tokens | 21% | `Monthly tokens`; same reset; 21% bar; `210K of 1M used` | `Monthly budget`; same reset; 73% bar; `$73 of $100 used` |
| Requests | 52% | `Monthly requests`; same reset; 52% bar; `520 of 1,000 used` | `Denied requests`; no reset or bar; `8 requests denied` |

Titles: `Budget · Demo`, `Tokens · Demo`, `Requests · Demo`, with matching glyph.
Add a compact two-line footer: `Alice · Engineering` and `Gateway only · Estimated cost`.
That footer is a deliberate Hormuz addition; keep the reference block spacing.

Supply fixture cases selectable in Settings and via launch argument
`--scenario normal|empty|exhausted|stale|offline|needs-auth|unsupported`:

- normal: the table above; first frame has all three rings visible.
- empty: 0 of the same caps; labels 0%; hover says `No requests this month`.
- exhausted: Budget 100%, `$100 of $100 used`, `Budget reached`.
- stale: normal values dimmed; footer `Last updated 10 minutes ago`.
- offline: neutral rings and `—`; hover `Gateway unavailable`.
- needs-auth: neutral rings and `—`; hover `Reconnect to Hormuz` with Settings action.
- unsupported: neutral rings and `—`; hover `Limit data unavailable`.

Production integration must show only metrics supported by real APIs. The current
usage endpoint provides counts and estimates but no cap denominators; neutral
tracks are required until an authorized budget contract supplies them.

## Interaction and motion contract

Default visibility: Always visible, matching the showcased three-ring composition.
Settings may switch to Fold when idle. Right-edge placement only in this handoff.
Menu-bar fallback offers Show widget, Settings, Refresh demo, and Quit.

| Input/state | Required result |
| --- | --- |
| Pointer enters a ring cell | Open/select its tooltip immediately; animate appearance, not an arbitrary wait. |
| Pointer moves between cells | Reuse one tooltip; glide to next ring, crossfade contents; no close/reopen flicker. |
| Pointer leaves cell for tooltip | Keep open across the gap. Cancel scheduled dismissal on card entry. |
| Pointer leaves cell/card union | Schedule dismissal after 250 ms; cancel on reentry. |
| Click ring / keyboard activation | Pin its tooltip; same action again unpins. This replaces the upstream provider-page click. |
| Escape / outside click | Dismiss/unpin; preserve underlying app focus for hover-only interaction. |
| Fold mode, pointer enters handle hot zone | Expand same shape in place, reveal cells with stagger. |
| Fold mode, leaves entire interaction region | Close tooltip after grace, then fold; never fold while pointer is over card/settings. |
| Settings handle hover | Resting curved arc transitions to black orb with white gear; click opens Settings. |
| Right-click widget | Settings, Refresh demo, Hide widget, Quit; Show widget remains available from menu bar. |

Use pinned motion constants: unfold spring(response .42, damping .78); content
spring(.36, .82); cell stagger 45 ms per index, capped at 180 ms; tooltip glide
spring(.50, .86); crossfade easeInOut .16 s; reading change spring(.90, .90);
settings arc merge easeIn .20 s. These are configuration values, not measured
animation durations. Anchor folds to the screen edge, not the panel center.

Tooltip appearance uses opacity 0→1 and scale .97→1 anchored at tail, with content
spring above (Hormuz-specific default). Keep content at final measured size while
the mask/outer silhouette changes. Reduced Motion removes springs, scaling,
stagger and numeric rolling; use immediate state change or at most .10 s opacity.
No infinite pulsing, ring rotation, or fake agent-busy animation in the demo.

Settings controls must work: visibility, UI scale, fixture scenario, display
selection (default primary display), Show widget, Quit. Persist only these
preferences. One edge panel on the selected display; reanchor on display/Dock
layout change, and fall back to primary if the saved display disconnects.

Use separate tight native panels for widget and tooltip where practical; a large
transparent window must not intercept desktop clicks. Narrow hit regions should
cover visible controls and the hover corridor only while needed. Hover must not
activate the app or steal typing focus. Use an intentional focusable path for
keyboard interaction through the menu-bar item and Settings. No global keylogging,
accessibility permission request, or screen-recording permission to run the widget.

## Execution order and completion gates

1. Read the two plan documents, open pinned frames, and inspect the reference
   constants. Scaffold the independent package and app bundler.
2. Implement pure geometry, palette, metric mapping, fixtures, and static right-edge
   panel. Capture a normal three-ring screenshot before adding networking.
3. Add hover card, tail, pinned state, gap grace/cancellation, and settings handle.
4. Add folding, spring motion, scale preference, reduced motion, keyboard recovery,
   and display reanchoring. All settings and fixture cases must operate.
5. Build, launch, compare against the reference, fix mismatches, rerun relevant
   tests, and deliver the local app plus evidence. Stop before backend/API work.

Required tests: 0/49/50/69/70/99/100% color boundaries; unknown denominator;
1/2/3-cell height/placement; scale factors; canceled hover dismissal; switching
cells while dismissal is pending; pinned tooltip persistence; display-bound clamping.

Required visual evidence in `docs/evidence/companion-visual/`:

- idle screenshot, Budget hover, Tokens hover, folded handle, settings orb hover;
- offline and exhausted screenshots, plus a 150% scale screenshot;
- a 10–20 second screen recording showing hover across all rings, crossing the gap,
  pin/unpin, leaving/reentering, and fold/unfold (or explicit blocker if recording
  cannot be captured; do not claim motion verified from still screenshots);
- side-by-side reference/implementation crops at equal ring diameter; preserve
  reference attribution, omit wallpaper and provider logos from fidelity scoring;
- a short QA report with build command/results, OS/display/backing scale,
  screenshots/recording paths, known issues, and exact commit if one was created.

Static acceptance: ring diameter, body depth, card width, spacing, and tail geometry
match specified values within 1 logical point at 100% scale; matching colors and
font rules; no seams, clipped text, rectangular transparent click blockers, or
visible layout jumps. Recheck at 1× and 2× backing scales where available; report
unavailable hardware coverage. Rounded-corner raster antialiasing may differ.

Motion acceptance: card follows the selected ring, text stays stable during outer
shape animation, crossing the gap never dismisses the card, and rapid reentry does
not leave stuck/prematurely hidden panels. No app-focus theft on hover. Inspect a
real running app; tests and a successful compile alone are insufficient.

The coding agent should complete implementation and local visual QA autonomously
using these defaults. If a dependency or permission prevents verification, finish
unaffected work and report the specific blocker. Do not claim the visual result
is accepted by the user; present it for their review. Do not publish or deploy.
