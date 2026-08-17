# Generic OIDC authentication

Hormuz has two standards-based OpenID Connect paths. Human employees can use browser authorization-code + PKCE login and receive short-lived, opaque Hormuz credentials. CI and service workloads can continue to present a JWT access token minted for the Hormuz API audience. Both resolve the exact `(issuer, subject)` pair to the same organization, team, person, clearance, and policy principal.

An OIDC ID token is accepted only inside Hormuz's server-side authorization-code callback and is never accepted as a gateway bearer credential. Provider access and refresh tokens are not retained.

## Human browser login and session broker

Register Hormuz as a confidential web OIDC client with the identity provider:

- authorization-code flow only;
- exact redirect URI `https://hormuz.example.com/v1/auth/callback`;
- `openid` scope, plus only claims genuinely needed for authentication;
- asymmetric ID-token signing, normally `RS256`;
- a server-held client secret; never install it on employee machines.
- a login client ID distinct from every workload API audience, so an ID token can never satisfy the resource-server audience check.

Generate a separate 32-byte session-store master key and place the base64url value and OIDC client secret in the Hormuz service environment. Keep the usage, context, and session databases separate.

```json
{
  "authentication": {
    "session_broker": {
      "enabled": true,
      "database": "./hormuz-sessions.sqlite3",
      "public_base_url": "https://hormuz.example.com",
      "master_key_env": "HORMUZ_SESSION_MASTER_KEY",
      "access_ttl_seconds": 600,
      "absolute_ttl_seconds": 43200,
      "enrollment_ttl_seconds": 300
    },
    "oidc": {
      "issuers": [
        {
          "issuer": "https://identity.example.com",
          "audiences": ["api://hormuz"],
          "algorithms": ["RS256"],
          "login": {
            "client_id": "hormuz-production",
            "client_secret_env": "HORMUZ_OIDC_CLIENT_SECRET",
            "scopes": ["openid"],
            "token_endpoint_auth_method": "client_secret_basic"
          },
          "subjects": [
            {
              "subject": "00u-company-stable-subject",
              "actor_id": "alice",
              "actor_name": "Alice Example",
              "team_id": "engineering",
              "team_name": "Engineering",
              "organization_id": "xpounder",
              "clearance": "confidential",
              "allowed_clients": ["codex", "claude-code"]
            }
          ]
        }
      ]
    }
  }
}
```

The master key must decode to exactly 32 bytes. Human access lifetime is constrained to 5–15 minutes and defaults to 10 minutes. Absolute session lifetime cannot exceed 12 hours; organizations may shorten it. Loopback HTTP is available only behind explicit development flags and never permits a non-loopback host.

An employee creates one client-bound profile for each AI client they use:

```bash
hormuz login --gateway https://hormuz.example.com --profile codex --client codex
hormuz login --gateway https://hormuz.example.com --profile claude --client claude-code

hormuz --config /etc/hormuz/hormuz.json client-config codex \
  --url https://hormuz.example.com --actor alice \
  --auth-mode session --profile codex

hormuz --config /etc/hormuz/hormuz.json client-config claude \
  --url https://hormuz.example.com --actor alice \
  --auth-mode session --profile claude

hormuz mcp-config codex \
  --url https://hormuz.example.com --profile codex
hormuz mcp-config claude \
  --url https://hormuz.example.com --profile claude
```

`hormuz login` opens the one-time URL in the operating system's external browser. Use `--no-open` to print it for a headless terminal. The browser receives no Hormuz access or refresh credential. The terminal redeems its independent enrollment secret once and stores the session through the OS secure store. Provider-gateway helpers invoke `hormuz auth token`; the MCP adapter resolves the same profile for every context-tool call. Both reuse an unexpired access credential or atomically rotate the access/refresh pair near expiry.

Hormuz supports macOS Keychain, Windows Credential Manager, and Linux Secret Service/KWallet through an allowlisted `keyring` backend. Persistent login fails when none is available; it does not silently write a refresh credential to a dotfile. `hormuz logout --gateway ... --profile ...` revokes the server-side family before deleting the local entry.

The session database contains keyed credential hashes, encrypted temporary PKCE verifier/nonce state, and tenant/actor/team/client binding metadata. It does not contain raw Hormuz credentials or retained provider tokens. Reuse of any rotated refresh credential revokes the current family. See [SESSION_ADMIN_API.md](SESSION_ADMIN_API.md) for capability-gated listing and immediate session, actor, team, or organization revocation.

## Workload JWT resource-server path

For CI or service accounts, the identity provider must issue a signed JWT access token whose audience represents the Hormuz API. The OAuth client that obtains that access token is separate from the API audience. Never use an ID token as this bearer credential.

## Workload identity-provider registration

Create an OAuth/OIDC API or resource in the company's identity provider:

- audience: a stable value such as `https://hormuz.example.com` or `api://hormuz`;
- signing: an asymmetric algorithm configured in Hormuz, normally `RS256`;
- required token claims: `iss`, `sub`, `aud`, and `exp`;
- token lifetime: short-lived according to company policy;
- keys: published by `jwks_uri` or the issuer's OIDC discovery document.

The OAuth client that obtains the access token is separate from the API audience. Use an organization-managed native client, device agent, or existing identity tool to acquire that token. Never put an OIDC client secret on employee machines.

## Hormuz configuration

Static identities are optional when at least one OIDC subject is configured. They may remain as tightly controlled bootstrap or break-glass credentials during rollout.

```json
{
  "authentication": {
    "oidc": {
      "issuers": [
        {
          "issuer": "https://identity.example.com",
          "audiences": ["api://hormuz"],
          "algorithms": ["RS256"],
          "clock_skew_seconds": 60,
          "discovery_cache_seconds": 3600,
          "subjects": [
            {
              "subject": "00u-company-stable-subject",
              "actor_id": "alice",
              "actor_name": "Alice Example",
              "team_id": "engineering",
              "team_name": "Engineering",
              "organization_id": "xpounder",
              "clearance": "confidential",
              "allowed_clients": ["codex", "claude-code"]
            }
          ]
        }
      ]
    }
  }
}
```

Hormuz derives `https://identity.example.com/.well-known/openid-configuration` when `jwks_uri` is omitted. Set `jwks_uri` explicitly only when the provider requires it. Issuer and JWKS URLs must use HTTPS. `allow_insecure_http: true` exists only for loopback integration testing and rejects non-loopback HTTP hosts.

Subject mapping uses the pair `(issuer, sub)`, not an email address or a caller-provided team claim. The mapping is the authorization boundary that assigns the employee to an actor, team, organization, clearance, and allowed clients. If the same actor has both static and OIDC credentials, all of those authorization fields must match.

Supported verification algorithms are `RS256`, `RS384`, `RS512`, `PS256`, `PS384`, `PS512`, `ES256`, `ES384`, and `ES512`. Symmetric JWT algorithms are rejected because an OIDC resource server must not share an HMAC signing secret with token issuers.

## Workload client connection

Make a current JWT access token available through the environment selected by the company's identity tooling, then generate OIDC client configuration for the mapped actor:

```bash
export HORMUZ_OIDC_ACCESS_TOKEN="current-short-lived-jwt-access-token"

hormuz --config /etc/hormuz/hormuz.json client-config codex \
  --url https://hormuz.example.com \
  --actor alice \
  --auth-mode oidc

hormuz --config /etc/hormuz/hormuz.json client-config claude \
  --url https://hormuz.example.com \
  --actor alice \
  --auth-mode oidc
```

For Codex, the generated `model_providers.hormuz.auth` block invokes `hormuz auth token`; Codex re-runs that helper every five minutes and after an authentication retry according to its [configuration reference](https://learn.chatgpt.com/docs/config-file/config-reference). For Claude Code, set the generated `apiKeyHelper`; Claude Code similarly re-runs it every five minutes or after HTTP 401 according to its [authentication reference](https://code.claude.com/docs/en/authentication). `hormuz auth token` prints only the selected environment credential and does not need the gateway configuration file.

These refresh hooks re-read the credential source; they do not mint or renew an access token. The organization must currently refresh the source or start the client with a newly acquired token before expiry.

Verify a token and mapping without sending a model request:

```bash
curl --fail-with-body \
  -H "Authorization: Bearer ${HORMUZ_OIDC_ACCESS_TOKEN}" \
  https://hormuz.example.com/v1/gateway/whoami
```

The response contains actor, team, organization, allowed-client, and authentication-source metadata. It never returns the JWT, OIDC subject, or provider credential.

## Validation and failure behavior

Before a token reaches policy evaluation, Hormuz:

1. limits credential size and requires a JWT structure;
2. selects only a configured issuer and asymmetric algorithm;
3. fetches bounded discovery and JWKS documents over approved transport;
4. verifies signature, exact issuer, allowed audience, expiry, and required subject;
5. refreshes JWKS once when an unknown `kid` indicates normal signing-key rotation;
6. requires an explicit `(issuer, sub)` mapping.

Authentication logs contain only a stable failure code. Hormuz does not log token contents or claims.

`hormuz doctor` fetches and validates every configured discovery/JWKS path and reports the number of usable signing keys. Run it from the same network boundary as the service before deployment.

## Current boundary

[Accepted ADR 0001](decisions/0001-oidc-login-and-session-architecture.md) governs the implemented login architecture. The repository includes protocol, persistence, HTTP, and CLI integration tests against a standards-shaped fake IdP. It has not yet been validated against the owner-selected real identity provider, and the local SQLite broker is not a claim of multi-node availability.

The local `session_admin` API and CLI provide immediate tenant-scoped revocation on this process and inspect cursor-paginated, metadata-only logout, refresh-replay, mapping-removal, and administrative-revocation evidence. This local query path is not an immutable audit sink. SCIM provisioning/deprovisioning, workload identity exchange, KMS-backed shared session storage, signed or externally immutable security-event export, and distributed enrollment throttling remain enterprise gates. Until live configuration reload and SCIM exist, changing a subject mapping takes effect after a service reload/restart; the next request then compares the stored organization/actor/team/clearance binding, revokes on mismatch, and fails closed. `hormuz logout` provides employee-initiated revocation.
