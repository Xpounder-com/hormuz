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
        +--> when enabled, retrieve an authorized verified Context Pack and render it at user priority
        +--> classify provider content blocks; deny configured opaque images/files
        +--> inspect JSON, provider query, and allowlisted headers at the final DLP boundary
        +--> detect, redact, require approval, or deny without mutating keys/query/headers
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

Provider billing evidence has separate authenticated and offline paths into that metadata ledger:

```text
provider admin key in operator environment      complete cost-report JSON pages
        |                                                |
        | fixed HTTPS origin, no redirects               | offline operator evidence
        | exact query scope and cursor chain              |
        +--------------------------+---------------------+
                                   |
                                   | bounded schema parsing, exact decimal normalization
                                   v
immutable organization/provider cost snapshot
        |
        +--> raw response and credential discarded; normalized billing metadata retained
        +--> authenticated source records exact API contract, query window, and fixed scope
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
        +--> disabled-by-default generation path
                +--> provider-specific user-priority render
                +--> complete mutated request enters DLP, budget, and provider path above
```

The HTTP path authenticates first, derives organization/team/actor from the static credential, workload OIDC token, or mapped Hormuz human session, enforces server-owned pack/rate limits, and filters authorized metadata in SQLite before decoding content. Callers cannot supply identity, policy version, or historical evaluation time.

## Code boundaries

- `hormuz/server.py` owns HTTP compatibility, authentication, upstream forwarding, streaming, and protocol-shaped errors.
- The server also owns shallow content-free liveness/readiness, atomic request admission, and bounded drain accounting. `SIGTERM` orchestration and its configured grace deadline live in `hormuz/cli.py`; neither surface claims dependency-wide health or HA.
- `hormuz/auth.py` verifies bootstrap, workload OIDC JWT, and login ID-token signatures against configured issuers.
- `hormuz/session.py` owns authorization-code + PKCE protocol behavior and maps opaque sessions back to current configured identities.
- `hormuz/session_store.py` owns a separate local session database, event-time identity bindings, keyed credential hashes, encrypted transient flow state, atomic rotation, replay detection, tenant-scoped administration, revocation, and metadata-only security events.
- `hormuz/credential_store.py` and `hormuz/session_client.py` own fail-closed OS secure-store custody and the CLI login/refresh/logout path.
- `hormuz/session_admin_client.py` owns the authenticated, redirect-refusing session-administration CLI transport and validates the metadata-only session and security-event response contracts.
- `hormuz/config.py` validates configuration, defines identity/route/rate-card policy data, and resolves monotonic organization/team/person model, DLP, budget, and context-injection policy.
- `hormuz/policy.py` evaluates access, fallback, caps, and budgets without transport concerns.
- The authenticated Claude Code catalog path resolves static organization/team/person model authorization through the policy engine and exposes only compatible policy aliases. It never contacts an upstream, reserves budget, or records usage; generation remains the authoritative budget and routing check.
- `hormuz/store.py` owns the SQLite schema and monthly aggregations.
- `hormuz/billing.py` validates complete OpenAI and Anthropic cost-report pages and normalizes exact provider amounts and supported billing dimensions without persistence or credentials.
- `hormuz/billing_client.py` owns fixed-origin authenticated provider cost collection, bounded retry and pagination, stable content-free errors, and query-source evidence without persistence.
- `hormuz/usage.py` parses bounded provider usage and actual-model metadata through provider-specific allowlists without storing response content.
- `hormuz/redaction.py` applies bounded credential, regulated-identifier, low-confidence PII, exact-dictionary, and provider-format-aware opaque-media rules to provider-bound JSON plus transport-supplied unredactable strings.
- `hormuz/dlp_evaluation.py` measures one configured organization detector over a strict local labeled corpus and emits aggregate content-free evidence without provider, transport, or persistence behavior.
- `hormuz/dlp_approval.py` computes domain-separated keyed fingerprints over canonical provider request material without persistence or transport concerns.
- `hormuz/dlp_client.py` implements the bounded, authenticated approver CLI transport and refuses redirects or non-loopback plaintext HTTP.
- `hormuz/context.py` authorizes, applies immutable lifecycle observations, quarantines high-confidence injection patterns, surfaces structured contradictions, ranks, budgets, and fingerprints explicit provider-neutral context packs without transport or persistence concerns.
- `hormuz/context_injection.py` extracts bounded direct user text and deterministically renders authorized packs as provider-specific user-priority reference data without transport, persistence, or policy concerns.
- `hormuz/context_lifecycle.py` defines strict evidence, promotion-policy, subject-fingerprint, conflict, negative-signal, and source/dependency transition rules without persistence or transport concerns.
- `hormuz/context_api.py` defines the versioned lifecycle mutation request and metadata-only response contracts without authentication, persistence, or network behavior.
- `hormuz/context_lifecycle_client.py` implements the bounded, authenticated remote connector transport, refuses redirects and non-loopback plaintext HTTP, and validates exact response shapes.
- `hormuz/context_store.py` implements the local governed-record, trusted-snapshot, immutable evidence, and resumable revalidation repository with optimistic concurrency, leases, integrity checks, and metadata-only mutation/read/lifecycle audit behind a content-codec boundary.
- `hormuz/mcp.py` implements the bounded dual-era MCP stdio protocol and an HTTPS client for the authenticated Context Pack API; it has no repository or provider access.
- `hormuz/context_benchmark.py` evaluates the production context-pack kernel against frozen synthetic snapshots and separated outcomes; it has no provider, network, or context-repository dependency.
- `hormuz/cli.py` exposes serving, diagnostics, policy checks, client configuration, usage and billing ingestion/reporting, offline DLP detector evaluation, local lifecycle operations, and config-independent remote connector commands.

## Trust boundary

Hormuz is trusted with plaintext requests and responses because it must inspect and relay them. The usage and DLP approval stores are deliberately metadata-only; the latter keeps only a keyed fingerprint and bounded binding metadata, never the payload. Caller-controlled provider-request headers cross a gateway-owned 1,024-byte visible-ASCII boundary before DLP, policy accounting, or egress; folded, control-bearing, non-ASCII, or overlong values receive a fixed provider-shaped rejection without a usage event. Provider-returned model IDs, request IDs, `Content-Type`, processing-time, and rate-limit headers independently cross a gateway-owned ASCII/length/duplicate boundary before they can enter telemetry or downstream headers; unsafe response metadata is omitted without retaining its value. Provider API credentials can leave the process only through a configured HTTPS upstream, except for literal loopback development fakes; upstream URLs cannot carry user credentials, queries, or fragments. Provider redirects are refused rather than followed, so a configured provider origin cannot move Hormuz's credential or request to a second origin; the `Location` and target are neither reflected nor retained. Offline provider billing import never receives an administrator credential. Authenticated billing fetch holds one transiently in the operator process and sends it only to the fixed provider HTTPS origin; neither path persists the credential or raw response. Both retain normalized financial metadata such as project/workspace IDs and line-item descriptions. Governed content is held in a different SQLite database; usage lineage stores pack/record IDs and versions, never record content or the retrieval query. The current local context codec is plainly labeled as unencrypted and is not an enterprise storage claim. Automatic context lookup occurs only after authentication and model authorization. Repository-scoped lookup additionally requires an exact effective-policy grant plus one supported-client selector; branch and revision only narrow that grant, and a revision must match the trusted repository/branch lifecycle snapshot. Authorized repository and branch IDs may enter the separate context-read audit, while invalid raw selectors never enter routine evidence and no Hormuz scope header reaches a provider. The metadata-only pack-read audit must commit before content can enter the provider-bound request. DLP then runs on the complete request, including injected context and valid consumed scope-header values, before provider storage policy, generation-budget reservation, and upstream serialization. Anthropic token-count requests use the same context mutation and DLP path but do not reserve a generation budget or create an inference-usage row. They retain the context-read audit and any metadata-only DLP security evidence. DLP inspects provider-bound JSON, one UTF-8 form-decoded view of the raw provider query, the exact allowlisted caller headers that the transport will forward, and valid consumed scope values; JSON keys, raw query, and forwarded header values are preserved, so would-be redaction in those locations fails closed. Team and actor overlays are selected only from the authenticated identity and can only strengthen an enabled organization rule; callers cannot request a weaker scope. Recognized opaque provider media is denied before egress unless the organization explicitly turns that rule off; Hormuz does not claim to inspect the underlying bytes. The off-mode exemption is limited to each recognized opaque object, so inspectable siblings remain governed by credential and DLP rules. Approval consumption is atomic, binds the operation/body/raw-query/forwarded-header/consumed-scope map, and precedes egress, so mutation or concurrent replay cannot duplicate the exception. DLP may transform rendered context; pack lineage identifies the selected governed sources rather than attesting to exact post-redaction provider bytes.

## Compatibility boundary

Hormuz implements the provider endpoints and local MCP stdio integration required by Codex and Claude Code rather than inventing a new employee-facing client. Provider and MCP protocol changes are compatibility risks and require executable conformance tests.

## Identity boundary

OIDC supports both a workload resource-server path and a human authorization-code + PKCE session path. Discovery and JWKS metadata are cached, an unknown signing-key ID triggers one refresh, and authorization attributes come only from the configured `(issuer, subject)` mapping. Hormuz does not trust caller-provided group or team claims. Lifecycle connectors use this same resource-server path and additionally require server-configured `context_promoter`; the bearer token proves the mapped workload identity, not the truth of a claimed CI or source event. Usage administration independently requires `usage_viewer`; report, self-usage, secret-summary, budget-total, and active-reservation queries derive organization from the authenticated identity rather than a caller-selected tenant. The local session kernel binds every new session to its organization, actor, team, clearance, and client; a mapping change or capability-gated administrative action revokes it before provider work. The kernel remains single-node; real-IdP validation, SCIM, shared immediate revocation, KMS, and HA persistence remain enterprise milestones. See [OIDC.md](OIDC.md), [CONTEXT_LIFECYCLE_API.md](CONTEXT_LIFECYCLE_API.md), [SESSION_ADMIN_API.md](SESSION_ADMIN_API.md), and [USAGE_ADMIN_API.md](USAGE_ADMIN_API.md).
