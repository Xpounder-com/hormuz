# v1.1.0 governed-run attribution preflight

This is the **pre-implementation checkpoint for #216**, not attribution
implementation, #214 final-candidate acceptance, or a release. The prerequisite
#215 registry must first have exact merged-main acceptance. This plan is based
on reviewed registry implementation commit
`b8cec8faba8d8e48d515dfcc3ec8eeaa78fc7926` in PR #234; its gate record must name
the resulting main commit and passing required CI before accepting this plan.

[attribution-transition-plan-v1.json](attribution-transition-plan-v1.json) freezes
the bounded change and required tests. The accepted ADR 0010, portfolio wire
bundle, immutable released-v1 source identity, and legacy manifest remain
unchanged. [REGISTRY_TRANSITION.md](REGISTRY_TRANSITION.md) retains the exact
v1.0.0 baseline archive/manifest digests and stop/migrate/restart procedure.

## Scope and version boundaries

| Boundary | Registry baseline | Attribution target |
| --- | --- | --- |
| SQLite migration ledger | 5 | 6 |
| PostgreSQL migration ledger | 9 | 10 |
| Product release target | v1.1.0 development | v1.1.0 development |
| New attribution envelopes | Not implemented | Approved schema version 1 |

The immutable released-v1 baseline is still SQLite 4 / PostgreSQL 8. The final
candidate must prove that complete transition, not merely this intermediate
step. No existing migration number or applied DDL may be edited/reused.

Only `GET /v1/admin/portfolio/attributions` and
`POST /v1/admin/portfolio/attributions`, their CLI equivalents, and optional
governed-request attribution admission belong to #216. Budget plans, source
connector ingestion, work-outcome associations and scorecards remain later
gates. A work assignment is not an outcome or causal-productivity claim.

## Optional request admission and native compatibility

The planned strict request header is:

```text
X-Hormuz-Work-Scope: v1;work_scope_id=<opaque_id>;version=<canonical_positive_integer>
```

It permits one occurrence, ASCII only, at most 192 bytes, an approved opaque ID
of at most 128 characters and a canonical positive version no greater than
2147483647. No extra parameters, free text, caller tenant/actor/team, connector
ownership or unversioned fallback grammar is accepted. Duplicate, malformed,
unsupported, unauthorized and stale references fail before reservation/egress.

Authenticated explicit metadata takes precedence over an operator-authorized
identity/client default. Otherwise the attempt is explicitly unattributed.
Only a separately explicit active attribution requirement may deny absence;
no legacy policy/configuration silently acquires that requirement. The new
operator configuration must bind existing identities and clients to exact
tenant-local use-case versions, be strict/versioned/bounded, default off, and
require the existing operator-controlled process restart. Portfolio roles
never grant inference eligibility. Parent ownership or possession of an ID
alone is not an admission permission.

Resolve authority from the authenticated identity and server configuration
before registry lookup, policy/budget work or provider egress. Recheck the
captured scope/version when committing admission so a concurrent material
scope change cannot silently retarget the request. Manual post-run corrections
require the separately authorized portfolio administrator capability, not
request-header authority.

Opted-in admissions use this new versioned result header, with only the fixed
status/reason vocabulary in the plan:

```text
X-Hormuz-Work-Scope-Result: v1;status=<fixed_status>;reason=<fixed_reason>
```

Existing OpenAI/Anthropic request/response bodies, existing v1 envelopes,
authentication and non-opted-in error behavior remain unchanged. New admission
failures use the provider-native error body shape plus the versioned result
header; never an administrator JSON envelope injected into a provider body.
Do not echo submitted IDs/header values. Strip attribution headers before
upstream delivery; test both OpenAI and Anthropic paths and compact requests.
Non-accounted operations such as token counting do not invent a governed
attempt; explicit attribution there is unsupported and visible as such.

## Immutable facts and coverage

The immutable v1 attempt root already captures event-time organization,
actor/team, client/protocol, policy version and requested/resolved/upstream
model. Its immutable terminal event links to `usage_event_id`; that usage event
owns provider-reported actual model and cost facts. Join through those stable
IDs and preserve absence. An alias is not proof of an actual model; a computed
estimate is not provider-final cost. Do not copy mutable current identity,
policy, price or work-scope state over historical facts.

There is at most one active primary use-case attribution per attempt. Events
are append-only, tenant-qualified and exact-version-bound. Corrections/voids
require expected prior event, authenticated actor, fixed reason and timestamp;
concurrent conflicting corrections fail. Idempotent replay returns the exact
immutable result without another mutation.

Missing or ambiguous admitted attribution is explicit non-primary state with
the corresponding confidence/reason. Authenticated invalid/unauthorized/stale/
unsupported admission is a fixed-class rejection receipt, not a fabricated
v1 request attempt. These rejections remain visible separately from eligible
governed-attempt denominators. Missing attribution rows after a crash, missing
actual model, unsupported dimensions and unknown provider outcomes must remain
visible in later coverage joins rather than being guessed or dropped. No
prompt, response, filename, ticket title/body, patch, source code or employee
behavior is an attribution source.

## Transaction and recovery boundary

Keep attribution-owned SQL/tables separate from v1 usage/attempt/cost/audit
SQL. #213's composition does not promise a transaction spanning repositories.
Preserve v1's atomic attempt-root/initial-event/reservation transaction. If
attribution commits after that transaction, provider egress must wait for its
successful commit. A crash or failed write cannot authorize egress/replay or
claim combined atomicity. A known pre-egress failure may be finalized through
the existing v1 lifecycle only when safely proven; otherwise retain the hold
for explicit reconciliation. Never free uncertain reservations speculatively.

Use this admission seam for later #217 integration, but do not claim portfolio
budget atomicity here. Privileged reads commit safe audit before delivery;
pagination, role changes, CAS/idempotency, outages and cross-tenant attempts
need explicit negative tests. Preserve the existing registry's IDs, versions,
bindings, audit, idempotency references, cursor state and permissions.

Stop all writers/pools, serialize migration and restart fresh processes.
Old processes refuse next/newer or partial schema; existing PostgreSQL readiness
is not a continuous version monitor. Restore a verified application/database
pair only with zero accepted post-checkpoint writes, into a fresh isolated
destination with candidate state retained. For nonzero or unknown writes,
retain the candidate and recover forward. Never decrement a ledger version,
drop accepted metadata to make an old binary start, or replay provider work.

## Executable preflight and limits

```bash
python3 tools/verify_attribution_transition_plan.py
python3 -m unittest -v tests.test_attribution_transition_plan
python3 -m unittest -v tests.test_sqlite_attribution_transition \
  tests.test_postgres_attribution_transition
```

Use the same explicitly disposable `HORMUZ_TEST_POSTGRES_DSN`, matched
`HORMUZ_TEST_PG_CONTAINER`, and digest-verified `HORMUZ_TEST_V1_PYTHON` as the
registry transition guide. Missing environments cause explicit local skips,
never proof. CI must provide them and run the cases from the isolated wheel.

The six cases on each backend seed real v1 usage/secret/attempt/unknown-hold
state plus all five populated registry tables. They deliberately prove that
the next migration is **missing**, then use unmistakably test-only probe DDL
to test rollback/retry, prior-state preservation, partial/newer-state refusal,
quiesced registry-pair restore and retained post-checkpoint writes. Exact
registry idempotent results and cursor continuation survive an isolated
old-pair restore. There is no
attribution table, public endpoint or inference integration in this preflight.
Empty policy/custody tables are not populated-domain recovery evidence.

The feature implementation must replace the missing-migration/probe assertions
with actual additive migrations and preserve every transition guarantee. It
must add the full admission, two-provider no-egress, scope race, strict native
header/body, stable-fact join, correction/CAS, RLS, content-exclusion and actual
populated forward-restore tests listed in the machine plan. Source/wheel proof
is not final signed-OCI/Compose proof and not external customer validation.

## Acceptance record

Accept only after #215 has exact merged-main evidence, this plan and red-first
tests have a recorded technical-lead review, all normal required checks pass,
and this preflight's exact merged-main CI passes. Record those links in #214,
#216 and #226. This accepts only #216's pre-implementation checkpoint; keep
#216's feature criteria and #214's final-candidate criteria open. No release,
tag, deployment, external outreach or customer-data collection is authorized.
