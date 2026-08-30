# Hormuz Mac — local development preview

A small native connector for a team's Hormuz gateway. It signs in through an
external browser, stores a revocable session in macOS Keychain, prepares a
dedicated Codex or Claude Code launcher, and shows the signed-in person's usage.
It is not a VPN, chat replacement, hosted signup service, or distributable release.

Build from the repository root with the installed Swift toolchain:

```sh
./script/build_and_run.sh
```

The script stages `clients/macos/dist/Hormuz.app`, applies an **ad hoc development
signature**, and opens it. It does not use your Apple Developer credentials or
submit anything for notarization. The Codex Run action calls the same script.
`--build-only` stages without opening; `--verify` additionally checks process
presence. `--debug`, `--logs`, and `--telemetry` support local troubleshooting.

SwiftPM products are `Hormuz` (the window and credential helper in one executable)
and `HormuzClientCore` (session, transport, Keychain and connector logic). The
test-only `HormuzFixtureProbe` executable is **not** copied into the app bundle.
There are no third-party Swift package dependencies. The customer's Mac app does
not require Python, Node.js or a copy of the server configuration. Codex or Claude
Code is installed separately. The deployment target is macOS 14 or later; clean
machine and multi-architecture distribution validation remain release gates.

Read [the local setup and verification guide](../../docs/MACOS_CLIENT_LOCAL.md)
before using a gateway or preparing distribution.
