# Direct Mac distribution

Hormuz's first customer distribution path is a Developer ID signed and Apple-notarized download. It does not require Mac App Store review, App Sandbox adoption, or an App Store listing; Apple's [notarization overview](https://developer.apple.com/documentation/security/notarizing-macos-software-before-distribution) describes this automated trust check as separate from App Review. The existing local preview remains an ad hoc build with bundle identifier `com.hormuz.mac.local`; it is never a customer artifact.

The permanent identifier is `com.xpounder.hormuz`, registered as an explicit App ID in Apple Developer team `R267LZMUTY`. Treat both values as identity decisions: changing the identifier or signing team later changes the app's designated requirement and can disrupt Keychain access, updates, and rollback behavior. The current app has no custom entitlements and uses no provisioning profile. Apple's [Developer ID guidance](https://developer.apple.com/support/developer-id/) requires a Developer ID provisioning profile only when an app adopts advanced capabilities such as CloudKit; registering the explicit App ID now reserves the customer identity without adding such a profile to this build.

## What the Developer membership supplies

The release needs two Apple-controlled credentials:

1. A **Developer ID Application** certificate and private key, exported as a password-protected PKCS#12 (`.p12`) file. This signs the app outside the Mac App Store.
2. An App Store Connect **team** API key authorized for notarization. Apple's [current API-key contract](https://developer.apple.com/documentation/appstoreconnectapi/creating-api-keys-for-app-store-connect-api) says individual keys cannot use `notarytool`. Team keys apply across every app in the account, so select the least privileged role that passes `notarytool store-credentials --validate`, dedicate the key to Hormuz notarization, and keep its one-time-download `.p8` private key outside the repository. Never put it in a workflow input, shell argument, issue, artifact, or log.

The app uses hardened runtime and no custom entitlements. The customer binary
targets Apple Silicon (`arm64`) on macOS 14 or later. Intel Macs are outside the
supported distribution boundary: the release is neither built nor qualified for
`x86_64`. Its only dynamic dependencies are Apple system frameworks and libraries.
The same signed executable provides the window and the Keychain credential helper.
Context optimization adds one separately signed arm64 standalone backend behind a
fixed local dispatcher; it contains the same Hormuz release code and uses the two
digest-verified tokenizer vocabularies bundled as resources. The dispatcher is a
shell resource at `Contents/Resources/ContextHelper/hormuz-context`, sealed by the
outer app signature. The Mach-O backend remains nested code at
`Contents/Helpers/hormuz-context-arm64` and carries its own Developer ID signature.
This layout keeps the script's integrity in the bundle seal when ZIP transfer drops
extended attributes used by standalone script signatures.

## Local packaging and notarization

Confirm that exactly one intended identity is available:

```sh
security find-identity -v -p codesigning
```

Store notarization credentials in Keychain using `xcrun notarytool store-credentials`; do not place credentials directly in the packaging command. This follows Apple's [custom command-line notarization workflow](https://developer.apple.com/documentation/security/customizing-the-notarization-workflow). Then build the upload archive:

```sh
HORMUZ_CODESIGN_IDENTITY='Developer ID Application: Company Name (TEAMID)' \
  ./script/package_macos_release.sh \
  --output-directory /private/tmp/hormuz-macos-1.2.0 \
  --bundle-id com.xpounder.hormuz \
  --version 1.2.0 \
  --build 1 \
  --context-helper-directory /private/path/context-helpers \
  --tokenizer-cache /private/path/context-tokenizers
```

Submit, staple, and repackage the same app:

```sh
./script/notarize_macos_release.sh \
  --bundle /private/tmp/hormuz-macos-1.2.0/Hormuz.app \
  --upload-archive /private/tmp/hormuz-macos-1.2.0/Hormuz-1.2.0-notarization-upload.zip \
  --keychain-profile hormuz-notary
```

Build the arm64 backend with `tools/build_context_helper.sh` on an Apple Silicon
runner. Populate the tokenizer directory with `hormuz context resources install`;
this is the explicit network step, while model request handling never downloads
resources. The protected workflow performs the helper build and resource
installation automatically.

Packaging refuses an existing output directory, a `.local` identifier, any app or
helper architecture other than exact `arm64`, tokenizer digest mismatches, an
ambiguous signing identity, non-system runtime dependencies, custom entitlements,
a missing secure timestamp, or unexpected archive files. Notarization must return
`Accepted`; the ticket is then stapled to the app, Gatekeeper is assessed, and a
new `Hormuz-<version>-notarized.zip` is produced. `distribution-proof.json`
records only digests and content-free verification results. Historical pre-v1.2
releases use the exact v2 shape. Context-capable v1.2.0 and later candidates
require v3, which adds helper packaging, the arm64 helper signature and digest,
the bundle-sealed launcher state and digest, tokenizer names, and an explicit runtime-verification boolean
while retaining the exact source commit and GitHub Actions run URL.

The notarization step also downloads Apple's private submission log into a temporary directory, requires no reported issues and at least one ticket entry for the Apple Silicon app, then deletes the raw log. Its retained summary contains only the submission ID, acceptance state, issue counts, and ticket-entry count. The final verifier extracts the customer ZIP and repeats signature, stapler, and Gatekeeper checks on that extracted copy, so packaging cannot silently discard the ticket.

For a credential-free rehearsal, use `--ad-hoc`. The resulting metadata always says `distribution_ready: false`; it cannot be promoted or given to customers.

## Protected GitHub workflow

The manual **Mac signed distribution** workflow performs the same steps on a GitHub-hosted Mac. Configure a protected `macos-distribution` environment with required review and these secrets:

| Secret | Value |
| --- | --- |
| `MACOS_DEVELOPER_ID_P12_BASE64` | Base64 of the password-protected Developer ID `.p12` |
| `MACOS_DEVELOPER_ID_P12_PASSWORD` | Export password for that `.p12` |
| `APPLE_NOTARY_KEY_P8_BASE64` | Base64 of the dedicated App Store Connect **team** API `.p8`; individual keys do not work with `notarytool` |
| `APPLE_NOTARY_KEY_ID` | API key ID |
| `APPLE_NOTARY_ISSUER_ID` | Team API issuer UUID |

The workflow separates general compilation and testing from final notarization.
An Apple Silicon helper job in the protected environment imports only the
Developer ID certificate and builds the one-file arm64 context backend with
PyInstaller's signing option, because its embedded libraries must be signed while
the executable is assembled. It executes a credential-free help path and Codex
relay check, then deletes its temporary Keychain and certificate before
transferring the helper. A credential-free job tests and builds the exact arm64
app, installs verified tokenizer resources, executes the helper, packages a
disposable ad hoc bundle, and records the source commit, permanent bundle
identifier, CI-derived build number, architecture, tokenizer digests, and payload
digests. A fresh protected runner independently repeats the manifest, commit,
architecture, helper signature, team, resource, and digest checks before receiving
notarization credentials. That final runner never executes the transferred
payload; its v3 proof therefore records `context_helper_runtime_verified: false`,
while the earlier jobs supply separate runtime execution gates. Its bundle and
archive checks are static plus Apple signature, notarization, stapler, and
Gatekeeper verification.

The dispatcher supplies only the three-component marketing version, and the workflow requires it to equal the root Hormuz package version. The bundle identifier is pinned in the protected workflow to `com.xpounder.hormuz`, and the imported Developer ID identity must belong to team `R267LZMUTY`. `CFBundleVersion` is derived as `GITHUB_RUN_NUMBER * 1000 + GITHUB_RUN_ATTEMPT`, which increases for both new workflow runs and reruns and reserves up to 999 attempts per run. Operators cannot reuse, lower, or replace these release-identity values through workflow inputs.

All jobs refuse a feature branch: the checked-out commit must be the repository's exact default-branch commit selected by the workflow run. The protected environment should independently restrict deployment to protected branches, require a reviewer, and disallow administrator bypass. The signing job creates an ephemeral Keychain, imports only the supplied identity, validates notarization credentials, then deletes the raw credential files and unsets their environment values before it handles the payload. It deletes the Keychain before the step exits. It has read-only repository permission. It uploads the notarized archive, dSYM, and content-free proofs for 30 days; it cannot create a GitHub release or publish the artifact. Publication remains a separate digest-reviewed decision.

Generate the team key with a dedicated name such as `Hormuz Notarization CI`. Apple makes the private half downloadable only once. Record the key ID and issuer ID separately, provision the five environment secrets through an encrypted secret-setting path, validate that the environment contains exactly those five names, then remove the downloaded `.p8` and exported `.p12` from ordinary working directories. Do not reuse an Admin key merely because it already exists: team keys are account-wide, and their role cannot be edited after creation.

Apple's stapler adds `Hormuz.app/Contents/CodeResources` to the accepted app. The final archive verifier requires that ticket file only in notarized mode, compares its exact archived bytes with the stapled bundle, and rejects it from pre-notarization archives. This keeps the upload and customer archive shapes distinct while proving that the distributed ZIP retains the offline ticket.

## Pilot qualification after notarization

Notarization proves Apple scanned and accepted the submitted bytes. It does not prove customer behavior, Keychain continuity, gateway availability, or safe updates. Before an external pilot:

- Download the artifact through the intended delivery channel, apply normal quarantine, extract it, and confirm Gatekeeper acceptance on a clean Apple Silicon Mac without developer tools.
- Install in `/Applications`, complete real IdP login, restart, lock/unlock, refresh, sign out, revoke, reinstall the same build, update to a newer build, and test a supported rollback. Confirm the credential remains available only where intended.
- Re-run the selected pinned-client `401` gate with the signed installed app.
  `codex_openai` requires Codex only: it refreshes and completes with one
  provider egress. The default `full_dual_provider` contract additionally
  requires Claude Code to refresh without egress on the rejected turn, then
  complete an explicit next request with the clean-credential egress count. The
  signed-artifact run must compose the selected semantics with the native
  Keychain helper. Missing Claude evidence never narrows the default contract.
- Run a real hosted gateway with production tenant isolation, provider custody, durable sessions, monitoring, recovery, and a documented support path. Keep Render authentication staging inference-disabled until that separate gateway profile exists.
- Complete security and accessibility review, then obtain independent initial and returning-user evidence. Internal and fixture runs do not change the `0/5 initial` or `0/1 returning` counts.

The executable [signed Mac pilot qualification](MACOS_PILOT_QUALIFICATION.md)
binds these gates to the exact notarized archive, distribution proof, and Apple
notarization summary. For real evidence it also authenticates the protected
workflow run and exact retained Actions-artifact members through GitHub's API.
Its synthetic fixture validates only the contract shape; it can never qualify a
pilot or change the external-onboarding counts. The protected **Mac
controlled-pilot operations** workflow now collects gates for clean install,
session lifecycle, and pinned-client authentication. It requires one clean
Apple Silicon self-hosted runner and a completed real run; a
developer workstation with Xcode or Command Line Tools is deliberately
rejected.

The operations workflow requires an explicit qualification contract. Its
default `full_dual_provider` path retains the existing two-client behavior; the
`codex_openai` choice emits schema v2 evidence, uses the
`external_pilot_openai` deployment, and neither installs, invokes, nor records
Claude Code. Signing and notarization establish artifact identity and macOS
distribution trust; they do not select or satisfy either hosted-client scope.

There is no authenticated automatic updater yet. Distribute versioned archives manually during the pilot and retain the previous notarized archive and digest for controlled rollback. Do not promise an availability or latency SLA from signing, notarization, or a single-node Render staging deployment.
