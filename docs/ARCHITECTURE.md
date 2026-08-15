# Hormuz architecture

Hormuz sits on the provider request path while employees continue using Codex or Claude Code.

```text
Codex / Claude Code
        |
        | bootstrap token or OIDC JWT access token
        v
Hormuz HTTP transport
        |
        +--> verify static/OIDC identity and resolve explicit actor/team metadata
        +--> resolve organization -> team -> person policy
        +--> allow, deny, reroute, or cap the request
        +--> enforce provider storage policy
        +--> redact or deny detected secret material
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
content-bearing JSONL
        |
        | identity-scope validation and idempotent import
        v
local SQLite context repository (separate from usage)
        |
        | SQL authorization/freshness filter before content decode
        v
deterministic lexical context pack
        |
        +--> commit metadata-only pack-read audit
        +--> CLI stdout or POST /v1/context/packs response
        +--> no provider call or automatic prompt injection
```

The HTTP path authenticates first, derives organization/team/actor from the static or OIDC identity, enforces server-owned pack/rate limits, and filters authorized metadata in SQLite before decoding content. Callers cannot supply identity, policy version, or historical evaluation time.

## Code boundaries

- `hormuz/server.py` owns HTTP compatibility, authentication, upstream forwarding, streaming, and protocol-shaped errors.
- `hormuz/auth.py` verifies bootstrap or OIDC JWT credentials and resolves them to configured policy identities.
- `hormuz/config.py` validates configuration and defines identity, route, rate-card, and policy data.
- `hormuz/policy.py` evaluates access, fallback, caps, and budgets without transport concerns.
- `hormuz/store.py` owns the SQLite schema and monthly aggregations.
- `hormuz/usage.py` parses provider usage metadata without storing response content.
- `hormuz/redaction.py` transforms provider-bound JSON values using configured secret controls.
- `hormuz/context.py` authorizes, filters, ranks, budgets, and fingerprints explicit provider-neutral context packs without transport or persistence concerns.
- `hormuz/context_store.py` implements the local governed-record repository, optimistic concurrency, integrity checks, and metadata-only mutation/read audit behind a content-codec boundary.
- `hormuz/cli.py` exposes serving, diagnostics, policy checks, client configuration, usage reporting, and explicit context lifecycle commands.

## Trust boundary

Hormuz is trusted with plaintext requests and responses because it must inspect and relay them. The usage store is deliberately metadata-only. Governed content is held in a different SQLite database and never written to the usage ledger. The current local context codec is plainly labeled as unencrypted and is not an enterprise storage claim. Redaction runs after authentication and policy selection but before upstream serialization. Future reusable-context injection must run after context authorization and before redaction so newly added context is inspected by the same egress controls.

## Compatibility boundary

Hormuz implements the provider endpoints required by Codex and Claude Code rather than inventing a new employee-facing client. Provider protocol changes are compatibility risks and require executable conformance tests.

## Identity boundary

OIDC authentication is currently a resource-server path for JWT access tokens. Discovery and JWKS metadata are cached, an unknown signing-key ID triggers one refresh, and authorization attributes come only from the configured `(issuer, subject)` mapping. Hormuz does not trust caller-provided group or team claims. Browser login, refresh-token custody, opaque-token introspection, SCIM, and active revocation remain separate enterprise milestones; see [OIDC.md](OIDC.md).
