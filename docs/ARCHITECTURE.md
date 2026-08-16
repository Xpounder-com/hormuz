# Hormuz architecture

Hormuz sits on the provider request path while employees continue using Codex or Claude Code.

```text
Codex / Claude Code
        |
        | bootstrap token, workload OIDC JWT, or opaque Hormuz access credential
        v
Hormuz HTTP transport
        |
        +--> verify static/OIDC/session identity and resolve explicit actor/team metadata
        +--> resolve organization -> team -> person model, budget, and DLP policy
        +--> allow, deny, reroute, or cap the request
        +--> classify provider content blocks; deny configured opaque images/files
        +--> detect, redact, require approval, or deny at the final DLP boundary
        +--> create or atomically consume a scoped 15-minute single-use approval
        +--> enforce provider storage policy on the transformed payload
        +--> atomically reserve applicable token and spend budget
        +--> replace the employee token with the company provider key
        v
OpenAI Responses API / Anthropic Messages API
        |
        +--> stream response to the original client
        +--> parse provider usage metadata without retaining content
        v
SQLite usage ledger
```

Governed context has explicit CLI and authenticated HTTP paths:

```text
content-bearing JSONL       trusted lifecycle envelope
        |
        | identity-scope validation, idempotent import/snapshot observation
        v
local SQLite context repository (separate from usage)
        |
        | SQL authorization/freshness filter before content decode
        | trusted dependency/source evaluation, quarantine, contradiction grouping
        v
deterministic lexical context pack
        |
        +--> commit metadata-only pack-read audit
        +--> CLI stdout or POST /v1/context/packs response
        +--> hormuz_get_context through local MCP stdio adapter
        +--> no provider call or automatic prompt injection
```

The HTTP path authenticates first, derives organization/team/actor from the static credential, workload OIDC token, or mapped Hormuz human session, enforces server-owned pack/rate limits, and filters authorized metadata in SQLite before decoding content. Callers cannot supply identity, policy version, or historical evaluation time.

## Code boundaries

- `hormuz/server.py` owns HTTP compatibility, authentication, upstream forwarding, streaming, and protocol-shaped errors.
- `hormuz/auth.py` verifies bootstrap, workload OIDC JWT, and login ID-token signatures against configured issuers.
- `hormuz/session.py` owns authorization-code + PKCE protocol behavior and maps opaque sessions back to current configured identities.
- `hormuz/session_store.py` owns a separate local session database, keyed credential hashes, encrypted transient flow state, atomic rotation, replay detection, and revocation.
- `hormuz/credential_store.py` and `hormuz/session_client.py` own fail-closed OS secure-store custody and the CLI login/refresh/logout path.
- `hormuz/config.py` validates configuration, defines identity/route/rate-card policy data, and resolves monotonic organization/team/person DLP actions for the exact provider and routed model.
- `hormuz/policy.py` evaluates access, fallback, caps, and budgets without transport concerns.
- `hormuz/store.py` owns the SQLite schema and monthly aggregations.
- `hormuz/usage.py` parses bounded provider usage and actual-model metadata through provider-specific allowlists without storing response content.
- `hormuz/redaction.py` applies bounded credential, regulated-identifier, low-confidence PII, exact-dictionary, and provider-format-aware opaque-media rules to provider-bound JSON values.
- `hormuz/dlp_approval.py` computes domain-separated keyed fingerprints over canonical provider operation/payload values without persistence or transport concerns.
- `hormuz/dlp_client.py` implements the bounded, authenticated approver CLI transport and refuses redirects or non-loopback plaintext HTTP.
- `hormuz/context.py` authorizes, applies immutable lifecycle observations, quarantines high-confidence injection patterns, surfaces structured contradictions, ranks, budgets, and fingerprints explicit provider-neutral context packs without transport or persistence concerns.
- `hormuz/context_store.py` implements the local governed-record and trusted-snapshot repository, optimistic concurrency, integrity checks, and metadata-only mutation/read/lifecycle audit behind a content-codec boundary.
- `hormuz/mcp.py` implements the bounded dual-era MCP stdio protocol and an HTTPS client for the authenticated Context Pack API; it has no repository or provider access.
- `hormuz/context_benchmark.py` evaluates the production context-pack kernel against frozen synthetic snapshots and separated outcomes; it has no provider, network, or context-repository dependency.
- `hormuz/cli.py` exposes serving, diagnostics, policy checks, client configuration, usage reporting, and explicit context lifecycle commands.

## Trust boundary

Hormuz is trusted with plaintext requests and responses because it must inspect and relay them. The usage and DLP approval stores are deliberately metadata-only; the latter keeps only a keyed fingerprint and bounded binding metadata, never the payload. Governed content is held in a different SQLite database and never written to the usage ledger. The current local context codec is plainly labeled as unencrypted and is not an enterprise storage claim. DLP runs after authentication and exact provider/model routing but before provider storage policy and upstream serialization. Team and actor overlays are selected only from the authenticated identity and can only strengthen an enabled organization rule; callers cannot request a weaker scope. Recognized opaque provider media is denied before egress unless the organization explicitly turns that rule off; Hormuz does not claim to inspect the underlying bytes. Approval consumption is atomic and precedes egress, so concurrent replay cannot duplicate the exception. Future reusable-context injection must run after context authorization and before DLP so newly added context is inspected by the same egress controls.

## Compatibility boundary

Hormuz implements the provider endpoints and local MCP stdio integration required by Codex and Claude Code rather than inventing a new employee-facing client. Provider and MCP protocol changes are compatibility risks and require executable conformance tests.

## Identity boundary

OIDC supports both a workload resource-server path and a human authorization-code + PKCE session path. Discovery and JWKS metadata are cached, an unknown signing-key ID triggers one refresh, and authorization attributes come only from the configured `(issuer, subject)` mapping. Hormuz does not trust caller-provided group or team claims. The local session kernel is single-node; real-IdP validation, SCIM, administrator revocation, and HA persistence remain enterprise milestones. See [OIDC.md](OIDC.md).
