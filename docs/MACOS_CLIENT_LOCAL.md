# Native Mac client: local milestone

This is a development milestone built on the opt-in [browser-login broker](HOSTED_LOGIN_LOCAL.md).
It does not change the released v1 gateway or the separate v1.1 portfolio program.
It requires no paid cloud service to build or test. No hosted signup, billing,
team dashboard, provider credential custody, automatic failover, availability
promise, or production-readiness claim is added here.

## Customer flow

1. Open the local Hormuz app. Enter the gateway origin, organization ID, supported
   client, and a model alias approved by the gateway operator. The optional issuer
   disambiguates gateways with multiple configured identity providers.
2. Select **Sign in with browser**, confirm that you initiated the connection, and
   complete your team's browser login. Credentials never appear in the browser.
3. Hormuz verifies the server-resolved human identity and displays personal usage.
   **Gateway verified** means identity and usage requests succeeded at the displayed
   time; it does not mean a model request, every client setting, or future uptime
   has been verified. Refreshing status does not send a model request.
4. Select **Set up client**, review the exact generated files, then save. Run the
   copied launcher command from your project directory. The launcher uses the
   separately installed `codex` or `claude` from your terminal's `PATH`.
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
| `profile.json` | Non-secret origin, organization, optional issuer, client, alias, local HTTP opt-in and random profile ID; mode `0600` |
| `connection.lock` | Metadata-only cross-process lock; mode `0600`; bounded acquisition |
| `<client>-<id>.command` | Quoted launcher with no credential values; mode `0700` |
| `claude-code-<id>.settings.json` | Helper command and routing overrides; no credential values; mode `0600` |
| `backup-<id>.txt` | Previous bytes of a changed Hormuz-owned generated file; mode `0600` |
| macOS Keychain service `com.hormuz.mac.session.v1`, account `active-connection-v1` | Access/refresh pair, expiry, bound profile metadata and refresh/revocation state |

The directory is private (`0700`). Symlinks, hard-linked files, foreign ownership,
and group/world-accessible existing files are rejected rather than repaired or
overwritten. Preview does not write configuration. Saving checks that every file
still matches its preview, preserves replaced bytes in a backup, and atomically
replaces each owned file. A partial multi-file write is not reported as complete;
a fresh preview can finish it. Repeated saves are idempotent.

Codex receives invocation-local TOML overrides for a complete `hormuz_connector`
provider table. Claude Code receives a separate `--settings` file. Common ambient
provider credential and backend selectors are cleared for that invocation. The
launcher accepts no extra override arguments. It does not weaken tool permissions,
sandbox policies or approval settings, or modify unrelated user preferences.
Managed/client settings and changing the tool configuration can still affect
routing; this is not device enforcement. Moving the app requires regenerating
the launcher so its absolute helper path remains valid.

## Session and network safety

The app and helper are the same executable. The helper accepts only a profile ID
and explicit private state directory; `--force-refresh` is available for controlled
diagnostics. **Its stdout is a machine credential channel. Do not paste, record,
or print it.** Errors contain fixed diagnostics, not server bodies or credentials.

The helper reads Keychain and validates the entire saved profile before returning
an access token. Both the app and helper take the same process lock. Before a
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
save-failure revocation. Keychain tests skip unless explicitly enabled:

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

For an actual local GUI and installed-client check:

```sh
python tools/verify_macos_client.py --serve
./script/build_and_run.sh
```

Use the printed loopback origin, `org-a`, and `safe-openai` for Codex or
`safe-claude` for Claude Code. Enable local HTTP. The browser's **fixture Alice**
button uses no real account or password. Save the connector, then run:

```sh
python tools/verify_macos_installed_client.py \
  --state-directory "$HOME/Library/Application Support/Hormuz" \
  --bundle clients/macos/dist/Hormuz.app \
  --client-command /absolute/path/to/pinned/codex-or-claude \
  --output /tmp/hormuz-macos-installed-client.json
```

The tool first requires the explicit test-fixture endpoint on loopback, captures
the native helper without displaying its credential, forces rotation, confirms
the old access token is rejected, and runs Codex **0.147.0** or Claude Code
**2.1.233** against the simulator. It isolates `CODEX_HOME` / `CLAUDE_CONFIG_DIR`,
preserves the login environment needed by Keychain, and compares the real user
settings' fingerprints. It retains only booleans, versions and artifact digests.
Read-only/safe client flags and a synthetic no-tools request are used. Sign out in
the app before stopping the fixture or rebuilding. These fixture servers must
never be deployed or exposed beyond loopback.

The settings mechanisms follow the pinned clients' behavior and the [Claude Code
CLI reference](https://code.claude.com/docs/en/cli-reference) and [settings reference](https://code.claude.com/docs/en/settings).
Client compatibility is version-specific. The blocking pinned-client gate gives
each client a stale access token while the synthetic credential command already
holds the rotated token. Codex reruns the command after `401` and completes with
exactly one provider egress. Claude Code reruns the command but does not replay
the rejected inference; the next explicit request succeeds with the same simulated
generation-egress count as a clean-credential control. This qualifies the
client-side retry semantics without a live provider. A signed installed-client
run must still compose that behavior with the native Keychain helper after lock,
restart, replacement, update, and rollback.

## Before any customer distribution

The executable release workflow and credential boundary are documented in
[direct Mac distribution](MACOS_DISTRIBUTION.md). The checklist below remains
the acceptance boundary for any customer pilot.

- Choose a permanent bundle identifier and signing/provisioning arrangement.
  Validate Keychain behavior in the app and CLI helper, after lock/unlock, denied
  access, restart, app replacement, update, and rollback. Decide and validate any
  Data Protection Keychain migration; do not infer its guarantees from this build.
- Produce supported architecture artifacts from a controlled build. Test on the
  declared minimum macOS version and clean Macs without Python or development
  tools. Install the app at a stable location before creating helper paths.
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
  mapping, consent, failure, deprovisioning, revocation and recovery. Qualify pinned
  clients' cached-credential 401 recovery without retrying ambiguous inference.
- Complete production gateway tenancy/provider custody, distributed rate limits,
  durable sessions, backup/recovery, operational monitoring and support. Those
  are hosted-service work, not supplied by this Mac window.
- Perform independent onboarding and security/accessibility review. Local fixture
  runs do not change the `0/5 initial` or `0/1 returning` onboarding counts.

No app-store submission is required by this local milestone. Direct signed and
notarized distribution remains the proposed first route. This change neither
merges nor releases either PR, creates a cloud service, configures billing, nor
accesses Apple signing credentials.
