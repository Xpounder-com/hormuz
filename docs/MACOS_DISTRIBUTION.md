# Direct Mac distribution

Hormuz's first customer distribution path is a Developer ID signed and Apple-notarized download. It does not require Mac App Store review, App Sandbox adoption, or an App Store listing; Apple's [notarization overview](https://developer.apple.com/documentation/security/notarizing-macos-software-before-distribution) describes this automated trust check as separate from App Review. The existing local preview remains an ad hoc build with bundle identifier `com.hormuz.mac.local`; it is never a customer artifact.

The proposed permanent identifier is `com.xpounder.hormuz`. Register and approve that exact identifier in the Apple Developer account before the first pilot build. Treat this as an identity decision: changing the identifier or signing team later changes the app's designated requirement and can disrupt Keychain access, updates, and rollback behavior.

## What the Developer membership supplies

The release needs two Apple-controlled credentials:

1. A **Developer ID Application** certificate and private key, exported as a password-protected PKCS#12 (`.p12`) file. This signs the app outside the Mac App Store.
2. An App Store Connect API key authorized for notarization. Keep its `.p8` private key outside the repository and never put it in a workflow input, shell argument, issue, artifact, or log.

The app uses hardened runtime and no custom entitlements. It is a universal `arm64`/`x86_64` binary for macOS 14 or later. Its only dynamic dependencies are Apple system frameworks and libraries. The same signed executable provides the window and the Keychain credential helper, avoiding a separately signed nested helper.

## Local packaging and notarization

Confirm that exactly one intended identity is available:

```sh
security find-identity -v -p codesigning
```

Store notarization credentials in Keychain using `xcrun notarytool store-credentials`; do not place credentials directly in the packaging command. This follows Apple's [custom command-line notarization workflow](https://developer.apple.com/documentation/security/customizing-the-notarization-workflow). Then build the upload archive:

```sh
HORMUZ_CODESIGN_IDENTITY='Developer ID Application: Company Name (TEAMID)' \
  ./script/package_macos_release.sh \
  --output-directory /private/tmp/hormuz-macos-0.1.0 \
  --bundle-id com.xpounder.hormuz \
  --version 0.1.0 \
  --build 1
```

Submit, staple, and repackage the same app:

```sh
./script/notarize_macos_release.sh \
  --bundle /private/tmp/hormuz-macos-0.1.0/Hormuz.app \
  --upload-archive /private/tmp/hormuz-macos-0.1.0/Hormuz-0.1.0-notarization-upload.zip \
  --keychain-profile hormuz-notary
```

Packaging refuses an existing output directory, a `.local` identifier, a non-universal binary, an ambiguous signing identity, non-system runtime dependencies, custom entitlements, a missing secure timestamp, or unexpected archive files. Notarization must return `Accepted`; the ticket is then stapled to the app, Gatekeeper is assessed, and a new `Hormuz-<version>-notarized.zip` is produced. `distribution-proof.json` records only digests and content-free verification results.

The notarization step also downloads Apple's private submission log into a temporary directory, requires no reported issues and at least two ticket entries for the universal app, then deletes the raw log. Its retained summary contains only the submission ID, acceptance state, issue counts, and ticket-entry count. The final verifier extracts the customer ZIP and repeats signature, stapler, and Gatekeeper checks on that extracted copy, so packaging cannot silently discard the ticket.

For a credential-free rehearsal, use `--ad-hoc`. The resulting metadata always says `distribution_ready: false`; it cannot be promoted or given to customers.

## Protected GitHub workflow

The manual **Mac signed distribution** workflow performs the same steps on a GitHub-hosted Mac. Configure a protected `macos-distribution` environment with required review and these secrets:

| Secret | Value |
| --- | --- |
| `MACOS_DEVELOPER_ID_P12_BASE64` | Base64 of the password-protected Developer ID `.p12` |
| `MACOS_DEVELOPER_ID_P12_PASSWORD` | Export password for that `.p12` |
| `APPLE_NOTARY_KEY_P8_BASE64` | Base64 of the App Store Connect API `.p8` |
| `APPLE_NOTARY_KEY_ID` | API key ID |
| `APPLE_NOTARY_ISSUER_ID` | Team API issuer UUID |

The job creates an ephemeral Keychain, imports only the supplied identity, validates notarization credentials, and deletes credential files and the Keychain before the step exits. It has read-only repository permission. It uploads the notarized archive, dSYM, and content-free proofs for 30 days; it cannot create a GitHub release or publish the artifact. Publication remains a separate digest-reviewed decision.

## Pilot qualification after notarization

Notarization proves Apple scanned and accepted the submitted bytes. It does not prove customer behavior, Keychain continuity, gateway availability, or safe updates. Before an external pilot:

- Download the artifact through the intended delivery channel, apply normal quarantine, extract it, and confirm Gatekeeper acceptance on clean Apple Silicon and Intel Macs without developer tools.
- Install in `/Applications`, complete real IdP login, restart, lock/unlock, refresh, sign out, revoke, reinstall the same build, update to a newer build, and test a supported rollback. Confirm the credential remains available only where intended.
- Qualify the pinned Codex and Claude Code clients when a cached access token receives `401`: refresh and retry only the authentication exchange, never an ambiguous inference request.
- Run a real hosted gateway with production tenant isolation, provider custody, durable sessions, monitoring, recovery, and a documented support path. Keep Render authentication staging inference-disabled until that separate gateway profile exists.
- Complete security and accessibility review, then obtain independent initial and returning-user evidence. Internal and fixture runs do not change the `0/5 initial` or `0/1 returning` counts.

There is no authenticated automatic updater yet. Distribute versioned archives manually during the pilot and retain the previous notarized archive and digest for controlled rollback. Do not promise an availability or latency SLA from signing, notarization, or a single-node Render staging deployment.
