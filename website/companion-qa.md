# Companion preview QA — 2026-09-07

The landing page now includes a contained desktop edge-widget preview. It shares
the approved BrandMark and native silhouette proportions, neutral usage tracks,
compact unavailable state, persistent gear, and connection/client/appearance
navigation. All values are synthetic and labeled. It makes no gateway calls and
has no access to a Mac Keychain. Setup actions link to the existing integrations
guide; session actions explicitly change the example only.

Verified in the built static export with the in-app browser:

- Gear opens controls; cost, token, and request rings show their matching totals.
- Connection preview toggles expired state and removes unavailable headline rows.
- Client setup links to `/integrations/`; all local destinations pass export checks.
- Appearance scales to 100%, 125%, and 150%; folded tab reopens controls.
- Escape closes controls and restores focus to a visible trigger, including after
  reopening from the folded tab. Hidden content is inert.
- Clicking outside a card dismisses it while keeping the widget expanded, without
  moving focus away from the clicked destination. Browser check passed with the
  card closed and the widget still unfolded.
- Desktop, 390-pixel mobile at 150%, and 320-pixel narrow mobile have no horizontal
  overflow. Cards scroll within the preview. No browser console errors observed.
- The floating founder CTA hides while this section is in view so it cannot cover
  the gear; existing page CTAs remain available.
- Reduced Motion disables transitions. Native and website gears keep a visible
  icon at rest; interactions do not depend on hovering.

Validation: production Webpack build and TypeScript passed; all 41 existing
website tests passed; export verification passed for 10 pages and 568 local link
occurrences. No lead, booking, or payment was submitted during this UI check.

Publication: these changes are local source and preview only. The published site
was verified to be pinned to `3f5831adfee99a293dc4af4d8ea1cd7530110b47` before edits.
Use the existing reviewed-source publication workflow when publishing.

## Discovery follow-up — 2026-09-08

The website preview now has a primary `Explore the controls` button, a prominent
in-canvas invitation, and clickable labels connected to each usage ring and the
settings gear. The labels use the existing buttons, including their focus and
keyboard behavior. They hide while a panel is open, return with `Pick another
view`, and offer a visible `Reopen the widget` action when folded. A first-use
ring cue runs twice; the existing reduced-motion rule disables it.

Verified on the final static export in the in-app browser:

- Clicking the label text opens the matching cost, token, request, or controls
  view. The intro button opens controls and brings the panel into view on mobile.
- Space opens the primary button; Escape closes the panel and restores its
  trigger. Opening waits until the panel is visible before moving focus.
- Folding and reopening through the new button works; closing afterward returns
  focus to the visible gear. Outside clicks still dismiss without folding.
- Expired-state totals remain hidden, and each label follows its compact ring.
- Desktop (1280), compact desktop (1024), mobile (390), and narrow mobile (320)
  were checked. The enlarged 150% widget keeps labels within the canvas; the
  final 320-pixel export has no horizontal overflow. Browser warnings/errors: 0.

Validation: production build, TypeScript, all 41 existing website tests, and
export verification passed (10 pages, 569 local links). Review found and fixed
mobile launch focus/scroll timing and label clearance at compact desktop widths.
No native code, dependencies, requests, credentials, lead submissions, or
commercial configuration changed. Publication follows the reviewed-source pin;
this UI check is not native app or provider qualification.
