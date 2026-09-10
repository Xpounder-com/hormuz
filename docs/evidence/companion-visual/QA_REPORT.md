# Hormuz Companion visual evidence

## Result

The fixture-driven native macOS companion builds, launches, and matches the
pinned Codenotch geometry and visual treatment closely enough for product review.
It has not been accepted by the user yet.

## Build and test

- Build/launch:
  `apps/macos/HormuzCompanion/script/build_and_run.sh --verify --scenario normal --always --show-detail budget`
- Tests: `apps/macos/HormuzCompanion/script/test.sh`
- Result: 11 tests passed, 0 failed.
- Toolchain: macOS 26.2 (25C56), Xcode 26.4.1 (17E202), Apple M5.
- Display: built-in Liquid Retina XDR, 3024 × 1964 Retina, main display, 2× backing.
- Repository base: `b8cec8faba8d8e48d515dfcc3ec8eeaa78fc7926`.
- Commit created: none; the companion implementation and evidence remain
  uncommitted for review.

## Visual evidence

- Normal Budget hover: `budget-hover-composite.png`
- Tokens hover: `tokens-hover-composite.png`
- Idle: `idle-composite.png`
- Folded handle: `folded-composite.png`
- Settings orb hover: `settings-hover-composite.png`
- Offline / unknown denominator: `offline-composite.png`
- Exhausted Budget: `exhausted-composite.png`
- 150% scale: `scale-150-composite.png`
- Source comparison: `reference-vs-hormuz-overall.png`
- Focused comparison: `reference-vs-hormuz-detail.png`
- 12-second motion tour: `interaction-tour.gif`
- Motion contact sheet: `interaction-tour-contact-sheet.png`

The motion tour shows Budget and Tokens detail movement, Requests pinning, fold,
and re-entry. A direct running-app check also activated the folded handle and
confirmed the complete three-ring body after the panel resize.

## Known limits

- This handoff uses deterministic demo fixtures and performs no gateway request or
  provider-credential read.
- A 1× display and a second physical display were unavailable for visual QA.
- The command-line `screencapture` process did not have display-capture access, so
  evidence uses the app's live hosted-panel recorder plus direct native inspection.
