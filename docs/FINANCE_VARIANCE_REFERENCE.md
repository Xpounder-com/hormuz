# Finance account-grain variance reference

`hormuz.finance_variance_reference` is a dormant, provider-free calculation
reference for #8. It accepts synthetic, caller-supplied provider cost rows and
original configured estimate amounts. It is not wired to collection, gateway
attempts, a report, CLI, HTTP, or a live provider. The account-binding sidecar
needed to establish real gateway scope remains unimplemented. In particular,
the `BoundGatewayScopeClaim` value is only an explicit precondition in this
reference: constructing one does not register a binding, prove account
ownership, or authenticate any attempt.

A future authorized report builder must first pin the collection as-of cutoff
and selected snapshot IDs/digests, authorize the tenant, validate each
immutable attempt binding against its registered source binding, and establish
compatible account, scope, half-open UTC period, currency, product and
collection profile. The reference compares only provider-account grain; it
cannot represent team, actor, application or model allocation. Exact coordinate
equality and complete provider coverage are checked before amounts are read.
An empty/missing/partial/stale provider bucket is not zero. Duplicate provider
observations or gateway attempts are rejected.

The calculator cannot verify the caller's complete-coverage assertion, prove
first-party transport or assign a boundary-crossing request to a provider's
accounting period. Those are prerequisites for the future report builder, not
properties inferred from equal coordinates or a price digest.

At that synthetic comparable grain, signed variance is the selected signed
provider aggregate minus the subtotal of available original configured
estimates. Absolute variance is its magnitude. Relative variance is an exact
signed numerator over the positive magnitude of the estimate denominator,
with no decimal rounding; zero denominator leaves it undefined even for
zero/zero. Unpriced attempts retain a count and make `supplied_attempt_pricing`
`incomplete`; even `complete` means only that the supplied attempt rows are
priced. The known subtotal and its difference are never a complete bill.
The reference never evaluates or passes a threshold and never labels an
aggregate or adjustment as a final invoice or a team charge. Signed provider
rows are counted once without inferring credits or bypass from their amount.

The source and installed-wheel tests use synthetic cases for positive,
negative and zero variance, exact ratios, incomplete pricing, signed rows,
duplicate evidence, and scope/currency/period mismatches before arithmetic.
This checkpoint adds no SQLite/PostgreSQL schema, ACL grant, credential,
provider call, public v1 contract or #214 final-candidate acceptance.
