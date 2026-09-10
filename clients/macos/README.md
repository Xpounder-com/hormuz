# Hormuz Mac — local development preview

A small native connector for a team's Hormuz gateway. It signs in through an
external browser, stores a revocable session in macOS Keychain, prepares a
dedicated client launcher, and shows the signed-in person's usage through a
small right-edge companion. The three rings show exact gateway-reported
estimated cost, token count, and request count. Hover a ring for details or
click to pin its card. The lower gear, folded handle, and menu-bar item open a
compact controls card beside the widget.

The card contains Connection (session, Keychain status, refresh, reconnect and
sign-out), Client (launcher preview/save/copy and the default-Off Context
optimization toggle), and Appearance (size, display, visibility). Browser
sign-in and advanced connection settings work inside the card. Back/Escape
navigates without clearing entered fields. The lower-right expand action opens
the optional full control center. The companion, control center, and credential
helper share one state owner and one Keychain session.

The explicit
**Hormuz hosted pilot — Codex/OpenAI** setup fixes the client to Codex, permits
only `openai-primary` or `openai-secondary`, and requires HTTPS. The **Custom
team gateway** setup preserves the existing Codex or Claude Code controls and
the loopback-only HTTP development option. The OpenAI pilot provides
same-provider model fallback; it does not qualify Claude Code, cross-provider
failover, or protection from an OpenAI-wide outage. It is not a VPN, chat
replacement, or hosted signup service. Customer distribution uses the separately
documented signed and notarized release workflow.

Build from the repository root with the installed Swift toolchain:

```sh
./clients/macos/script/build_and_run.sh
```

The script stages `clients/macos/dist/Hormuz.app`, applies an **ad hoc development
signature**, and opens it. It does not use your Apple Developer credentials or
submit anything for notarization. The Codex Run action calls the same script.
`--build-only` stages without opening; `--verify` additionally checks process
presence. `--debug`, `--logs`, and `--telemetry` support local troubleshooting.

The non-secret setup selector is stored in `profile.json`. A legacy profile
without the field remains `custom`; an explicit null or unknown value is
rejected. A hosted-pilot profile also fails closed if its client, alias, scheme,
or loopback setting does not match the bounded preset.

On first launch without a saved session, Hormuz opens the edge Connection card. With a
saved profile, it restores and refreshes the session behind the edge UI. The app's
menu-bar item remains available if the widget is hidden or folded.

The current gateway usage contract has no budget, token, or request denominator.
Hormuz therefore renders neutral rings with a connection-status marker and exact
raw values. It does not manufacture percentages. A real progress sweep is reserved
for a future verified limit contract.

SwiftPM products are `Hormuz` (the companion, control center, and credential helper
in one executable) and `HormuzClientCore` (session, transport, Keychain, connector,
and companion value types). The test-only `HormuzFixtureProbe` executable is **not**
copied into the app bundle. There are no third-party Swift package dependencies.
A distribution artifact embeds one version-matched Apple Silicon context helper,
its Python runtime, and two verified tokenizer vocabularies, so the customer does
not install Python or keep a copy of the server configuration. The local
development bundle uses this checkout's `.venv` through a clearly labeled
development wrapper. Codex or Claude Code is installed separately. The signed
customer app supports Apple Silicon (`arm64`) on macOS 14 or later; Intel Macs are
neither built nor tested. Clean-machine and exact-architecture distribution
validation remain release gates.

For deterministic visual QA, contributors can launch the local bundle with
`--companion-preview connected|empty|expired|offline`, optionally adding
`--companion-show-detail cost|tokens|requests`. Preview data affects only the
presentation adapter; these runs skip real session restoration and refresh.
`--companion-hub home|connection|client|appearance|setup` opens an edge page.
With an explicit preview and `--companion-capture-dir <directory>`,
`--companion-layout-audit` exercises live scaling in both directions, records
actual native frame/host geometry, and captures folding and hub recovery.

Read [the local setup and verification guide](../../docs/MACOS_CLIENT_LOCAL.md)
before using a gateway or preparing distribution.

The separate [direct-distribution guide](../../docs/MACOS_DISTRIBUTION.md)
documents Apple Silicon packaging, Developer ID signing, notarization, the protected
manual GitHub workflow, and the remaining clean-machine and update gates. The
release package uses the existing Hormuz product icon; local and distribution
signing identities remain deliberately separate.
