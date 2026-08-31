# Durable data boundary

Hormuz v1 is self-hosted. Hormuz does not operate a hosted customer-data service, and the project has no remote control plane that can
export or delete an organization's deployment. The machine-readable source of
truth for this document is
[`durable-data-v1.json`](durable-data-v1.json).

`metadata-only` does not mean public or anonymous. The database contains
employee and service identities, team membership at event time, model and
policy decisions, token and estimated-cost records, provider request IDs,
administrator identities, and security-control results. Customer operators
must protect it as organizational security and usage data even though Hormuz
does not write prompt or response bodies to these stores.

## Database classes

SQLite is the default one-process evaluation store. PostgreSQL is the optional
shared store and control-plane backend. Every table Hormuz currently creates
is assigned below; the verifier fails if a future migration introduces an
unregistered table.

| Data class | SQLite tables | PostgreSQL tables | Content boundary |
| --- | --- | --- | --- |
| `portfolio_outcome_metadata` | `portfolio_outcome_contexts`, `portfolio_outcome_coverage_events`, `portfolio_outcome_events`, `portfolio_outcome_observations` | `portfolio_outcome_contexts`, `portfolio_outcome_coverage_events`, `portfolio_outcome_events`, `portfolio_outcome_observations` | Strict allowlisted source metadata; descriptive events; historical binding/use-case versions, source revision uncertainty and key-version references; coverage distinguishes source-event from delivery units. No webhook, title, body, comment, path, prompt, response, credential or source content. |
| `portfolio_outcome_control` | `portfolio_outcome_audit_events`, `portfolio_outcome_cursors`, `portfolio_outcome_dead_letters`, `portfolio_outcome_receipts`, `portfolio_outcome_retention_events` | `portfolio_outcome_audit_events`, `portfolio_outcome_cursors`, `portfolio_outcome_dead_letters`, `portfolio_outcome_receipts`, `portfolio_outcome_retention_events` | Audited receipt/replay fingerprints, bounded fixed-code failure metadata, role/registration/retention-bound cursors and separate administrator tombstones. No key values, raw request bodies, fabricated provider receipts or destructive erasure. |
| `portfolio_attribution_metadata` | `portfolio_attribution_events`, `portfolio_attribution_rejections` | `portfolio_attribution_events`, `portfolio_attribution_rejections` | Immutable tenant-qualified attempt/use-case version references, source confidence, append-only corrections, and fixed-class admission receipts. Rejections are separate from eligible attempts. No request header, prompt, response, filename, source/work content, or guessed model facts. |
| `portfolio_attribution_control` | `portfolio_attribution_audit_events`, `portfolio_attribution_cursors`, `portfolio_attribution_idempotency` | `portfolio_attribution_audit_events`, `portfolio_attribution_cursors`, `portfolio_attribution_idempotency` | Safe read/mutation audit, role-bound frozen-window cursors, keyed request digests and immutable-result references. No copied v1 financial facts or raw JSON mutation bodies. |
| `portfolio_registry_metadata` | `portfolio_binding_events`, `portfolio_work_scope_versions` | `portfolio_binding_events`, `portfolio_work_scope_versions` | Append-only tenant-qualified scope/binding IDs, pinned hierarchy/ownership/lifecycle versions, and bounded administrator-entered scope display names. No external work content. |
| `portfolio_registry_control` | `portfolio_audit_events`, `portfolio_cursors`, `portfolio_idempotency` | `portfolio_audit_events`, `portfolio_cursors`, `portfolio_idempotency` | Content-free audit IDs/change classes; actor/role-bound frozen-window cursor state; idempotency identities, keyed request digests, and references to immutable results. No duplicated display labels or raw request/response JSON. |
| `schema_migration_state` | `hormuz_schema_migrations` | `hormuz_schema_migrations` | Applied migration version and state; operational metadata only. |
| `usage_and_secret_evidence` | `gateway_secret_events`, `gateway_usage_events` | `gateway_secret_events`, `gateway_usage_events` | Event-time identity/team, client/model/policy outcome, tokens, estimated cost, provider-request metadata, and rule IDs/counts. No prompt, response, or matched secret value. |
| `budget_reservations` | `gateway_budget_reservations` | `gateway_budget_reservations` | Temporary conservative token/cost reservations bound to organization, team, actor, and attempt metadata. |
| `request_attempt_ledger` | `gateway_request_attempt_events`, `gateway_request_attempts` | `gateway_request_attempt_events`, `gateway_request_attempts` | Content-free pre-egress attempt identity, routing/policy/redaction metadata, reservations, terminal/unknown state, and usage linkage. |
| `audit_chain_state` | `gateway_audit_chain_checkpoints`, `gateway_audit_chain_entries`, `gateway_audit_chain_epochs`, `gateway_audit_chain_heads` | `gateway_audit_chain_checkpoints`, `gateway_audit_chain_entries`, `gateway_audit_chain_epochs`, `gateway_audit_chain_heads` | Tenant-qualified event references, sequence, timestamps, hashes, checkpoint receipts, and external object versions. |
| `policy_control` | — | `policy_active_versions`, `policy_administrators`, `policy_control_events`, `policy_tenants`, `policy_versions` | Administrator identity keys, immutable policy JSON/documents, activation pointers, hashes, summaries, and control events. Policy documents are organization configuration, not request content. |
| `custody_control` | — | `custody_administrators`, `custody_control_events`, `custody_operation_approvals`, `custody_operation_intents`, `custody_tenants` | Tenant/admin identities, fixed operation types, approvals, target/parameter fingerprints, retention configuration, and content-free control events. No plaintext protected input. |
| `custody_execution` | — | `custody_execution_attempts`, `custody_execution_events` | Authorized execution IDs, operation metadata/fingerprints, fixed state/reason codes, and canonical metadata-only evidence. |
| `custody_lifecycle_and_projection` | — | `custody_envelope_attestations`, `custody_lifecycle_asset_identities`, `custody_lifecycle_chain_heads`, `custody_lifecycle_events`, `custody_runtime_projection_acks`, `custody_runtime_projection_barriers`, `custody_runtime_projection_heads`, `custody_runtime_projection_restrictions`, `custody_runtime_replicas` | Immutable asset identities/fingerprints, restriction events, projection versions, replica leases/acknowledgments, recovery codes, hashes, and envelope attestations. It stores no customer KMS key or provider credential plaintext. |
| `custody_deletion_block_evidence` | — | `custody_deletion_events` | Evidence that a custody-history deletion request was blocked by retention, legal hold, or stronger-approval requirements. This table is not a delete executor and cannot authorize tenant deletion. |

## v1.1 registry data and remaining portfolio plan

The #215 source implementation adds the five registry tables above in SQLite
migration 5 and PostgreSQL migration 9. The #216 source implementation adds five
separate attribution tables in SQLite migration 6 / PostgreSQL migration 10.
The #218 source implementation adds nine outcome tables in SQLite migration 7
/ PostgreSQL migration 11. None is a v1.1.0 release. Budgets, live connectors, scorecards and
recommendations remain separately gated and have no tables in this inventory.
#214 stays open for final-candidate
transition proof. See [REGISTRY.md](REGISTRY.md) for the opt-in authority and
[REGISTRY_TRANSITION.md](REGISTRY_TRANSITION.md) for the application/database
pair boundary.

[ATTRIBUTION.md](ATTRIBUTION.md) describes opt-in identity/client authority,
native header handling, administrator corrections and coverage limits.
[ATTRIBUTION_TRANSITION.md](ATTRIBUTION_TRANSITION.md) binds the real registry
predecessor, immutable released-v1 baseline, and retained-state recovery rules.
All five attribution tables follow the customer-controlled export/retention/
backup/deletion boundary below; a void supersedes an assignment and does not
erase the original event, immutable v1 facts, or existing backups.

[OUTCOMES.md](OUTCOMES.md) documents source-neutral ingestion, administrator
reads and internal retention. A separate authorized tombstone removes an
observation from new pages and invalidates old cursors; its original source,
financial and audit facts remain. This does not delete backups/exports or stop
an enabled source from sending new observations. Disable collection separately.
Coverage is an event log: group by source identity and respect source-event
versus delivery units, rather than summing every historical status row.
Eligibility is inconclusive until a separately versioned rule is approved.
Injected key values are never stored, only key-version references and keyed
digests. Customer operators retain the export/retention/deletion authority below.

The [persistence composition boundary](ARCHITECTURE.md#usage-and-portfolio-persistence-composition)
provides a fully declared v1 usage protocol and a typed factory slot for the
separate registry owner. The bundle is an in-memory reference holder,
not a durable data class, connection pool owner, transaction coordinator, or
portfolio store itself. The registry owns its new SQL/transactions and borrows
the existing runtime pool without owning its lifetime. Existing v1 evidence
and its access controls are unchanged.

The planned plane has eight versioned entity families: work-scope versions,
external-work binding events, governed-run attribution events, work-budget
plans, work-outcome events, run-outcome association events, model scorecards,
and policy recommendations. Each is tenant-qualified by `organization_id`.
Mutable business facts are expressed as immutable versions, events, or
supersession links; no new table may update the v1 request-attempt, usage,
policy, or audit evidence in place.

Permitted values are bounded metadata such as opaque identifiers, timestamps,
fixed enums, counts, token and cost components with explicit bases, coverage,
digests, and one bounded administrator-supplied work-scope display name. The
plane excludes prompts, responses, code, patches, paths, filenames, ticket or
project titles and bodies, comments, review text, raw connector payloads,
credentials, and secret or matched-detector values. Signed connector ingress
may hold bounded raw bytes only long enough to verify a signature and parse an
allowlisted event; it does not persist those bytes.

The implementation gate must extend the machine-readable durable-data
inventory, SQLite/PostgreSQL ownership checks, forced-RLS tests, migrations,
rollback/recovery rules, exports, retention, and deletion documentation before
any portfolio store ships. See
[PORTFOLIO_INTELLIGENCE.md](PORTFOLIO_INTELLIGENCE.md).

## Files and external artifacts

| Artifact | Created when | Location and content boundary | Operator authority |
| --- | --- | --- | --- |
| `sqlite_database_file` | Gateway startup or the first local operation. | Customer-selected path; the reference container uses `/var/lib/hormuz/hormuz.sqlite3`. Contains the SQLite classes above and no prompt/response body. | Customer database/filesystem operator owns export, retention, backup, restore, and deletion. |
| `postgresql_schema` | A customer operator runs Hormuz migrations. | Customer-operated PostgreSQL containing the PostgreSQL classes above. | Customer database operator owns export, retention, backup, restore, and deletion. |
| `audit_export_jsonl` | An operator runs `audit export` with a file output. | Operator-selected `0600` file containing metadata-only employee, usage, and security events. | Customer operator owns its downstream access, retention, backup, and deletion. |
| `audit_chain_checkpoint` | An operator runs `audit chain anchor`. | Operator-selected `0600` file containing tenant/chain identifiers, sequence, timestamps, and digests. | Customer operator owns the file; recovery and external-anchor requirements may require retaining a trusted copy. |
| `encrypted_custody_envelope` | An operator runs `custody seal` or `custody rewrap`. | Operator-selected `0600` file containing ciphertext plus tenant, purpose, algorithm, and key-reference metadata. `custody seal` accepts operator-supplied plaintext and does not inspect or constrain its data class. The encrypted payload may therefore be a credential, other secret material, or request content; Hormuz never classifies this artifact as metadata-only. | Customer secret/KMS operator owns its plaintext authorization, access, retention, backup, rotation, and deletion. |
| `object_lock_audit_artifact` | An operator runs an audit anchor command. | Customer-operated S3-compatible Object Lock. The payload is metadata-only audit evidence; the selected adapter stores it directly with storage encryption or as an encrypted envelope. | Customer object-storage policy, retention, and legal hold are authoritative; the gateway runtime has no bypass or retention-shortening authority. |
| `public_release_artifacts` | The protected release workflow runs. | Public GitHub/GHCR source, signed OCI image, SBOM, provenance, signature, and release metadata. These contain no customer runtime data. | They are public distribution artifacts, not tenant data. |

Configuration JSON, provider credentials supplied through environment or a
customer secret manager, database backups/WAL/snapshots, and captured
stdout/stderr/logs/metrics are customer-controlled inputs or deployment
outputs. Hormuz does not operate their retention service. Redirecting stdout
or importing an audit export into another system creates a new
customer-operated copy with its own obligations.

## Export, retention, backup, and deletion

For the v1 self-hosted release, customer database and backup operators are responsible
for export, retention, backup, restore, and deletion using their controlled
infrastructure. Hormuz does not introduce `tenant_data_admin`, automated
tenant deletion, tenant-deletion approval workflows, or a tenant-lifecycle
service.

Issue #106 is a deferred future enterprise/managed-control-plane design gate.
Any destructive implementation requires a new product-owner decision grounded
in an actual customer requirement. Existing custody lifecycle restrictions and
`custody_deletion_events` protect custody evidence; they are not a general
tenant-erasure mechanism.

Hormuz makes no universal-erasure claim. Deleting a self-hosted Hormuz database
does not delete data held by OpenAI or Anthropic, an identity provider, a
customer KMS, retained Object Lock versions, database backups/WAL/snapshots,
client-local history, or customer observability systems. Their respective
customer operators and providers remain authoritative.

| External system ID | Authoritative owner |
| --- | --- |
| `provider_platform_data` | Customer provider account and provider. |
| `identity_provider_data` | Customer identity-provider operator. |
| `kms_keys_and_policy` | Customer KMS operator. |
| `object_lock_retained_versions` | Customer object-storage operator and active retention/legal hold. |
| `database_backups_wal_and_snapshots` | Customer database and backup operator. |
| `client_local_history` | Customer client operator and client vendor. |
| `deployment_logs_metrics_and_traces` | Customer observability operator. |

## Verify the inventory

```bash
python tools/verify_durable_data_inventory.py
python -m unittest -v tests.test_durable_data_inventory
```

The verifier compares the registry to SQLite schema ownership, every bundled
PostgreSQL migration, and the migration-ledger DDL owned by PostgreSQL
bootstrap. A newly created table cannot enter the release unnoticed or inherit
an undocumented content boundary.
