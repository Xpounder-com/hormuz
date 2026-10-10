# ADR 0014: Customer-local AI work and opt-in exact answer reuse

- Status: Accepted implementation; production qualification remains separate
- Date: 2026-10-08
- Decision owner: Product owner
- Approval record: the product owner explicitly requested implementation of the
  agreed product and webpage list in this task on 2026-10-08. This records that
  conversation authorization; it does not impersonate a GitHub approval comment.
- Supersedes ADR 0003 only for the new `ai_work` contract

An explicitly enabled, single-node work runtime associates authenticated
provider attempts, capacity fallbacks and exact answer reuse with an owned job.
Workspace and repository plans cover a UTC month; job plans cover its lifetime.
Reservations remain charged against available allowance until an actual cost is
known. A provider response is not evidence that the customer's work completed.

The router reads bounded local profile snapshots and applies the customer's
cost, speed or outcome preference inside existing client/model/provider policy.
Profiles use comparable submitted workflow observations, configured model
fingerprints and request capability signatures. These are observational signals;
they do not certify model quality or causal savings. Missing evidence selects the
approved baseline. Continuing tool/provider sessions preserve model affinity.

Answer reuse is a new, explicit opt-in contract. It requires an authenticated
actor-owned job, supplied context revision, deterministic text request, exact
canonical request match, matching model/provider/policy, and an unexpired entry.
Response bodies live only in bounded process memory, never in this SQLite
ledger. Corrections and reopening invalidate reuse. The gateway checks policy
and secret redaction before reuse. Streams, tool actions, opaque provider state,
refusals and truncated outputs bypass it. Semantic answer reuse is unsupported.

Provider prefix-cache usage remains a separate provider-reported token category.
A high answer-cache hit rate cannot establish a successful job. Exact recurrence
is a possible rework signal with explicit exclusions and unknown quality.

This does not revive the archived experimental context-pack cache. See
[AI_WORK_RUNTIME.md](../AI_WORK_RUNTIME.md) for the supported configuration,
limits, interfaces and recovery boundary.
