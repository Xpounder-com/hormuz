# Work-outcome foundation (v1.1.0 development)

Issue #218 adds source-neutral, metadata-only outcome storage and administrator
reads. This is source implementation, not a released connector, associated or
controlled evidence, final-candidate acceptance, or a live customer pilot.
GitHub (#219), Linear (#220), association (#221), and evaluation (#222) remain
separate gates. A source observation is not proof of AI productivity or a basis
for employee ranking.

## Read outcomes

With the existing operator-configured portfolio administrator identity:

Set the existing bearer credential through `HORMUZ_PORTFOLIO_TOKEN` (or the
existing `--token-env` option), never a literal secret on the command line.

```bash
hormuz --config /path/to/config.json portfolio outcomes --limit 50
```

The additive HTTP equivalent is `GET /v1/admin/portfolio/outcomes`. Existing
portfolio authentication and safe errors apply. There is no outcome POST route
or generic JSON write API. Finance, platform, team, and self roles do not gain
raw-outcome access. The repository rechecks current administrator authority
before opening storage, and commits the read audit before returning results.

The page defaults to 50 items, at most 100. Optional filters are `connector_id`,
`work_scope_id`, `start_at` (inclusive), and `end_at` (exclusive). Times are UTC.
Results sort by event time, connector ID, then source event ID, all descending.
A cursor freezes the sequence snapshot, filters and as-of time; a continuation
may change the limit but not the filters. It expires after one hour and is
bound to tenant, actor, roles and connector configuration. Retention changes
invalidate older cursors. A malformed or inconsistent stored cursor fails
closed without returning a partial page or committing its read audit.

The installed event/page/receipt catalogue is an exact dependency-closed subset
of the [approved wire bundle](portfolio-intelligence-wire-v1.json). The public
event has no new work-scope or provider field. Separate versioned internal
contracts hold the source observation, immutable binding context, coverage and
administrator retention marker. This preserves the approved public shapes.

## Connector-author boundary

`OutcomeIngestor` requires an explicitly injected verifier, normalizer, repository
and versioned provenance keys. It activates no transport. Every delivery must
be authenticated against the server-registered connector before JSON parsing,
normalization or repository access. `AuthenticatedDelivery` is metadata, not a
credential. The test verifier is explicitly synthetic, not GitHub/Linear proof.

The verifier must authenticate exact bytes and configured source ownership.
A shared application signature alone is insufficient tenant authorization;
copying an expected installation/workspace from configuration does not prove
that the delivery belongs there. The later provider adapter must establish the
signed parent context and reject spoofed installation/workspace/container
claims. Body-supplied tenant, actor or work-scope fields never grant authority.
The repository revalidates the exact registered connector at commit.

Only allowlisted metadata crosses the storage boundary. Supported object
classes are issues and pull requests; lifecycle classes are created, started,
completed, reopened, accepted, reverted, defect_reported, canceled, deleted,
and unsupported. Quality is accepted, rejected, reverted, defect, unknown, or
not_applicable. These describe source facts, not performance scores. Each real
adapter must document which source events it can actually support.

Object/container IDs are provider-native numeric IDs (GitHub) or UUIDs (Linear).
Delivery/event IDs use the closed numeric/UUID/hex identity grammar, with a
bounded optional event ordinal. Never encode titles, URLs, filenames, content,
credentials or personal behavior into an ID. No payload body, prompt, response,
ticket text, patch, code, exception text or signing secret is persisted.

The normalizer emits at most 100 closed observations. Unknown fields, invalid
types, unsupported source scope, duplicate identities and invalid correction
references fail closed. Authenticated normalization and repository-domain
rejections append fixed failure metadata, not a success receipt. An unavailable
database cannot provide durable failure evidence and must not be acknowledged
as accepted. There are zero automatic retries or provider-work replays.

## Facts, ordering and coverage

An accepted transaction atomically appends the original receipt, metadata,
normalized facts, immutable source/binding context, coverage and safe audit.
Exact authenticated redelivery returns the original receipt without reparsing
or appending writes, including after a normalizer upgrade or supported key
rotation. Reusing a delivery identity for different bytes or changed connector
authority fails closed. Reusing a source event identity in another delivery
cannot overwrite the event. Corrections require a new identity and an existing
same-tenant/connector/object predecessor; cycles and competing corrections are
rejected. Failed delivery identities also bind their original bytes. The same
bytes may recover after a normalizer repair without erasing failure history.

Only verified revision counters or source-updated timestamps in the same
explicit ordering domain are comparable. UUIDs, hashes, absent revisions,
equal-conflicting and incomparable revisions remain uncertain; late revisions
remain late. Neither can erase a later authoritative fact. Unsupported events
never become authoritative. Source object type is part of current-state lookup.

Event, observed and ingestion times remain distinct. Unknown source time is
explicitly null in the internal observation; its public event uses observed
time, with missing-evidence status for an ordinary observation (correction and
deletion reasons remain intact). The internal context records that source time
is unknown; this fallback is not an actual source timestamp. Registry
binding is captured as of known source time, including the exact use-case
version. Pre-enrollment observations, future times, missing bindings and stale
versions are unmatched/excluded. Registry changes cannot retarget prior facts.

Coverage is an append-only event log, not a blindly summable denominator.
Every member has an explicit unit: source event or delivery. Failed/unsupported
deliveries without normalized events remain delivery-level coverage; recovery
and retention preserve earlier coverage. Group by the stable member identity
and a predeclared versioned rule before computing populations. The internal
100-row diagnostic window is not a complete-population metric API. Eligibility
remains inconclusive, with no rule ID/version, until the association/evaluation
gates establish their contracts. Unsupported, unmatched, ambiguous, late,
excluded, superseded, missing and failed evidence must not silently disappear.

## Bounds, keys and incidents

Bounds are 1 MiB per request, JSON depth 16, 4,096 members, 100 events/delivery,
eight concurrent ingestions per process, a ten-second absolute streaming-read
deadline, five-second database statements, and 2,048 bytes of dead-letter
metadata. The transport must enforce header limits, reject duplicate lengths
and unsupported transfer encodings, and restore socket timeouts. The streaming
helper does not activate an HTTP ingress or replace those transport checks.

Provenance keys are injected, copied, versioned, 32–128 bytes, and hidden from
representations. Tenant/connector/domain-separated HMACs distinguish exact-byte
replay fingerprints from metadata provenance. Retain old versions needed for
replay/verification; a missing old key fails closed. Source signing credential
rotation is separate, and only its non-secret version is recorded. No new
environment variable, secret store, live source credential or key export is
introduced by this foundation.

For an incident, disable the configured connector/transport first; preserve
content-free receipts and evidence. Owner-controlled signing/key rotation,
source-binding review and coverage reconciliation precede re-enablement. Never
put payloads, credentials or free-form source exception messages in issues or
logs. Real adapters must supply provider-specific rotation and incident steps.

## Retention and recovery

An explicit internal administrator action can append a separate retention
tombstone for one tenant-owned observation, including after connector removal.
It records actor, original identity, fixed reason and server times, with keyed
idempotency. It is not a fabricated provider event or connector receipt, and
there is no new public retention mutation or automatic schedule. New pages
omit that observation; old cursors become invalid. Removing the current fact
does not resurrect an older fact as current. Source/audit/financial facts and
their links remain immutable. This is logical application retention, not
universal erasure from customer databases, backups or exports.

SQLite migration 7 and PostgreSQL migration 11 add nine outcome-owned tables.
Stop writers and pools, migrate once, then restart fresh processes. Partial or
newer schemas fail closed; old binaries cannot downgrade them. A verified
zero-post-checkpoint-write rollback restores the verified old application and
database pair into a separate destination. Nonzero or unknown writes require
retained candidate state and forward recovery. Never release uncertain holds,
rewrite migration history, or replay provider work to force recovery.

See [OUTCOME_TRANSITION.md](OUTCOME_TRANSITION.md) for real predecessor and
populated restore tests, [DURABLE_DATA.md](DURABLE_DATA.md) for storage ownership,
and [REGISTRY.md](REGISTRY.md) for existing configuration.
These tests do not substitute for #214 final source/wheel/signed-OCI/Compose
candidate evidence or #225 independent real-organization validation.
