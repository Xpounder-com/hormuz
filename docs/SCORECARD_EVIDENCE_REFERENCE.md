# Scorecard evidence qualification reference for #222

`hormuz.scorecard_evidence_reference.qualify_evidence` is an offline calculation boundary,
not a scorecard endpoint or an accepted scorecard snapshot. It accepts typed,
already selected evidence for one organization and one immutable use-case
version. A future authorized builder must verify the owner's approval and
resolve the work scope, cost facts,
actual provider/model/version, outcome revisions, run-to-outcome associations,
coverage denominators, stratum membership, and guardrail rules before calling
it. Caller-supplied references here do not prove source authority or a database
join.

The reference returns exact rational coverage for seven named dimensions and
keeps excluded quantity visible. The declared minimum applies to each other
dimension individually. A null quantity or zero denominator is inconclusive.
The sample threshold counts distinct, previously source-qualified opaque
work-item IDs across strata, so
retries for one work item cannot inflate evidence. Every declared stratum must
be nonempty and pass all eight mandatory guardrails; even one small failing
stratum blocks eligibility. A rule must be versioned and declared before the
window, and the observation must cover the closed window and meet the owner's
freshness bound. Unknown actual model version, association rule, policy, or
required estimate rate card is inconclusive. Provider aggregates, discounts,
and unavailable cost are not per-work-item cost bases.

The result contains only `eligible` or `inconclusive`, fixed reason codes,
distinct sample count, coverage ratios, and the preserved cost basis. It does
not calculate quality-qualified cost per accepted item, uncertainty, a Pareto
frontier, optimization lift, or a causal claim. Those need the remaining #8,
#221, and #222 work, plus SQLite/PostgreSQL lineage, authoritative access
control, frozen fixture parity, and protected-main qualification. Nothing in
this reference closes #222 or qualifies v1.3.0 for release.
