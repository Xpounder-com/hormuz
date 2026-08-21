# Microsoft Entra ID reference profile

> Status: **planned reference profile; not certified**

Hormuz's product contract is generic OpenID Connect. It is a confidential OIDC
relying party for human browser login and a JWT resource server for workloads;
it is never an identity provider. Microsoft Entra ID is the first planned
reference configuration because it exercises the ordinary standards-based
contract without adding Entra-specific code. A company that already operates a
different conformant IdP should validate that provider instead of creating
unnecessary identity infrastructure.

This guide is not a claim that any Entra tenant, application, or employee flow
has been certified. It contains no tenant identifier, client ID, subject,
token, secret, or employee information.

## Contract boundary

The reference profile uses only the generic Hormuz configuration surface:

- tenant-scoped OIDC discovery and JWKS;
- OAuth authorization code with PKCE S256 for browser enrollment;
- a confidential web client with a server-held credential;
- exact `(issuer, sub)` mapping or the separate policy-owned SCIM binding;
- short-lived, opaque Hormuz credentials for Codex and Claude Code helpers.

It does **not** use Microsoft Graph, Entra group claims, an Entra SDK, a custom
Hormuz token issuer, or an Entra-specific policy model. Hormuz owns its own
authorization policy; the IdP establishes who authenticated.

## Non-production app registration

Create or use a non-production **Web** application registration in the existing
Entra tenant. Prefer a tenant-specific authority rather than `common` or
`organizations`: Hormuz compares the configured issuer exactly and maps an
identity using the exact `(issuer, sub)` pair.

Register this exact callback URI as a Web redirect URI:

```text
https://hormuz.example.com/v1/auth/callback
```

The public gateway address must be HTTPS and match the registration exactly.
Use authorization code flow with the `openid` scope. Do not enable implicit or
hybrid flows for Hormuz. Hormuz does not need email, profile, group, Microsoft
Graph, or other directory claims to authorize a person.

The Entra v2 discovery metadata advertises `client_secret_post` for a shared
secret confidential client. Configure that method explicitly. The service
environment, not a configuration file or employee machine, holds the secret.
For a future production credential posture that requires an application
certificate or federated client assertion, Hormuz needs separately reviewed
`private_key_jwt`/custody work; that remains part of the KMS/BYOK release gate.

```json
{
  "authentication": {
    "session_broker": {
      "enabled": true,
      "backend": "postgresql",
      "public_base_url": "https://hormuz.example.com",
      "master_key_env": "HORMUZ_SESSION_MASTER_KEY"
    },
    "oidc": {
      "issuers": [
        {
          "issuer": "https://login.microsoftonline.com/<directory-tenant-id>/v2.0",
          "audiences": ["api://hormuz-workload"],
          "algorithms": ["RS256"],
          "login": {
            "client_id": "<application-client-id>",
            "client_secret_env": "HORMUZ_OIDC_CLIENT_SECRET",
            "scopes": ["openid"],
            "token_endpoint_auth_method": "client_secret_post"
          },
          "subjects": [
            {
              "subject": "<stable-test-user-subject>",
              "actor_id": "entra-test-user",
              "actor_name": "Entra Test User",
              "team_id": "engineering",
              "team_name": "Engineering",
              "organization_id": "xpounder",
              "clearance": "internal",
              "allowed_clients": ["codex", "claude-code"]
            }
          ]
        }
      ]
    }
  }
}
```

The `audiences` field is for the distinct workload JWT resource-server path. It
must not equal the browser-login application client ID. Remove it if the
reference run does not exercise workloads. Do not copy the placeholder subject
into a deployment; configure a stable subject mapping through the approved
identity/policy path.

Microsoft documents tenant-scoped v2 discovery, exact registered redirect URIs,
and authorization-code token redemption for confidential web applications in
its [OpenID Connect guide](https://learn.microsoft.com/en-us/entra/identity-platform/v2-protocols-oidc)
and [authorization-code flow reference](https://learn.microsoft.com/en-us/entra/identity-platform/v2-oauth2-auth-code-flow).

## Certification evidence required

The reference can become certified only after an authorized operator performs
all of the following in a non-production tenant:

1. Run `hormuz --config /etc/hormuz/hormuz.json doctor` from the deployed
   network boundary. It must validate discovery, JWKS, authorization-code,
   PKCE S256, ID-token signing, and `client_secret_post` without logging a
   secret.
2. Complete `hormuz login --gateway https://hormuz.example.com --profile codex
   --client codex` with the mapped test employee. Verify that the browser sees
   no Hormuz access or refresh credential.
3. Generate and exercise Codex and Claude Code session configurations through
   the normal helpers. Verify a request, a `401` helper retry, logout, and
   administrator revocation without a provider credential on the employee
   machine.
4. Capture content-free evidence: configuration digest, resolved generic
   protocol details, pass/fail outcomes, test version, and timestamps. Do not
   commit client secrets, authorization codes, ID tokens, subjects, email
   addresses, tenant IDs, or browser URLs.
5. Exercise at least one fail-closed case: mismatched redirect URI, unconfigured
   subject, invalid signature, or unavailable discovery/JWKS endpoint.

The repository's generic OIDC protocol tests remain required in addition to
this real reference run. Passing the reference does not certify Entra SCIM,
Graph permissions, Conditional Access policy, device posture, tenant recovery,
or Hormuz production KMS/HA/DR operations.

## Selection rule

Entra is the default first reference only when no easier existing IdP is
available. If the organization already uses another standards-conformant IdP,
record that provider as the selected reference for the same checklist. Do not
change Hormuz runtime behavior, policy semantics, or provider-neutral docs for
the sake of a reference deployment.
