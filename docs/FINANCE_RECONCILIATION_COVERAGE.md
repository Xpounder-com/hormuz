# Offline finance coverage preview for #8

`hormuz.finance_reconciliation_coverage` builds a provider-free preview over an
`AsOfCollectionView` and immutable finance-attempt sidecar events. The pure
function still requires its caller to authorize and select both inputs. The
`hormuz finance report` CLI supplies a source-oriented preview, while
`hormuz finance reconcile` adds an exact immutable account-binding filter and
bounded variance calculation. Both commands are administrator-only durable
reads. Neither authenticates account ownership, certifies an invoice, or
performs live-finance verification.

For a selected cost profile, run:

```console
hormuz --config hormuz.json finance report SOURCE_BINDING_ID VERSION \
  openai.organization-costs.v1 2026-09-01T00:00:00Z 2026-09-03T00:00:00Z \
  --currency USD --as-of-commit-sequence 42
```

For the account-bound comparison, run:

```console
hormuz --config hormuz.json finance reconcile ACCOUNT_BINDING_ID VERSION \
  openai.organization-costs.v1 2026-09-01T00:00:00Z 2026-09-03T00:00:00Z \
  --currency USD --as-of-commit-sequence 42
```

The reconciliation command verifies the selected account registration, linked
source binding, every present attempt-account sidecar, and each matching audit
source inside the same tenant transaction. Only attempts with the exact
account-binding ID, version, digest, and source coordinates enter the gateway
subtotal. Attempts for another verified account are counted and excluded.
Unbound attempts, historical attempts with no account sidecar, a different
binding at the same account grain, a missing matched finance sidecar, or an
attempt lifetime crossing the selected UTC boundary keep variance unavailable.
A pending attempt whose account is exact, unbound, historical, or at the same
account grain is also an unresolved gap. Pending attempts for another verified
account are counted and excluded. The response exposes each count instead of
assigning an unknown attempt.

Variance is calculated only when every selected provider bucket is observed,
all exact-bound gateway attempts have same-currency configured estimates, and
no unknown account or period gap remains. It uses the original immutable rate
card estimate and defines signed variance as provider aggregate minus gateway
estimate. Relative variance is an exact numerator/denominator pair, with a null
value for a zero gateway denominator. The response labels account matching as
`operator_attested_unverified`, period matching as a fully contained attempt
lifetime in the selected UTC window, bypass as unknown, and provider/invoice
finality as false.

Omit `--as-of-commit-sequence` to pin the current tenant publication high-water
mark in the response. The command authenticates the existing portfolio token
from `HORMUZ_PORTFOLIO_TOKEN` or `--token-env`, then requires the exact current
`portfolio_admin` binding for that token's tenant. It neither accepts a tenant
argument nor reads a provider credential or fingerprint key. `finance_viewer`
and other roles receive no report access in this slice. The selected daily
window is at most 31 days, and more than 10,000 combined terminal and pending
attempts or selected cost rows fails closed. The repository reads the provider
selection, terminal gateway attempt sidecars, and unresolved attempt roots under
one tenant transaction. It checks each present sidecar's canonical stored event
against its audit source and exposes terminal attempts with no finance sidecar
and relevant pending attempts as separate coverage gaps. Each selected snapshot
also retains its stored `evidence_origin` and `scope_provenance` in
`selected_snapshot_provenance`: customer file imports remain distinct from
authenticated API collections, and both remain scope-unverified.

The JSON response labels the `preview` as `offline_unverified_coverage_preview`.
Its selected snapshot cutoff applies to provider collections; it is not a
historical cutoff for gateway attempts, which are read at report time. Re-run
can therefore include later terminal attempts even with the same collection
cutoff. The response includes only metadata identities, digests, counts, and
numeric subtotals. It does not emit native usage payload JSON, credentials,
provider account identifiers, prompt or response content, or raw provider
line items.

Before returning a successful result, the repository builds the bounded preview
and appends a strict `hormuz.finance-query-audit-event` plus its v2 audit-chain
entry in the same tenant transaction. The event contains only the actor, fixed
query class, source-binding coordinates, requested window and currency,
collection cutoff, result counts, and occurrence time. The response returns the
committed `query_audit_event_id` as a receipt. An audit-source mismatch, insert
failure, authorization change, or chain failure rolls the transaction back and
the CLI emits no report. The append-only table has forced PostgreSQL RLS and the
runtime role has only `SELECT` and `INSERT`; SQLite and PostgreSQL both require
the exact canonical source row before the chain entry can commit.

This first account-bound slice reuses that version-1 source-query audit event.
It commits the selected source coordinates and base result counts, but it does
not yet persist a dedicated reconciliation-detail row or response digest.
Durable replay of the exact account-match breakdown therefore remains a later
schema and report-custody requirement.

This qualifies the `finance report` read only. Other finance, platform, team,
pagination, export, and API reads required by issue
[#223](https://github.com/Xpounder-com/hormuz/issues/223) still need their own
bounded query contracts and commit-before-delivery audit proof.

The preview keeps the selected provider cost aggregate and the original gateway
configured-rate estimate in different fields with different cost-basis labels.
It pins the collection cutoff, selected snapshot IDs/digests, observation keys,
attempt evidence IDs, and original rate-card identities. `finance report` keeps
signed variance absent because it does not consult account bindings. The
reconciliation path supplies the durable account and source checks required by
the existing pure variance reference, while retaining the operator-attestation
and UTC-period limitations in its response.

The builder accepts at most 31 canonical daily buckets and 10,000 observations
or attempts. It checks that coverage and rows belong to the selected snapshot,
that counts match, and that duplicate observations or attempts cannot be summed.
It uses exact decimal arithmetic with canonical bounded amounts. An explicit
`no_observation` bucket and a missing bucket are counted separately; neither
becomes numeric zero. A selected row in another currency fails closed. Gateway
attempts in another currency remain in the denominator but are excluded from
the selected-currency subtotal. An unavailable estimate is counted as unpriced
even when its currency also mismatches; these diagnostic counts can overlap.
Conflicting digests or currencies for one rate-card ID/version fail closed.
Distinct attempts that reuse a terminal event ID or non-null usage event ID
also fail closed before their estimates can be summed.
Failed, rate-limited, unknown-outcome, and unpriced attempts remain visible.
Negative provider rows are counted, including
unclassified ones, without inferring credit, discount, bypass, or an employee
charge. The preview never recalculates a historical estimate from a later rate
card.

`all_selected_buckets_observed` describes only the supplied selected buckets;
it does not prove complete provider-account coverage. Similarly,
`all_supplied_attempts_priced` describes only sidecar events, and the CLI's
`terminal_attempts_missing_sidecar_count` must be read alongside it. Pending
attempts have no terminal cost and remain outside those preview counts; the
account reconciliation separately exposes and blocks on
`pending_account_gap_count`. A second source-binding record with the same
provider-account fingerprint, fingerprint-key version, scope kind, and scope
fingerprints is a same-account gap even when its source ID, version, or digest
differs. Fingerprints made with different key versions are not comparable;
`uncomparable_account_identity_count` and
`uncomparable_account_pending_attempt_count` expose those terminal and pending
gaps and also block variance. Only comparable, different account/scope
coordinates enter the `other_account` counters. The preview has no team, actor,
or application attribution, independent bypass evidence, approved allocation,
threshold policy, or invoice fact. Those requirements, an
independently verified account/period authority, live OpenAI finance evidence,
#214,
[#223](https://github.com/Xpounder-com/hormuz/issues/223), and #225 remain
open before #8 or v1.3.0 can close.

Focused verification:

```console
python -m unittest -v \
  tests.test_finance_reconciliation_coverage \
  tests.test_finance_coverage_report_cli \
  tests.test_finance_account_reconciliation_cli
```
