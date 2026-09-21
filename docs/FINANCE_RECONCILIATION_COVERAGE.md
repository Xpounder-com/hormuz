# Offline finance coverage preview for #8

`hormuz.finance_reconciliation_coverage` builds a provider-free preview over an
`AsOfCollectionView` and immutable finance-attempt sidecar events. The pure
function still requires its caller to authorize and select both inputs. The
`hormuz finance report` CLI now supplies a read-only, administrator-only path
to that preview from durable evidence. It is not an account match, an invoice,
or live-finance verification.

For a selected cost profile, run:

```console
hormuz --config hormuz.json finance report SOURCE_BINDING_ID VERSION \
  openai.organization-costs.v1 2026-09-01T00:00:00Z 2026-09-03T00:00:00Z \
  --currency USD --as-of-commit-sequence 42
```

Omit `--as-of-commit-sequence` to pin the current tenant publication high-water
mark in the response. The command authenticates the existing portfolio token
from `HORMUZ_PORTFOLIO_TOKEN` or `--token-env`, then requires the exact current
`portfolio_admin` binding for that token's tenant. It neither accepts a tenant
argument nor reads a provider credential or fingerprint key. `finance_viewer`
and other roles receive no report access in this slice. The selected daily
window is at most 31 days, and more than 10,000 terminal attempts or selected
cost rows fails closed. The repository reads the provider selection and
terminal gateway attempt sidecars under one tenant transaction. It checks the
sidecar's canonical stored event against its audit source and exposes the count
of terminal attempts with no sidecar as an explicit coverage gap. Each selected
snapshot also retains its stored `evidence_origin` and `scope_provenance` in
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
Conflicting currencies for one immutable rate-card identity fail closed.
Failed, rate-limited, unknown-outcome, and unpriced attempts remain visible.
Negative provider rows are counted, including
unclassified ones, without inferring credit, discount, bypass, or an employee
charge. The preview never recalculates a historical estimate from a later rate
card.

`all_selected_buckets_observed` describes only the supplied selected buckets;
it does not prove complete provider-account coverage. Similarly,
`all_supplied_attempts_priced` describes only sidecar events, and the CLI's
`terminal_attempts_missing_sidecar_count` must be read alongside it. Pending
attempts have no terminal cost and are outside both counts. The preview has no
team, actor, or application attribution, independent bypass evidence, approved
allocation, threshold policy, or invoice fact. Those requirements, the actual
account binding and comparable period
contract, PostgreSQL transition/recovery, live OpenAI finance evidence, #214,
and #225 remain open before #8 or v1.3.0 can close.

Focused verification:

```console
python -m unittest -v tests.test_finance_reconciliation_coverage tests.test_finance_coverage_report_cli
```
