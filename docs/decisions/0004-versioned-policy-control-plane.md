# ADR 0004: Versioned PostgreSQL policy control plane

- Status: **Accepted**
- Decision date: 2026-08-21
- Decision owner: Product owner
- Approval record: [issue #21 design decision](https://github.com/Xpounder-com/hormuz/issues/21#issuecomment-5376899650)
- Implementation issue: [#21](https://github.com/Xpounder-com/hormuz/issues/21)

## Decision

Hormuz remains a CLI-first enterprise AI gateway, but managed policy administration uses a clean internal policy-service boundary backed by PostgreSQL. It is not a direct-database CLI and Hormuz is not an identity provider.

1. Configuration may name tenant-qualified bootstrap administrators only for first tenant initialization. Bootstrap identifies static callers by `(organization_id, actor_id)` and OIDC callers by `(organization_id, issuer, subject)`.
2. Bootstrap persists the tenant initialization marker, administrators, and audit event in one PostgreSQL transaction. Once initialized, everyday policy authority comes only from PostgreSQL; changing configuration cannot drift administrator state.
3. An IdP or SCIM system provides verified identity and membership facts. It does not automatically grant `policy_admin`; group display names, email, usernames, clearance, capabilities, and allowed-client values are not policy-authority keys.
4. `policy_admin` is root authorization authority, not a model or budget entitlement. An active policy can deny inference to a policy administrator, and an inference identity has no policy authority unless explicitly granted.
5. Policy documents are strict, immutable, canonical JSON with a SHA-256 version. They may contain only allowlisted routing, cap, budget, and supported egress-control fields. Change summaries and events are content-free structural metadata.
6. Activation atomically changes one tenant's active-version pointer. Rollback is a new audited activation of a previously active immutable version. A gateway reads and pins the active version at request start.
7. The gateway runtime role is read-only for active policy versions. A separate policy-control role administers tenant policy state. A migration role changes schema. All operate with tenant-qualified transactions and forced PostgreSQL row-level security.
8. A distinct, opt-in break-glass mechanism may recover authority only after all active administrators are lost. It requires a separately managed recovery secret and a fixed audit reason.

## Consequences

The CLI currently invokes the authenticated `PolicyControlService` in process. It accepts a credential environment variable, not a self-asserted actor. A future HTTP API or restricted local control socket can use the same service contract without rewriting policy semantics.

The small control-plane implementation does not close production operations gates. Real generic-IdP conformance, KMS/BYOK and rotation, tamper-evident retention, TLS/HA, pooling, backup/PITR, multi-instance coordination, and independent review remain separate milestones.

## Rejected alternatives

- **Configuration as continuing source of policy-admin authority:** rejected because deployment drift could silently change root authorization.
- **IdP/SCIM group-to-root-role mapping:** rejected because identity membership must not authorize model/budget policy by itself.
- **Direct PostgreSQL writes from the CLI:** rejected because a self-asserted actor or broad control credential would bypass governed authorization and audit semantics.
- **Mutable active-policy rows:** rejected because requests and evidence need an exact immutable version.
