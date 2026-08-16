# ADR 0007: Accepted-task economics evaluation

- Status: **Proposed — owner approval required**
- Date proposed: 2026-08-16
- Decision owner: Product owner
- Tracking issue: [#15](https://github.com/Xpounder-com/hormuz/issues/15)
- Unblocks after acceptance: implementation of the accepted-task evaluation runner and release gate under [#15](https://github.com/Xpounder-com/hormuz/issues/15)

## Decision requested

Choose the evidence rule Hormuz will use before claiming that governed context lowers the cost of successful engineering work without weakening quality:

1. **Matched-task evaluation with paired task-cluster resampling and predeclared non-inferiority gates — recommended.** Compare the ordinary-context and Hormuz-context arms on the same frozen tasks and model settings. Estimate uncertainty by resampling whole task clusters, not individual requests. Require a one-sided confidence bound to clear owner-approved practical cost, quality, and time margins, with separate model and cold/warm results plus hard safety stops.
2. **Point estimates with fixed thresholds.** Run the same matched experiment but pass or fail on observed percentages and averages without uncertainty intervals.
3. **Bayesian hierarchical decision rule.** Model task, cohort, and provider effects jointly and pass on predeclared posterior probabilities.
4. **Descriptive evidence only.** Publish cost, quality, and time distributions without allowing the evaluation to clear a release claim.

This ADR proposes option 1. It does not approve a statistical margin, implement the evaluator, or authorize a product claim until the product owner approves the method and later approves the numeric release thresholds before treatment results are unblinded.

## Context

Tokens per request are a consumption measure, not a measure of successful engineering work. A context system can reduce input tokens while increasing retries, review time, CI cycles, defects, or rollbacks. Hormuz therefore needs one reproducible evaluation contract that accounts for the full cost of accepted work and treats quality and safety as release constraints.

Issue #15 already requires frozen pre-fix repositories and memory snapshots, matched model/tool/prompt settings, separate cold and warm cohorts, at least two customer-selected models or providers, broad cost accounting, and a predeclared non-inferiority or improvement rule. The remaining decision is how observations are matched, how uncertainty is estimated, how heterogeneous cohorts are combined, and which failures stop the experiment.

NIST distinguishes point estimates from interval estimates, describes bootstrap methods as an empirical way to estimate uncertainty when analytic intervals are difficult, and cautions that statistical and practical significance are different. See the [NIST Engineering Statistics Handbook](https://www.itl.nist.gov/div898/handbook/eda/section3/eda35.htm). FDA non-inferiority guidance is written for clinical trials rather than software evaluation, but its core governance lesson is applicable here: the margin and hypothesis must be chosen deliberately before the outcome is known. See [Non-Inferiority Clinical Trials](https://www.fda.gov/regulatory-information/search-fda-guidance-documents/non-inferiority-clinical-trials). NIST information-retrieval research also supports reporting confidence intervals and preserving replicate structure when comparing systems; see [Computing confidence intervals for common IR measures](https://www.nist.gov/publications/computing-confidence-intervals-common-ir-measures) and [Using Replicates in Information Retrieval Evaluation](https://www.nist.gov/publications/using-replicates-information-retrieval-evaluation).

Those sources motivate uncertainty-aware evaluation. The exact paired task-cluster design below is Hormuz's proposed engineering decision, not a claim that any source mandates this specific implementation.

## Proposed decision

### Frozen experiment specification

Every evaluation starts from a versioned experiment specification committed before treatment outcomes are inspected. It defines:

- a corpus version and keyed fingerprints for each task, pre-fix repository snapshot, memory snapshot, hidden acceptance checks, and blind review rubric;
- the comparator arm using the customer's ordinary context path and the treatment arm using Hormuz governed context;
- any optional naive-retrieval diagnostic arm, which cannot replace the comparator in the release decision;
- selected provider, actual model, client, tool profile, prompt template, repository revision, limits, sampling settings, retry policy, context budget, and replicate count;
- cold-start and warm-memory cohort definitions;
- the statistical implementation/version, deterministic bootstrap seed, resample count, stratum weighting, confidence level, and numeric release margins;
- cost-rate and infrastructure-cost sources, review/rework valuation, currency, and price-table versions; and
- every quality, safety, privacy, authorization, and latency stop condition.

The specification is immutable for one evaluation ID. Any change creates a new evaluation version. A blinded baseline-only planning run may estimate variance and inform sample size or proposed practical margins, but it cannot inspect treatment-arm outcomes. Once the product owner approves numeric margins, sample size, and weights, they cannot change for that evaluation.

### Matched assignment and complete blocks

The unit of evaluation is an engineering task, not a provider request. Each task is run in both comparator and treatment arms within the same predeclared model, cohort, and replicate block.

A block is matchable only when both arms have identical keyed fingerprints for task, pre-fix repository and memory snapshots, prompt template, tools, limits, sampling settings, provider, actual model, client, replicate, and hidden acceptance contract. A mismatch is an invalid block, not a lossy best-effort join. Missing arms, provider substitutions, exhausted budgets, infrastructure failures, and invalid blocks are reported explicitly and cannot silently disappear from denominators.

The final answer or accepted patch from any arm may not enter another arm's repository, memory snapshot, prompt, retrieval index, or reviewer material. Arm execution and writeback are isolated. Final-answer leakage is a hard experiment failure.

### Primary estimands

Hormuz reports three co-primary outcomes:

1. **Net cost per verified accepted task:** total eligible experiment cost across all attempts in an arm divided by the number of tasks that satisfy the frozen acceptance contract. Failed and retried tasks remain in the numerator. If an arm accepts zero tasks, its value is undefined/infinite and the arm fails the release gate.
2. **Verified first-pass success rate:** the share of assigned tasks that pass deterministic checks and the frozen blind review gate before human correction or an additional model attempt.
3. **Time to accepted change:** median and p90 elapsed time from the frozen task start to all acceptance gates passing. Acceptance/completion rate and censored or timed-out tasks are always reported beside these statistics so a fast surviving subset cannot hide failures.

Cost is represented in exact micro-units of the configured currency and includes:

- provider inference, including input, output, cached, cache-write, and reasoning categories when authoritatively exposed;
- embeddings, summarization, and other memory-system model calls;
- provider and Hormuz cache operations;
- allocated memory/storage/compute infrastructure;
- CI execution; and
- measured human review and rework under the experiment's predeclared valuation rule.

Provider-reported or reconciled cost, rate-card-derived estimated cost, and proportionally allocated shared cost remain separate fields. The report must not relabel allocated estimates as final per-request billing.

### Uncertainty and release decision

The evaluator uses a deterministic paired nonparametric bootstrap with at least 10,000 resamples. Each resample draws whole task clusters with replacement. All arms, model/provider strata, cold/warm cohorts, and replicates for a selected task travel together so paired comparisons and within-task dependence are not destroyed.

The release report includes point estimates, one-sided 95% confidence bounds used by the gate, and two-sided 95% intervals for interpretation. It evaluates practical effect sizes rather than a null-hypothesis p-value alone.

Before treatment outcomes are unblinded, the product owner must approve:

- the minimum acceptable relative reduction in net cost per verified accepted task;
- the maximum acceptable treatment-minus-comparator loss in verified first-pass success rate;
- maximum acceptable median and p90 time regressions;
- the required task count derived from a baseline-variance and power analysis;
- the weighting of model/provider strata; and
- nonzero thresholds, if any, for non-security guardrails.

The proposed release rule is conjunctive:

- the lower one-sided confidence bound for relative net-cost reduction exceeds the approved cost-improvement margin;
- the lower one-sided confidence bound for the treatment-minus-comparator first-pass-success difference is above the negative approved non-inferiority margin;
- the relevant upper confidence bounds for median and p90 time regression stay within their approved margins;
- cold and warm cohorts are reported separately and neither may fail quality, safety, or privacy gates;
- every selected model/provider stratum is reported separately and none may fail quality, safety, or privacy gates; and
- all hard-stop guardrails pass.

The overall release estimand uses equal predeclared weight per selected model/provider stratum so a high-volume or inexpensive model cannot hide failure in another selected model. Production-mix weighting may be reported as a secondary planning view, but cannot replace the equal-weighted release gate without a new owner-approved experiment specification.

If any co-primary statistic is undefined, unstable, underpowered, or missing a required stratum, the result is **inconclusive**, not pass. Failure to demonstrate non-inferiority is not proof of inferiority; the evidence report preserves that distinction.

### Guardrails and stops

The following observations stop the experiment and make the evaluation ineligible for a release claim:

- final-answer or accepted-patch leakage between arms;
- cross-organization, cross-team, actor, repository, branch, revision, or clearance authorization leakage;
- use of context that was already stale, invalidated, contradictory without warning, or quarantined under the frozen policy;
- prompt, response, source, patch, reviewer-text, credential, or secret leakage into routine usage, audit, log, metric, error, or publishable result artifacts; or
- a changed experiment specification, hidden acceptance contract, rate card, or statistical rule after treatment unblinding.

Unsupported assertions, escaped defects, reopen/rollback, failed CI, context-assembly latency, evaluator errors, and provider/client coverage gaps are measured per the frozen contract. The product owner may designate additional zero-tolerance or numeric stop thresholds before the trial.

### Content-free evidence boundary

The controlled evaluation plane necessarily reads task prompts, repositories, candidate patches, model output, tests, and reviewer material. Access to that content is explicit, least-privilege, separated by arm, and governed by corpus-specific retention. It is not copied into the routine Hormuz usage ledger.

The machine-readable release result and concise evidence report are content-free by default. They may contain:

- opaque evaluation, task, arm, cohort, provider/model, and replicate IDs;
- keyed fingerprints and version identifiers for the corpus, snapshots, prompts, tools, policies, manifests, rate cards, statistical implementation, and acceptance contract;
- token categories, exact cost components and basis, durations, retries, tool/CI/review counts, acceptance and guardrail outcomes, aggregate effects, intervals, exclusions, and coverage; and
- safe enumerated failure codes and artifact hashes.

They exclude prompts, responses, source code, patches, test contents, file paths, repository URLs, branch names, commit messages, reviewer text, employee rankings, raw provider/conversation IDs, secrets, credentials, and unkeyed content hashes. Debug content requires a separate opt-in retention policy and is never a release artifact.

### Claims and interpretation

A passing offline replay supports only the exact frozen corpus, clients, model/provider versions, policies, and cost basis tested. It does not prove universal productivity improvement or causality in live organizations.

Hormuz keeps evidence labels distinct:

- **controlled replay:** matched frozen experiment under this ADR;
- **controlled live pilot:** predeclared randomized or otherwise controlled production evaluation;
- **associated:** observational usage linked to later engineering outcomes without controlled assignment; and
- **descriptive:** tokens, spend, requests, latency, or task movement with no outcome attribution.

Only a passing controlled evaluation may support an improvement claim. Historical dashboard associations and ticket movement cannot be described as caused by Hormuz.

## Alternatives considered

### Point estimates with fixed thresholds

This is simple and deterministic, but a small or noisy corpus can cross a threshold by chance and point estimates do not communicate uncertainty. Rejected as the release rule; point estimates remain part of every report.

### Bayesian hierarchical decision rule

A hierarchical model could share information across tasks and providers and express decisions as posterior probabilities. It also adds prior selection, convergence, model-checking, and explanation decisions before Hormuz has baseline data. Deferred until repeated evaluations justify the additional model-governance burden.

### Descriptive evidence only

This is honest and useful for early instrumentation, but it cannot decide whether a product claim cleared a quality-preserving economics gate. Retained as the outcome for incomplete, observational, or underpowered runs.

### Request-level resampling

Provider requests, retries, and tool calls from one task are dependent. Treating them as independent observations would inflate the apparent sample size and break the pairing between arms. Rejected.

### Pool every model and cohort into one headline

A pooled aggregate can hide a quality regression in one provider or warm/cold cohort. Rejected for the release gate. An explicitly weighted aggregate may be secondary, but strata remain visible and independently guarded.

## Consequences if accepted

- Hormuz gains a reproducible, uncertainty-aware standard for a commercially important product claim.
- Evaluation is more expensive than a token benchmark because it requires complete paired task blocks, hidden acceptance checks, isolated arms, multiple models/providers, and blind review.
- Release evidence remains portable and content-free while corpus content stays in a separate controlled plane.
- A directional 50–100-task pilot may inform variance and feasibility, but it cannot be called definitive merely because it reached that range; the approved power rule decides sample adequacy.
- Issue #15 remains open until the evaluator, corpus, multi-provider runs, privacy checks, and versioned release artifacts satisfy its acceptance criteria.

## Verification required after acceptance

- red-first tests for exact block matching, missing/duplicate arms, provider/model substitution, deterministic seeds, cluster preservation, incomplete strata, and denominator behavior;
- reference-vector tests for every metric, exact micro-cost component, relative effect, quantile, interval, confidence-bound direction, and pass/fail/inconclusive outcome;
- simulations covering no effect, target effect, quality inferiority, high variance, zero accepted tasks, censored time, sparse defects, and unstable intervals;
- cold/warm and model/provider stratification tests proving no pooled result can hide a guarded stratum failure;
- final-answer leakage, arm writeback isolation, stale/unauthorized context, hidden-contract mutation, and post-unblinding configuration-change hard-stop tests;
- filesystem, database, ledger, log, metric, error, wheel, and release-artifact scans proving excluded evaluation content is absent;
- a synthetic corpus with known outcomes plus a controlled organization-representative corpus under explicit access and retention policy;
- at least two owner-selected model/provider runs with frozen versions and documented client coverage;
- independent statistical review of the implemented estimator, power procedure, and release report before an enterprise claim; and
- source/wheel build-install checks plus existing Codex/Claude compatibility, privacy, lifecycle, retrieval, and strict benchmark gates.

## Owner approval record

Pending. Approval must identify option 1, 2, 3, or 4. For option 1, a later pre-unblinding approval must also record the cost, quality, time, sample-size/power, stratum-weighting, and guardrail thresholds for the first release evaluation.
