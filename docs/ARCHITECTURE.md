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
Usage/evidence repository (SQLite by default; PostgreSQL optional)

Policy administration (managed PostgreSQL mode)
        |
        +--> authenticated CLI -> PolicyControlService
        +--> tenant-qualified root policy administrators
        +--> immutable policy documents -> atomic active-version pointer
        +--> gateway reads and pins one active version at request start
```

## Code boundaries

- `hormuz/server.py` owns HTTP compatibility, authentication, upstream forwarding, streaming, and protocol-shaped errors.
- `hormuz/auth.py` verifies bootstrap or OIDC JWT credentials. Runtime identity mapping and policy-administration authority are deliberately separate decisions.
- `hormuz/config.py` validates routes, identity facts, local policies, and one-time managed-policy bootstrap identities.
- `hormuz/policy.py` evaluates access, fallback, caps, and budgets from exactly one request-bound policy snapshot.
- `hormuz/policy_runtime.py`, `hormuz/policy_document.py`, and `hormuz/postgres_policy_store.py` read strict immutable policy documents, then resolve and pin the active PostgreSQL version when managed policy control is enabled.
- `hormuz/policy_control.py` is the narrow authenticated service boundary used by the CLI for bootstrap, administrator changes, staging, activation, rollback, and break-glass recovery.
- `hormuz/store.py` owns the SQLite schema and monthly aggregations; `hormuz/postgres_usage_store.py` implements the same narrow usage/evidence repository with transaction-local organization scope and PostgreSQL RLS.
- `hormuz/usage.py` parses provider usage metadata without storing response content.
- `hormuz/redaction.py` transforms provider-bound JSON values using configured secret controls.
- `hormuz/cli.py` exposes serving, diagnostics, policy checks, client configuration, and usage reporting.

## Trust boundary

Hormuz is trusted with plaintext requests and responses because it must inspect and relay them. The usage store is deliberately metadata-only. Redaction runs after authentication and policy selection but before upstream serialization. The core has no context retrieval, lifecycle, cache, provenance, memory, or content-storage path; the separately packaged experiment is not imported by normal gateway operation. See [CONTEXT_EXPERIMENT_MIGRATION.md](CONTEXT_EXPERIMENT_MIGRATION.md).

## Compatibility boundary

Hormuz implements the provider endpoints required by Codex and Claude Code rather than inventing a new employee-facing client. Provider protocol changes are compatibility risks and require executable conformance tests.

## Identity boundary

OIDC authentication is currently a resource-server path for JWT access tokens. Discovery and JWKS metadata are cached, an unknown signing-key ID triggers one refresh, and authorization attributes come only from the configured `(issuer, subject)` mapping. Hormuz does not trust caller-provided group or team claims. Browser login, refresh-token custody, opaque-token introspection, SCIM, and active revocation remain separate enterprise milestones; see [OIDC.md](OIDC.md).

## Root-authority boundary

`policy_admin` is not a model entitlement; it is root authority to change a tenant's policy. In managed mode, configuration may seed tenant-qualified bootstrap identities only before that tenant is initialized. PostgreSQL then becomes the source of truth for authority. The runtime database role cannot read administrators or mutate policies; the policy-control role cannot alter immutable version/event history. A CLI caller is authenticated from a credential and routed through `PolicyControlService`, never through a direct database command or a self-asserted actor. See [POLICY_CONTROL.md](POLICY_CONTROL.md).
