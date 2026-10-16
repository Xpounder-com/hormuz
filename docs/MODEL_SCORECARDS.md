# Personal model scorecards

Build a local benchmark from tasks that represent your work, score the replies
against independent references, and optionally use the results to choose among
eligible local models. The public interfaces are the benchmark CLI and the
existing connect → coding-client request → result/status → disconnect workflow.

## Create your benchmark

```sh
hormuz benchmark init --output "$PWD/my-work.json"
```

This creates a private, editable version-1 `hormuz.benchmark-suite` file without
contacting a model. Replace the starter box-count tasks with representative
examples and references. Keep development examples separate from held-out cases
you did not use to tune the model or prompt. The sample tasks illustrate the
format; they do not establish broad capability.

For example, a suite for structured extraction can contain:

```json
{
  "schema_id": "hormuz.benchmark-suite",
  "schema_version": 1,
  "name": "invoice-extraction",
  "targets": {
    "objective": "latency",
    "min_pass_rate": 1,
    "min_cases": 3,
    "max_p95_latency_ms": 3000,
    "min_check_rates": {"invoice_accuracy": 1}
  },
  "cases": [
    {
      "id": "invoice-001",
      "task_type": "structured_extraction",
      "partition": "held_out",
      "request": {
        "input": "Extract JSON fields vendor and total from: Vendor: Acme. Total: 42.",
        "max_output_tokens": 128
      },
      "checks": [
        {"metric": "invoice_accuracy", "kind": "json_equals", "value": {"vendor": "Acme", "total": 42}}
      ]
    }
  ]
}
```

Add at least three distinct held-out inputs to meet that example's coverage
target. Changing case names or repeating an input does not create more distinct
evidence. Use separate suites when workloads need different targets.

Each check has a user-chosen metric name, a kind, and an expected value:

| Kind | Expected value | Meaning |
| --- | --- | --- |
| `text_equals` | String | Exact reply after trimming surrounding whitespace |
| `text_contains_all` | Nonempty list of strings | Case-sensitive phrase coverage; this alone does not prove correctness |
| `json_equals` | JSON value | Parsed JSON equals the reference, independent of object key order |
| `tool_call` | Object with `name` and `arguments` | Exactly one function call with matching name and parsed arguments |

The runner never executes returned tools or generated code. Ground truth belongs
in `checks`, outside the model request. There is no model judging itself and no
automatic collection from your history. Direct benchmarks support
`summarization`, `translation`, and `structured_extraction`. Suites containing
`code_change` or `code_review` fail preflight. Actual coding edits and independent
execution checks use the separate
[local coding qualification](LOCAL_CODING_QUALIFICATION.md) workflow. A benchmark
label or successful answer check cannot grant coding qualification.

Each task label must match the router's deterministic classification of its
user input. Recognized wording includes `Summarize ...`, `Translate ... into ...`,
and `Extract ... JSON ...`; ambiguous or mismatched labels fail before model calls.

## Run and compare

Start the router through the normal connection workflow. Select existing local
Responses aliases from your configuration; replace the example aliases below
with your own. Models, runtimes and credentials are never downloaded or changed.

```sh
hormuz --config /absolute/path/hormuz.json benchmark run \
  --suite "$PWD/my-work.json" \
  --models local-code local-fast \
  --output "$PWD/scorecard.json" \
  --repeats 3 --timeout-seconds 30 --deadline-seconds 300

hormuz benchmark show --scorecard "$PWD/scorecard.json" --format markdown
```

Omit `--format markdown` for JSON. `show` works without the configuration,
credentials or a live router. If the configuration has several actors, select
one explicitly with `run --actor ACTOR_ID`.
A run uses a loopback listener, `ingress.mode: local`, and the configured static
gateway identity. OIDC-only
configurations are outside the current local-router configuration contract.

| Recorded metric | Interpretation |
| --- | --- |
| Attempts, completions, passed tasks, error rate | Failed, timed-out and unstarted cases remain in the denominator |
| Task success and named check pass rates | All checks must pass for task success; check names preserve your domain metrics |
| Mean, p50, p95 and p99 latency | End-to-end time through the router, including policy and forwarding; unstarted requests have no latency sample |
| Header and first-body-byte timing | Separate observed stages of response delivery |
| First-token timing | First nonempty streamed text delta; unknown for nonstreaming or streams without a delta |
| Output tokens/second | Observed output tokens divided by full request time; includes input processing and router overhead |
| Input, output and cached tokens | Provider-reported observations; missing fields remain unknown |
| Total and mean estimated cost | Explicit configured prices applied to observed tokens; absent prices remain unknown |
| Runner CPU time and peak RSS | Benchmark process measurements, separate from the inference process |

Model/GPU peak memory, energy and inference-only decode speed are currently
unobserved. A missing value is not zero. Cost is a configured-rate estimate,
not an invoice or local electricity measurement. When a provider omits cache
details, the explicitly labeled estimate prices input as uncached. Inconsistent
cache counts, including cached tokens exceeding total input,
leave the cache measurement and estimated cost unknown. Timing is
affected by caches, model order and other machine activity; no claim of isolated
hardware benchmarking is made.

Targets apply independently to each task type, request profile and input-size
bucket. `min_cases` counts distinct held-out inputs, while repeated observations
improve measurement coverage without increasing that count. A workload needs
complete execution evidence, sufficient held-out coverage, the minimum task
success rate, and any named-check, p95 latency or mean-cost limits. Missing a
required measurement fails that target. The objective is `quality`, `latency`,
or `cost`; cost selection requires an observed cost estimate.
Replies containing provider errors and streams with explicit failure events
remain failed evidence, even when they also announce completion.
The response's stream mode must match the requested profile before it can earn
scores or satisfy targets.

Requests run sequentially over one reusable HTTP client: at most eight selected
models, 32 cases, three repeats, and 128 planned requests. Each output is bounded
to 4096 tokens and each response to 256 KiB. There are no retries, redirects,
speculative calls or background benchmark workers. Default deadlines are 30
seconds per request and 300 seconds per run; maximums are 120 and 1800 seconds.
HTTP 429/529 stops further requests regardless of the error body's format, as do
three consecutive request failures or local socket exhaustion. Remaining cases
are recorded as unstarted. Error bodies are closed without parsing.
Before model work, one authenticated `/v1/router/benchmark-state` snapshot checks
the actor, full route fingerprint and explicit-price fields against the running
router. The fingerprint includes the endpoint, revision and capabilities. Every
model response must match that live destination generation and requested model;
the provider-reported model must also match before scores or prices are certified.
Benchmark receipts report the forwarded request's byte count and output budget
after router preparation; recorded byte bounds use these observed values.
An unexpected live policy cap invalidates the measurement for the requested
generation budget. The running router also rejects benchmark probes while its
shadow helpers are enabled, even if the configuration file has since changed.
Benchmark probes disable operational failover so another model's answer cannot
be attributed to the selected one; admission also refuses to substitute another
alias when the requested route is unready. A configuration with `shadow_router` is
rejected before execution because its extra helper calls exceed this benchmark's
controlled request envelope.

The routing boundary is recorded in [ADR 0029](decisions/0029-personal-benchmark-scorecard-routing.md).

## Enable scorecard-based routing

Running a benchmark does not activate routing. Preview and stage the change in
a new configuration beside the current one:

```sh
hormuz --config /absolute/path/hormuz.json benchmark configure \
  --scorecard "$PWD/scorecard.json" \
  --output /absolute/path/hormuz-scorecards.json --dry-run

hormuz --config /absolute/path/hormuz.json benchmark configure \
  --scorecard "$PWD/scorecard.json" \
  --output /absolute/path/hormuz-scorecards.json
```

Use the existing disconnect/reconnect procedure with the new configuration.
The receipt says `requires_reconnect`; creating a file does not change a running
router. Existing local-only privacy remains local-only. If the prior mode was
`configured`, the staged mode becomes `auto`, and existing local qualifications
are still required. The active source file is never overwritten.
Staging uses the normal 1 MiB configuration parser and a credential-free metadata
preflight. The staging shell does not need the service's token environment or
access to its credential files.

Advanced configurations can explicitly add an absolute private report path:

```json
{"route_admission": {"mode": "local_only", "scorecard": "/absolute/path/scorecard.json", "local_routes": {}}}
```

Keep your existing qualified `local_routes`; the empty object above is only a
field-shape illustration and admits no local models.

For a fresh `POST /v1/responses` request, the router first applies identity, policy,
privacy, capability, readiness and coding-qualification checks. It then ranks
eligible local candidates whose scorecard workload matches the task hint,
generation controls including the exact output-token budget, tool/format schema,
input-size bucket and tested request bounds. Quality is the held-out pass rate; latency uses p95; cost
uses the mean estimate. Other metrics break ties deterministically. An explicitly
enabled scorecard may replace the client's default local alias within that set.
Cost-based preferences and cost targets require the relevant prices to remain
explicitly configured; removing a zero rate makes that price unknown.
Other API operations, including compaction, retain their existing admission and
cannot inherit generation scores.

Inspect `X-Hormuz-Scorecard-Status`, `X-Hormuz-Routing-Reason` and, when selected,
`X-Hormuz-Scorecard-SHA256`. The selected reason is `benchmark_scorecard`.
Missing, corrupt, expired, differently owned, changed-route or unmatched evidence
retains existing admission behavior. It never adds a cloud permission. Opaque
and tool continuations retain their successful model and destination affinity,
even if the report is deleted or changes.

Reports expire after seven days. Route fingerprints include endpoint, protocol,
model/revision, capabilities and configured prices. Stable facts can survive a
normal reconnect; live conversation affinity remains bound to its own generation.
The report records model/revision identity, destination, OS, architecture,
logical CPU count and available physical-memory facts. These are observations,
not hardware or endpoint attestation.
Rebenchmark after hardware, runtime, model, pricing, prompt or workload changes.

## Local data and compatibility

Suites contain the task text and references you supply. Scorecards contain case
labels/digests, observations, metric summaries, targets and binding metadata;
they omit task text, reference values, model reply text and credentials. Reports
and staged configurations are private mode-0600 files and never overwrite an
existing target. Reading rejects symlinks, unsafe files and oversized documents;
suites and provider replies are limited to 256 KiB and aggregate scorecards to 2 MiB.
Existing separately consented history collection remains governed by its own
history settings. No benchmark data is uploaded or used to train a classifier.

These are user-owned, unsigned local reports. Validation detects malformed and
internally inconsistent summaries; it cannot prove that someone controlling
the local account supplied honest references or outcomes. Scores express
performance on the declared cases, not universal model quality.

The suite, scorecard, runtime-snapshot, routing-configuration and error receipts start at schema
version 1 and appear in `hormuz contract manifest`. The
[compatibility fixture](../tests/fixtures/model-benchmark-v1.json) freezes the
initial shape. Scorecard-enabled and benchmark-probe relay metadata uses version
4, including observed `X-Hormuz-Benchmark-Request-Bytes` and
`X-Hormuz-Benchmark-Max-Output-Tokens` on probes. Ordinary configurations without
scorecard routing retain existing versions. Reconnect after upgrading the router
before running probes; an older running process cannot provide the version-1
runtime snapshot. Existing SQLite and AI Work storage need
no migration. Re-run into a new report path to retain earlier comparisons.

For rollback, disconnect with the current binary, remove `route_admission.scorecard`
or select the earlier configuration, and reconnect. Retain or delete the local
suite/report files as desired. An older binary will reject the new configuration
field and cannot interpret the new receipts; remove the field before downgrading.
