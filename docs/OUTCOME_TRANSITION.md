# v1.1.0 work-outcome pre-implementation checkpoint

This is the #214 compatibility checkpoint for #218, not outcome implementation,
feature acceptance, final-candidate proof, or a release. Its frozen contract is
[outcome-transition-plan-v1.json](outcome-transition-plan-v1.json). The exact
accepted predecessor is attribution PR #237: reviewed source
`ade456b90a3f065ebee5b51893dbf111e815ff05`, merged main
`ec7c5d89149b8e172a9c8da76b1e0a259bdf24bc`, exact-main CI `33342815449`.

## Affected operators and unchanged interfaces

| Boundary | Accepted attribution | Outcome target |
| --- | --- | --- |
| SQLite migration ledger | 6 | 7 |
| PostgreSQL migration ledger | 10 | 11 |
| Existing v1, registry and attribution behavior | Implemented | Unchanged |
| Public outcome event/page/receipt | Frozen planned schema v1 | Implement the same shapes |

No production migration, endpoint, CLI command, connector, new configuration,
secret, schema registration, or runtime behavior is introduced by this
checkpoint. Existing operators need no edits for the preflight alone. Later
outcome implementation must be additive and opt-in; never change applied DDL,
the immutable v1 manifest, historical transition plans, or the approved wire
bundle. Changing target migration numbers requires a reviewed superseding plan,
not silent reuse of this checkpoint after another feature takes the numbers.

The immutable released-v1 baseline remains SQLite 4 / PostgreSQL 8. This
intermediate checkpoint cannot replace the complete final-candidate transition.
See [REGISTRY_TRANSITION.md](REGISTRY_TRANSITION.md) and
[ATTRIBUTION_TRANSITION.md](ATTRIBUTION_TRANSITION.md) for their exact evidence.

## Source-neutral implementation boundary

The only planned public read in #218 is `GET /v1/admin/portfolio/outcomes`, plus
an additive local CLI equivalent. Use the existing authenticated administrator
dispatch, closed outcome page, frozen cursor rules, fixed errors and audit
commit before delivery. Until #223 accepts aggregate role views, raw records
require `portfolio_admin`; finance, platform, team and self scopes gain no new
raw-outcome or peer-employee access by inference.

There is no generic JSON outcome-write endpoint. #218 supplies the internal
source-neutral verified-delivery boundary and append-only repository. #219 and
#220 separately own GitHub/Linear signature verification, source normalization,
live installation/workspace authority, ingress route activation and integration
proof. A fixture verifier is never proof of a working source connector.

Use the existing operator-registered connector binding as server authority.
Authenticate the connector and exact signed delivery before JSON parsing,
normalization, repository access or external lookup. A payload organization,
installation, workspace, repository/project, actor or work-scope claim cannot
grant authority. Revalidate exact permitted source-container scope at commit;
an ID's syntax or a provider signature alone is not tenant authorization.

The frozen public `hormuz.work-outcome-event` does not contain a work-scope
field. Preserve that envelope. Separate versioned immutable internal provenance
must capture server-bound source/connector/container, registry binding event,
exact use-case version or explicit missing/ambiguous binding, authenticated
correction authority, and key version. A registry change must not retarget old
observations. Source observations are not run-to-outcome associations: #221
owns those joins, including unmatched and ambiguous denominators.

Distinguish these layers explicitly:

- Raw source **observation metadata**, never a retained raw payload or work text.
- The closed normalized v1 event and original durable delivery receipt.
- Immutable binding/provenance and coverage state, separate from public facts.
- Derived eligibility, supersession and tombstone interpretation. Missing a
  versioned predeclared eligibility rule means inconclusive, not a winner.

## Ordering, idempotency and failure invariants

Receipt, normalized observations, context, coverage and safe audit must commit
atomically. Exact authenticated delivery redelivery returns its original
receipt and result without writes; one delivery identity with conflicting bytes
fails closed. Compare tenant-keyed verified-byte fingerprints without retaining
raw payloads. Metadata provenance is separately tenant-keyed and versioned;
credential or key values never appear in rows, receipts, logs or errors.

Source revisions are opaque unless the adapter establishes a verified ordering
domain. Compare only revisions in the same explicit domain; never sort UUIDs,
hashes, display strings or missing revisions into an invented causal sequence.
Retain late, equal-conflicting and incomparable observations with uncertainty;
they cannot erase a later authoritative state. Event, observed and ingestion
times stay distinct, with server ingestion authoritative for receipt/retention.
Corrections append their prior identity, authorized actor/connector, fixed
reason and timestamp; no row rewriting or historical backfill.

The planned hard limits are 1 MiB/request, JSON depth 16 and 4,096 members,
100 events/delivery, eight ingestion operations/process, ten-second request
reads, five-second database statements, zero internal automatic retries, and
2,048 bytes/dead-letter metadata. Provider adapters may be stricter. Bound
transport reads before parsing and database work before acknowledgement. A
retry after timeout requires the original authenticated delivery identity;
receipt uncertainty never permits provider-work replay. Dead-letter records
contain fixed failure metadata only, not payload fragments or exception text.

Connector output is always descriptive. Associated evidence requires #221;
controlled evidence needs a separately approved predeclared design. Preserve
unsupported, unmatched, ambiguous, excluded, late, superseded, failed and
missing observations in their declared coverage. No causal-productivity,
employee-ranking, final-invoice or universal-erasure claim is introduced.

## Migration, rollback and recovery

Stop all writers and pools, serialize migration, then start fresh processes.
Existing PostgreSQL readiness is not a continuous schema-version monitor.
Readers and writers must refuse partial or newer schemas without attempting
repair. A failed new migration must roll back its DDL and ledger change while
preserving every v1, registry and attribution row, ACL/RLS boundary and replay
reference; a retry must be idempotent.

Only a verified zero-post-checkpoint-write rollback may restore the verified
old application/database pair, into a separate destination. Retain the
candidate snapshot. Nonzero or unknown accepted writes require preserved
candidate state and forward recovery. Never decrement migration ledgers, drop
new facts to start an old binary, release uncertain reservations, or replay
provider work. Tombstones do not erase linked financial/audit facts. Customer
operators remain responsible for database, backup, export and retention policy.

## Executable preflight and its limits

Build the exact accepted attribution source snapshot, not an archive of the
current working tree:

```bash
git -c tar.umask=0000 archive --format=tar --prefix=hormuz-attribution-baseline/ \
  --output=/path/to/attribution-baseline-ade456b.tar \
  ade456b90a3f065ebee5b51893dbf111e815ff05
```

Required SHA-256:
`c838abe03ff4ceba5145da37cfafe1c4b81a2dc64a88ed7c9a503f5fda742d72`.
Install that archive with its `[postgres]` extra in a separate virtual
environment and retain the archive at its original path. The fixture verifies
the installed distribution, isolated module location, direct-URL archive hash,
schema 6/10, and all 99 installed runtime source/data files byte-for-byte.
Missing, extra or changed runtime files fail verification. It loads fixtures
from the same in-memory archive bytes whose digest was checked, without
reopening the archive path. This is a Git checkpoint, not a published release.

Set `HORMUZ_TEST_ATTRIBUTION_PYTHON` to that interpreter. Supply the immutable
released-v1 `HORMUZ_TEST_V1_PYTHON` and the explicitly disposable matching
`HORMUZ_TEST_POSTGRES_DSN`/`HORMUZ_TEST_PG_CONTAINER` from the registry guide.
For the older attribution transition suite, also supply its verified
`HORMUZ_TEST_REGISTRY_PYTHON`. Missing test environments cause explicit local
skips and cannot count as proof. CI provides all three actual predecessors.

```bash
python tools/verify_outcome_transition_plan.py \
  --baseline-archive /path/to/hormuz-1.0.0.tar.gz \
  --baseline-manifest /path/to/hormuz-v1.0.0-candidate-manifest.json \
  --attribution-archive /path/to/attribution-baseline-ade456b.tar
python -m unittest -v tests.test_outcome_transition_plan
python -m unittest -v tests.test_sqlite_outcome_transition \
  tests.test_postgres_outcome_transition
```

Six cases per adapter prove actual missing-next-migration refusal, test-only
DDL failure/rollback/retry, unchanged populated predecessor rows, partial/newer
refusal by the actual attribution and immutable released-v1 binaries, isolated
quiesced old-pair restore, and retained post-checkpoint writes for forward
recovery. Both registry and attribution idempotency results and cursor
continuations survive old-pair restoration; immutable actual-model facts and
uncertain holds survive as well. PostgreSQL uses its matching dump/restore
tools and uniquely owned synthetic schemas/databases.

The test-only probe table is not an outcome repository or migration. Empty
policy/custody tables are not populated-domain recovery proof. Implementation
must replace these probes with real migrations, populate every outcome-owned
table, and pass every security, parser, ordering, retention, outage, content
exclusion and adapter-parity case in the frozen plan before feature merge.
It must also publish connector-author guidance for supported classes, coverage
gaps, deletion, key/secret rotation, disablement and incident response.

Accept this checkpoint only after technical-lead review, normal required PR
checks, protected merge and exact merged-main CI, then record the exact commits
and evidence in #214/#218/#226. This unblocks only #218 implementation. #214
remains open for the complete source/wheel/signed-OCI/Compose candidate, and
#225 requires real external evidence. No release, tag, customer deployment,
external data collection or outreach is authorized by this checkpoint.
