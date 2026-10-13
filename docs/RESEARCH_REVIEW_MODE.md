# Research Review mode

Planning date: 2026-10-13. Parent: [desktop epic #479](https://github.com/Xpounder-com/hormuz/issues/479).
The [integrated UI gate #492](https://github.com/Xpounder-com/hormuz/issues/492)
must include the simulated review journeys before any exposed runtime mode is
implemented. This document supplements the
[desktop roadmap](ELECTRON_DESKTOP_ROADMAP.md) and
[project UI plan](DESKTOP_UI_PLAN.md). Focused delivery is tracked in
[#502 — Research Review UI/spec/simulation](https://github.com/Xpounder-com/hormuz/issues/502)
and [#503 — bounded Research Review runtime](https://github.com/Xpounder-com/hormuz/issues/503).

This is a product and execution contract, not an implemented research engine.
It starts no model, provider request, real project-file read, account cleanup,
deployment or infrastructure change. Simulations and later application/runtime
implementation each follow their explicit work order. A model review is not
scientific validation, a truth guarantee or deployment authority.

## Ready to start the UI

The existing roadmap is sufficient to begin a bounded UI work order. Start with
the selected glossy visual language and project-window/quick-bar handoff, then
project/chat/context, model setup, connections and preferences. Research Review
adds a review surface inside the project window; it does not add several model
controls to the minimal bar or force research review on ordinary chat.

The first mock slice reviews an existing draft, with synthetic sources and two
distinct reviewer identities. Show the selected scope, role plan, privacy,
whole-round cap, progress, findings and version-specific human decision. Add
optional authoring and reconciliation as explicit variants of that flow.
Complete the integrated simulation in #492 before final host/data contracts and
real execution. Current simulations and papers do not satisfy this gate by
themselves.

Before runtime work, assign an implementation owner; record the approved finish
and journeys; select exact model/provider versions and protocol envelopes;
freeze storage/context limits, custody and the role/budget contract; and name
the OS/architecture/desktop qualification candidates. Backend packaging and
installed-platform evidence remain required for a supported release. None of
these later decisions requires Render for local or direct-provider operation.
Account and infrastructure cleanup follows the separate consumer/data/cost and
retirement work in [#494](https://github.com/Xpounder-com/hormuz/issues/494); it is
not a UI prerequisite and this review mode neither deletes nor activates an
account or service.

## Bounded workflow

Two entry points share the same review contract:

| Entry point | One explicit round | Maximum model calls |
| --- | --- | --- |
| Review an existing draft — default | Freeze selected draft and source packet → two blind reviews → findings/disagreement summary → human decision | Two reviews; at most one optional model reconciliation, for a maximum of three calls. A deterministic findings summary needs no extra model call. |
| Draft and review — optional | One author drafts from the selected packet → freeze that result → two blind reviews → at most one author reconciliation → human decision | Four calls across at most three selected model identities: author, reviewer A and reviewer B. |

There is one reviewer pass per round. Do not repeat calls until the models agree,
hide failed reviews behind retries, use majority voting as the sole approval
rule, or generate speculative parallel answers. The UI exploration permits at
most **two explicitly started mock rounds**; it never starts round two merely
because round one found problems. A later runtime declares a finite configured
round/call/output/time limit and requires fresh user intent plus budget admission
for another round. Ordinary chat remains available independently.

Reconciliation is optional. It may organize or respond to findings, preserving
accepted, rejected-with-reason, deferred and unresolved dispositions. It cannot
change the reviewers' original verdicts or imply that they reviewed a revised
draft. An additional model invocation, including reconciliation, counts against
the declared call and whole-round budget.

## Scope and evidence packet

Reviewing a draft is not reviewing an entire project. Before Send, the person
selects an explicit manifest containing:

- Target artifact and immutable revision, or the authoring question and
  completion criteria.
- Selected project chats, files, instructions and source excerpts, with their
  own revisions and locators. Project membership does not imply inclusion.
- Declared review dimensions, such as factual support, logical consistency,
  method/assumptions, missing evidence, limitations and the proposed next step.
- Explicit exclusions, inaccessible sources, unsupported formats and any
  scope that exceeds the qualified context budget.

“Whole project” requires a visible manifest and coverage summary. No silent
folder crawl, recursive source ingestion, retrieval, browser research, file
editing, code execution or connector access is introduced by this mode.
Unknown or excluded material stays unreviewed; a successful review of selected
text cannot become an all-project approval. The first runtime scope uses the
already qualified bounded text-context subset. Additional document parsing or
source retrieval needs its own explicit contract and qualification.

Each finding carries its reviewer identity/version, target artifact revision,
artifact section/locator, fixed severity/status, explanation, supporting source
IDs/locators and suggested resolution where available. Distinguish:

- A citation supplied by a model.
- An accessible source matching that citation.
- A located passage that supports the particular claim.
- A claim independently checked by a recorded qualified method.

A plausible link, citation agreement, or a provider response does not establish
support or verification. Missing evidence, contradictions and reviewer
disagreement remain visible. Never invent a verified source badge from a model's
assertion. The mock uses fictional source packets with explicit simulated
checks; it makes no real research-validation claim.

## Reviewer separation and provenance

Both reviewers receive the same frozen target, criteria and selected source
packet. Before recording their initial verdicts, they see neither the other
reviewer's answer nor a reconciliation or majority verdict. They run in fresh
isolated sessions; unrelated chat history, project content and previous review
rounds are not silently carried over. Author/source text remains untrusted data
and cannot enlarge application permissions, change role assignments or override
privacy/budget policy.

Choose distinct verified reviewer model identities. Compare actual configured
model/version/origin fingerprints, not display names or aliases. Two aliases,
two sampling seeds, or two instances of the same model/version do not count as
distinct model reviewers. If author provenance is known, that author identity
cannot fill a reviewer slot. The author can reconcile after the blind verdicts,
but its reconciliation is not a third independent review.

Unknown author or provider/model provenance is recorded as unknown; do not claim
verified independence that cannot be established. Even distinct models and
fresh sessions can share training, failure modes or unsupported assumptions.
“Blind review” describes the application's input separation, not statistical
independence or scientific peer review. Unavailable eligible reviewers produce
an incomplete plan with setup/selection remedies, not a duplicate disguised as
another reviewer.

## Version-specific state and human decision

The review record freezes local profile/project/chat identity, target and source
revisions, selected instructions and criteria, actual role/model versions,
privacy/budget intent and attempt lineage. Navigation changes presentation;
output and cancellation stay attached to their captured origin. Local project
or review IDs never confer managed actor, organization or AI Work authority.

Use honest states: Draft, Reviewing, Changes requested, Review incomplete,
Review round complete, Ready for your review, Human approved and Review stale.
Show which version each reviewer evaluated and which version is now displayed.
A timeout, failure, cancellation, invalid response, missing reviewer or budget
refusal cannot become unanimous approval. A completed round can still contain
blockers, unsupported claims or disagreement.

Any material edit to the artifact, source packet, included context, criteria or
effective review settings invalidates the relevant review/approval status.
Preserve the old results as history tied to their original revisions. A
reconciled v2 is not independently reviewed merely because reviewers examined
v1. If reconciliation changes the target, mark the current draft unreviewed or
stale and offer an explicit new bounded round. If it only summarizes findings
without changing the target, retain the original version and dispositions.

The person may explicitly accept a named revision with disclosed limitations,
including unreviewed changes. Record that as a human decision with its scope,
not as fresh model approval. Human approval of an earlier version never carries
forward to a later version automatically. Review comments are not approval of
every selected file or of future work.

The UI may say **Ready for your review** when the required checks and verdicts
are complete for the displayed version; it must disclose outstanding findings
and limitations. Only a deliberate human action records Human approved. Avoid
“all models prove this correct” or an automatic project-wide green light.
Neither status launches implementation, publishes work, deploys a service,
spends beyond the approved envelope or grants an agent new permissions.

## Eligibility, execution and accounting

Auto may propose eligible role assignments and their local/cloud locations from
a bounded snapshot. This preview remains pure: no classifier/provider request,
model download/load, ledger mutation or budget allocation. Role overrides are
explicit intent, constrained by the same policy/privacy/capability/budget rules
as ordinary model pins. Model selection cannot weaken reviewer separation.

Before starting a real round:

1. Validate scope/revisions and all role/model/context/protocol requirements.
   Freeze the plan and disclose every role's egress and quoted upper estimate.
   If an eligible independent reviewer is missing, do not silently substitute.
2. Enforce local-only across author, reviewers, reconciler and any helper.
   Local-only cannot upload the source packet or fall back to a cloud reviewer.
   Selecting “Review” does not authorize online source retrieval or downloading
   a new model from the typing preview.
3. Admit a defensible **whole-round upper budget** covering maximum inputs,
   outputs and the optional reconciliation, alongside per-request and applicable
   parent caps. Direct-provider versus managed-workspace authority remains
   explicit; there is no mandatory hosted account.
4. Preserve individual governed attempt/reservation/settlement records and a
   bounded round-level link. Estimates, known actuals and uncertain holds remain
   distinct. A failed or interrupted attempt is not assumed free. Do not create
   a second learning or finance ledger that contradicts existing owners.
5. Bound role execution, output, timeouts and cancellation. Under the first
   one-loaded-local-model policy, local roles swap/load sequentially under exact
   owned handles; the UI shows that wait. No hidden concurrent multi-model
   residency or unlimited worker fan-out. Metadata/download and inference
   channels retain separate resource controls.
6. Never replay an ambiguous request or partial stream automatically. Existing
   narrowly qualified capacity-fallback semantics, if exposed, still require
   separate admission and preserved lineage and must respect the role identity
   and total call envelope. A replacement that breaks diversity is incomplete.
7. Save project review content only through the approved local content store.
   Temporary review stays in memory. Prompts, drafts, source excerpts and review
   bodies do not enter content-free gateway/work-learning/billing evidence;
   credentials never enter either store or an exported review bundle.

Capture cancellation and per-role success/failure separately. Stopping a round
prevents unstarted roles; it does not erase already incurred/uncertain cost or
assert that a provider canceled already-dispatched work. Failed roles and
unresolved findings stay visible to the human decision.

## Focused delivery and qualification

The [UI slice #502](https://github.com/Xpounder-com/hormuz/issues/502) depends on
the project/context/preferences mock contracts and
selected visual language; its Review journeys feed #492. It does not wait for
a live engine, paid provider, storage implementation or runtime orchestration.
Its fixtures should cover:

- Existing draft reviewed by two distinct identities without authoring.
- Optional authoring and one optional reconciliation within the four-call cap.
- Blind initial verdicts, substantive disagreement and unsupported citations.
- Known author selected as reviewer, same-version aliases, unknown provenance
  and no eligible distinct reviewer.
- Local-only rejection of a cloud role, whole-round cap refusal, canceled or
  failed reviewer, and partial/uncertain attempt status.
- Explicit project manifest, omitted/inaccessible material and scope coverage.
- Project switching during review, v1 findings versus a reconciled/edited v2,
  stale human approval and an explicitly started second mock round.
- Honest Ready for your review and revision-specific human approval without
  publishing or starting implementation.

The [runtime slice #503](https://github.com/Xpounder-com/hormuz/issues/503) follows
the baseline #493 context store, #483 local engine,
#485 paid/streaming execution and #484 explicit routing intent contracts, plus
relevant #339 ownership. Their qualified APIs are inputs; no runtime closure
cycle should require an exposed Research Review mode to qualify plain chat.
The runtime is optional after the ordinary baseline, but is required before the
product exposes real Review execution. If not qualified, show an explicitly
simulated or unavailable mode rather than a functional claim.

Validate provider-free orchestration, role separation, same-version refusal,
budget/identity/context races, cancellation, restart/unknown outcomes and stale
approval first. Later authorized, small spend-capped model/provider checks and
exact installed-platform journeys provide separate runtime evidence. For
quality evaluation, compare matched tasks against the single-model baseline
with a known reference or explicit human rubric; record cost, latency, missed
and false findings, limitations and disagreements. No review-count or consensus
metric alone establishes correctness, customer value or research maturity.

## Research motivation and limits

Du et al. report improvements in factuality and mathematical/strategic reasoning
for their tested multiagent-debate procedure. Those results motivate trying
multiple model perspectives; they do not establish Hormuz's selected models,
research tasks, review UI or bounded procedure. [Improving Factuality and Reasoning
in Language Models through Multiagent Debate](https://arxiv.org/abs/2305.14325)

Cui et al. describe conformity and error-propagation risks in consensus-focused
debate, and report benchmark results for a consensus-free, single-round method.
This motivates preserving blind initial judgments, disagreement and bounded
rounds instead of treating agreement as success. Hormuz does not implement or
inherit that paper's scoring method merely by citing it.
[Free-MAD: Consensus-Free Multi-Agent Debate](https://arxiv.org/abs/2509.11035)

These papers are design motivation, not Hormuz validation. Qualification still
requires the exact implemented role/version/context/budget contract and honest
task-level evidence. Human approval remains a decision about the declared
revision and scope, with fallible model assistance.
