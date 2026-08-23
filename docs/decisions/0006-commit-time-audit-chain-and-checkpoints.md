# ADR 0006: Per-organization commit-time audit chains and asynchronous checkpoints

- Status: **Accepted**
- Decision date: 2026-08-23
- Decision owner: Product owner
- Approval record: [issue #72](https://github.com/Xpounder-com/hormuz/issues/72)
- Implementation issue: [#72](https://github.com/Xpounder-com/hormuz/issues/72)

## Decision

Hormuz will maintain one versioned, metadata-only audit chain per organization.
It will append a chain entry in the same durable transaction as every current
usage or secret-egress audit event. It will not maintain a global chain.

1. A chain digest binds the complete canonical metadata-only event,
   organization ID, chain version, epoch, sequence, and prior digest.
2. The audit event, entry, and updated tenant chain head commit atomically. A
   failure rolls back all three.
3. Runtime database authority may read and insert entries but must not directly
   update or delete historical chain entries. PostgreSQL enforces that through
   restricted grants; SQLite uses no-update/no-delete triggers for standard
   runtime access. Neither guard claims to stop a database owner or host root
   administrator from altering the source store, which is why external
   checkpointing remains necessary.
4. An intentional restore or migration creates a new epoch. The epoch stores
   the last trusted checkpoint's predecessor epoch, sequence, and digest; it
   never silently restarts numbering.
5. External Object Lock receives a compact, canonical checkpoint tuple
   asynchronously through an operator command. It is not in the model request
   path. Local receipts support maximum-anchor-age readiness monitoring without
   contacting Object Lock.
6. Verification checks entry ordering, source-event correspondence, tenant
   binding, head integrity, and an optional trusted checkpoint. If an older
   recovered database is bridged by an explicit epoch but lacks the predecessor
   event locally, verification requires the referenced external checkpoint.

## Product claim and boundary

> Once a chain checkpoint is externally anchored, Hormuz can detect
> modification, deletion, reordering, or truncation of any committed event in
> that anchored per-organization history.

Hormuz cannot prove traffic that bypassed its gateway. Events after the latest
checkpoint remain in an anchor-delay risk window. A self-hosted Object Lock lab
does not prove host-root protection, production retention operations, HA, or
customer recovery objectives.

## Consequences

The durable evidence contract gains versioned entry and checkpoint schemas and
storage schema v4. Existing audit evidence remains readable but is not
retroactively chained. A deployment that configures a maximum anchor age must
run a separate scheduled anchor job and treat an overdue readiness result as a
local evidence-health alert.

The design preserves Codex and Claude Code streaming behavior because it does
not buffer provider responses for external storage acknowledgement. It also
does not retry provider work or Object Lock writes automatically.

## Rejected alternatives

- **One global chain:** rejected because tenants need isolated evidence and a
  global head would create avoidable cross-tenant coupling.
- **Object Lock write before every provider request:** rejected because it
  would put a remote storage dependency and latency on the client stream.
- **Silently accepting a restored older backup:** rejected because it could
  make a gap look like continuous history.
- **Giving the runtime `UPDATE` or `DELETE` access to historical entries:**
  rejected because it weakens the append-only evidence boundary.
