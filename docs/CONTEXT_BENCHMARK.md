# Governed context benchmark

Hormuz ships a deterministic synthetic benchmark for the retrieval, freshness, authorization, compression, and latency contract of governed context packs. It is a product regression and release gate, not evidence that AI improves employee productivity or software quality.

## Frozen corpus

The version-1 corpus contains 60 tasks: ten each for bug fixes, features, refactors, incidents, onboarding, and policy questions. Each task binds its records to a frozen synthetic repository revision and a SHA-256 memory-snapshot digest. The six challenge types are evenly represented:

- cross-scope authorization;
- expired relevant records;
- superseded decisions;
- active contradictions;
- changed dependencies;
- malicious instructions embedded in context.

Twelve tasks form the fast CI subset. The task corpus and reference outcomes are separate files. The corpus SHA-256 is bound into the reference file, every record is bound to the task's repository revision, and the runner rejects unknown fields, mismatched snapshots, invalid source revisions, or task/outcome ID drift.

The separation is a leakage control, not an access-control claim: both synthetic files ship with the package so anyone can reproduce the score. The runner checks that exact normalized reference outcomes and their SHA-256 digests are absent from the task records. Final task patches are intentionally absent, but semantic answer leakage still requires human review when the corpus changes.

Regenerate and verify the checked-in artifacts with:

```bash
python3 benchmarks/context/generate_corpus.py
python3 benchmarks/context/generate_corpus.py --check
```

## Baselines

Every task is evaluated against the same frozen input and token/item limits:

1. `no_memory` returns no context.
2. `full_history` truncates records in snapshot order without authorization or relevance ranking.
3. `simple_lexical` ranks raw records by query-term frequency without governance filters.
4. `hormuz_governed` calls the production `build_context_pack` kernel, which authorizes and filters lifecycle state before deterministic ranking and budgeting.

The runner never calls OpenAI, Anthropic, a ticketing system, or a live repository.

## Metrics

The machine-readable report includes per-baseline aggregate metrics and only selected record IDs as task evidence; it does not copy record content or reference outcomes into the evidence file.

- Precision = labeled relevant selected records / all selected records.
- Recall = labeled relevant selected records / all labeled relevant records.
- Useful-pack rate = tasks containing every relevant record and no labeled authorization, stale, dependency, malicious, or contradiction hazard.
- Authorization-leak rate = tasks selecting at least one wrong-organization or wrong-team record.
- Lifecycle-stale rate = tasks selecting an expired or superseded record.
- Dependency, malicious, and contradiction rates use only tasks containing that challenge.
- Compression = `1 - selected estimated tokens / full-snapshot estimated tokens`, averaged across tasks.
- p50/p95 latency measure in-process selection only. They exclude disk, network, model, and MCP latency.
- Determinism failures compare complete selection results across repeated runs.

## Profiles

`report` records evidence and always exits zero after a valid run. `regression` exits 2 if Hormuz regresses guarantees it already implements: zero authorization leaks, zero expired/superseded selections, zero budget violations, deterministic output, zero exact leakage failures, and p95 in-process latency below 500 ms. CI runs this profile on the 12-task subset.

```bash
hormuz context-benchmark \
  --profile report \
  --iterations 5 \
  --output context-benchmark.json

hormuz context-benchmark \
  --profile regression \
  --ci-subset \
  --iterations 5 \
  --output context-benchmark-ci.json
```

`release` runs all 60 tasks and adds the intended enterprise requirements: at least 0.90 precision and useful-pack rate, at least 0.95 recall, and zero stale, dependency-stale, malicious, or contradiction selections.

```bash
hormuz context-benchmark \
  --profile release \
  --iterations 5 \
  --output context-benchmark-release.json
```

The current alpha is expected to exit 2 for the release profile. It passes authorization, expiry, supersession, budget, recall, and determinism checks, but it does not yet automatically invalidate changed dependencies, quarantine malicious context, or resolve active contradictions. Those are open product gaps, and the release profile must not be weakened merely to make it green.

Evidence files use mode `0600`, refuse overwrite unless `--force` is explicit, and include the corpus/reference hashes, Python and Hormuz versions, threshold results, aggregate metrics, and per-task selection IDs.

## Interpretation limits

This corpus is synthetic and its labels are authored with the fixtures. A passing score proves only the checked retrieval contract. It does not measure model answer quality, accepted patches, human rework, causal productivity impact, distributed authorization, hosted latency, or behavior on a customer's data. Those require a separately frozen offline replay and controlled live evaluation without allowing final patches to enter trial memory.
