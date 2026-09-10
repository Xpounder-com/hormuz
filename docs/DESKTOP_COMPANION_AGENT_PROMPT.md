# Copy this prompt to the coding agent

> Historical execution prompt: the fixture-only visual build described below has
> been completed. The production integration is documented in
> `MACOS_COMPANION_INTEGRATION.md` and implemented under `clients/macos/`.

Implement the Hormuz desktop companion visual demo in this repository.

Read `docs/DESKTOP_COMPANION_VISUAL_SPEC.md` first, then
`docs/DESKTOP_COMPANION_PLAN.md`. Open both reference images under
`docs/design/companion-reference/docs/design/` and inspect the bundled layout,
palette, geometry, and motion source before coding. Follow applicable repository
instructions and relevant macOS implementation skills.

The priority is visual fidelity to Codenotch: a black right-edge notch, three
stacked rings, a pointed hover card, the settings arc/gear, and smooth hover and
fold transitions. A normal menu-bar popover is not an acceptable replacement.
Use the exact dimensions, fixtures, colors, state transitions, and acceptance
criteria in the visual spec; avoid redesigning or asking routine design questions.

Build a native macOS 14+ SwiftUI app with a narrow AppKit panel host in
`apps/macos/HormuzCompanion/`, inside the Hormuz repository, separate from the
parent MemoWiki app. Produce a runnable local `.app`. Use clearly labeled,
deterministic demo data. Complete all five execution steps in the visual spec,
including functional settings, meaningful tests, launch verification, screenshot
comparison, and motion evidence. Preserve required upstream attribution.

Do not implement new gateway APIs, discover provider credentials, or replace
unknown live limits with demo numbers. Live integration and release packaging
are later tasks. Do not publish, deploy, or modify unrelated work.

Continue through local completion using the supplied decisions. Fix visible
mismatches before handing back. Return the app/build instructions, evidence and
QA report paths, tests actually run, and any specific unresolved blocker. Do not
claim visual verification from a successful build alone.
