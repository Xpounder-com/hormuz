# Finance reconciliation — decision preflight for v1.1.0

Status: **owner-approved scope; exact #214 preflight acceptance pending**.
The owner approved prospective metadata-only account capture on 2026-09-06;
[durable approval record](https://github.com/Xpounder-com/hormuz/issues/8#issuecomment-5562564894).
This is a continuation of #8, not a replacement for its remaining criteria.
Baseline: protected main `c877f49da8baf6a837924f33f06494964cd7118b`
([collection acceptance](https://github.com/Xpounder-com/hormuz/issues/8#issuecomment-5560266260)).
SQLite is 12; PostgreSQL is 17. This document changes no runtime, schema,
permission, public contract, credential, provider access, or release gate.

## Management result

The finished work must answer: what was budgeted, what the gateway estimated,
what the provider reported, how much of each is comparable, and what requires
finance review? Team/use-case/model views must retain their actual evidence
grain. A provider account total is not a team invoice total.

The next work orders remain dependency ordered:

1. Establish immutable attempt-to-provider-account scope evidence.
2. Freeze and implement reconciliation calculations and reproducible reports.
3. Implement versioned exception thresholds and append-only review decisions.
4. Complete #8's coverage, adjustment, historical, package and recovery proofs.
   Role-scoped decision views remain under #223; model scorecards also require
   #221. Neither a collection checkpoint nor this draft closes #8 or #214.

## Observed gap in the accepted predecessor

The gateway attempt root contains tenant, actor, team, application, protocol,
requested/routed model, policy and reservation evidence. The native-finance
sidecar additionally pins observed usage and configured price identity.
Neither record captures an immutable provider billing-account binding.

By contrast, `SourceBindingVersion` in `finance_collection_repository.py`
identifies a tenant-bound account fingerprint, source scope, credential
reference/version and fingerprint-key version. Those are collection-side
coordinates; their existence does not associate a gateway attempt with them.

`configured_route_rate_card` hashes route/pricing inputs, not the selected
upstream credential or account. `server._forward` selects upstream transport
separately. Changing a provider credential can therefore preserve the same
price identity. Protocol is also not proof of first-party provider transport:
an OpenAI-compatible upstream is not automatically an OpenAI billing account.

The bounded executable probe in `docs/evidence/reconciliation_scope_probe.py`
checks these exact predecessor boundaries without reading credentials or
making network calls. It is a source/SQLite diagnostic, not PostgreSQL,
end-to-end provider, or implementation acceptance evidence.
It pins seven relevant source-file SHA-256 values from that baseline; this
is not verification of every distribution file. Run it with a Python
environment containing the project's dependencies:

```console
python docs/evidence/reconciliation_scope_probe.py
```

Do not infer account identity from protocol, model, price digest, a current
configuration, an actor, or a numerically plausible provider total. Existing
records must remain explicitly unbound unless a separately approved historical
attestation mechanism exists. This draft does not authorize that mechanism.

## Owner-approved decision: prospective scope capture

Approved scope: add a **separate, versioned, metadata-only account-binding
sidecar captured with the attempt root before egress**, using an explicitly
operator-approved binding between the configured upstream and the collection
account/scope. This is additional gateway evidence, not collection-only work.

The approved scope boundary is:

- The operator attests the exact tenant, upstream identity/version, provider
  account/scope and source-binding version. API authentication and account
  ownership attestation remain distinct evidence. Do not hash credential
  values or use environment-variable names as account identity.
- Pin immutable binding ID/version/digest and account/scope fingerprint-key
  version for each attempt. Preserve the selected transport profile so a
  reseller or compatible proxy cannot silently become first-party evidence.
- Capture in the same database transaction as the attempt root and budget
  reservation, using the existing internal adapter seam. Do not widen the
  frozen public v1 request-attempt record or change historical audit entries.
- Missing or invalid scope evidence prevents financial matching, not ordinary
  v1 inference. Represent it as unbound. Existing authorization, budget and
  fail-closed persistence behavior still applies; no new inference policy is
  approved here. A persistence failure must not yield partially bound egress.
- Resolve the binding and selected upstream together for the attempt. A later
  credential/configuration change must not rebind prior attempts. Revocation,
  rotation and failover need explicit race tests before runtime acceptance.
- Include failed, rate-limited and unknown-outcome attempts. A retry/failover
  is a separate attempt, with its own binding; it never repairs old evidence.
- Old records stay unchanged and report an explicit historical scope gap.
  New evidence does not retroactively authenticate an old account mapping.
- No new read role, credential creation/rotation, provider access, account
  allocation, raw content, automatic policy change or production migration.

Approval authorizes developing this bounded successor preflight, not
accepting its runtime. Its precise configuration contract, table shapes,
audit-source additions, migration versions and any fixed ACL delta must be
measured and reviewed before implementation. Versions 12/17 and the accepted
199-entry ACL boundary are not rewritten or weakened. No successor version
or fingerprint is reserved or claimed by this draft. The internal evidence
contract is recorded in `finance-account-binding-contract-v1.json`; its
verifier/tests freeze the scope and failure semantics, not an implemented
gateway, configuration surface, migration, or final transition checkpoint.

### Credential and attestation limits

Inference credential identity and collection/admin credential identity are
different coordinates. An existing collection credential reference cannot
prove which inference credential sent a request. Operator-controlled opaque
reference versions identify those configurations; neither is the credential
value, a hash of that value, or independent verification of account ownership.
Replacing a secret under an unchanged reference cannot be detected by this
metadata-only evidence. Operators must version the binding when changing the
account/credential association; reports must preserve this attestation limit.

Binding-registration-version and source-binding-version checks must share the tenant-bound
transaction that captures attempt evidence. Do not use a second repository
transaction and claim atomicity. Missing, revoked, stale, ambiguous or
unsupported binding semantics produce explicit unbound evidence. If a real
database write fails, the whole attempt transaction fails under the existing
pre-egress durability rule; swallowing that failure and forwarding would not
be preserved v1 behavior.

## Reconciliation contract to freeze after the scope decision

### Comparable evidence, not a forced match

Each report pins the organization, account/scope binding versions, requested
UTC half-open interval, exact selected snapshot IDs/digests, collection/parser
profiles, attempt evidence IDs, price identities, attribution and budget
versions, review-policy version and calculation version. Record a consistent
database read boundary: separate latest-state queries are not one snapshot.

The existing `current_observations` method selects latest coverage and has no
explicit as-of argument. Runtime design must provide a bounded, reproducible
selection and preserve exact input identities. Do not obtain a historical
report by calling that method again after a provider refresh. Empty newer
coverage suppresses old observations but is not an explicit zero value.

Compare only compatible account, scope, period, currency, product/profile and
dimensions. Keep provider observation time and gateway attempt/terminal time
distinct; a request crossing midnight cannot be silently assigned the
provider's accounting time. Missing time/granularity authority is a coverage
exception. Partial periods must not be prorated without a separate rule.

### Amounts and variance

Use existing bounded decimal primitives and original attempt estimates. Never
reprice old facts with the current rate card, round to display precision
before summing, convert currency implicitly, or substitute budget holds for
cost. Preserve signed provider rows and their classification provenance.

At a proven comparable grain, define signed variance as provider aggregate
minus the sum of available configured estimates. Report the estimate as a
known subtotal with unpriced-attempt counts when incomplete, not as a complete
bill. A numerical comparison of that subtotal must be labeled incomplete and
must not pass a reconciliation threshold as if all costs were known.

Define absolute variance as the magnitude of that difference. Relative
variance uses the magnitude of the configured-estimate denominator. A zero
denominator produces an undefined relative variance, including zero/zero;
absolute variance remains available where comparable. Negative provider
adjustments remain signed and never become employee/team final charges.

Do not choose operational thresholds on the owner's behalf. An unconfigured
review policy reports `not_evaluated`. A later explicit versioned policy must
specify amounts/currencies, relative thresholds, strict/inclusive comparison,
combination rule and coverage guards. Unknown or incomplete scope cannot pass
by falling below a numerical threshold.

### Adjustment and coverage treatment

- Explicit provider credits/discounts/invoice adjustments retain source,
  sign, scope and evidence basis. A negative unknown row stays unclassified;
  free text is not classification authority. Never add an adjustment again
  if it is already included in the selected provider aggregate.
- Batch, cache/tier/modality differences, failures, rate limits, rounding and
  collection gaps each have separate supported, unsupported or unknown
  treatment. Do not invent a discount or suppress a failed attempt's cost.
- Separately report gateway priced/unpriced attempts, attributed/unattributed
  governed spend, unbound account scope, provider covered/missing/empty/stale
  buckets, unsupported dimensions and independently evidenced bypass.
  These overlap; do not sum them as a disjoint traffic denominator.
- Unknown bypass remains unknown. Provider-minus-gateway variance is not
  evidence of bypass or of an employee's spend.
- Provider usage quantities and provider cost are separate sources. They are
  not joined through a many-to-many model/dimension match or allocated by
  token weights. Missing provider model/team detail remains unsupported.
- Final invoice evidence, provider aggregates, gateway estimates and any
  separately approved allocation remain distinct in every output/export.

### Useful report sections

1. Budget versus governed estimate/consumption, with original budget versions
   and basis labels. Link to the accepted #217 report rather than redefine it.
2. Provider-account reconciliation with comparable window/scope, signed and
   absolute variance, completeness, provenance and last observation time.
3. Team/use-case/application/model gateway breakdowns, including unmatched
   and unpriced evidence. No provider-total distribution without authority.
4. Finance-review exceptions with reason, affected scope, exact source IDs,
   policy version and append-only review status. Resolving a review does not
   change historical amounts or certify an invoice.

## Required implementation evidence

- Two different upstream/account bindings with identical protocol, model and
  prices must never collide or cross-match. Cover tenant isolation, account
  switches, rotation, revocation and unsupported transport profiles.
- Include failed/rate-limited/unknown attempts; prove no automatic replay,
  reservation release, reattribution or historical mutation.
- Pin source selection under concurrent refresh, including overlapping and
  empty buckets. Replay a report after newer snapshots, price/budget changes
  and attribution corrections and reproduce its original result.
- Freeze exact reference cases for positive/negative/zero variance, zero
  denominator, incomplete pricing, differing currencies, duplicate/late
  observations, signed adjustments, boundary-crossing requests and partially
  overlapping scopes. Scope guards must run before arithmetic or joins.
- Prove authorization before lookup and aggregate work; public v1 contracts
  stay unchanged and #223 receives no premature role expansion.
- For any actual schema/ACL successor, prove both database transitions,
  fault rollback/retry, pinned predecessor refusal, old-pair restoration and
  populated forward recovery; preserve the literal historical ACL gates.
- Verify source and installed wheel/package boundaries, review the exact PR
  head and linked issues, pass every protected check, merge normally, then
  verify exact merged-main CI before accepting the bounded checkpoint.

Customer-authorized provider evidence is still required for a live-finance
label. Contract tests are not that evidence. Final-candidate #214, external
pilot #225, and release authorization remain separate gates.
