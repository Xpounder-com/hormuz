# Generic OIDC authentication

Hormuz can authenticate a short-lived JWT access token from a standards-based OpenID Connect issuer and resolve it to the same organization, team, person, and policy principal used by static bootstrap credentials.

This is resource-server authentication. The employee's identity provider must issue a signed JWT access token whose audience represents the Hormuz API. An OIDC ID token is not an API access token and must not be supplied to Hormuz. Providers that issue only opaque access tokens require the future Hormuz login/session broker described under **Current boundary**.

## Identity-provider registration

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

## Client connection

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

The implemented path works with identity providers that issue JWT access tokens for a Hormuz audience. Hormuz does not yet include its own browser authorization-code/PKCE flow, refresh-token custody, opaque-token introspection, SCIM provisioning, or revocation endpoint. [Proposed ADR 0001](decisions/0001-oidc-login-and-session-architecture.md) specifies the session-broker recommendation and its security boundary. It remains non-binding until the product owner explicitly approves issue #2.
