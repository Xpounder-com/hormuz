# Local browser-login milestone

This is the first implementation slice for the hosted direction: **we operate
your team's governed access**. It provides browser login, client-bound opaque
sessions, governed requests, personal usage, refresh, and logout without a
paid cloud account or model call. It does not launch a hosted service, close
[#13](https://github.com/Xpounder-com/hormuz/issues/13), or change the separate
v1.1 portfolio program. No cloud accounts, production IdP registration, or
customer credentials are required by the tests.

The [accepted session decision](decisions/0001-oidc-login-and-session-architecture.md)
was previously implemented in experimental commit `49d3086`. Only the session
components were adapted; directory/SCIM, retired context features, and the old
capability-based administrator API were not imported.

The next opt-in slice adds [local team onboarding](TEAM_ONBOARDING.md): a
server-local operator can create teams, issue browser invitations and remove
member access. It does not grant employee sessions administrator privileges or
change the existing native client protocol.

The [local administrator console](ADMIN_CONSOLE_LOCAL.md) builds on that directory
with separate browser sessions, explicit operator grants, scoped usage reporting
and member removal. It remains an opt-in draft, not a hosted launch or a real-IdP
qualification. Employee access credentials remain unable to administer a team.

## Verify locally

Install the checkout in an isolated Python environment, then run:

```sh
python -m pip install -e '.[client]'
python -m unittest tests.test_session_config tests.test_session_store tests.test_credential_store tests.test_session_broker
```

The integration tests start loopback-only identity and model fixtures. They
simulate the external browser's consent and callback requests and inject an
in-memory credential store. They do not exercise a real identity provider,
the user's Keychain, a packaged Mac application, or official AI clients.
The fixtures never contact model-provider endpoints.

## Operator configuration

Browser login is disabled by default. Existing static and audience-bound OIDC
JWT authentication keep their behavior. To opt in, add this object under
`authentication` in an existing gateway configuration:

```json
{
  "session_broker": {
    "enabled": true,
    "public_base_url": "https://gateway.example.com",
    "database": "./sessions.sqlite3",
    "master_key_env": "HORMUZ_SESSION_MASTER_KEY",
    "access_ttl_seconds": 600,
    "absolute_ttl_seconds": 43200,
    "enrollment_ttl_seconds": 300
  }
}
```

The selected `authentication.oidc.issuers[]` also needs a `login` object:

```json
{
  "client_id": "your-registered-hormuz-login-client",
  "client_secret_env": "HORMUZ_OIDC_CLIENT_SECRET",
  "scopes": ["openid"],
  "token_endpoint_auth_method": "client_secret_basic"
}
```

Supply these values through the deployment's secret injection mechanism, not
JSON or committed files. The master key must be base64 encoding of 32 random
bytes, distinct from other credentials. The OIDC login client ID must differ
from resource-server audiences, so a login ID token is not an inference token.
Register exactly `https://gateway.example.com/v1/auth/callback` with the IdP.
Discovery must advertise authorization code, PKCE S256, `form_post`, a
configured asymmetric signing algorithm, and the selected client-secret
authentication method. Hormuz does not request `offline_access` or retain IdP
access/refresh tokens. Validate discovery with the existing `doctor` command.

External HTTP ingress still requires the existing trusted TLS-proxy boundary.
Only explicit loopback development configuration may use HTTP. Its `Lax`
cookie works with the same-site local fixture; a real cross-site IdP requires
HTTPS and the secure `SameSite=None` browser cookie. The callback accepts a
form POST, never authorization codes in a query. See the
[OpenID form-post specification](https://openid.net/specs/oauth-v2-form-post-response-mode-1_0.html).

## Customer commands

Customers do not need the server configuration, provider keys, Render accounts,
or the OIDC client secret. They use a gateway address and an existing mapped
identity:

The Python client installation is `python -m pip install 'hormuz[client]'`;
the gateway-only package does not add an OS-keyring dependency.

```sh
hormuz login --gateway https://gateway.example.com --client codex --profile team-codex --organization your-org
hormuz client config codex --auth-mode session --url https://gateway.example.com --profile team-codex --model your-approved-model-alias
hormuz logout --gateway https://gateway.example.com --profile team-codex
```

Use a separate profile and `--client claude-code` for Claude Code; print its
setup with `hormuz client config claude --auth-mode session --url ... --profile ...`.
Configuration is printed for review, not written over existing client files.
Re-login to a populated profile requires logout first. `--no-open` prints the
non-credential enrollment URL for opening in a browser. A headless environment
without an approved secure store must use the existing workload/JWT path;
there is no plaintext persistence fallback.

Generated setup invokes `hormuz auth session --gateway ... --profile ...` as a
credential helper. Its stdout is deliberately a secret channel for the client;
do not log it or run it just to display a token. The helper refreshes near
expiry under a profile lock. `--force-refresh` supports deliberate rotation;
it does not retry a model request. A failed or interrupted generation is never
automatically replayed by this login implementation.

## HTTP boundary

All routes are under `/v1/auth/`, opt-in, no-store, and tied to the configured
gateway host. Native JSON endpoints reject browser `Origin` headers, unknown
or duplicate fields, ambiguous body framing, and bodies over 16 KiB.

| Method and route | Input | Success |
| --- | --- | --- |
| `POST /enrollments` | `client`, independent `enrollment_secret`, optional configured `issuer` and `organization_id` | 201: enrollment ID, login URL, expiry, polling interval |
| `GET /login?enrollment=...` | Non-credential enrollment ID | 200: browser confirmation page and temporary HTTP-only cookie |
| `POST /invitations/accept` | Opt-in browser form: invitation code, enrollment and state; matching Origin and browser cookie | 200: sign-in confirmation link; no credential; invitation is consumed only after verified IdP callback |
| `POST /callback` | IdP form containing `state`, `code`, optional `iss`; browser cookie required | 200: completion page with no credential |
| `POST /enrollments/{id}/redeem` | Original enrollment secret | 200: access/refresh pair and access/session expiry; 409 while unavailable |
| `POST /refresh` | Current refresh credential | 200: rotated access/refresh pair with unchanged absolute expiry |
| `POST /logout` | Current credential or previously consumed refresh credential from this session | 200: idempotent revocation result |

Errors reuse the existing versioned Hormuz error envelope and public enum.
HTTP status distinguishes invalid input (400), invalid/expired/replayed session
(401), non-redeemable enrollment (409), process limit (429), and dependency
failure (503). Fixed diagnostic reasons appear in the message and metadata-only
logs; clients must not parse message text as a stable error-code contract.
This adds no entries to the frozen v1.1 portfolio schema manifest.

Identity and personal usage remain `/v1/gateway/whoami` and
`/v1/gateway/usage`. The session is scoped to the exact mapped organization,
actor, team, clearance, and client. A caller-supplied organization or client
header cannot broaden it. Session credentials are deliberately not accepted
by the policy or custody administrator authentication path.

## Security and remaining launch gates

Access credentials default to 10 minutes (configurable 5–15); sessions have a
12-hour maximum absolute lifetime. Each refresh rotates both credentials;
reusing the predecessor revokes the family. The current identity mapping is
checked before redemption/refresh returns credentials and on authenticated
requests. Mapping removal or authorization changes revoke access. In-flight
model requests are not cancelled by logout. Configuration changes take effect
when the process is restarted; no directory synchronization is implied.

The separate SQLite session store contains keyed hashes, identity bindings,
and encrypted transient nonce/PKCE state. It has private file permissions and
a closed schema; credential-bearing dataclasses omit secrets from repr.
Key derivation includes the configured gateway origin, so another gateway
cannot accept these credentials even if its operator reuses the database/key.
Session credentials are also recognized by the built-in secret redactor.
See the [secret inventory](SECRET_CUSTODY_INVENTORY.md) and
[durable data inventory](DURABLE_DATA.md) for retention and restore limits.

This is a single-node reference implementation with a bounded process-wide
authentication request limit and enrollment capacity. Before a paid hosted
pilot, implement/verify the production HTTP adapter, persistent distributed
sessions and throttling, tenant-scoped provider custody, administrator web
sessions/roles beyond local operator removal, directory synchronization, immutable session security evidence,
real IdP and official-client refresh/401 behavior, and cross-platform secure
storage. Restoring a session-only backup requires master-key rotation to prevent
credential replay. The separate Render staging profile instead provides an
encrypted, origin-bound managed-directory archive that restores every authority
closed; see its [recovery runbook](../deploy/render/gateway/README.md#offline-snapshot-encrypted-export-and-conservative-restore).
A signed/notarized Mac wrapper, customer dashboard, fresh-disk qualification,
recovery timing, billing, and compatible provider
failover remain separate milestones. This slice makes no availability or
latency guarantee and performs no deployment or billing operation.
