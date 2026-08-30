# v1.1.0 registry compatibility and transition

This guide now covers the **#215 registry implementation**, following the
accepted #214 registry preflight in PR #232. The historical
[version-1 preflight plan](registry-transition-plan-v1.json) remains unchanged.
The [version-2 implementation plan](registry-transition-plan-v2.json) replaces
only its phase and missing-migration probes with real migration verification;
baseline digests, compatibility and rollback policy remain identical.
Neither plan closes #214 or authorizes a v1.1.0 release. See
[REGISTRY.md](REGISTRY.md) for the new operations and operator authority.

## Versions and exact baseline

The product target is **v1.1.0**. Product versions, database migration integers,
and version-1 wire envelopes are separate identifiers:

| Boundary | Released v1.0.0 | #215 implementation |
| --- | --- | --- |
| SQLite migration ledger | 4 | 5 |
| PostgreSQL migration ledger | 8 | 9 |
| Existing v1 API, evidence, configuration, policy, and CLI | Existing contracts | Unchanged |
| Portfolio registry request/response envelopes | Absent | Additive schema version 1 |

These migration numbers now belong to the registry. Later features must use
new migration numbers; never reuse a number or edit an applied migration.

The baseline is the immutable [v1.0.0 release](https://github.com/Xpounder-com/hormuz/releases/tag/v1.0.0),
source commit `2fc0605252e41f731c85cc9146fbff6eb3b34669`. Its final release
intentionally points to canonical, digest-addressed candidate assets without
copying or rebuilding them:

- [Released source archive](https://github.com/Xpounder-com/hormuz/releases/download/candidate-v1.0.0-2c3b16c1742ee76032a33f3714492a8d8515c5291d4d57520441882cd8bc5b5a/hormuz-1.0.0.tar.gz),
  SHA-256 `2c3b16c1742ee76032a33f3714492a8d8515c5291d4d57520441882cd8bc5b5a`.
- [Candidate manifest](https://github.com/Xpounder-com/hormuz/releases/download/candidate-v1.0.0-2c3b16c1742ee76032a33f3714492a8d8515c5291d4d57520441882cd8bc5b5a/hormuz-v1.0.0-candidate-manifest.json),
  SHA-256 `85774aa45a8b30be88d1cb1a7b543222cc1396523aec31c17de07470b09d56b2`.

Do not substitute current main, an automatically generated GitHub tarball,
or a different candidate. Installing this source archive may build a local
wheel; that wheel is an installation of the released source, not a claim
that those wheel bytes were a published v1 artifact. The isolated driver
checks the installed package's source-archive digest and import location.

## Compatibility inventory

The existing eight routes remain unchanged: `GET /health`, `GET /ready`,
`GET /v1/gateway/usage`, `GET /v1/gateway/whoami`, `POST /v1/responses`,
`POST /v1/responses/compact`, `POST /v1/messages`, and
`POST /v1/messages/count_tokens`. Preserve their request and response shapes,
status/error codes, static/OIDC authentication, actor/team scoping, ordering,
retry behavior, provider protocol handling, and budget/attempt semantics.

Preserve existing configuration defaults, policy compare/preview/apply and
rollback behavior, CLI names/options/output/error behavior, and evidence
schemas. Existing callers need no registry enrollment or configuration edit
to retain their v1 behavior. No mandatory work-scope header is introduced by
#215; request attribution belongs to its separately gated feature.

The exact frozen v1 contract manifest is retained, including its historical
`current_release_line: "0.2"` value. Correcting that value in place would
break the frozen manifest. A corrected manifest, if needed, requires an
explicitly versioned, opt-in successor or compatibility adapter, not a silent
change under the existing command/schema.

Only these six approved routes belong to #215:

| Method | New resource |
| --- | --- |
| GET, POST | `/v1/admin/portfolio/work-scopes` |
| GET | `/v1/admin/portfolio/work-scopes/{work_scope_id}` |
| POST | `/v1/admin/portfolio/work-scopes/{work_scope_id}/versions` |
| GET, POST | `/v1/admin/portfolio/work-bindings` |

Use the accepted [wire bundle](portfolio-intelligence-wire-v1.json), not new
ad-hoc envelopes. It freezes closed fields, bounded admin metadata, typed
query filters, role/tenant authorization, fixed safe errors, idempotency,
and frozen-window pagination (default 50, maximum 100). Tenant and role
authority must be resolved server-side before domain access. Registry roles
do not grant inference, policy-root, or cross-tenant authority.

## Storage and implementation boundary

Use #213's explicit portfolio repository factory beside the unchanged usage
repository. Share configured connection facilities only under their existing
ownership rules; the seam does not imply cross-repository atomicity or
transfer pool lifetime. Keep portfolio SQL out of the usage protocol and
usage adapter modules. A broad storage rewrite is not a prerequisite.

The feature migration must be additive: append-only work-scope versions and
external-work-binding events, tenant-qualified stable IDs, immutable parent/
owner/lifecycle versions, and safe audit IDs/change classes. Archive or
tombstone never deletes linked evidence. Any materialized current pointers
or indexes must be derived transactionally from those authoritative records.
The feature PR must specify their exact DDL, grants, forced RLS, uniqueness,
foreign keys, and concurrent version/idempotency rules before acceptance.

Do not rewrite, backfill, reinterpret, or renumber existing usage, secret,
request-attempt, reservation, audit, policy, or custody records. Do not weaken
existing RLS, append-only/retention triggers, role separation, or privileges.
No automatic provider replay, attribution, outcome import, or release of an
uncertain reservation is a migration action. Existing uncertainty survives
upgrade and recovery. Update the durable-data inventory only when real
portfolio tables exist, not for the test probe.

## Operator sequence and rollback decision

Both registry migrations are bundled. This remains the bounded registry
operator sequence, not final v1.1.0 candidate/production deployment acceptance.

1. Identify the exact old application artifact, database schema and backup,
   configurations, role bindings, and evidence/checkpoint references. Confirm
   sufficient space for separate old and candidate recovery copies.
2. Disable ingress and scheduled writers; drain bounded in-flight requests
   without replay. Resolve or retain ambiguous attempts and their holds.
   Stop **all** gateway, policy, custody, and other writer processes and pools
   before migration. Use one authorized operator, not concurrent migrators.
3. Take and verify a consistent backup of the quiesced v1 state. For SQLite,
   use the backup API or the documented consistent backup procedure, not a
   blind copy of a live WAL database. PostgreSQL recovery must preserve the
   schema, data, roles/grants, functions, triggers, and RLS; logical and physical
   recovery profiles have distinct evidence requirements.
4. Apply the feature migration in its owning transaction. On failure, retain
   diagnostics and state, verify rollback, and retry only the migration after
   addressing its cause. Never manually stamp a partial migration as applied.
5. Start fresh candidate processes and verify schema, tenant isolation,
   audit/evidence integrity, and applicable contracts before reopening writes.
   Keep the verified v1 backup and the candidate state separately retained.

**Readiness is not a continuous PostgreSQL schema-version monitor.** The
existing runtime verifies the schema at construction; `verify_ready()` checks
tenant access and privileges. A previously started instance can still report
ready after an out-of-band schema-marker change. The preflight characterizes
this behavior explicitly. Never use that signal to justify mixed old/new
instances, rolling migration, or skipping the stop/restart boundary.

| Observed state | Required action |
| --- | --- |
| No accepted writes since the verified, quiesced checkpoint; candidate snapshot retained | Restore the verified **old application + old database pair** into a new isolated destination, verify it, then explicitly cut over before resuming writes. |
| Any accepted writes after that checkpoint, including v1 usage or new registry metadata | Preserve candidate state and recover forward; an old backup would discard accepted writes. |
| Write count, backup verification, quiescence, or candidate retention is unknown/unverified | Refuse old-pair restore. Retain state and establish a verified forward recovery plan. |
| New schema presented to a fresh v1 process | Fail closed with `storage_schema_newer_than_binary`; do not lower the ledger number. |
| Partial migration marker presented to a migrator or fresh process | Fail closed with `storage_schema_partial_upgrade`; no automatic repair or provider replay. |

There is no in-place downgrade. A future nonzero-data-loss rollback option
would require a separate explicit decision and evidence; this plan does not
authorize it. `rollback_disposition()` is only an offline decision table for
supplied facts, not a backup verifier, write counter, or restore executor.

## Executable implementation checks and evidence limits

Run the versioned plan and source-level checks:

```bash
python3 tools/verify_registry_transition_plan.py
python3 -m unittest -v tests.test_registry_transition_plan tests.test_sqlite_registry_transition
python3 -m unittest -v tests.test_sqlite_portfolio_registry tests.test_portfolio_api_cli
```

To run actual released-binary checks, download the two exact assets above
and verify **before installing**:

```bash
python3 tools/verify_registry_transition_plan.py \
  --baseline-archive /absolute/temporary/path/hormuz-1.0.0.tar.gz \
  --baseline-manifest /absolute/temporary/path/hormuz-v1.0.0-candidate-manifest.json
python3 -m venv /absolute/new/temporary/path/v1-baseline
/absolute/new/temporary/path/v1-baseline/bin/python -m pip install \
  '/absolute/temporary/path/hormuz-1.0.0.tar.gz[postgres]'
```

Set `HORMUZ_TEST_V1_PYTHON` to that environment's Python. For PostgreSQL, set
`HORMUZ_TEST_POSTGRES_DSN` to an operator account on a **disposable test server**,
and `HORMUZ_TEST_PG_CONTAINER` to that server's container. The restore test
uses the container's matched `pg_dump`/`pg_restore`, creates a uniquely named
test database, and removes only that test database and owned fixture schemas/
roles. Never point these tests at customer or production databases.

```bash
python3 -m unittest -v tests.test_sqlite_registry_transition \
  tests.test_postgres_registry_transition tests.test_postgres_test_boundaries
```

| Case | Evidence provided by the implementation tests |
| --- | --- |
| Released baseline identity | Exact source and manifest digests; isolated installed-source provenance; legacy manifest equality. |
| v1 rows and holds preserved | All 10 SQLite / 32 PostgreSQL table snapshots compared; seeded usage, secret, attempt, reservation, and audit evidence preserved. Policy/custody tables are empty in these fixtures, not populated-domain recovery proof. |
| Actual registry migration | Real SQLite 4-to-5 / PostgreSQL 8-to-9 DDL creates five new tables, preserves the v1 snapshot, and is idempotent. |
| Transaction failure and retry | Failure injected after the actual registry DDL rolls back tables and ledger; retry applies it once. Registry mutations additionally prove atomic record/audit/idempotency rollback. |
| Partial and newer state | Fresh current/released binaries and migrators refuse the relevant markers without mutation; startup-only PostgreSQL checking is characterized. |
| Quiesced verified pair restore | Consistent SQLite backup and PostgreSQL logical dump restore into fresh test destinations; released v1 opens restored state with the unknown hold intact. The candidate remains available. |
| Writes require forward recovery | Populated registry versions, bindings, audit, idempotency and cursors plus post-upgrade v1 writes/unknown holds survive retry and current-candidate backup/restore into a fresh destination. Replays return the exact original results without new writes; a saved cursor continues. Original candidate state remains intact. The decision table rejects old-backup restore after writes or uncertainty. No automatic recovery executor is claimed. |
| Legacy and registry wire contracts | Existing contract validator, digest bindings, six-route scope, negative plan tests, CLI/HTTP operation tests, and installed schema parity. |

The existing required PostgreSQL CI job runs both backends' transition tests
against an isolated current wheel and an independently installed released
source baseline. Its baseline download/digest/install failures fail the job;
they must not turn into skips. Local tests skip release-dependent checks when
the explicit baseline environment is absent; skips are not evidence. The
package gate requires the guide, plan, verifier, and tests in the source
archive. CI and artifact identities belong in the issue/PR review record.

## Required next evidence and gate acceptance

Accept #215 only with the reviewed PR head, exact merged-main SHA, green
required jobs, baseline digests, actual migration/rollback tests and registry
authorization/RLS/concurrency/package evidence recorded under #214 and #215.
The old missing-migration guards are replaced, not skipped: version 2 requires
the actual DDL and its failure/retry proof. Other features still need their own
#214 checkpoint. Source implementation and a verified plan are not a release.

Final closure of #214 additionally requires real final-candidate migration
and recovery with populated usage/policy/custody/registry history; full
API/config/policy/CLI/evidence comparisons; exact source/wheel/signed-OCI/
Compose artifact bindings; verified backups/role recovery and post-write
forward recovery; no duplicate attribution/outcomes/provider work; and
exact-main CI. This preflight is not final-candidate, HA, zero-downtime,
universal orchestrator, disaster-recovery certification, external-onboarding,
or customer-value evidence. It does not authorize tagging or publishing.
