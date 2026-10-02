# Hormuz Mac: one sign-in path

Status: the one-button flow is verified in an internal development Mac build
against the existing paid `hormuz-https-preflight` service. The app completes
browser sign-in with the existing Okta setup, saves its session in Keychain,
loads its server-selected Codex profile and usage, and prepares **Open Codex**.
The same account restores after restarting the app. Before any governed request,
the companion shows verified zero activity. No paid model request was sent during
this verification. Signed distribution, clean-machine installation, and external
provider qualification remain separate gates.

Verified live on 2026-10-01 at `d8cdd14912cfc15e805e8a2cf80a3734f8f0b03f`, Render deploy
`dep-dav79s942hec73d9ubhg`, on fresh instance `srv-daaqhpvavr4c73b7b0kg-66cfd8f49b-98cfx`.
`/health`, `/ready`, `/v1/auth/desktop/profile`, `/v1/gateway/whoami`, and
`/v1/gateway/usage` returned `200`; `provider-check` verified the private
configuration, restricted PostgreSQL runtime, and four-connection pool.
The migration credential is absent from the serving environment. The client
launcher and its packaged helper accepted the saved desktop-managed profile.
The ad hoc development rebuild requested macOS Keychain permission; after the
owner approved it on the Mac, session, identity, and usage checks all returned
`200`, and the native companion showed verified zero activity.

The added `hormuz-desktop` service remains suspended. This pilot uses the
existing paid web service and PostgreSQL instance, without another service,
database instance, disk, or provider credential. The public site remains
`https://usehormuz.github.io`.

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
auto-deploy disabled. That historical revision predates the desktop routes;
the current internal pilot was upgraded on 2026-10-01 as recorded above. Its
preflight hostname is deliberately rejected by the signed Mac release gate.
The Render project's “Production” label does not change the service's pilot contract.
That deployed source expects SQLite usage schema 12 and PostgreSQL schema 17;
the current branch expects 19 and 24. Reusing its paid PostgreSQL instance
avoids another resource charge. A second logical database within that instance
was initialized with distinct roles on 2026-10-01. The offline SQLite
12-to-19 migration completed under maintenance before desktop routes were
activated on the same provider-pilot service.

On 2026-10-01, `hormuz_desktop_v1` was created as a second logical database
inside the same paid PostgreSQL 16 instance. The original logical database
remains intact. New direct migration and runtime logins authenticate without
role impersonation; Hormuz's restricted bootstrap completed schema 24 and
verified four authorization roles. The migration password is held in the Mac
login Keychain, not in the web-service environment. After database preparation,
the gateway was deployed, its runtime verified, and real Mac sign-in completed
as recorded above. The original logical database remains available.

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
internal development build injects that origin and has completed real desktop
sign-in. No desktop origin has been placed in a signed Mac build; signed
distribution and a launched live provider request remain unqualified.
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
3. Open the returned login URL in the system browser. The gateway sets its
   callback cookie and redirects directly to the configured Okta sign-in page,
   without an intermediate invitation form. The browser completes OIDC.
   When the person belongs to one active organization, continue automatically.
   The secondary **Use team invitation** action opens a separate join page.
   A managed invitation binds its organization there; a returning
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

## Public account registration

The selected account flow is public signup with team access approved separately:
**Continue with Hormuz → Okta Sign up → verify email → return to Hormuz**.
Users create a Hormuz account in the existing Okta organization; they do not
need to create or administer their own Okta organization.

For an Okta Identity Engine organization, create a dedicated user profile policy
for the Hormuz OIDC application, allow self-service registration, require email
verification, and assign that policy only to the Hormuz application. The hosted
sign-in widget then offers the signup form. Keep registration and app assignment
separate from team permissions, administrative groups and provider access. These
are external Okta settings, not enabled by deploying this gateway change.
See [Okta self-service registration](https://help.okta.com/oie/en-us/Content/Topics/identity-engine/policies/about-ssr.htm)
and [user profile policies](https://help.okta.com/oie/en-us/content/topics/identity-engine/policies/configure-profile-enrollment-policy.htm).

An Okta Integrator Free Plan organization supports only ten active users and is
intended for non-production testing. Its registration policy can qualify the
pilot signup flow, but public customer onboarding requires a production identity
plan. Changing the identity plan is a separate cost decision.
See [Integrator limits](https://developer.okta.com/docs/reference/org-defaults/)
and [Okta's production-use boundary](https://developer.okta.com/blog/2025/05/13/okta-developer-edition-changes).

The gateway continues to require an active Hormuz membership or configured
identity. A new Okta account without approved team access sees **Team access
required** after authentication and receives no gateway session, provider access
or administrative privilege. A team administrator approves the person's access
through the existing managed onboarding workflow. **Use team invitation** is the
explicit join action for an approved invitation; returning members use normal
sign-in. Public signup does not automatically enroll anyone in the internal pilot
organization. The existing operator-issued invitation delivery remains manual.

## Hosted entry point

Use one canonical HTTPS origin for desktop enrollment, session APIs, gateway
identity, usage, and governed inference. This avoids passing an access token
between unrelated origins. Keep the public site on GitHub Pages. To avoid a
second recurring Render bill, first qualify the new desktop contract on the
existing paid `hormuz-https-preflight` service and its existing PostgreSQL
database. This is an internal pilot only. The service's preflight hostname is
deliberately excluded from the signed distribution build; production release
configuration and external provider qualification remain pending. Real sign-in
has been verified for the internal development build as recorded above.
Repurposing this same service for distribution would require explicit operational review and
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
2. Use the existing paid `hormuz-https-preflight` service and the new
   `hormuz_desktop_v1` logical database in its existing paid PostgreSQL instance
   for a one-person internal pilot. Both exact callback URLs
   are already registered in Okta. The provider profile needs an explicit
   `desktop_defaults` alias for its existing organization and approved Codex
   route; adding this field leaves the initialized identity binding intact.
   Review and back up the private profile and existing stores, close inference
   in maintenance, then deploy the reviewed code while maintenance remains on.
   Follow the hosted runbook for the SQLite 12-to-19 migration. The new logical
   database already has PostgreSQL schema 24, so point the service's restricted
   runtime DSN at it without putting its retained migration credential in the
   serving environment. Add the desktop default and distinct authorization
   roles to the private provider profile, validate it with `provider-check`,
   and restore the same `provider-pilot` scope. Verify a real browser callback
   and enrolled Codex session. Do not add a Render service, PostgreSQL instance,
   disk, or provider credential.
   This internal sign-in pilot was completed on 2026-10-01 as recorded above.
   A green `/ready` response remains intermediate evidence for production
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
  multi-membership, client installation recovery, and clean-machine cases
  above stay open. A real returning account, installed Codex selection, the
  native desktop profile, and packaged-helper compatibility were verified for
  the internal development build.
- An installed Mac app completes first sign-in and a returning launch without
  entering gateway, organization, issuer, model, or a terminal command.
- A launched supported client sends one fixture request through the Hormuz
  relay; the resulting gateway usage appears in the widget. An already-running
  client does not falsely appear as governed usage.
- Keychain storage, launcher file integrity, sign-out revocation, Reduced Motion,
  keyboard navigation, and clean-machine installation are checked separately.
  No paid provider call or deployment is implied by local fixture success.
