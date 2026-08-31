# v1.1.0 finance value foundation

This is the first **non-durable implementation slice** of #8, under the
[finance checkpoint](FINANCE_TRANSITION.md). It supplies pure, provider-free
accounting helpers. It is not an import/fetch CLI, a financial ledger, a
reconciliation report, a gateway-native usage sidecar, or live provider proof.
The v1 gateway parser, request-time estimates, report shapes and database
schemas are unchanged by these pure helpers. The separate
[durable rate-card slice](FINANCE_RATE_CARDS.md) adds registration and audited
exact-version reads without changing these value contracts or completing #8.

## Implemented boundary

- `hormuz.finance_values`: bounded JSON decoding without binary floats, exact
  finite decimal values, and provider-specific cost-unit conversion.
- `hormuz.finance_usage`: allowlisted numeric aggregate-usage metadata and
  immutable normalized token categories, separate from the old `Usage` class.
- `hormuz.finance_rate_cards`: the closed `hormuz.finance-rate-card` v1 value,
  its canonical content digest, and exact configured token estimates.

There is no file, network, environment, credential or database access in these
modules. A caller must resolve authenticated tenant/source authority **before**
loading data or calling future adapters. A provider name, model, tenant field,
digest, or in-memory value is not proof of account ownership or customer consent.
The helpers do not set a live-verification flag or create provider-final cost.

## Exact values and provider conventions

Money is a finite decimal string with at most 18 integer and 18 significant
fractional places. Wire strings reject floats, exponent notation, leading
zeros, surrounding whitespace and non-finite numbers. Calculations use their
own exact decimal context, independent of caller precision/traps. Insignificant
arithmetic zeroes can be removed without rounding. Results requiring more
precision or range remain unavailable; no implicit rounding or FX is applied.

`provider_amount` accepts the native documented amount type: OpenAI decoded
JSON numbers (`int`/`Decimal`) in the named currency's major units, versus
Anthropic decimal strings in USD fractional cents. Anthropic conversion divides
by 100 exactly. Signs survive unchanged; a negative value does not itself
authorize a credit/discount classification, invoice-final label or allocation.
Currency case normalization is not a claim about settlement or minor units.
Missing values must be represented by the future observation envelope as
unknown, not supplied to this known-amount helper as zero.

`decode_provider_json` bounds a page to 1 MiB, 16 nested containers and 65,536
members, with numeric lexemes bounded to 128 characters. It rejects duplicate
members, malformed Unicode and non-finite/unrepresentable numbers, preserving
the fixed resource-limit error for oversized lexemes. Bounded finite numeric
metadata is decoded exactly without imposing money-specific precision; that
limit is enforced when an actual monetary field is normalized. The decoded page
exists only in caller memory; callers must discard it after allowlisted
normalization. Collection-level 16 MiB / 32-page / 4,096-record / deadline,
pagination, conflict and authorization checks remain future adapter work.

The source dialects are documented in the frozen
[provider source contract](finance-source-contract-v1.json):

| Quantity | OpenAI organization completions usage | Anthropic Messages Usage Report |
| --- | --- | --- |
| Input total | Already includes cache reads and writes | Derived only when uncached, read, 5-minute and 1-hour counts are all known |
| Cache writes | One aggregate cache-write count | Separate 5-minute and 1-hour categories retained |
| Uncached input | Native when returned; otherwise derivable only from known total/read/write | Native uncached input count |
| Missing reasoning / billable count | Unknown, never inferred from output or totals | Unknown, never inferred from output or totals |
| Request count | Native count when returned | Unknown in this profile |

Known counts are nonnegative integers at most `2^63 - 1`. Booleans, strings,
floats, overflow and inconsistent known partitions fail. A fully known modality
partition must equal its parent, not merely stay below it. Source-native counts
remain separate from derived totals. Cached/uncached modality fields are
subcategories, not additional input. No arbitrary work text, user IDs, keys,
unrecognized fields or raw provider object is retained by the usage vector.
Null or absent metadata stays `None`; a later converter must not coerce it to
zero. An unknown category inside Anthropic cache creation or server-tool usage
rejects the source profile instead of silently disappearing from totals.
This is aggregate-report normalization, not retroactive per-attempt
native capture.

## Rate-card v1 and unavailable estimates

`rate_card_from_mapping` accepts a closed, versioned value with:

- tenant, stable rate-card ID, positive version, provider and actual model;
- currency, inclusive/exclusive UTC effective interval, exact service tier and
  explicit batch flag;
- `source_kind: operator_configured`, `unit: per_million_tokens`, and
  `rounding: exact_or_unavailable_v1`;
- a provider-specific pricing profile and all of that profile's named rates.
  A rate can be explicitly unknown (`null`), never silently defaulted.

The `openai_text_tokens_v1` profile uses uncached input, cache read, cache write
and output rates. It refuses known non-text or unreturned audio/image
categories. These are configured category rates, not proof that a provider's
report exposes every modality or commitment charge. The
`anthropic_messages_tokens_v1` profile keeps five-minute and one-hour write
rates separate and refuses unknown/nonzero web-search tool usage, since no
tool rate exists in this profile. Other source products, pricing profiles,
model tiers and tool charges are not silently included.

The caller supplies the exact tenant, actual model, event time, tier and batch
state; a mismatch or unknown dimension returns an unavailable estimate with a
fixed reason and null amount/currency, as required by the frozen finance wire.
The configured currency remains available separately on the immutable rate card.
No automatic batch discount, model-alias substitution, historical
backfill, invoice inference or proportional allocation occurs. Known complete
zero usage produces an explicitly estimated zero; missing metadata does not.

Rate-card values and estimates are immutable in memory. Canonical key order,
normalized timestamps and exact decimal values produce a content digest that
pins the full identity and rules. Exported dictionaries are independent copies.
A new rate-card value cannot mutate an existing estimate. A future append-only
repository must enforce one content value per tenant/card/version and preserve
stored snapshots; these pure helpers do not claim that persistence exists.

## Verification and remaining gates

The tests use **synthetic rates and usage**, not published prices or customer
records. They cover both cache dialects, two distinct rate-card versions,
fractional cents and signed amounts, unknown/invalid metadata, bounded parsing,
hostile decimal contexts, exact interval/dimension matching and immutable
copies. An independent rational-number oracle checks 200 provider/category
combinations. CI also runs the value tests against the installed core wheel
outside the checkout; the source kit contains the same helpers and tests.

Still required for #8: complete authenticated provider/export adapters,
bounded collection and failure/retry behavior, source bindings, append-only
persistence and atomic receipts, per-attempt sidecars, reconciliation and
review policies, all-dimension coverage, populated database recovery and the
separately authorized live-evidence gate. This slice changes no issue closure,
release/tag, deployment or #214/#225 acceptance requirement.
