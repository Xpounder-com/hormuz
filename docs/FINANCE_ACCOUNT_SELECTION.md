# Provider-free finance account selection

This source checkpoint implements only the configuration and selected-upstream
boundary from the accepted [account-binding preflight](FINANCE_ACCOUNT_BINDING_TRANSITION.md)
for #8 and #214. Both issues remain open. The frozen contract, transition plan
and their historical acceptance flags are unchanged.

Optional `upstreams.<protocol>.finance_identity` and top-level
`finance_account_bindings` use the exact metadata shapes documented in that
preflight. The loader copies them into immutable values. It does not read
provider credentials, inspect credential values, hash secrets, contact providers
or look up billing accounts. Metadata cannot select a different host or
credential, change route eligibility or grant tenant authority.

Only the server-authenticated tenant and selected upstream reference may select
a configured registration reference. Identifiers and versions use the frozen
bounds, and at most 1,024 mappings are accepted. Duplicate tenant/upstream
entries are ambiguous even when identical. A malformed row with valid selection
keys disables that selection; a malformed row whose keys cannot be identified,
or an oversized/invalid array, disables finance selection for the array. There
is no fallback to another tenant or registration.

Absent metadata produces `not_configured`. Invalid metadata, missing mappings,
duplicates and unsupported transport produce fixed preflight reasons. These
results do not deny inference. The selected transport must use the exact
first-party HTTPS origin and corresponding OpenAI or Anthropic profile before
producing a candidate; compatible proxy protocols do not establish provider
billing identity. Anthropic coverage here is synthetic/offline only.

**A candidate is not a durable account binding.** No account fingerprint, source
scope, current registration state or revocation status is resolved. Registered
versions cannot yet be verified as current. Candidates are neither persisted nor
used for financial matching, reports, allocation or repricing. Replacing a secret
under unchanged metadata references remains undetectable by metadata alone.

At startup, the gateway copies one upstream configuration for credential
resolution and freezes the resulting transport, metadata and already resolved
credential mappings into one generation. Credential values remain separate from
finance candidates and are excluded from the generation's diagnostic
representation. The existing credential availability and custody checks remain
in force.

After ordinary authentication, routing and custody checks, each request reads
that one generation and selects its immutable tenant-specific metadata context
before beginning the attempt. The same context reaches the pre-egress attempt
boundary and forwarding. Same-protocol model failover retains this context
alongside its already selected credential. Replacing or mutating the original
configuration/credential dictionaries cannot mix generations before selection
or egress. Configuration changes require a server restart; no hot-reload API or
atomic durable capture is implemented.

Whole-file JSON, duplicate-member, nonfinite-number, size/depth and ordinary
configuration rules remain in force. The two optional metadata surfaces are
opaque to the legacy capability scan; unknown fields within them disable finance
selection instead of changing inference. No public HTTP, attempt, audit, budget
or finance-evidence fields are added.

The SQLite 12/PostgreSQL 17 schemas and fixed 199-permission PostgreSQL boundary
remain unchanged. Registration, same-transaction sidecar capture, successor DDL,
RLS/audit enforcement and approval of the successor ACL are future work.
Production migration, deployment, live provider access and final upgrade/recovery
or release qualification are outside this checkpoint.

Provider-free regressions cover immutable references, selection isolation,
ambiguity, invalid metadata, first-party origin checks, unchanged legacy config
digests, mutation before selection and loopback gateway behavior during a
controlled test-only publication of a complete replacement generation.
The gateway regressions exercise initial and failover attempts with synthetic
credentials and an owned loopback fake server. They do not prove live billing
account ownership or financial reconciliation.
