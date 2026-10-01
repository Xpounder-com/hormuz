# Hormuz Mac: one sign-in path

Status: the desktop enrollment contract, server-chosen profile, Mac first-run
screen, Keychain session path, and automatic Hormuz-owned launcher are
implemented and verified with local fixtures. The new HTTPS service was
suspended on 2026-09-30 to avoid an additional paid Render instance. Its Okta
callbacks remain registered, but it is not serving traffic. The existing paid
provider pilot and PostgreSQL database are the no-new-resource path for a Mac
development pilot. Desktop routes, real desktop sign-in, and a signed release
are **not deployed**.

Live Render check on 2026-09-30: `hormuz-desktop`
(`srv-dauev8h7lnhs73b3vrvg`) has the assigned origin
`https://hormuz-desktop.onrender.com`. Its first deploy
(`dep-dauev917lnhs73b3vtsg`) succeeded from `main` at
`dde4dbf245ee0c47d796f0d4ebd61ed6e7cec0ee`, before these desktop routes.
It has its own 1 GB disk at `/var/lib/hormuz/private`, auto-deploy disabled,
and `HORMUZ_HOSTED_MODE=maintenance`. Direct HTTPS checks returned `200` with
`status=maintenance` and `inference_enabled=false` at `/health`, and `503` at
`/ready`, `/console`, both auth callbacks, and a model route. This is a closed
entry point, not a functioning Mac sign-in or production inference service.

On 2026-09-30, the existing Okta app **Hormuz Hosted Login (non-production)**
retained both preflight callbacks and saved the dedicated service's exact
`/v1/auth/callback` and `/v1/admin/auth/callback` URLs. Its PKCE requirement
remained enabled. Render deploy `dep-daufig6k1f9s73bl3lj0` succeeded from the
same `dde4dbf245ee0c47d796f0d4ebd61ed6e7cec0ee` source revision after
the six-field `hormuz-hosted.json` and `HORMUZ_CONFIG` path were saved. The
mounted profile was copied into a private regular file on the new disk and
validated by the deployed Linux loader with synthetic credentials. Identity
state is not initialized and no service credentials were entered. Direct HTTPS
checks still returned maintenance health and `503` for readiness, both
callbacks, and desktop enrollment. The service is now suspended; Render's
dashboard says suspended services are not billed. No second PostgreSQL database
was created. The short period before suspension accrued a small charge.

Live Render check on 2026-09-29: `hormuz-https-preflight`
(`srv-daaqhpvavr4c73b7b0kg`) was serving `provider_pilot` and returned ready
at deployed commit `d854a5a453fcbe20cb3f4c1e261e146f2da93855`. It builds
`deploy/render/gateway/Dockerfile` from `Xpounder-com/hormuz` `main`, with
auto-deploy disabled. That revision predates these desktop routes; its preflight
hostname is deliberately rejected by the signed Mac release gate. The Render
project's “Production” label does not change the service's pilot contract.
That deployed source expects SQLite usage schema 12 and PostgreSQL schema 17;
the current branch expects 19 and 24. Reusing its paid PostgreSQL instance
avoids another resource charge. A second logical database within that instance
can be initialized with distinct roles, while the existing login state still
needs an offline SQLite migration under maintenance before the new desktop
routes can serve traffic.

On 2026-10-01, `hormuz_desktop_v1` was created as a second logical database
inside the same paid PostgreSQL 16 instance. The original logical database
remains intact. New direct migration and runtime logins authenticate without
role impersonation; Hormuz's restricted bootstrap completed schema 24 and
verified four authorization roles. The migration password is held in the Mac
login Keychain, not in the web-service environment. This database preparation
does not mean the gateway code or Mac sign-in is deployed.

## Decision

The default hosted Mac experience has one setup action: **Continue with Hormuz**.
The browser handles identity. Hormuz then supplies the approved organization,
gateway origin, client, and model alias. The Mac app prepares its own launcher and
offers **Open Codex** or **Open Claude Code**. Returning users restore the saved
session and see the companion immediately. Self-hosted operators retain the
existing manual connection path under Advanced.

This path needs a canonical Hormuz HTTPS origin shipped with the signed Mac app.
`https://usehormuz.github.io` remains the public site. Its static GitHub Pages
hosting cannot run the enrollment, OIDC callback, session, and gateway routes.
The new Render service has the HTTPS `onrender.com` address above, but is
suspended. The existing paid preflight service is the current pilot origin. The
repository still has examples and a provider-free hosted staging profile; no
desktop origin has been placed in a signed Mac build or qualified for live
desktop sign-in and inference.
A signed one-click build must explicitly set a qualified canonical origin and
reject example, local, pilot, preflight, staging, or GitHub Pages hosts. A signed
build without that setting keeps the existing manual setup, so this unfinished
hosted pilot does not block unrelated Mac releases. A development build may
explicitly inject a local fixture or the existing preflight origin for a bounded
internal pilot. That does not qualify the preflight service for distribution.

## User flow

1. Install and open the app. Show one primary button, **Continue with Hormuz**,
   in the regular Mac window. Keep Connection, Client, Appearance, gateway URL,
   organization ID, issuer, and model alias out of the default first-run view.
2. The app prefers an installed Codex executable, then Claude Code, using the
   process path and common macOS install locations. It requests a short-lived
   desktop enrollment from the fixed Hormuz origin. Client detection is a hint;
   the server validates which client the signed-in person may use.
3. Open the returned login URL in the system browser. The browser completes OIDC.
   When the person belongs to one active organization, continue automatically.
   A managed invitation binds its organization in the browser; a returning
   managed member can continue automatically when the issuer has one managed
   organization. Static identity mappings work alongside managed onboarding.
   A browser picker for multiple managed memberships remains to be built. Do
   not ask for an organization ID in the Mac app.
4. Redeem the enrollment in the app. The server returns a tenant-bound session
   plus a versioned desktop profile with its canonical gateway origin and one
   approved default model alias for the selected client. The app verifies the
   profile, saves the session in Keychain, and fetches identity and usage.
5. Prepare a Hormuz-owned launcher in the existing private state directory.
   Never modify Codex or Claude Code's existing settings, login, prompts, or
   history. Offer **Open Codex** or **Open Claude Code** as the sole next action.
   Launching starts a new client session through Hormuz's existing loopback relay.
6. Show the edge companion once identity and usage are verified. Before the first
   governed request, say **No Hormuz activity yet**. The rings must never show
   sample numbers in a non-preview run. Existing client sessions remain outside
   Hormuz until restarted through the app.

The current Mac flow detects presence when choosing a client. The launcher
helper checks the pinned client version at launch and refuses unsupported
versions; the Mac UI does not yet show an install or version recovery screen.
A session
or profile failure retains the last trustworthy status and offers Retry or Sign
out; it never falls back to an arbitrary gateway or provider credential.

## Hosted entry point

Use one canonical HTTPS origin for desktop enrollment, session APIs, gateway
identity, usage, and governed inference. This avoids passing an access token
between unrelated origins. Keep the public site on GitHub Pages. To avoid a
second recurring Render bill, first qualify the new desktop contract on the
existing paid `hormuz-https-preflight` service and its existing PostgreSQL
database. This is an internal pilot only. The service's preflight hostname is
deliberately excluded from the signed distribution build; production release
configuration and live sign-in qualification remain pending. Repurposing this
same service for distribution would require explicit operational review and
acceptance before changing that release gate.
Before more than one customer uses that origin, provider
routes, provider credentials, rate cards, and usage attribution must resolve
from the authenticated organization rather than from a shared gateway default.

The current `/v1/auth/enrollments` flow requires a client and may require an
organization before browser sign-in. Keep it for existing clients. Add a separate
versioned desktop contract so older clients do not change behavior:

| Route | Auth | Purpose |
| --- | --- | --- |
| `POST /v1/auth/desktop/enrollments` | None | Create a short-lived, one-time enrollment bound to a device secret and preferred supported client. Return an exact-origin browser login URL, enrollment ID, expiry, and bounded poll interval. |
| `POST /v1/auth/desktop/enrollments/{id}/redeem` | Device secret | Return the access and refresh session pair plus the validated desktop profile only after browser login and membership selection. |
| `GET /v1/auth/desktop/profile` | Session bearer | Return the current organization, allowed clients, selected client, approved model alias, and profile version for restore or policy changes. |

The desktop profile has `schema_id = "hormuz.desktop-profile"`,
`schema_version = 1`, `gateway_origin`, `organization_id`, `client`,
`model_alias`, and `profile_version`. The server derives all fields from the
authenticated membership and operator configuration. The client-provided
preferred client only selects among allowed clients; it never grants access.
The server requires one explicit default model alias per enabled
organization/client pair under `authentication.session_broker.desktop_defaults`.
For example:

```json
"desktop_defaults": {
  "org-a": {"codex": "safe-openai", "claude-code": "safe-claude"}
}
```

Configuration validation requires a routed alias with the matching protocol.
Redemption and profile refresh check the signed-in person's active effective
policy, including a PostgreSQL managed policy when enabled. A new active policy
version changes the desktop profile version, so the credential helper withholds
the saved launcher token until the person reconnects. An absent or disallowed
default is an administrator action state, not a Mac-side picker.

The browser login uses the existing OIDC state, nonce, PKCE, and cookie checks.
Enrollment credentials remain in the native app and never appear in the URL or
browser page. The redeem operation is one-time, bounded by expiry, and uses the
existing session-token rotation and revocation semantics. The native client
accepts only the configured HTTPS origin and rejects redirects, mismatched
organization/client/profile fields, oversized responses, or an unrecognized
schema version. The app continues to show the chosen origin in connection
details so the user can identify the service receiving requests.

## Build sequence

1. Implement the desktop enrollment/profile routes and native one-button flow:
   complete in the local source and provider-free tests.
2. Use the existing paid `hormuz-https-preflight` service and existing
   PostgreSQL database for a one-person internal pilot. Both exact callback URLs
   are already registered in Okta. The provider profile needs an explicit
   `desktop_defaults` alias for its existing organization and approved Codex
   route; adding this field leaves the initialized identity binding intact.
   Review and back up the private profile and existing stores, close inference
   in maintenance, then deploy the reviewed code while maintenance remains on.
   Follow the hosted runbooks for the SQLite 12-to-19 and PostgreSQL 17-to-24
   migrations. The PostgreSQL step needs the retained direct migration
   credential, which is intentionally absent from the serving process. Add the
   desktop default to the private provider profile, validate it with
   `provider-check`, remove the migration credential from the service, and
   restore the same `provider-pilot` scope. Verify a real browser callback and
   enrolled Codex session. Do not add a service, database, disk, or provider
   credential.
   A green `/ready` response is intermediate evidence, not production
   qualification. Use an ad hoc development Mac build with the preflight origin
   for this pilot; keep the signed distribution gate unchanged.
3. Add browser selection for multiple managed memberships and client
   installation/version recovery if the hosted product requires those cases.
4. After the live pilot and production qualification, decide whether to
   repurpose the existing paid service as the canonical hosted entry point.
   Set the protected GitHub environment variable `HORMUZ_DESKTOP_ORIGIN` for a
   one-click release only after qualifying the production HTTPS origin. Package
   and notarize a signed build,
   and verify installed initial and returning sign-in plus a launched client
   request. The public GitHub Pages site can then link to the reviewed Mac
   download. Do not start another paid resource without a separate cost decision.

## Native implementation boundaries

- Keep `ConnectionModel` as the single owner of the session, dashboard, and
  connector. Add a dedicated `signInDesktop()` path rather than making manual
  fields look optional while the old enrollment call still requires them.
- Restore a valid Keychain session before showing first-run UI. Refresh the
  desktop profile and usage; do not infer a current connection from a saved
  launcher alone.
- Reuse `ConnectorPlan` and the loopback relay. Save the Hormuz-owned launcher
  automatically after verified sign-in, with the same integrity checks and
  backup behavior as the current explicit Save action. Keep its exact contents
  inspectable under Connection details.
- Launch only after the person chooses **Open Codex** or **Open Claude Code**.
  A first sign-in must not silently start a terminal process. Use the user's
  selected project directory when one exists; otherwise use their home
  directory and show it in the launch action.
- Keep manual self-hosted connection in Advanced. Never silently switch an
  existing manual profile to the canonical hosted origin.

## Acceptance before calling this shipped

- Provider-free tests cover server-selected organization/model, policy and
  client rejection, one-time redemption, restore profile, governed fixture
  inference, and usage. Swift tests cover profile-origin rejection, session
  revocation on mismatch, and changed profile on restore. The remaining
  membership, installed-client, and installed-app cases above stay open.
- An installed Mac app completes first sign-in and a returning launch without
  entering gateway, organization, issuer, model, or a terminal command.
- A launched supported client sends one fixture request through the Hormuz
  relay; the resulting gateway usage appears in the widget. An already-running
  client does not falsely appear as governed usage.
- Keychain storage, launcher file integrity, sign-out revocation, Reduced Motion,
  keyboard navigation, and clean-machine installation are checked separately.
  No paid provider call or deployment is implied by local fixture success.
