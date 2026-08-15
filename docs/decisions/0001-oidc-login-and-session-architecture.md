# ADR 0001: OIDC login and Hormuz session architecture

- Status: **Proposed — owner approval required**
- Date proposed: 2026-08-15
- Decision owner: Product owner
- Tracking issue: [#2](https://github.com/Xpounder-com/hormuz/issues/2)
- Unblocks after acceptance: [#13](https://github.com/Xpounder-com/hormuz/issues/13), part of [#7](https://github.com/Xpounder-com/hormuz/issues/7)

## Decision requested

Choose how employees authenticate to Hormuz while continuing to use Codex and Claude Code:

1. **Hormuz session broker — recommended.** Hormuz performs generic OIDC browser login, maps the verified issuer and subject to a Hormuz principal, and issues its own short-lived, revocable, opaque client credentials.
2. **Customer-minted JWT access tokens only.** Keep the implemented resource-server path and require every customer to mint and continuously refresh a JWT access token whose audience is Hormuz.

This ADR proposes option 1. It is not accepted and does not authorize implementation until the product owner approves it.

## Context

Hormuz already validates JWT access tokens using OIDC discovery and JWKS, then resolves the exact `(issuer, subject)` pair to an organization, team, actor, clearance, and client policy. It deliberately rejects ID tokens as API credentials. That path is appropriate for service accounts, CI, and identity providers that can mint a JWT for a custom resource audience.

It does not yet solve the general employee login problem. Some identity providers issue opaque access tokens, customers vary in how they configure resource audiences, and the Codex command-backed auth hook and Claude Code `apiKeyHelper` need a credential that Hormuz can renew without asking employees to learn another AI client.

[OpenID Connect Core](https://openid.net/specs/openid-connect-core-1_0-final.html) defines authentication through the authorization-code flow and ID-token validation. [RFC 8252](https://www.rfc-editor.org/rfc/rfc8252.html) requires native apps to use an external user agent and PKCE. [RFC 9700](https://www.rfc-editor.org/rfc/rfc9700.html) requires PKCE for public clients and refresh-token rotation or sender constraining to detect replay.

## Proposed decision

Hormuz will be a confidential OIDC relying party and a session broker for human CLI clients. The existing direct JWT resource-server path remains supported for workloads and customer-managed identity agents.

### Browser enrollment flow

1. `hormuz login --gateway <url>` creates a high-entropy enrollment secret locally and requests a short-lived enrollment from Hormuz.
2. Hormuz returns a one-time HTTPS login URL. The CLI opens it in the operating system's external browser; it never embeds the identity-provider page.
3. Hormuz starts OIDC Authorization Code Flow with `state`, `nonce`, and PKCE S256 values that are unique, single-use, expiry-bound, and cryptographically bound to the enrollment and browser session.
4. The identity provider redirects only to Hormuz's pre-registered HTTPS callback. Hormuz requires exact issuer and redirect matching, exchanges the code server-side, and validates the ID token signature, issuer, audience, expiry, nonce, and subject.
5. Hormuz resolves `(issuer, subject)` through the authoritative tenant identity mapping. Email and caller-supplied team claims are never authorization inputs.
6. The browser shows a completion page containing no session credential. The CLI redeems the enrollment once using its independent secret and receives a Hormuz access/refresh pair.
7. The enrollment and temporary browser session are destroyed after redemption or expiry. A callback cannot directly place credentials in a URL, browser storage, or shell history.

The authorization request asks only for the scopes needed to authenticate and map the employee. Hormuz does not retain provider access or refresh tokens after the callback exchange.

### Hormuz credentials

- Access and refresh credentials are opaque, independently random values. Only keyed hashes and session metadata are stored server-side.
- Human access credentials default to 10 minutes and may be configured only within a documented 5–15 minute range.
- A human session defaults to a 12-hour absolute lifetime. Organizations may shorten it; increasing it is a separate security-policy choice.
- Every refresh rotates both credentials. Reuse of an invalidated refresh credential revokes the entire credential family and emits a metadata-only security event.
- Access credentials are audience-, tenant-, actor-, client-, and session-bound. They cannot be used for another Hormuz deployment or client identity.
- Disabling an actor, removing a membership, changing a clearance, admin revocation, logout, or detected refresh reuse revokes all affected sessions immediately.
- Static bootstrap credentials remain break-glass only and can be disabled per deployment.

The proposed lifetimes are defaults, not a promise that all customers must use the same values. They are included in the owner decision because they materially affect employee experience and exposure after credential theft.

### Client custody and refresh

- macOS stores session credentials in Keychain.
- Windows stores them in Credential Manager.
- Linux desktop stores them through Secret Service/libsecret.
- The CLI uses a per-session local lock and atomic replacement so concurrent Codex and Claude helpers cannot race refresh-token rotation.
- `hormuz auth token` reuses an unexpired access credential or rotates the session near expiry, then prints only the access credential for the calling client.
- If no supported secure store exists, persistent human login fails closed. A foreground, non-persistent credential or the workload/JWT path may be used instead; Hormuz will not silently write a refresh credential to a plaintext dotfile.
- CI and service accounts do not use a human browser session. They use the existing audience-bound JWT path or a later approved workload-identity exchange with short-lived credentials.

### Logout, revocation, and outages

`hormuz logout` revokes the server-side credential family before deleting the local credential. Tenant administrators can revoke one session, one actor, one team membership, or all tenant sessions. Revocation checks occur before policy evaluation and provider work.

During an identity-provider outage, new login fails closed. Existing Hormuz access and refresh credentials remain usable only until their existing absolute session expiry unless the tenant has configured a stricter outage policy. Hormuz does not extend a session because the identity provider is unavailable. A Hormuz session-store outage fails authentication closed; it never falls back to trusting an unverified client value.

SCIM and identity-provider event integration are required for prompt deprovisioning but remain a separate milestone. Until that exists, administrators can revoke through Hormuz and the short absolute session lifetime limits stale identity exposure.

## Security invariants

- Authorization responses use HTTPS except a future deliberately implemented loopback native callback; the proposed server callback is HTTPS only.
- Redirect URIs are exact allowlisted values. There is no caller-controlled post-login redirect.
- State, nonce, PKCE verifier, enrollment secret, authorization code, and session credentials are single-use and bounded in size and lifetime.
- PKCE uses S256; implicit and password grants are unsupported.
- The callback accepts only the issuer selected at enrollment and validates discovered endpoints and signing keys under the existing safe-URL rules.
- Login CSRF, authorization-code interception, issuer mix-up, session fixation, refresh replay, concurrent refresh, and signing-key rotation have explicit negative tests.
- Tokens, codes, claims, cookies, and enrollment secrets never enter logs, usage events, audit exports, URLs, or error bodies.
- Browser cookies are `Secure`, `HttpOnly`, narrowly scoped, and use an appropriate `SameSite` policy; the enrollment browser state is not a long-lived Hormuz session.

## Alternatives considered

### Customer-minted JWT access tokens only

This has less Hormuz credential state and is already partly implemented. It places issuer-specific audience setup and refresh automation on every customer, does not support opaque provider tokens, and makes the promised “existing clients, automatic company policy” setup materially harder. Keep it as the workload path, not the only human path.

### Store provider refresh tokens on employee machines

This exposes provider-specific credentials to every client installation and makes logout, revocation, and multi-provider behavior inconsistent. Rejected in the proposal.

### Embedded web view or password capture

This prevents reliable SSO cookie reuse and expands Hormuz's access to login credentials. It conflicts with native-app OAuth guidance. Rejected.

### Long-lived Hormuz API keys

This is easy to implement but weakens attribution, offboarding, and replay containment. Retain static keys only as explicit bootstrap or break-glass credentials.

## Consequences if accepted

- Hormuz must operate a highly available, tenant-scoped session store and revocation path before calling identity enterprise-ready.
- The CLI needs secure-store adapters for three desktop platforms and explicit headless behavior.
- Customer IdP setup becomes conventional OIDC client registration rather than custom JWT audience plumbing for every employee.
- Employees keep using Codex and Claude Code; their existing auth-helper hooks receive short-lived Hormuz credentials.
- A real IdP integration is still required before the milestone closes. A fake IdP alone is insufficient evidence.

## Verification required

Acceptance of this ADR does not prove the implementation. Issue #13 closes only with:

- protocol and state-machine tests for every step and expiry boundary;
- fake-IdP integration tests covering success, denial, bad state/nonce/PKCE, mix-up, key rotation, replay, concurrent refresh, revocation, and dependency outage;
- secure-store tests on macOS, Windows, and Linux runners or documented equivalent evidence;
- real Codex and Claude Code authentication-helper smoke tests;
- one owner-selected real identity provider tested in a non-production tenant;
- metadata/log scans proving credential contents are absent.

## Owner approval record

Pending. To accept, the product owner must approve either:

- **A — Hormuz session broker (recommended), including the proposed default lifetimes**, or
- **B — customer-minted JWT access tokens only**, with any requested constraints.
