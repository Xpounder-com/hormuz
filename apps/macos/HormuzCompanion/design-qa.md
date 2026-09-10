# Hormuz Companion design QA

**Findings**

- No actionable P0, P1, or P2 differences remain in the current native build.
- The Hormuz detail card is taller than the Codenotch card because the approved
  product specification adds the `Alice · Engineering` and gateway-disclosure
  footer. Its width, corner radius, content rhythm, and tail geometry retain the
  source proportions.
- The resting settings arc is absent from the primary reference frame, but is an
  explicit Hormuz behavior in the visual specification and resolves to the
  separately verified black gear orb on hover.

**Comparison Target**

- Source visual truth:
  `docs/design/companion-reference/docs/design/frame-124-hover-tooltip.png`
- Secondary source:
  `docs/design/companion-reference/docs/design/frame-125-detail.png`
- Rendered implementation:
  `docs/evidence/companion-visual/budget-hover-composite.png`
- State: normal fixture, Budget ring selected, detail card open, 100% UI scale,
  dark appearance.
- Native viewport: right edge of the built-in 3024 × 1964 Retina display. The
  widget lays out in logical points at 2× backing scale.
- Source pixels: 2000 × 2000. Implementation composite: 822 × 1012 pixels.
  There is no CSS viewport or browser device-scale factor for this native app.
- Density normalization: the source crop is scaled by 0.752 so its 117-pixel
  reference ring and the implementation's 88-pixel @2x ring both represent the
  required 44-point ring diameter.

**Comparison Evidence**

- Full view:
  `docs/evidence/companion-visual/reference-vs-hormuz-overall.png`
  (1591 × 1052 pixels).
- Focused card, tail, and first-ring region:
  `docs/evidence/companion-visual/reference-vs-hormuz-detail.png`
  (1591 × 542 pixels).
- The source and implementation appear together in each comparison image. The
  source wallpaper, computer frame, and provider logos are excluded from fidelity
  scoring as required by the product specification.

**Required Fidelity Surfaces**

- Fonts and typography: SF system type, semibold percentage/title hierarchy,
  monospaced percentage digits, secondary gray labels, line heights, and
  truncation match the specified native treatment. No visible wrapping or clipped
  text remains in normal, offline, exhausted, or 150% states.
- Spacing and layout: the continuous right-edge silhouette, 44-point rings,
  roughly 70-point body depth, fixed 225.64-point card width, cell pitch, inverse
  joins, card radius, tail, and internal block rhythm match the measured constants.
  The equal-ring comparison shows no actionable geometry drift.
- Colors and tokens: black surfaces, `#303030` ring tracks, `#2D2D2D` bar tracks,
  white/`#808080` text, and green/yellow/orange usage bands match the pinned source
  constants and Hormuz thresholds.
- Image and asset quality: the widget uses native vector SF Symbols for the three
  approved Hormuz metrics. Provider logos and source wallpaper were intentionally
  omitted; no placeholder imagery, emoji, custom inline SVG, or raster substitute
  appears in the shipped view.
- Copy and content: metric names, frozen fixture values, `· Demo`, actor/team,
  reset text, unavailable-state explanation, and gateway disclosure are coherent
  and visibly distinguish fixture data from live gateway data.
- Icons and interaction states: Budget, Tokens, Requests, pin, gear, stale,
  unavailable, fold, hover-card movement, pin/unpin, and settings states are
  implemented. The exact bundle was inspected as a running app; activating the
  folded handle changed accessibility from `Show Hormuz usage demo` to all three
  metric controls and rendered the complete three-ring body.
- Accessibility and resilience: descriptive accessibility labels and hints are
  present; reduced-motion handling removes springs and stagger; the 100%, 125%,
  and 150% scales are uniform. Tooltip placement clamps to the visible display.

**Comparison History**

1. Earlier P2 — the exhausted message made the fixed-height card clip its footer.
   Fix: card height now derives from whether a scenario has a message, blocks, or
   an unavailable placeholder. Post-fix evidence:
   `docs/evidence/companion-visual/exhausted-composite.png`.
2. Earlier P1 — unfolding could resize the native panel while leaving the hosted
   SwiftUI content at the folded bounds, producing a clipped ring fragment.
   Fix: the hosting view now autoresizes with the panel and its bounds/layout are
   synchronized before and after frame animation. Post-fix evidence: the running
   app rendered all three rings after folded-handle activation, and
   `docs/evidence/companion-visual/interaction-tour-contact-sheet.png` includes the
   folded and re-entered states.
3. Final normalized comparison — after both fixes, the full and focused comparison
   images show no remaining P0/P1/P2 mismatch in typography, geometry, palette,
   assets, copy, or the selected state.

**Residual Test Gaps**

- Only the built-in 2× Retina display was available; 1× backing-scale hardware and
  multi-display reanchoring were not physically available for visual inspection.
  Pure geometry tests cover scale conversion and display-bound clamping.
- Native `screencapture` lacked display capture access in this environment. The
  retained GIF is produced from the live hosted panel views, and the running app
  was also inspected directly through its exact bundle identity.

**Implementation Checklist**

- [x] Match source silhouette, rings, colors, type hierarchy, detail card, and tail.
- [x] Verify normal, Tokens, exhausted, offline, folded, settings-hover, and 150% states.
- [x] Verify ring hover selection, gap grace, pin/unpin, fold, and re-entry.
- [x] Run focused geometry, threshold, unknown-limit, and hover tests.
- [x] Build and launch the actual accessory `.app` without provider access.

final result: passed
