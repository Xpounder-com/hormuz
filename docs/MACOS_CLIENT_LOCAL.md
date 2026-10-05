# Native Mac client

Hormuz v1.2.0 publishes a Developer ID signed and notarized Apple Silicon client
for the opt-in [browser-login broker](HOSTED_LOGIN_LOCAL.md). This document also
keeps the local development and verification path explicit. Context optimization
ships Off by default while the separate v1.3 portfolio program remains
independently gated. Building and testing locally requires no paid cloud service.
The app alone does not create hosted signup, billing, provider credential custody,
automatic failover, an availability promise, or general production readiness.

## Customer flow

1. Open the local Hormuz app and select the gateway setup before entering the
   gateway origin and organization ID. **Hormuz hosted pilot — Codex/OpenAI**
   fixes the client to Codex, allows only `openai-primary` or
   `openai-secondary`, and requires HTTPS. **Custom team gateway** preserves the
   existing Codex or Claude Code and operator-approved model-alias controls,
   including the explicit loopback-HTTP development option. The optional issuer
   disambiguates gateways with multiple configured identity providers.
2. Select **Sign in with browser**, confirm that you initiated the connection, and
   complete your team's browser login. Credentials never appear in the browser.
3. Hormuz verifies the server-resolved human identity and displays personal usage.
   **Gateway verified** means identity and usage requests succeeded at the displayed
   time; it does not mean a model request, every client setting, or future uptime
   has been verified. Refreshing status does not send a model request.
4. Select **Set up client**, review the exact generated files, then save. Run the
   copied launcher command from your project directory. The launcher uses the
   separately installed `codex` or `claude` from your terminal's `PATH`. The
   default-Off **Context optimization** toggle applies to the next request and
   does not require another connector save.
5. **Sign out** first disables local use, then revokes the server session and removes
   its Keychain item. If the gateway cannot confirm revocation, the credential
   remains suspended solely for a sign-out retry. The UI does not claim success.

Only one active connection is supported in this slice. Sign out before changing
the gateway, organization, model alias, issuer, or client. A new sign-in creates a
new profile ID; save a new connector. Old launchers fail closed. This is explicit
connection setup, not interception of unrelated tools or personal accounts.

## Configuration stays separate

The app writes **only Hormuz-owned files** under
`~/Library/Application Support/Hormuz/`. It does not open or rewrite the user's
Codex/Claude configuration or login files:

| Local data | Purpose and custody |
| --- | --- |
| `profile.json` | Non-secret setup selector, origin, organization, optional issuer, client, alias, local HTTP opt-in and random profile ID; mode `0600` |
| `connection.lock` | Metadata-only cross-process lock; mode `0600`; bounded acquisition |
| `context-optimization-<id>.json` | Schema version and one boolean toggle; mode `0600` |
| `context-optimization.lock` | Metadata-only toggle lock; mode `0600` |
| `<client>-<id>.command` | Quoted launcher with no credential values; mode `0700` |
| `backup-<id>.txt` | Previous bytes of a changed Hormuz-owned generated file; mode `0600` |
| macOS Keychain service `com.hormuz.mac.session.v1`, account `active-connection-v1` | Access/refresh pair, expiry, bound profile metadata and refresh/revocation state |

The directory is private (`0700`). Symlinks, hard-linked files, foreign ownership,
and group/world-accessible existing files are rejected rather than repaired or
overwritten. Preview does not write configuration. Saving checks that every file
still matches its preview, preserves replaced bytes in a backup, and atomically
replaces each owned file. A partial multi-file write is not reported as complete;
a fresh preview can finish it. Repeated saves are idempotent.

The saved `setup` value is either `openai-pilot` or `custom`. Profiles written
before this field existed decode as `custom`, preserving their former behavior.
An explicitly present `null`, an unknown setup string, or a hosted-pilot profile
that selects Claude Code, loopback HTTP, or any other alias fails closed. Loading
and re-saving a valid profile retains its setup and profile ID.

The current development launcher invokes the bundled Rust client relay, passing
the Swift credential broker, packaged optimizer, and app-owned private socket.
The published v1.3.0 app retains its earlier context-helper launcher. The relay
starts an authenticated loopback listener and gives Codex
invocation-local TOML overrides or Claude Code invocation-local environment
overrides. Common ambient provider credential and backend selectors are cleared
for that invocation. The launcher accepts no extra override arguments. It does
not weaken tool permissions, sandbox policies or approval settings, or modify
unrelated user preferences.
Managed/client settings and changing the tool configuration can still affect
routing; this is not device enforcement. Moving the app requires regenerating
the launcher so its absolute helper path remains valid. After an app restart,
an already-saved launcher is regenerated following successful connection refresh
with the new app's lifetime lease. Keep the resident app running; hiding its
panels does not close that lease.

## Session and network safety

The app executable remains the Keychain credential helper. The Rust relay
accepts only the profile ID, explicit private state directory, and the fixed
helper/socket paths supplied by the generated launcher. It starts one relay and
one client process, then exits with the client. App quit closes the owner lease
and stops the direct client and owned optimizer work; deliberate detachment,
abrupt relay death, and interactive quit/update qualification remain open in
#341. **Credential-helper stdout is a
machine credential channel. Do not paste, record, or print it.** Errors contain
fixed diagnostics, not server bodies or credentials.

The relay binds an OS-selected IPv4 loopback port and creates a fresh
credential for each launch. It accepts only the expected local host, protocol
paths, and methods, replaces that credential before gateway egress, follows no
redirect, retries no inference request, and logs no model body. With optimization
Off it forwards body bytes without JSON inspection or tokenizer loading. With it
On, selection, reconstruction checks, and token estimates stay on the device;
only one chosen request representation reaches the gateway. See [client-side
context optimization](CONTEXT_OPTIMIZATION.md).

The app's credential-helper mode validates the entire saved profile before
returning an access token. The Rust relay also supplies its complete expected
profile over bounded stdin; the broker compares it under the same process lock
before reading Keychain or refreshing. A profile replacement with the same ID
cannot rebind an active relay. Before a
refresh it persists a pending state, so a crash or lost response cannot cause the
next helper to replay a possibly consumed refresh token. Interrupted refresh
requires sign-out/revocation and a new login. The native code does not replay
inference requests. Access rotation preserves the server's absolute session expiry.

The local build uses macOS's **file Keychain through SecItem**, with the OS access
control list and no plaintext fallback. It does not claim device-bound storage or
screen-lock behavior. Data Protection Keychain requires a separately validated
signing/provisioning setup. `kSecAttrAccessible` is deliberately not supplied to
the file backend, where it is unsupported. See Apple's [Mac Keychain overview](https://developer.apple.com/documentation/technotes/tn3137-on-mac-keychains)
and [attribute requirements](https://developer.apple.com/documentation/security/ksecattraccessible).
Ad hoc rebuilds can change code identity and Keychain access; sign out before
rebuilding. Never work around an OS denial by exporting credentials to a file.

Native requests use an ephemeral URLSession without cookies or disk caching,
bounded response bodies and timeouts, and no redirects. HTTPS is required except
for an explicit loopback HTTP opt-in. The browser URL must exactly match the
enrollment URL at the configured origin. Gateway responses must match the known
identity/usage contracts; an organization or client mismatch aborts and attempts
revocation before retaining the new session. Cleanup after a failed secure-store
write is best effort: a simultaneous gateway outage needs operator revocation or
server expiry. No stronger atomicity between Keychain and the server is claimed.

## Build and verification

From the repository root:

```sh
./script/build_and_run.sh --build-only
swift test --package-path clients/macos
python -m pip install --editable .
python tools/verify_macos_client.py \
  --probe clients/macos/.build/debug/HormuzFixtureProbe \
  --output /tmp/hormuz-macos-local-proof.json
```

`swift test` requires full Xcode with XCTest, not only Command Line Tools. If the
selected toolchain lacks XCTest, set `DEVELOPER_DIR` for this command to the
installed Xcode `Contents/Developer` directory; do not change the machine-wide
selection. The app itself can build with Command Line Tools. The Mac CI job uses
the runner's Xcode toolchain and uploads only the metadata proof, never an unsigned
app or credentials.

Tests cover profile validation, shell quoting, safe files, stale previews, backups,
concurrent helpers, interrupted refresh, pending logout, identity mismatches and
save-failure revocation. Python tests additionally cover exact compaction,
request-pinned settings, authenticated relay forwarding, gateway reconstruction,
and Off byte equivalence. Keychain tests skip unless explicitly enabled:

```sh
HORMUZ_TEST_KEYCHAIN=1 swift test --package-path clients/macos --filter KeychainTests
```

That test creates a unique synthetic service, exercises add/read/update/delete,
then removes its item. It never queries an existing customer credential. The HTTP
probe uses an in-memory secure-store fixture and the real native transport against
the Python gateway, fake IdP and model simulator. Its 12 checks include two tenants,
wrong-client denial, personal usage, token rotation, old-token rejection, logout,
redirect refusal, response bounds and no credentials in generated files. It is
mechanical evidence, not external onboarding or real-IdP validation.

For the current Rust launcher's provider-free installed-client checks, extract
an ad hoc local validation archive and select that archive's executables:

```sh
HORMUZ_NATIVE_RELAY_BINARY=/absolute/path/Hormuz.app/Contents/Helpers/hormuz-client-relay \
HORMUZ_NATIVE_OPTIMIZER_HELPER=/absolute/path/Hormuz.app/Contents/Resources/ContextHelper/hormuz-context \
HORMUZ_NATIVE_CREDENTIAL_HELPER=/absolute/path/Hormuz.app/Contents/MacOS/Hormuz \
HORMUZ_NATIVE_OFFICIAL_CLIENT_DIRECTORY=/absolute/path/pinned-clients/node_modules/.bin \
  python -m unittest -v tests.test_native_macos_relay
```

The isolated client directory must contain Codex **0.147.0** and Claude Code
**2.1.233**. The suite uses disposable configuration roots and synthetic
credentials against loopback gateways. It tests extracted Rust/optimizer
binaries, actual Codex tool compaction and Claude streaming, exact Off bytes,
failed helpers, bad authentication, and owner-exit cancellation. The packaged
Swift broker rejects malformed or mismatched profiles without reading an
existing Keychain item. Positive credential custody is separately covered by
the isolated Keychain and coordinated session tests; this suite does not
establish interactive GUI, live-account, or clean-install acceptance.

For a separate interactive local GUI check:

```sh
python tools/verify_macos_client.py --serve
./script/build_and_run.sh
```

Select **Custom team gateway**, then use the printed loopback origin, `org-a`,
and `safe-openai` for Codex or `safe-claude` for Claude Code. Enable local HTTP.
The browser's **fixture Alice**
button uses no real account or password. Save the connector and launch the
pinned client through it. Check that hiding a panel preserves traffic and
quitting ends the direct invocation. Those interactive checks are still open
in #341. The historical `verify_macos_installed_client.py` targets the earlier
direct-connector format and does not qualify the current Rust launcher.
Sign out in the app before stopping the fixture or rebuilding. These fixture
servers must never be deployed or exposed beyond loopback.

The settings mechanisms follow the pinned clients' behavior and the [Claude Code
CLI reference](https://code.claude.com/docs/en/cli-reference) and [settings reference](https://code.claude.com/docs/en/settings).
Client compatibility is version-specific. The blocking pinned-client gate gives
each client a stale access token while the synthetic credential command already
holds the rotated token. Codex reruns the command after `401` and completes with
exactly one provider egress. Claude Code reruns the command but does not replay
the rejected inference; the next explicit request succeeds with the same simulated
generation-egress count as a clean-credential control. This qualifies the
client-side retry semantics without a live provider. The v1.2.0 release
qualification separately composes the selected Codex/OpenAI path with the signed
native Keychain helper after lock, restart, replacement, update, and rollback.
Later candidates must repeat that composition for their declared scope.

## Released distribution and remaining customer gates

The executable release workflow and credential boundary are documented in
[direct Mac distribution](MACOS_DISTRIBUTION.md). Hormuz v1.2.0 completed
Developer ID signing, notarization, a clean Apple Silicon install and lifecycle,
the selected Codex/OpenAI refresh behavior, and bounded Okta/Render recovery for
the exact release candidate. The checklist below remains the acceptance boundary
for each later candidate and customer deployment.

- Choose a permanent bundle identifier and signing/provisioning arrangement.
  Validate Keychain behavior in the app and CLI helper, after lock/unlock, denied
  access, restart, app replacement, update, and rollback. Decide and validate any
  Data Protection Keychain migration; do not infer its guarantees from this build.
- Produce supported architecture artifacts from a controlled build. Test on the
  declared minimum macOS version and clean Macs without Python or development
  tools. The artifact must include the signed native Apple Silicon context helper
  plus both digest-verified tokenizer vocabularies. Intel Macs are outside the
  supported and tested distribution boundary.
  Install the app at a stable location before creating helper paths.
- Sign with the intended **Developer ID Application** identity, hardened runtime
  and a secure timestamp. Use only justified entitlements. The local `codesign -s -`
  step is not Developer ID signing. Verify the bundle with `codesign --verify --strict`
  and inspect its entitlements and runtime dependencies.
- Submit the exact intended archive to Apple's notary service using protected
  credentials, inspect acceptance, staple the ticket and verify Gatekeeper on a
  quarantined downloaded copy. Prepare an authenticated update/rollback path.
  Follow [Apple's notarization requirements](https://developer.apple.com/documentation/security/notarizing-macos-software-before-distribution)
  and [hardened-runtime guidance](https://developer.apple.com/documentation/security/hardened-runtime).
- Complete a real, owner-selected IdP integration: HTTPS callback, issuer/subject
  mapping, consent, failure, deprovisioning, revocation and recovery. Qualify the
  pinned clients selected by the explicit pilot contract: Codex for
  `codex_openai`, or Codex and Claude Code for `full_dual_provider`. Never infer
  a narrower contract from missing Claude evidence, and never retry ambiguous
  inference.
- Complete production gateway tenancy/provider custody, distributed rate limits,
  durable sessions, backup/recovery, operational monitoring and support. Those
  are hosted-service work, not supplied by this Mac window.
- Perform independent onboarding and security/accessibility review. Local fixture
  runs do not change the `0/5 initial` or `0/1 returning` onboarding counts.

No Mac App Store submission is required for this distribution path. Hormuz
v1.2.0 is available as a versioned direct signed and notarized download. Its
release evidence does not activate a customer, configure billing, qualify Intel
Macs or Anthropic, complete independent onboarding/security/accessibility review,
or promise availability or latency.
