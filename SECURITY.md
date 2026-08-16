# Hormuz security posture

Hormuz is alpha software and has not received a third-party security review. Do not expose the current development server directly to the public internet.

## Current guarantees

- Employee bootstrap or OIDC credentials are never forwarded to OpenAI or Anthropic.
- Provider credentials are read from server-side environment variables.
- Prompt and response bodies are relayed in memory and are not written to the usage database.
- Usage storage contains identity, team, client, protocol, model, policy outcome, token, cost, status, and provider-request metadata.
- Configurable egress controls inspect JSON string values and keys for high-confidence credential formats, every configured Hormuz/provider credential, valid hyphenated US SSNs, Luhn-valid card candidates, low-confidence email syntax, and bounded organization dictionaries before upstream serialization. They also decode bounded UTF-8 base64/base64url strings and textual data URIs up to three layers before applying the same rules, then re-encode only transformed values. JSON keys are never renamed; a credential or DLP key finding that would require redaction denies the complete request instead.
- Provider-format-aware DLP denies recognized OpenAI image/file/screenshot inputs and Anthropic image or non-text document/file inputs by default. It records only rule metadata and counts; it does not persist the URL, file ID, filename, encoded bytes, or surrounding content. Explicitly turning this rule off exempts only the recognized opaque objects; inspectable sibling values still pass through credential and DLP enforcement.
- DLP rules support provider and exact routed-model scopes plus detect, redact, deny, and approval-required actions. Authenticated team/person overlays may narrow scope and choose only a stronger organization action; actions equal to or weaker than the organization rule, unknown scopes, cross-organization-ambiguous team IDs, and broader provider/model scopes fail configuration validation. Optional approvals require a separate `dlp_approver`, reject self-approval, bind a keyed canonical payload/operation fingerprint to event-time scope and effective policy, expire after 15 minutes, and atomically permit one retry.
- Security evidence stores only policy/rule metadata and counts, never matched values.
- OpenAI Responses requests are forced to `store: false`, and background mode is denied, unless an administrator explicitly allows those storage modes.
- Identity-token comparisons use constant-time comparison.
- OIDC JWT access tokens require a configured issuer and audience, asymmetric signature verification, expiry, a key ID, and an explicit issuer-subject mapping. Discovery and JWKS use bounded responses and HTTPS outside loopback tests; unknown key IDs cannot trigger unlimited refreshes.
- Request bodies have a configurable size limit and upstream calls have a configurable timeout.
- The local MCP adapter has no direct context-store or provider access. It sends only the documented narrowing fields to the authenticated Context Pack API, refuses plaintext HTTP outside loopback, does not follow redirects, bounds messages and responses, and writes only protocol messages to stdout.
- Governed-context authorization and freshness checks occur before lifecycle scanning or ranking. Exact server-side lifecycle snapshots invalidate mismatched `git:` source revisions and explicit dependencies; structured contradictions are excluded and surfaced rather than silently merged.

## Current limitations

- The built-in server does not terminate TLS.
- Static environment-provided identity tokens remain available for bootstrap and break-glass use.
- The workload OIDC path verifies short-lived JWT access tokens. The human path implements authorization-code + PKCE browser login and issues opaque, rotating Hormuz credentials from a separate local session store.
- Raw Hormuz access/refresh credentials are not stored server-side. The session database uses keyed hashes, encrypts transient PKCE state, rotates both credentials, and revokes a family on refresh replay.
- The CLI permits persistent human login only through allowlisted macOS Keychain, Windows Credential Manager, or Linux Secret Service/KWallet backends. Plaintext keyrings fail closed.
- Provider access/refresh tokens are not retained after the OIDC callback. ID tokens are never accepted as gateway bearer credentials.
- Real-IdP validation, SCIM provisioning/deprovisioning, administrator revocation APIs, distributed throttling, KMS-backed custody, and HA session persistence remain release gates.
- SQLite is a single-node development store.
- Configuration contains rate cards and policy, but there is not yet a signed configuration or change-approval workflow.
- Structured DLP remains deterministic and does not understand image/file contents. Recognized provider media blocks are denied rather than decoded. Supported encoded text is limited to single-string standard or URL-safe base64 that decodes to bounded, predominantly printable UTF-8, including textual data URIs; whitespace-wrapped encodings, compression, archives, binary payloads, other encodings, caller-controlled provider headers, source paths, and semantically sensitive company information remain unclassified. Email matching remains detect-only; organization evaluation, approval notifications/queue UX, and HA approval persistence are open gates.
- Governed context matching narrow high-confidence policy-override, secret-exfiltration, or instruction-escalation patterns is quarantined before ranking. This is a deterministic safety layer, not comprehensive semantic prompt-injection detection; all returned context remains untrusted reference data.
- MCP makes returned context available to the model but does not itself enforce tool use or prevent an unrecognized injection. Provider-bound tool results are inspected by the existing egress controls when the client sends the next model request through Hormuz.
- Lifecycle snapshots are trusted operator/connector input. The local snapshot store contains artifact URIs and revisions, while metadata-only lifecycle audit events contain only scope, versions, hash, and artifact count.
- Logs and provider behavior still require deployment-specific review.

Terminate TLS and enforce network access controls in front of Hormuz for any shared test deployment. Use unique identities for every human or service account, never shared team credentials. Do not send an OIDC ID token where an API access token is required.

Report security issues privately to the repository maintainers. Do not include real API keys, identity tokens, prompts, responses, or customer data in a report.
