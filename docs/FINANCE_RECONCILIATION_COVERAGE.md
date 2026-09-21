# Offline finance coverage preview for #8

`hormuz.finance_reconciliation_coverage` is a dormant, provider-free preview
over an `AsOfCollectionView` from the authorized collection repository and a
caller-supplied tuple of immutable finance-attempt sidecar events. It is not a
CLI/API report, an account match, an invoice, or live-finance verification.
The caller must authorize the tenant and obtain both selections before invoking
it; this pure function does no database, provider, credential, or policy I/O.

The preview keeps the selected provider cost aggregate and the original gateway
configured-rate estimate in different fields with different cost-basis labels.
It pins the collection cutoff, selected snapshot IDs/digests, observation keys,
attempt evidence IDs, and original rate-card identities. Its signed variance is
always absent: current attempt sidecars have no immutable provider-account
registration and terminal time is not automatically provider accounting time.
Equal tenant, provider, and calendar window are insufficient to compare them.
The existing account-grain variance reference remains a separate synthetic
calculation with caller-asserted preconditions.

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
`all_supplied_attempts_priced` describes only supplied events, not every gateway
attempt in the period. The preview has no team/actor/application attribution,
independent bypass evidence, approved allocation, threshold policy, or invoice
fact. Those requirements, the actual account binding and comparable period
contract, PostgreSQL transition/recovery, live OpenAI finance evidence, #214,
and #225 remain open before #8 or v1.3.0 can close.

Focused verification:

```console
python -m unittest -v tests.test_finance_reconciliation_coverage
```
