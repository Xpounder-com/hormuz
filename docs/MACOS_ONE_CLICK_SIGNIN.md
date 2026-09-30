# Hormuz Mac: one sign-in path

Status: the desktop enrollment contract, server-chosen profile, Mac first-run
screen, Keychain session path, and automatic Hormuz-owned launcher are
implemented and verified with local fixtures. A production HTTPS origin, live
OIDC/provider configuration, and a signed release are **not deployed**.

Live Render check on 2026-09-29: `hormuz-https-preflight`
(`srv-daaqhpvavr4c73b7b0kg`) was serving `provider_pilot` and returned ready
at deployed commit `d854a5a453fcbe20cb3f4c1e261e146f2da93855`. It builds
`deploy/render/gateway/Dockerfile` from `Xpounder-com/hormuz` `main`, with
auto-deploy disabled. That revision predates these desktop routes; its preflight
hostname is deliberately rejected by the signed Mac release gate. The Render
project's “Production” label does not change the service's pilot contract.

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
A dedicated production Render web service could use its HTTPS `onrender.com`
address without requiring a custom domain. The repository currently has examples
and a provider-free hosted staging profile, but no configured production origin.
The release build must fail if its canonical origin is unset or uses an example,
local, pilot, preflight, staging, or GitHub Pages host. A development build may
inject a local fixture origin explicitly.

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
   The current managed onboarding path requires a tenant-scoped gateway with
   exactly one active organization. Static identity mappings can resolve one
   organization after login. A browser picker for multiple managed memberships
   remains to be built. Do not ask for an organization ID in the Mac app.
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
identity, usage, and governed inference in the first hosted release. This avoids
passing an access token between unrelated origins. With the current domain
setup, keep the public site on GitHub Pages and put those dynamic routes on a
dedicated production Render web service. The Render hostname is a release
configuration decision; this design does not claim deployment of that service.
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
Redemption also checks the signed-in person's effective policy; an absent or
disallowed default is an administrator action state, not a Mac-side picker.

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
2. Create a dedicated Render web service and use its assigned HTTPS
   `onrender.com` origin for the sign-in and gateway API. Register that exact
   origin's `/v1/auth/callback` and `/v1/admin/auth/callback` redirect URLs in
   the existing Okta setup. Configure the service's private hosted profile with
   the same `public_origin`, Okta issuer/client, tenant-scoped provider route,
   and an explicit `desktop_defaults` alias for the intended client. Verify a
   real browser callback and governed request on the new service before using
   it in a release. The current provider-free staging and external pilot modes
   are not a production service by a hostname change alone.
   Provision its own persistent disk and operator profile; do not point a new
   service at the existing pilot state or reuse its session master key. Keep
   the service in maintenance while preparing state and Okta callbacks. A
   provider-pilot deploy and a green `/ready` response are intermediate evidence,
   not production qualification.
3. Add browser selection for multiple managed memberships and client
   installation/version recovery if the hosted product requires those cases.
4. Set the protected GitHub environment variable `HORMUZ_DESKTOP_ORIGIN` to
   that verified Render origin, package and notarize a signed build, and verify
   installed initial and returning sign-in plus a launched client request.
   The public GitHub Pages site can then link to the reviewed Mac download.

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
