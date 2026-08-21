# ADR 0009: Policy-owned SCIM group bindings

- Status: **Accepted**
- Date accepted: 2026-08-20
- Decision owner: Product owner
- Tracking issue: [#7](https://github.com/Xpounder-com/hormuz/issues/7)

## Decision

Hormuz treats SCIM as an identity lifecycle input, not an authorization-policy
input.

1. The policy system owns `team_bindings` (conceptually
   `policy.team_bindings`; represented in configuration as
   `policies.team_bindings`).
2. A binding uses the immutable SCIM Group `externalId`, never its mutable
   `displayName`.
3. Each binding is tenant-qualified:
   `(organization_id, scim_group_external_id) -> (internal_team_id, policy_id)`.
4. A `policy_admin` alone stages, activates, or rolls back the bindings and
   pre-approved authorization profiles. An `identity_admin` may provision users
   and memberships but cannot grant models, budgets, clients, clearance, or
   capabilities through a SCIM group.
5. An active unbound SCIM group denies the subject by default. An organization
   may choose only an explicit fallback profile in its active policy.
6. A profile supplies the team metadata, clearance, allowed clients,
   capabilities, and an additional restrictive policy overlay. Multiple active
   groups must select the same `(team_id, policy_id)` or access is denied.

The SCIM Group contract is therefore a lifecycle-only directory extension.
Legacy policy-bearing group fields are rejected for new writes and ignored for
authorization if they remain in pre-existing storage rows.

## Why

Identity providers are authoritative for people and memberships. They are not
the authority that decides an organization's AI model access, spend limits, or
administrator capabilities. Mixing the two lets an identity administrator or
connector expand AI permissions by changing a group payload.

The chosen boundary preserves the product role of Hormuz as enterprise AI
parental control: identity tells Hormuz who belongs where; Hormuz policy decides
what that membership is permitted to do.

## Consequences

- Policy projection v4 carries profiles, bindings, and explicit unbound-group
  behavior atomically with model and budget rules.
- A policy activation can immediately tighten or revoke a dynamic user's
  effective access without a connector changing the SCIM group.
- The directory resolves policy after tenant routing but before returning a
  dynamic identity; unavailable or invalid policy fails closed.
- Existing direct workload identity records remain a separate compatibility
  surface. They now require `policy_admin`; mapping workloads to profiles is a
  follow-on decision.
- SCIM vendor certification, real-IdP conformance, external immutable audit,
  KMS, HA, pooling, and backup/PITR remain release gates outside this decision.

## Owner approval record

The product owner explicitly approved the stronger policy-owned design,
including stable external-ID bindings, tenant qualification, policy-admin
authority, default denial for unbound groups, and pre-approved authorization
profiles. The issue checkpoint records the implementation evidence and any
remaining non-production gates.
