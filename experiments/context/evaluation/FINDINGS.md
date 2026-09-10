# Context optimization: bounded evaluation decision

Scope update, 2026-09-07: the user selected integration into the main Hormuz
product and release lifecycle. Revision 3 of the
[implementation handoff](../../../docs/CONTEXT_COMPACTION_IMPLEMENTATION_HANDOFF.md)
supersedes this report's experiment-only packaging and gateway-side runtime
recommendations. The current feature uses a client-side optimizer and user toggle.
The measurements and limitations below remain historical evidence.

Date: 2026-09-07. Hormuz source inspected: `581508151eff848c05ed6773eaa8ffd4a5d81196`
(checkout has unrelated desktop work). Headroom source:
[`e67b3c8a29443a60d6b0018fb22f525c5cd7e709`](https://github.com/headroomlabs-ai/headroom/tree/e67b3c8a29443a60d6b0018fb22f525c5cd7e709).

The accepted implementation proceeds with the small, optional tool-result
encoding as a client-side Hormuz feature. Headroom's whole proxy and tool-schema
compactor remain outside the product. The evidence supports potential savings
from structure, independently of a persistent cache, retrieval loop, or semantic
summarizer. It does not yet prove improvement on real user tasks or authorize a
deployment.

## Measured results

Thirteen deterministic synthetic content cases; tiktoken 0.12.0 with two
encodings. Baselines are the same original strings, and JSON baselines are already
minified. No savings are attributed merely to pretty-print whitespace removal.
No aggregate workload savings is reported: these are deliberately selected
fixtures, not a representative frequency-weighted traffic sample.

| Fixture | cl100k tokens before → after | o200k tokens before → after | o200k reduction |
| --- | ---: | ---: | ---: |
| Repeated log with one error | 559 → 35 | 559 → 35 | 93.7% |
| Unique timestamped log | 1261 → 1261 | 1261 → 1261 | 0.0% |
| Search results sharing a file | 840 → 486 | 840 → 486 | 42.1% |
| Path listing | 540 → 304 | 540 → 304 | 43.7% |
| Repeated configuration stanzas | 490 → 106 | 490 → 112 | 77.1% |
| Uniform JSON tool results | 1612 → 799 | 1612 → 800 | 50.4% |
| Nested uniform JSON | 1083 → 864 | 1043 → 865 | 17.1% |
| Heterogeneous JSON | 17 → 17 | 17 → 17 | 0.0% |
| Dense instruction text | 20 → 20 | 20 → 20 | 0.0% |
| Source code | 21 → 21 | 20 → 20 | 0.0% |
| Diff including index metadata | 49 → 49 | 50 → 50 | 0.0% |
| ANSI-colored log | 440 → 440 | 520 → 520 | 0.0% |
| Literal compression-marker collision | 51 → 51 | 51 → 51 | 0.0% |

Text results use Headroom's actual `compact_lossless` helper with an additional
exact-byte reconstruction guard. JSON results use the **independent native table
baseline**, not Headroom SmartCrusher. The direct upstream helper does not
handle JSON, so its raw JSON results are passthrough. The evidence retains raw
upstream and guarded token counts separately.

Six cases compacted; seven passed through. Dense/code/heterogeneous inputs were
not forced into another representation. The stricter guard rejected upstream
ANSI removal and diff index stripping, even though Headroom calls them
meaning-preserving. The marker collision safely passed through.

Three synthetic request-envelope checks placed the uniform JSON fixture into
Chat Completions, Responses, and Anthropic tool results, preserving the rest of
each envelope. Full serialized-JSON o200k counts changed respectively from
1763 → 886, 1757 → 880, and 1770 → 893. All three reconstructed the original
request exactly. These are local serialization counts, **not provider billing or
live protocol-conformance results**, including for the Anthropic-shaped request.

## Tool-schema finding: reject the current implementation for adoption

The actual upstream `compact_tools` recursively removes annotation-like keys
except directly under `properties`. In six small probes:

| Probe | Observed preservation |
| --- | --- |
| Actual property named `title` | Preserved |
| `title` inside an object-valued `const` | Removed; allowed value changes |
| `title` inside object-valued `enum` entries | Removed; distinct values collapse |
| `title` inside an object-valued `default` | Removed; supplied default changes |
| Definition named `title` under `$defs` | Removed; local `$ref` becomes dangling |
| Two spaces inside a quoted instruction in `description` | Collapsed to one |

These are executable helper-level reproductions in `results.json` and tests,
not assertions that every provider accepts all six input schema forms. Even
before model-quality evaluation, this generic recursive behavior is unsuitable
as a drop-in governance-preserving transform. Removing descriptions/examples can
also alter tool behavior even when validation constraints survive.

No upstream issue or message was posted. The synthetic before/after examples
are available for a separately authorized report.

## Fit with Hormuz's actual request path

The inspected `hormuz/server.py` applies identity/model/output/storage policy,
then inspects the request with `SecretRedactor` around line 751, serializes the
redacted request around line 805, and begins the governed attempt before
forwarding. `_begin_governed_attempt` reserves an input bound using **serialized
body byte length** around line 1284; this is not tokenizer-exact billing.

A future opt-in hook belongs after original-request secret inspection/denial
and before final serialization/reservation. Its transformed request value and
serialized body must remain consistent for the first attempt and any permitted
failover. Actual provider usage must remain the settlement source; measured
compaction deltas cannot replace billed usage. A fallback must still go through
normal budget enforcement. Compression must not hide an original secret from
the deny decision, reconstruct secrets after redaction, or inject active tools.

Only explicitly selected, understood tool-result text should enter the first
slice. Preserve user/system instructions, tool definitions/arguments, images,
reasoning/signature blocks, identifiers, and unknown result shapes. Config and
source files used as executable/verbatim artifacts should stay untouched even
when the text could be mathematically packed. The standalone config fixture
demonstrates potential; it is not permission to rewrite every config file.

Multi-turn integration still needs a cache-stability test: the same historical
content must keep the same transmitted representation. Deterministic,
query-independent transforms reduce drift, but enabling the feature or changing
its version mid-session can change a provider cache prefix. The first deployed
trial should use new sessions with a pinned transform version. This evaluator
has no session tracking, persistent cache, or live prefix-cache evidence.

## Candidate selection

| Candidate | Decision | Reason |
| --- | --- | --- |
| Exact repeated-line/block and path compaction | Advance to narrow quality evaluation | Measured savings; exact reconstruction; no model or storage dependency |
| Shared-column JSON representation | Advance as a native comparison | Preserves complete records; significant synthetic savings; LLM interpretation still untested |
| Generic tool-schema annotation/description stripping | Do not adopt as implemented | Concrete contract and instruction changes reproduced |
| AST/prose lossy compression | Defer | Requires task-level quality evidence and larger dependency surface |
| Provider tool-search deferral | Defer as a separate compatibility feature | Provider-specific; tool availability and discovery behavior change |
| Persistent originals and CCR retrieval proxy | Defer | Adds content custody, TTL/eviction, permissions, and extra provider calls |

The best near-term design is a small opt-in transform owned by Hormuz in this
experimental boundary. Use Headroom as a pinned comparison/reference. Before
copying upstream code, retain its Apache-2.0 license/attribution and verify any
applicable NOTICE requirements; this evaluation only imports the external
checkout and does not vendor it. Do not introduce Headroom's broad runtime
dependency graph solely for a few standard-library transforms.

## Validation and remaining acceptance

Completed: 15 focused tests, 13 offline synthetic cases, six schema probes, and
three reconstructable protocol-shaped envelopes. The evaluation CLI blocks
network connections; public source and tokenizer vocabularies were downloaded
separately during setup. No provider calls, customer traffic, or production
configuration were used. Runtime helper exceptions and malformed/unsupported
gateway input are not fully hardened by this fixture-only adapter.

Still required before enabling a product feature:

1. A small approved/sanitized task corpus with fixed expected answers. Include
   count/aggregation, rare exceptions, exact values, file-and-line navigation,
   mixed formats, and adversarial instruction/marker cases.
2. Paired original/compact model runs using the same model/settings and enough
   repetitions to distinguish variability from regressions. Record task success,
   total input/output/reasoning/cache usage, retries, and end-to-end latency.
3. Existing gateway loopback integration tests expanded to prove secret-denial
   ordering, budget bounds, unchanged routes/tool calls, streaming, and failover
   for the exact transformed request. The current envelope tests do not replace
   those tests.
4. New-session cache behavior and a content-free audit record for transform
   version/action and measured counts; do not persist prompts or compressed text.

Choose promotion criteria before those paired runs. A reasonable starting
proposal is zero failures on exact-value/authorization preservation checks,
no observed paired task regression requiring retries, and lower measured total
cost without unacceptable latency. The latency tolerance and statistical
quality margin need a workload-specific decision; this report does not invent
either from token counts.

## Evidence sources

- [Pinned Headroom fold implementation](https://github.com/headroomlabs-ai/headroom/blob/e67b3c8a29443a60d6b0018fb22f525c5cd7e709/headroom/transforms/lossless_compaction.py)
- [Pinned tool-schema compactor](https://github.com/headroomlabs-ai/headroom/blob/e67b3c8a29443a60d6b0018fb22f525c5cd7e709/headroom/proxy/tool_schema_compaction.py)
- [Pinned session engine](https://github.com/headroomlabs-ai/headroom/blob/e67b3c8a29443a60d6b0018fb22f525c5cd7e709/headroom/proxy/session_engine.py)
- [Hormuz gateway](../../../hormuz/server.py)
- [Existing context experiment boundary](../../../docs/CONTEXT_EXPERIMENT_MIGRATION.md)
- [Machine-readable local evidence](results.json)
