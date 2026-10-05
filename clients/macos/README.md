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

The next hosted first-run flow is **Continue with Hormuz**. The app opens the
system browser, receives the approved organization and model from the gateway,
saves the session in Keychain, and prepares its own client launcher. Manual
self-hosted setup is under Advanced. This flow requires an injected
`HormuzDesktopOrigin` in the app bundle and is currently local source only;
see [one sign-in path](../../docs/MACOS_ONE_CLICK_SIGNIN.md). To preview it
against a local fixture, set `HORMUZ_DESKTOP_ORIGIN` to an HTTPS or loopback
origin before running `./clients/macos/script/build_and_run.sh`.

Previously saved **Hormuz hosted pilot — Codex/OpenAI** profiles remain
supported. Manual setup under Advanced preserves Codex or Claude Code controls
and the loopback-only HTTP development option. The OpenAI pilot provides
same-provider model fallback; it does not qualify Claude Code, cross-provider
failover, or protection from an OpenAI-wide outage. Customer distribution uses
the separately documented signed and notarized release workflow.

Build from the repository root with Swift and the Rust toolchain pinned in
`clients/rust/rust-toolchain.toml`:

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

The development app bundles `hormuz-client-relay`. Its saved launcher binds
the existing Swift Keychain broker to the complete launch profile under the
session lock, and uses an app-owned private Unix socket as a no-data lifetime
lease. Closing or hiding a panel does not close that lease; app quit does.
Previously saved launchers are regenerated after a successful connection
refresh, so restarting or moving the app requires that refresh before use.
The Rust relay stops its direct client on lease closure and cancels owned
optimizer work without replaying a model request. Ordinary optimizer process
groups are cleaned up. Detached client descendants, abrupt relay death, and
interactive quit/update acceptance remain open in #341.

The standalone optimizer supports the Rust relay's bounded bridge protocol.
Off never starts it. On starts it on demand and uses bundled tokenizer
resources; cold tokenizer initialization is outside the existing 100 ms
candidate-work guard, within the relay's overall 30-second helper deadline.
This is not a 100 ms end-to-end latency promise.

`tests.test_native_macos_relay` tests the extracted local archive when supplied
`HORMUZ_NATIVE_RELAY_BINARY`, `HORMUZ_NATIVE_OPTIMIZER_HELPER`,
`HORMUZ_NATIVE_CREDENTIAL_HELPER`, and
`HORMUZ_NATIVE_OFFICIAL_CLIENT_DIRECTORY`. It exercises pinned Codex `0.147.0`
and Claude Code `2.1.233` against a synthetic loopback gateway, not real
provider accounts. CI retains metadata only. The latest published notarized
Mac app remains v1.3.0; v1.7.0 publishes core/source improvements, not a new
customer Mac archive, and does not submit anything to Apple.

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
