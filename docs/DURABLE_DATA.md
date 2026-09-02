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
| `portfolio_finance_rate_cards` | `portfolio_finance_rate_cards` | `portfolio_finance_rate_cards` | Immutable operator-configured canonical rate-card versions, explicit currency/rates/intervals, content digest and original registration receipt. No provider payload, automatic repricing or native request-cost capture. |
| `portfolio_finance_audit` | `portfolio_finance_audit_events` | `portfolio_finance_audit_events` | Tenant-qualified register/read audit IDs, actor, card ID/version/digest, timestamp and sequence. Administrative reads commit audit before delivery; retries retain the original receipt. |
| `portfolio_work_budget_plans` | `portfolio_work_budget_activation_events`, `portfolio_work_budget_active_plans`, `portfolio_work_budget_plan_versions` | `portfolio_work_budget_activation_events`, `portfolio_work_budget_active_plans`, `portfolio_work_budget_plan_versions` | Immutable amount/model/token/request-cap plan versions and at most 1,000 activation facts per plan plus a compare-and-set active pointer. Request enforcement validates the current event against its immediate predecessor; management reads validate the bounded full chain. Scope/version, policy digest, currency, actor, reason and timestamps are metadata; no work item, prompt or response content is stored. |
| `portfolio_work_budget_accounting` | `portfolio_work_budget_reservation_bindings` | `portfolio_work_budget_reservation_bindings` | Immutable attempt-to-plan bindings with exact attribution, plan generation, configured route-rate identity, policy identity, wire-safe selected `model_routes` mapping-key reference, reserved amount/output tokens and valuation-rule identity. The generated model-ID namespace is reserved against configured-key impersonation. Provider-native names remain in request/rate-card evidence and cannot trigger a narrower runtime-only grammar failure. Window and currency are foreign-key-bound to the exact plan version; request-time accounting fails closed at 10,000 bindings per plan window. A successful provider response without complete input/output usage evidence remains an uncertain conservative reservation rather than becoming a fabricated zero-cost terminal fact. These are conservative gateway estimates, not provider invoices or general-ledger reconciliation. |
| `portfolio_work_budget_audit` | `portfolio_work_budget_audit_events` | `portfolio_work_budget_audit_events` | Tenant-qualified create/activate/preview/report and fixed-class denial events with actor, plan/version, evaluation timestamp and sequence. Denials retain the transaction's evaluation time even when their mandatory audit commits after the rejected reservation rolls back. A required tenant/plan/operation/evaluation-time index keeps per-plan denial reporting bounded to its report keyspace; every declared PostgreSQL column must be a true key attribute rather than only an `INCLUDE` attribute. Reports inspect at most 10,001 matching denial facts and fail closed beyond the supported 10,000-row window without deleting or truncating evidence. No submitted JSON body or provider payload is retained. |
| `portfolio_outcome_metadata` | `portfolio_outcome_contexts`, `portfolio_outcome_coverage_events`, `portfolio_outcome_events`, `portfolio_outcome_observations` | `portfolio_outcome_contexts`, `portfolio_outcome_coverage_events`, `portfolio_outcome_events`, `portfolio_outcome_observations` | Strict allowlisted source metadata; descriptive events; historical binding/use-case versions, source revision uncertainty and key-version references; coverage distinguishes source-event from delivery units. No webhook, title, body, comment, path, prompt, response, credential or source content. |
| `portfolio_outcome_control` | `portfolio_outcome_audit_events`, `portfolio_outcome_cursors`, `portfolio_outcome_dead_letters`, `portfolio_outcome_receipts`, `portfolio_outcome_retention_events` | `portfolio_outcome_audit_events`, `portfolio_outcome_cursors`, `portfolio_outcome_dead_letters`, `portfolio_outcome_receipts`, `portfolio_outcome_retention_events` | Audited receipt/replay fingerprints, bounded fixed-code failure metadata, role/registration/retention-bound cursors and separate administrator tombstones. No key values, raw request bodies, fabricated provider receipts or destructive erasure. |
| `portfolio_attribution_metadata` | `portfolio_attribution_events`, `portfolio_attribution_rejections` | `portfolio_attribution_events`, `portfolio_attribution_rejections` | Immutable tenant-qualified attempt/use-case version references, source confidence, append-only corrections, and fixed-class admission receipts. Rejections are separate from eligible attempts. No request header, prompt, response, filename, source/work content, or guessed model facts. |
| `portfolio_attribution_control` | `portfolio_attribution_audit_events`, `portfolio_attribution_cursors`, `portfolio_attribution_idempotency` | `portfolio_attribution_audit_events`, `portfolio_attribution_cursors`, `portfolio_attribution_idempotency` | Safe read/mutation audit, role-bound frozen-window cursors, keyed request digests and immutable-result references. No copied v1 financial facts or raw JSON mutation bodies. |
| `portfolio_registry_metadata` | `portfolio_binding_events`, `portfolio_work_scope_versions` | `portfolio_binding_events`, `portfolio_work_scope_versions` | Append-only tenant-qualified scope/binding IDs, pinned hierarchy/ownership/lifecycle versions, and bounded administrator-entered scope display names. No external work content. |
| `portfolio_registry_control` | `portfolio_audit_events`, `portfolio_cursors`, `portfolio_idempotency` | `portfolio_audit_events`, `portfolio_cursors`, `portfolio_idempotency` | Content-free audit IDs/change classes; actor/role-bound frozen-window cursor state; idempotency identities, keyed request digests, and references to immutable results. No duplicated display labels or raw request/response JSON. |
| `browser_session_identity` | `session_enrollments`, `human_sessions`, `consumed_refresh_credentials` (separate opt-in database) | — | Exact issuer/subject, tenant/actor/team/client bindings and expiry, keyed credential hashes, and AEAD-encrypted transient nonce/PKCE verifier. No raw access/refresh credential, IdP token, authorization code, prompt, or response. |
| `browser_session_security_events` | `session_security_events` (separate opt-in database) | — | Local metadata-only logout, refresh-replay, and mapping-removal events. These are not immutable audit-chain evidence. |
| `team_onboarding_identity` | `onboarding_organizations`, `onboarding_teams`, `onboarding_memberships`, `onboarding_invitations` (session database) | — | Operator-assigned identity metadata, stable subject bindings, membership status/version and keyed recipient/code hashes. No raw email or invitation code. |
| `team_onboarding_events` | `onboarding_events` (session database) | — | Transactional organization, team, invitation and member transitions with a truthful local-operator/member actor. Not immutable audit-chain evidence. |
| `console_authorization` | `console_grants` (session database) | — | Operator-assigned, versioned usage-viewer/member-administrator grants. No implicit authority from employee sessions or IdP roles. |
| `console_sessions` | `console_login_flows`, `console_sessions` (session database) | — | Separate keyed console-cookie hashes, encrypted transient nonce/PKCE state, membership/grant versions and bounded expiry. No raw browser cookie, CSRF token or IdP token. |
| `console_events` | `console_events` (session database) | — | Transactional grant/session transitions and verified administrator actor IDs. Local metadata, not externally anchored audit evidence. |
| `schema_migration_state` | `hormuz_schema_migrations` | `hormuz_schema_migrations` | Applied migration version and state; operational metadata only. |
| `usage_and_secret_evidence` | `gateway_secret_events`, `gateway_usage_events` | `gateway_secret_events`, `gateway_usage_events` | Event-time identity/team, client/model/policy outcome, tokens, estimated cost, provider-request metadata, and rule IDs/counts. No prompt, response, or matched secret value. |
| `budget_reservations` | `gateway_budget_reservations` | `gateway_budget_reservations` | Temporary conservative token/cost reservations bound to organization, team, actor, and attempt metadata. |
| `request_attempt_ledger` | `gateway_request_attempt_events`, `gateway_request_attempts` | `gateway_request_attempt_events`, `gateway_request_attempts` | Content-free pre-egress attempt identity, routing/policy/redaction metadata, reservations, terminal/unknown state, and usage linkage. |
| `provider_reliability_evidence` | `gateway_provider_attempt_metrics`, `gateway_provider_failover_events` | `gateway_provider_attempt_metrics`, `gateway_provider_failover_events` | Append-only per-egress monotonic header/first-byte/total timing, provider/downstream byte counts, provider status, and one-hop attempt linkage with fixed trigger reason. No prompt or response body. |
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
/ PostgreSQL migration 11. The bounded #8 rate-card slice adds two finance tables
in SQLite migration 8 / PostgreSQL migration 12. The #217 work-budget runtime
adds five tables in SQLite migration 9 / PostgreSQL migration 13. None of these
source changes is by itself a v1.1.0 release. Provider-invoice/general-ledger
reconciliation, live connectors, scorecards and recommendations remain
separately gated and have no tables in this inventory.
The provider-reliability source slice adds two content-free, append-only tables
in SQLite migration 10 / PostgreSQL migration 14. They store gateway-observed
latency/byte counters and the exact one-hop failover relationship, never a
prompt or response body. See [PROVIDER_RELIABILITY.md](PROVIDER_RELIABILITY.md).
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

[FINANCE_RATE_CARDS.md](FINANCE_RATE_CARDS.md) describes internal administrative
registration and exact-version reads, not a financial-report API. Both finance
tables follow the same customer-owned export/backup/retention/deletion boundary.
There is no row-deletion operation, automatic rate selection, retrospective
native-usage backfill or change to existing request costs. A newer card must use
a new version. Its content digest identifies bytes; it is not a keyed integrity
proof against an operator rewriting the database and audit together.

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
| `console_browser_cookies` | A member starts or completes console login. | Opaque, short-lived HttpOnly flow/session cookies in the browser; HTTPS uses host-only Secure cookies. No browser local storage. | Member logout, operator/member revocation and expiry invalidate access. Do not export cookie credentials. Browser backups are controlled by the browser/user. |
| `hosted_profile_file` | An operator configures hosted authentication staging. | Owner-only runtime JSON containing origin, issuer, client identifier and state path without credentials. | Operator owns access review, retention, replacement and deletion. |
| `hosted_state_marker` | An operator initializes or restores hosted staging. | Owner-only random instance identifier, recovery flag and keyed configuration binding. | Operator owns offline backup/restore and must preserve the bound origin, issuer, client and session key. |
| `hosted_state_snapshot` | An operator runs the low-level offline snapshot. | Owner-only plaintext directory containing consistent session/usage SQLite copies and a keyed digest manifest. | Same-disk inspection or restore only; never transfer or retain it offsite as plaintext. |
| `hosted_offsite_backup_archive` | An operator runs `hormuz.hosted backup-export`. | Owner-only AES-256-GCM archive of the fixed hosted snapshot file set. The public header contains only a format marker and nonce. | Operator owns transfer, verification, off-disk retention, restore and deletion and must keep the key separate. |
| `hosted_backup_key_file` | An operator independently generates a backup key. | Owner-only file containing base64 encoding of 32 random bytes, distinct from the session master key. | Operator owns secret custody, access, rotation, retention and deletion; loss makes the archive unrecoverable. |

Configuration JSON, provider credentials supplied through environment or a
customer secret manager, database backups/WAL/snapshots, and captured
stdout/stderr/logs/metrics are customer-controlled inputs or deployment
outputs. Hormuz does not operate their retention service. Redirecting stdout
or importing an audit export into another system creates a new
customer-operated copy with its own obligations.

## Export, retention, backup, and deletion

The team operator command creates `team_invitation_file`, a new POSIX owner-only
file containing a one-time secret code and connection metadata for manual private
delivery. Keep it out of repositories, logs and shared artifacts; delete it after
delivery/expiry under your retention policy and revoke outstanding invitations
before disposing of a lost file. The operator-supplied recipient-email input file
also remains under the operator's retention/deletion control. This preview does
not send email or automatically erase these files or their backups.

The opt-in local login adapter also creates `session_database_file`, an
owner-only SQLite file plus WAL/SHM sidecars at the configured session path,
and `client_session_secure_store`, an access/refresh pair held by the approved
OS credential store. The session file is separate from routine usage data;
schema 3 introduced managed membership, invitation hashes and local transition
events. Schema 4 adds the console tables above in the same transaction boundary.
See [the console](ADMIN_CONSOLE_LOCAL.md) for v2/v3 upgrade and authority rules,
and [team onboarding](TEAM_ONBOARDING.md) for the original schema 2 upgrade and
offboarding/reinvite behavior. Membership tombstones prevent email reuse from
reassigning an established account and must not be deleted to re-enable access.
Its identity bindings are sensitive even though credential values are hashed.
Only nonce/PKCE flow material is recoverably encrypted. Expired enrollment
rows are removed on the next enrollment, and consumed refresh hashes expire
after the session's absolute lifetime. Revoked/expired session rows and local
security-event retention remain operator responsibilities. Logout revokes the
server session before deleting the local credential record.

Console sessions have a ten-minute idle limit and one-hour absolute lifetime.
New console login replaces old console sessions for that grant; console logout
does not revoke the native client. Member removal revokes both kinds of sessions
and the console grant together. Console flow secrets are removed at callback
consumption, failure, or the next login's expiry sweep. Expired/revoked console
rows, grants and local events still require operator retention decisions.

Backups must protect both the session database and the independently injected
master key. Restoring an old session database can otherwise resurrect revoked
or consumed credentials: rotate the master key before restoring the local
broker, forcing fresh logins. Online key rotation, distributed session storage,
immutable session events, and automated tenant erasure are not implemented by
this adapter. See [local login](HOSTED_LOGIN_LOCAL.md).

That session-only reset is insufficient once managed team onboarding is in use:
recipient hashes also depend on the key, and restored membership decisions can
be stale. Managed-directory key migration and revocation reconciliation are
explicit deployment gates in [team onboarding](TEAM_ONBOARDING.md); do not serve
a restored directory as if an old backup preserved current access decisions.

The separate opt-in [hosted authentication staging profile](../deploy/render/gateway/README.md)
adds `hosted_profile_file`, private origin/issuer/client/path configuration with no
secret values; `hosted_state_marker`, a keyed configuration binding plus an empty
advisory lifecycle lock; `hosted_state_snapshot`, consistent plaintext copies of
the session and usage databases with a keyed digest manifest;
`hosted_offsite_backup_archive`, their authenticated encrypted transfer form; and
`hosted_backup_key_file`, its separately retained random key. No database tables
or request-content fields are added. The profile initializes only on an explicit
operator command and refuses missing state or changed key/identity bindings at
startup. Its low-level snapshot remains owner-only plaintext for same-disk checks.
Use `backup-export` for authenticated encrypted transfer; a copy on the same disk
is not disaster recovery, and the archive key must remain in separate custody.

The staging restore command preserves subject/recipient bindings and the master
key, disables all restored memberships, and revokes native and console sessions,
invitations, pending login flows and administrator grants before activation. Only
explicit reinvitation and new login can restore member access; administrator
roles require a new operator grant. This is a conservative single-node recovery
procedure, not online key migration or restoration of decisions newer than the
snapshot. Restoring raw files around that command is outside the supported path.
Private transfer, backup retention, actual cloud disk recovery and access review
remain operator responsibilities and production qualification gates.

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
