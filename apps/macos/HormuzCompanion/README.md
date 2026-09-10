# Hormuz Companion visual demo

This package is retained as the deterministic visual prototype and motion-evidence
harness. The product implementation now lives in `clients/macos/`, where the same
side-edge treatment is connected to the existing Hormuz session and client setup
workflow. See `docs/MACOS_COMPANION_INTEGRATION.md`.

This package implements the fixture-driven native macOS side widget specified in
[`docs/DESKTOP_COMPANION_VISUAL_SPEC.md`](../../../docs/DESKTOP_COMPANION_VISUAL_SPEC.md).
It does not connect to a Hormuz gateway or read provider credentials.

Build, bundle, launch, and verify the process:

```bash
./script/build_and_run.sh --verify
```

Launch a deterministic scenario or pin a detail card for visual review:

```bash
./script/build_and_run.sh --scenario exhausted --show-detail budget
./script/build_and_run.sh --always --show-detail budget
./script/build_and_run.sh --folded
./script/build_and_run.sh --ui-scale 1.5 --show-detail tokens
./script/build_and_run.sh --show-settings
```

For deterministic visual evidence, add `--capture-dir <directory>` and an optional
`--capture-name <name>`. This captures the live hosted panel views after launch.
Use `--record-motion <path.gif>` with `--show-detail budget` to record the
deterministic 12-second interaction tour from the same hosted panels.

The menu-bar item `Hormuz Demo` always provides Show Widget, Settings, detail,
refresh, and quit actions. The app intentionally uses accessory activation and
does not add a Dock icon.

Run focused tests with:

```bash
./script/test.sh
```

Selected geometry and interaction behavior are adapted from Codenotch at pinned
commit `743601acd69e701131602b88082fcaeee0c2e88b`, used under the MIT license.
The source snapshot and license live under `docs/design/companion-reference/`.
