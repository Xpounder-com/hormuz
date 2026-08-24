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

Custody authorization (managed PostgreSQL mode)
        |
        +--> authenticated CLI -> CustodyControlService
        +--> tenant-qualified root custody administrators
        +--> immutable content-free intent + distinct approvals
        +--> future separate executor; no KMS work in this service
```

## Code boundaries

- `hormuz/server.py` owns HTTP compatibility, authentication, upstream forwarding, streaming, and protocol-shaped errors.
- `hormuz/server.py` also owns dependency-free liveness and content-free dependency readiness. Readiness verifies only the local policy/evidence path and turns false before graceful shutdown drains active handlers; it never probes a model provider.
- `hormuz/auth.py` verifies bootstrap or OIDC JWT credentials. Runtime identity mapping and policy-administration authority are deliberately separate decisions.
- `hormuz/config.py` validates routes, identity facts, local policies, and one-time managed-policy bootstrap identities.
- `hormuz/policy.py` evaluates access, fallback, caps, and budgets from exactly one request-bound policy snapshot.
- `hormuz/policy_runtime.py`, `hormuz/policy_document.py`, and `hormuz/postgres_policy_store.py` read strict immutable policy documents, then resolve and pin the active PostgreSQL version when managed policy control is enabled.
- `hormuz/policy_control.py` is the narrow authenticated service boundary used by the CLI for bootstrap, administrator changes, staging, activation, rollback, and break-glass recovery.
- `hormuz/custody_control.py` and `hormuz/postgres_custody_store.py` own tenant custody authority and content-free lifecycle approvals. They do not receive plaintext, construct KMS clients, or execute an approved operation.
- `hormuz/store.py` owns the SQLite schema and monthly aggregations; `hormuz/postgres_usage_store.py` implements the same narrow usage/evidence repository with transaction-local organization scope and PostgreSQL RLS. In PostgreSQL gateway mode, `hormuz.postgres.PostgresConnectionPool` supplies one bounded runtime pool shared with managed-policy reads; each checkout receives fresh transaction-local role, search-path, and tenant state.
- `hormuz/audit_chain.py` owns canonical per-organization commit-time chain and checkpoint primitives. Storage adapters append a chain entry with each durable current audit event; custody adapters retain the compact checkpoint only through an explicit out-of-band operator command.
- `hormuz/custody.py` owns provider-neutral encrypted-envelope and audit-anchor contracts; `hormuz/aws_custody.py` provides the optional AWS reference adapters, while `hormuz/openbao_custody.py` and `hormuz/self_hosted_custody.py` provide the optional OpenBao and S3-compatible Object Lock adapters. `hormuz/custody_runtime.py` resolves owner-only encrypted provider credentials at gateway startup.
- `hormuz/usage.py` parses provider usage metadata without storing response content.
- `hormuz/redaction.py` transforms provider-bound JSON values using configured secret controls.
- `hormuz/cli.py` exposes serving, diagnostics, policy checks, client configuration, and usage reporting.

## Trust boundary

Hormuz is trusted with plaintext requests and responses because it must inspect and relay them. The usage store is deliberately metadata-only. Redaction runs after authentication and policy selection but before upstream serialization. The core has no context retrieval, lifecycle, cache, provenance, memory, or content-storage path; the separately packaged experiment is not imported by normal gateway operation. See [CONTEXT_EXPERIMENT_MIGRATION.md](CONTEXT_EXPERIMENT_MIGRATION.md).

For an enterprise-facing listener, customer-controlled infrastructure terminates
public TLS. Hormuz accepts only a network-restricted and separately
authenticated private proxy hop; this ingress credential is not an employee
identity and cannot authorize policy or provider access. Forwarded headers are
not a source of identity or tenant facts. See [DEPLOYMENT.md](DEPLOYMENT.md).

## Compatibility boundary

Hormuz implements the provider endpoints required by Codex and Claude Code rather than inventing a new employee-facing client. Provider protocol changes are compatibility risks and require executable conformance tests.

## Identity boundary

OIDC authentication is currently a resource-server path for JWT access tokens. Discovery and JWKS metadata are cached, an unknown signing-key ID triggers one refresh, and authorization attributes come only from the configured `(issuer, subject)` mapping. Hormuz does not trust caller-provided group or team claims. Browser login, refresh-token custody, opaque-token introspection, SCIM, and active revocation remain separate enterprise milestones; see [OIDC.md](OIDC.md).

## Root-authority boundary

`policy_admin` is not a model entitlement; it is root authority to change a tenant's policy. In managed mode, configuration may seed tenant-qualified bootstrap identities only before that tenant is initialized. PostgreSQL then becomes the source of truth for authority. The runtime database role cannot read administrators or mutate policies; the policy-control role cannot alter immutable version/event history. A CLI caller is authenticated from a credential and routed through `PolicyControlService`, never through a direct database command or a self-asserted actor. See [POLICY_CONTROL.md](POLICY_CONTROL.md).

`custody_admin` is a separate tenant root authority for secret-envelope
lifecycle authorization. It grants no inference, policy, identity, or direct
customer-KMS entitlement. Human approval, the dedicated custody-control
service/database role, the future machine executor, customer KMS authority, and
all-administrator-loss break glass remain separate. See
[CUSTODY_CONTROL.md](CUSTODY_CONTROL.md).

## Key-custody and audit-retention boundary

The gateway can obtain an upstream provider credential from an encrypted,
owner-only envelope rather than a plaintext environment value. Key custody and
immutable audit anchoring use provider-neutral contracts: the optional AWS
profile uses customer-managed KMS and SSE-KMS Object Lock, while the optional
self-hosted profile uses OpenBao Transit and envelope-encrypts the artifact
before a customer-operated S3-compatible Object Lock service receives it. All
data-key operations bind the tenant and purpose, and rotation re-encrypts the
wrapped data key without printing a secret. A named storage implementation is
only a separately evidenced target; Ceph RGW is the first optional self-hosted
candidate. An externally retained per-organization checkpoint makes committed
history up to that checkpoint tamper-evident; it does not prove gateway-bypass
traffic or events inside the later anchor-delay window. See [AUDIT.md](AUDIT.md)
and [CUSTODY.md](CUSTODY.md).
