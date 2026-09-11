# Account-binding preflight for v1.3.0

This is the pre-implementation checkpoint for the prospective account-binding
slice of #8/#214. It does not implement reconciliation or enable account binding
in the gateway. The source baseline is protected main
`16fe3640abd9935d9d16d8ccc80af236b546dbfd`; its 161 runtime files are identical to
published v1.2.0 commit `d854a5a453fcbe20cb3f4c1e261e146f2da93855`.

The [owner-approved boundary](https://github.com/Xpounder-com/hormuz/issues/8#issuecomment-5562564894)
and frozen `finance-account-binding-contract-v1.json` remain unchanged. That
contract's v1.1.0 target and earlier baseline are historical provenance. The
new `finance-transition-plan-v8.json` supplies the precise current proposal.
Its acceptance flags remain false: exact-head review, protected checks, normal
merge and exact-main evidence must be recorded on #214 before acceptance.

## What is proposed

Introduce two additive, append-only internal tables at SQLite 13 / PostgreSQL
18. These are the proposed next versions, not installed or bundled migrations.
If another feature consumes either version first, publish a reviewed successor
plan and rerun the transition proofs; never edit a shipped migration.

`portfolio_finance_account_binding_versions` registers an operator's immutable
association between a configured inference upstream and a collection account.
`gateway_finance_attempt_account_bindings` records one bound or explicitly
unbound sidecar per new durable attempt. Existing attempt/public v1 shapes,
audit bytes, reservations, native finance evidence and collection observations
are untouched. No row is manufactured for historical attempts.

The machine-readable plan specifies every column, key, nullability rule,
foreign key, audit source and allowed grant. Table definitions in the tests
are deliberately small **DDL witnesses**, not these production schemas. The
implementation must replace them with real successor DDL and add schema,
append-only, audit-source, RLS and gateway behavior tests.

## Configuration and registration contract

All configuration described here is prospective. Do not add these fields to a
running v1.2.0 service. Existing configurations need no edits to keep inference
working. Public HTTP routes, policy, auth, pagination and errors remain unchanged.

The optional `upstreams.<protocol>.finance_identity` object contains exactly:

```json
{
  "upstream_reference_id": "openai-primary",
  "upstream_reference_version": 1,
  "transport_profile": "openai.first-party.v1",
  "inference_credential_reference_id": "inference-primary",
  "inference_credential_reference_version": 1
}
```

These are opaque operator-assigned metadata references, never secrets, secret
hashes, URLs, environment-variable names as account identity, or independently
verified account ownership. The existing upstream slot determines transport;
finance metadata cannot select a different host or credential. The fixed
transport profiles are `openai.first-party.v1` and
`anthropic.first-party.v1`; the latter has offline contract scope only unless
separately authorized. Match the actual selected transport to its fixed
first-party HTTPS origin (`https://api.openai.com` or
`https://api.anthropic.com`) and expected provider profile before binding.
Compatible proxies/resellers are unbound, even if they speak the same protocol.
No DNS/account probe or provider API call occurs during binding.

An optional top-level `finance_account_bindings` array maps the authenticated
tenant and upstream reference to exactly one configured registration version:

```json
[
  {
    "organization_id": "acme",
    "upstream_reference_id": "openai-primary",
    "binding_id": "primary-account",
    "binding_version": 1
  }
]
```

The maximum is 1,024 mappings; identifiers use the plan's ASCII grammar
`[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}` and versions are integers 1–2,147,483,647,
never booleans. Duplicate tenant/upstream mappings are ambiguous, even if equal.
Unknown fields, invalid versions or malformed optional metadata disable finance
binding for the affected selection and produce a fixed unbound reason. They
must not add an inference denial or fall back to another tenant/account.
An invalid configuration file as a whole still follows existing startup rules.

Add one local operator command, following the existing finance authentication
flow: `hormuz --config CONFIG finance account bind FILE [--token-env ENV]`.
There is no new HTTP endpoint, daemon, scheduler, read role or credential store.
Authenticate and authorize the existing `portfolio_admin` for the server-derived
tenant **before reading FILE or looking up a binding**. The strict JSON file is
at most 64 KiB, rejects duplicate keys/non-finite numbers/unknown fields, and has
the exact request shape recorded in the plan. It names the source binding's
ID, version and digest, the selected upstream/credential references, state and
expected registration version. It never accepts a tenant, account fingerprint,
actor, event ID, timestamp or authority grant from the file.

In one tenant-bound transaction, reauthorize; take the existing organization
serialization lock; return any exact historical request replay; otherwise load
the exact source binding and current registration;
verify active source/version/digest/provider/scope and the configured upstream
context; copy the source account/scope/fingerprint-key coordinates; then append
the registration and its audit entry. Use compare-and-swap on `expected_version`
(`null` for version 1). Exact canonical replays return the original immutable
receipt, even after later versions; a conflicting body at an already consumed
expected version is `binding_conflict`, not a silently revised mapping. Canonical
request digests use metadata only and must include the authenticated tenant.

Revocation is another immutable registration version with state `revoked` and
reason `revoked`. It may copy a previously pinned source that has since become
revoked; revoking a binding must not depend on the source still being active.
Reactivation requires a new explicitly configured active version and a current
active source. Registration errors are the existing content-free finance codes
`unauthenticated`, `forbidden`, `invalid_request`, `binding_conflict`, `unavailable`;
never include the file, credential, provider response or database message.
Success returns only the metadata receipt shape in the plan. Displaying a
receipt is not permission to read or use provider administrative credentials.

## Capture and race contract

Resolve the selected upstream object and credential-reference metadata once,
after authentication and ordinary routing. Carry the same immutable context
to capture and forwarding; do not perform a second config lookup before egress.
Finance metadata never changes route eligibility, budget policy or failover.

Inside the existing attempt-root/initial-event/reservation transaction, take
the same organization serialization lock as registration/source revocation;
check the configured binding version is the current active registration and
its source version is current and active. Compare tenant, actual transport,
upstream/credential references, source digest/account/scope and fingerprint-key
version. Append exactly one bound sidecar or an all-null unbound sidecar with
one reason from the frozen internal contract, and append its audit entry.
The evidence timestamp equals the attempt root's timestamp. A semantic mismatch
does not block inference. Any database/audit write failure rolls back the whole
existing attempt transaction and prevents egress; do not catch it as unbound.

Use SQLite `BEGIN IMMEDIATE`; on PostgreSQL retain the existing budget-month
lock, then `work-budget:<schema>:<tenant>` before `portfolio:<schema>:<tenant>`
when both are needed, then perform registration/source resolution and the
existing attempt/audit work in that transaction. Do not invent a second
transaction or lock row. Prove concurrent capture, source revocation and
registration respect this ordering without deadlocks. No
network operation is performed under these locks. A revocation committed
after capture cannot certify the provider's processing time; the sidecar proves
the selected operator-attested version at capture only.

All terminal outcomes retain the original sidecar, including rate limits,
failures and unknown outcomes. Retries/failover create distinct attempts with
their own selected context. No automatic replay, uncertain-hold release,
historical rebinding, account matching, allocation or repricing is introduced.
Replacing a secret without updating its reference/version is undetectable by
metadata alone. This limitation must remain visible in future reports.

## Storage and permission proposal

Both tables use tenant-qualified keys and foreign keys. Bound evidence must
resolve the exact registration in that tenant. Unbound evidence has no binding
coordinates at all. Version rows and sidecars reject UPDATE, DELETE, TRUNCATE
(PostgreSQL) and SQLite replacement/upsert mutation. The implementation must
protect duplicate primary keys and alternate event/idempotency identities.

Extend only the finite version-2 commit-audit source union with
`hormuz.finance-account-binding-version` v1 and
`hormuz.finance-attempt-account-binding` v1. Each resolves its exact tenant,
event ID and canonical `evidence_json` from the matching new table. Preserve
all old union cases, old audit rows, chain heads/epochs, indexes, uniqueness and
source guards. Unknown sources and cross-tenant/mismatched JSON fail closed.
No free-form or wildcard audit source is proposed.

For PostgreSQL, enable and force tenant RLS using the existing tenant setting,
append-only triggers and restricted runtime role. The proposal adds only
SELECT and INSERT on each table: four permissions, no UPDATE/DELETE/TRUNCATE,
sequence, function, PUBLIC, column, grant-option or new-role grant. The fixed
203-entry proposal and rejected injected-204 digest are in the plan. The
current 199 boundary remains unchanged and rejects both measured proposals.
Schemas 15/16 retain their accepted 185/186 boundary. This measurement is not
authorization to change production privileges; runtime implementation needs
the explicitly reviewed successor and actual least-privilege/RLS tests.

## Executable evidence and its limits

The driver installs the published wheel, checks its exact release digest and
installation location, compares all 161 installed runtime files against the
digest-verified published source kit, and loads predecessor fixtures only from
that same verified buffer. Missing, altered or extra runtime files fail closed.
There is no editable candidate import in the old-binary process.

For each backend the suite populates usage, attempts/uncertain reservations,
audit, registry, attribution, outcomes, rate cards, budgets, native finance,
reliability and every collection table. Provider usage/cost and empty buckets
are synthetic OpenAI fixtures. Receipt replays retain their original IDs and
the existing audit chain verifies. Policy/custody tables are included in every
snapshot but not populated by this fixture; final-candidate full-domain proof
remains under #214. No Anthropic spending or live provider request is involved.

The tests prove actual missing-successor refusal, real migration-transaction
rollback after injected test DDL failure, idempotent witness retry, exact
published/current-binary refusal of partial/newer ledgers, isolated old-pair
restore, and retention/forward restore of synthetic post-checkpoint writes.
The PostgreSQL recovery path uses server-matched `pg_dump`/`pg_restore` in an
owned disposable container. Test witnesses do not qualify actual successor schema, gateway binding or reconciliation.

Run the contract/source-kit verifier, then the suite with these environment
variables pointing only to disposable fixtures:

```console
python tools/verify_finance_account_binding_preflight.py \
  --predecessor-source /tmp/hormuz-1.2.0.tar.gz \
  --predecessor-wheel /tmp/hormuz-1.2.0-py3-none-any.whl
HORMUZ_TEST_ACCOUNT_BINDING_PYTHON=/tmp/v120/bin/python \
HORMUZ_TEST_ACCOUNT_BINDING_SOURCE=/tmp/hormuz-1.2.0.tar.gz \
HORMUZ_TEST_POSTGRES_DSN="$DISPOSABLE_POSTGRES_DSN" \
HORMUZ_TEST_PG_CONTAINER="$DISPOSABLE_POSTGRES_CONTAINER" \
  python -m unittest -v tests.test_finance_account_binding_transition_preflight
```

Download the wheel/source from the exact v1.2.0 release and verify both before
installation; the plan pins their digests. CI requires all four variables and
executes these tests using its isolated candidate wheel. Source discovery with
missing opt-in environments may skip them and is not transition acceptance.

## Operator rollout and recovery contract

These are instructions for the future implemented successor, not commands to
run against production during this preflight:

1. Inventory every writer, gateway replica, pool and background operator; stop
   them all. Take a consistent SQLite backup or matching PostgreSQL logical
   dump, preserve exact old application/configuration artifacts and record
   hashes. Verify restoration in a separate destination before migration.
2. Hold the existing migration lock and run the approved additive migrator
   once. On failure, retain evidence and verify the complete old snapshot is
   unchanged; retry only after resolving the cause. Never mark a partial
   ledger applied manually or delete migration history to start an old binary.
3. Validate actual new table shapes, literal ACL, old evidence/audit bytes and
   fresh-process readiness before starting any writer. A running PostgreSQL
   pool's earlier readiness check is not continuous schema compatibility.
4. An old binary must refuse 13/18 and partial state. There is no in-place
   downgrade. With **zero verified post-checkpoint writes**, restore the
   verified old app/config/database pair into a separate destination, validate
   readiness/audit/receipts, then switch traffic through an approved rollout.
5. With nonzero or unknown post-checkpoint writes, retain the candidate state
   and use forward recovery. Do not discard new sidecars, events, receipts or
   uncertain reservations by restoring an earlier database over the candidate.
   Replay no provider request; compare full snapshots and original receipts.

The complete v1.0.0/v1.2.0-to-v1.3.0 source/wheel/signed-OCI/Compose release
matrix, actual runtime migration replacements, live OpenAI finance authority,
reconciliation/reporting and #225 remain separate unfinished work. Accepting
this preflight closes neither #8 nor #214 nor #226, and authorizes no service
deployment, customer-data collection, migration, tag or release.
