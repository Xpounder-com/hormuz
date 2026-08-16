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

Provider billing evidence has a separate offline path into that metadata ledger:

```text
complete OpenAI / Anthropic cost-report JSON pages
        |
        | bounded schema parsing, exact decimal normalization, pagination check
        v
immutable organization/provider cost snapshot
        |
        +--> raw response discarded; normalized project/workspace/line-item fields retained
        +--> compare aggregate provider-reported cost with organization-bound request estimates
        +--> expose unresolved variance without allocating it to employees or calling it bypass
```

Governed context has explicit local and authenticated connector paths:

```text
content-bearing JSONL       trusted lifecycle envelope       evidence envelope
        |                             |                              |
        | local operator              | local CLI or authenticated   | local CLI or authenticated
        | identity-scope validation   | remote connector             | remote connector
        | idempotent provisional      | promoter capability          | promoter capability
        | import                      | versioned observation        | hash raw reference
        +-----------------------------+------------------------------+
                                      v
local SQLite context repository (separate from usage)
        |
        +--> durable policy/snapshot/record-set/evidence-set-bound revalidation job
        |       +--> bounded lease, cursor, counters, atomic verification mutation
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
- `hormuz/session_store.py` owns a separate local session database, event-time identity bindings, keyed credential hashes, encrypted transient flow state, atomic rotation, replay detection, tenant-scoped administration, revocation, and metadata-only security events.
- `hormuz/credential_store.py` and `hormuz/session_client.py` own fail-closed OS secure-store custody and the CLI login/refresh/logout path.
- `hormuz/session_admin_client.py` owns the authenticated, redirect-refusing session-administration CLI transport and validates the metadata-only session and security-event response contracts.
- `hormuz/config.py` validates configuration, defines identity/route/rate-card policy data, and resolves monotonic organization/team/person DLP actions for the exact provider and routed model.
- `hormuz/policy.py` evaluates access, fallback, caps, and budgets without transport concerns.
- `hormuz/store.py` owns the SQLite schema and monthly aggregations.
- `hormuz/billing.py` validates complete OpenAI and Anthropic cost-report pages and normalizes exact provider amounts and supported billing dimensions without persistence or credentials.
- `hormuz/usage.py` parses bounded provider usage and actual-model metadata through provider-specific allowlists without storing response content.
- `hormuz/redaction.py` applies bounded credential, regulated-identifier, low-confidence PII, exact-dictionary, and provider-format-aware opaque-media rules to provider-bound JSON values.
- `hormuz/dlp_approval.py` computes domain-separated keyed fingerprints over canonical provider operation/payload values without persistence or transport concerns.
- `hormuz/dlp_client.py` implements the bounded, authenticated approver CLI transport and refuses redirects or non-loopback plaintext HTTP.
- `hormuz/context.py` authorizes, applies immutable lifecycle observations, quarantines high-confidence injection patterns, surfaces structured contradictions, ranks, budgets, and fingerprints explicit provider-neutral context packs without transport or persistence concerns.
- `hormuz/context_lifecycle.py` defines strict evidence, promotion-policy, subject-fingerprint, conflict, negative-signal, and source/dependency transition rules without persistence or transport concerns.
- `hormuz/context_api.py` defines the versioned lifecycle mutation request and metadata-only response contracts without authentication, persistence, or network behavior.
- `hormuz/context_lifecycle_client.py` implements the bounded, authenticated remote connector transport, refuses redirects and non-loopback plaintext HTTP, and validates exact response shapes.
- `hormuz/context_store.py` implements the local governed-record, trusted-snapshot, immutable evidence, and resumable revalidation repository with optimistic concurrency, leases, integrity checks, and metadata-only mutation/read/lifecycle audit behind a content-codec boundary.
- `hormuz/mcp.py` implements the bounded dual-era MCP stdio protocol and an HTTPS client for the authenticated Context Pack API; it has no repository or provider access.
- `hormuz/context_benchmark.py` evaluates the production context-pack kernel against frozen synthetic snapshots and separated outcomes; it has no provider, network, or context-repository dependency.
- `hormuz/cli.py` exposes serving, diagnostics, policy checks, client configuration, usage and billing reporting, local lifecycle operations, and config-independent remote connector commands.

## Trust boundary

Hormuz is trusted with plaintext requests and responses because it must inspect and relay them. The usage and DLP approval stores are deliberately metadata-only; the latter keeps only a keyed fingerprint and bounded binding metadata, never the payload. Provider billing imports add normalized financial metadata such as project/workspace IDs and line-item descriptions, but discard the raw provider response and never receive the administrator credential. Governed content is held in a different SQLite database and never written to the usage ledger. The current local context codec is plainly labeled as unencrypted and is not an enterprise storage claim. DLP runs after authentication and exact provider/model routing but before provider storage policy and upstream serialization. Team and actor overlays are selected only from the authenticated identity and can only strengthen an enabled organization rule; callers cannot request a weaker scope. Recognized opaque provider media is denied before egress unless the organization explicitly turns that rule off; Hormuz does not claim to inspect the underlying bytes. The off-mode exemption is limited to each recognized opaque object, so inspectable siblings remain governed by credential and DLP rules. Approval consumption is atomic and precedes egress, so concurrent replay cannot duplicate the exception. Future reusable-context injection must run after context authorization and before DLP so newly added context is inspected by the same egress controls.

## Compatibility boundary

Hormuz implements the provider endpoints and local MCP stdio integration required by Codex and Claude Code rather than inventing a new employee-facing client. Provider and MCP protocol changes are compatibility risks and require executable conformance tests.

## Identity boundary

OIDC supports both a workload resource-server path and a human authorization-code + PKCE session path. Discovery and JWKS metadata are cached, an unknown signing-key ID triggers one refresh, and authorization attributes come only from the configured `(issuer, subject)` mapping. Hormuz does not trust caller-provided group or team claims. Lifecycle connectors use this same resource-server path and additionally require server-configured `context_promoter`; the bearer token proves the mapped workload identity, not the truth of a claimed CI or source event. Usage administration independently requires `usage_viewer`; report, self-usage, secret-summary, budget-total, and active-reservation queries derive organization from the authenticated identity rather than a caller-selected tenant. The local session kernel binds every new session to its organization, actor, team, clearance, and client; a mapping change or capability-gated administrative action revokes it before provider work. The kernel remains single-node; real-IdP validation, SCIM, shared immediate revocation, KMS, and HA persistence remain enterprise milestones. See [OIDC.md](OIDC.md), [CONTEXT_LIFECYCLE_API.md](CONTEXT_LIFECYCLE_API.md), [SESSION_ADMIN_API.md](SESSION_ADMIN_API.md), and [USAGE_ADMIN_API.md](USAGE_ADMIN_API.md).
