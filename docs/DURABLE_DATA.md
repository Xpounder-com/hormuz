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
