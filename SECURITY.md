# Hormuz security posture

Hormuz is alpha software and has not received a third-party security review. Do not expose the current development server directly to the public internet.

## Current guarantees

- Employee bootstrap or OIDC credentials are never forwarded to OpenAI or Anthropic.
- Provider credentials are read from server-side environment variables.
- Prompt and response bodies are relayed in memory and are not written to the usage database.
- Usage storage contains identity, team, client, protocol, model, policy outcome, token, cost, status, and provider-request metadata.
- Configurable secret controls redact or deny high-confidence credential formats, every configured Hormuz/provider credential, and exact environment-provided values before upstream serialization.
- Secret-control evidence stores only rule identifiers and detection counts, never matched values.
- OpenAI Responses requests are forced to `store: false`, and background mode is denied, unless an administrator explicitly allows those storage modes.
- Identity-token comparisons use constant-time comparison.
- OIDC JWT access tokens require a configured issuer and audience, asymmetric signature verification, expiry, a key ID, and an explicit issuer-subject mapping. Discovery and JWKS use bounded responses and HTTPS outside loopback tests; unknown key IDs cannot trigger unlimited refreshes.
- Request bodies have a configurable size limit and upstream calls have a configurable timeout.
- The local MCP adapter has no direct context-store or provider access. It sends only the documented narrowing fields to the authenticated Context Pack API, refuses plaintext HTTP outside loopback, does not follow redirects, bounds messages and responses, and writes only protocol messages to stdout.

## Current limitations

- The built-in server does not terminate TLS.
- Static environment-provided identity tokens remain available for bootstrap and break-glass use.
- The OIDC path verifies short-lived JWT access tokens but does not yet implement browser login, refresh-token custody, opaque-token introspection, active revocation, or SCIM provisioning.
- SQLite is a single-node development store.
- Configuration contains rate cards and policy, but there is not yet a signed configuration or change-approval workflow.
- Secret detection is best-effort and text-only. It does not inspect images, decode arbitrary encodings or archives, or infer semantically sensitive company information.
- Governed context returned through MCP is explicitly marked as untrusted reference data. MCP makes it available to the model but does not itself prevent prompt injection; provider-bound tool results are inspected by the existing egress controls when the client sends the next model request through Hormuz.
- Logs and provider behavior still require deployment-specific review.

Terminate TLS and enforce network access controls in front of Hormuz for any shared test deployment. Use unique identities for every human or service account, never shared team credentials. Do not send an OIDC ID token where an API access token is required.

Report security issues privately to the repository maintainers. Do not include real API keys, identity tokens, prompts, responses, or customer data in a report.
