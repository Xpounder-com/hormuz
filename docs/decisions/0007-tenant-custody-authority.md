# ADR 0007: Tenant custody authority and governed lifecycle approvals

- Status: **Accepted**
- Decision date: 2026-08-23
- Decision owner: Product owner
- Approval record: [issue #89 approved boundary](https://github.com/Xpounder-com/hormuz/issues/89)
- Implementation issue: [#89](https://github.com/Xpounder-com/hormuz/issues/89)

## Decision

Hormuz persists `custody_admin` as a tenant-scoped root authority for
secret-envelope lifecycle authorization. It grants no inference, policy,
identity, direct customer-KMS administration, or arbitrary database
entitlement.

Human authorization, custody-control persistence, custody-executor machine
permissions, customer KMS authority, and all-administrator-loss break-glass
recovery are separate boundaries. The normal gateway runtime and CLI do not
become a governed lifecycle executor.

Configuration supplies tenant-qualified bootstrap identities only before first
initialization. PostgreSQL then becomes authoritative. OIDC administrators are
identified by organization, issuer, and subject; no email, username, group, or
inference identity automatically grants custody authority.

Operation intents use a fixed vocabulary and content-free digests. Routine
sealing, rewrapping, and restore verification require one administrator.
Envelope retirement, provider-credential disablement, key-reference retirement,
and recovery resolution require two distinct active administrators. The
initial secret owner may provide material through a protected input handle; an
administrator sees and approves only its digest.

## Consequences

PostgreSQL schema v5 adds forced-RLS custody tenants, administrators, operation
intents, append-only approvals, and immutable control events. A dedicated
custody-control role owns only that surface. PostgreSQL schema v6 adds a
separate custody-executor role and immutable routine-execution attempt/events.
The executor must atomically claim one exact, current routine intent before any
side effect, and it cannot change the human authority ledger. Managed mode
fails closed rather than letting the legacy CLI execute KMS lifecycle work
directly.

The final active administrator cannot be removed by the ordinary path. Loss of
all administrators returns a break-glass-required error, but this decision does
not define or implement that recovery mechanism.

Customer KMS and IAM remain authoritative. Hormuz records envelope and key
references; it does not edit customer key policy, disable safeguards, or delete
customer-owned keys. The isolated executor consumes only authorized, unexpired
routine work; destructive lifecycle execution remains a separate release gate.

## Rejected alternatives

- **Make `custody_admin` a general Hormuz administrator:** rejected because
  custody, policy, identity, and inference need independent compromise domains.
- **Execute lifecycle work in the administrator CLI:** rejected because human
  approval and machine KMS permission would collapse into one process.
- **Authorize by email or IdP group name:** rejected because both are mutable
  and neither is a tenant-qualified stable principal key.
- **Use one approval for irreversible retirement:** rejected because a single
  compromised root administrator could destroy recoverability.
- **Store initial secret material with the approval:** rejected because the
  authorization ledger is metadata-only; the secret owner uses a protected
  executor-owned input path.
- **Let Hormuz manage customer KMS IAM or key deletion:** rejected because the
  customer key service remains the authoritative custody boundary.
