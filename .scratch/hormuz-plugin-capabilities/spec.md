# Koto plugin capabilities

Status: ready-for-agent
Labels: ready-for-agent

## Problem Statement

An individual developer connecting a coding client to local models needs more than a successful setup command. They need to know whether the connection works, whether a model can perform their task, where an observed request ran, which policy applied, what usage was measured, and how to recover or restore their settings. Unclear coverage, estimated prices, and an untested model can otherwise look like verified privacy, savings, or quality.

Koto already has a shared connection workflow and optional local history. Its plugin entry point needs focused workflows that make those underlying capabilities understandable and expose useful evidence without taking over the coding client's agent loop.

## Solution

Develop a coherent plugin experience across seven capability areas: setup diagnosis, request explanations, model selection, model qualification, usage insights, privacy and budget controls, and maintenance/restoration.

Design first for an individual developer using Codex CLI on macOS. The plugin guides workflows; the customer-local router enforces supported policies for intercepted model requests. The coding client retains editing, tools, permissions, conversation, and the agent loop.

Define release readiness through five demonstrated user outcomes: connect successfully, diagnose a failure, understand an observed routing decision, qualify a model for defined tasks, and restore owned configuration. Determine the exact first-release depth and deferred capabilities through the existing decision map. Diagnosis, explanations, and qualification are initial candidates rather than a settled release selection.

## User Stories

1. As an individual developer, I want Koto to identify the target coding client and its compatibility, so that I know whether my setup is supported.
2. As an individual developer, I want clear prerequisite checks, so that missing software, credentials, or model assets have concrete recovery steps.
3. As an individual developer, I want to preview connection changes, so that I understand what my setup request will change.
4. As an individual developer, I want matching authorized changes applied without repeated approval requests, so that setup requires little manual intervention.
5. As an individual developer, I want connection readiness distinguished from model readiness and task completion, so that a successful setup is not mistaken for successful work.
6. As an individual developer, I want to see the connected-client scope, so that I know which model requests Koto can observe and govern.
7. As an individual developer, I want relevant read-only diagnosis, so that I can understand a failure without changing my configuration.
8. As an individual developer, I want configuration conflicts and overrides identified, so that I can understand why expected routing did not occur.
9. As an individual developer, I want router failures distinguished from model failures, so that I can take an appropriate recovery action.
10. As an individual developer, I want occupied resources handled safely, so that unrelated applications and processes are preserved.
11. As an individual developer, I want an observed request linked to its execution destination, so that I can understand where the work ran.
12. As an individual developer, I want the routing policy and any fallback explained, so that I can assess whether the request followed my intent.
13. As an individual developer, I want available latency and usage evidence shown, so that I can judge performance from observations.
14. As an individual developer, I want optimizations explained only when supported by evidence, so that pass-through requests are not reported as optimized.
15. As an individual developer, I want missing or incomplete evidence stated, so that uncertainty is visible.
16. As an individual developer, I want model recommendations to consider my hardware and existing assets, so that suggested options are practical.
17. As an individual developer, I want model suitability tied to tested task requirements, so that fitting in memory is not confused with being capable.
18. As an individual developer, I want downloads kept within an explicitly authorized scope, so that recommendations do not silently consume storage or bandwidth.
19. As an individual developer, I want bounded qualification checks for tool use, structured output, and representative task quality, so that local-route suitability has evidence.
20. As an individual developer, I want qualification results to identify the tested model, runtime, client, and task envelope, so that their applicability is clear.
21. As an individual developer, I want relevant setup changes to invalidate stale qualification, so that historical success does not silently authorize a changed setup.
22. As an individual developer, I want unsupported tasks and unqualified routes handled explicitly, so that Koto does not invent suitability.
23. As an individual developer, I want evaluations bounded by resource and time limits, so that qualification does not become a load test.
24. As an individual developer, I want paid evaluations separately scoped, so that qualification cannot silently incur charges.
25. As an individual developer, I want local-only policy enforced for intercepted requests, including helpers, so that unavailable local execution cannot silently become cloud execution.
26. As an individual developer, I want cloud fallback governed by explicit authorization, so that an operational failure does not expand my privacy permissions.
27. As an individual developer, I want privacy and budget controls to distinguish enforced behavior from advice, so that I can rely on the promises actually supported.
28. As an individual developer, I want history capture to require separate opt-in, so that connecting a coding client does not imply consent to retain text.
29. As an individual developer, I want metadata-based usage insights with stated coverage, so that I understand what the report includes and misses.
30. As an individual developer, I want spending estimates separated from invoices and unknown costs, so that a configured price is not mistaken for a confirmed charge.
31. As an individual developer, I want recommendations linked to observed usage, so that suggested changes address a measured problem.
32. As an individual developer, I want before-and-after measurements to state their limits, so that speculative counterfactual savings are not presented as observed savings.
33. As an individual developer, I want to disable capture, delete retained entries, and export locally, so that I retain control of my local history.
34. As an individual developer, I want retained text kept out of ordinary agent results, so that local history is not inadvertently forwarded to a remote model.
35. As an individual developer, I want compatibility checked before maintenance changes, so that updates do not silently break a working connection.
36. As an individual developer, I want disconnect to restore owned configuration while preserving my newer edits, so that cleanup does not destroy unrelated preferences.
37. As an individual developer, I want cleanup to distinguish owned and externally managed processes, so that Koto stops only resources it owns.
38. As an individual developer, I want incomplete recovery and shutdown reported as pending, so that I am not told cleanup succeeded prematurely.
39. As an individual developer, I want focused plugin workflows using the same operations as the CLI, so that behavior is consistent across entry points.
40. As an individual developer, I want installation distinguished from active routing, so that merely loading a skill does not imply interception is enabled.

## Implementation Decisions

- The specification covers all seven candidate capability areas. Exact release selection remains an explicit decision; the user has not selected every candidate for immediate implementation.
- The initial supported audience is Codex CLI on macOS. Host compatibility and target coding-client routing compatibility are separate. Historical version pins must be rechecked against the chosen implementation baseline.
- Reuse the shared CLI and connected-client lifetime for setup, status, recovery, disconnect, and conditional configuration restoration. Plugin workflows must not introduce a second configuration owner.
- Preserve the domain distinction among coding client, router, execution destination, qualified local route, local-only policy, automatic routing, connected-client lifetime, local history, and spending estimate.
- The current glossary describes the plugin as a skills-only entry point. MCP tools and an optional interface are proposals requiring an explicit interaction-model decision, not existing capabilities or approved architectural changes.
- The coding client retains its agent loop, permissions, tools, and conversation. Installing the plugin does not activate routing or prove that unrelated tools and client surfaces are intercepted.
- Read-only diagnosis and explanations may run when relevant. Configuration changes require a preview and authorization through the user's request; matching authorized changes do not need redundant confirmation.
- Cloud fallback, downloads, history capture, and paid evaluations require their own explicit scope. Local-only denial must not be treated as authorization to change policy.
- Router readiness, model readiness, qualification, and completed work are separate observable states. Claims must identify which state has evidence.
- Model qualification is limited to tested task envelopes and the tested setup. Checks cover tool use, structured output, and representative task quality, within explicit resource/time limits. Relevant client, runtime, or model changes can make results stale.
- Preserve execution-destination and continuation guarantees when recommending routing changes. Do not infer that an ongoing conversation can safely migrate from a recommendation alone.
- Report observed usage, configured-price spending estimates, unknown costs, and coverage separately. Token or cost reduction is a measured outcome where evidence exists, not a release-wide guarantee.
- Keep history consent distinct from connection authorization. Reuse the existing device-local history controls and privacy boundary; retained text must not appear in ordinary agent results.
- Reuse existing policy, admission, accounting, and evidence modules where applicable. Exact receipt schemas, retention for explanation metadata, qualification thresholds, budget semantics, and automatic remediation rules remain open in the decision map.
- Maintenance preserves unrelated settings and newer user edits, acts only on owned resources, and distinguishes pending cleanup from completed restoration.

## Testing Decisions

- The user confirmed the shared CLI workflow as the primary verification boundary: public commands and receipts, isolated client homes, fake model endpoints, and a small opt-in live smoke test.
- Prefer one high-level seam exercising the actual configuration, connected-client lifetime, router, policy/admission, transport, evidence/accounting, and temporary storage. Replace external coding-client executables and model services with disposable stand-ins rather than mocking the feature's own decisions.
- Existing connected-client lifetime workflow tests are prior art for preview, connection, process ownership, restoration, and user edits. Existing execution-destination workflow tests exercise connect, client request, result, and disconnect. Existing local-history workflow tests exercise consent, public metadata, capture, review, export, and deletion.
- Assert external behavior: public outcomes, receipts, observable configuration changes, routing destination, denied cloud requests, preserved user edits, and released resources. Avoid assertions on private helper calls or implementation structure.
- Cover the five required success scenarios and meaningful failure paths: incompatible client, unavailable engine, occupied endpoint, overridden settings, unsupported task, stale qualification, policy denial, incomplete evidence, interrupted setup, and pending cleanup.
- Verify secrets and retained prompt/response text do not leak into ordinary status, diagnosis, explanations, or insights. Verify history remains disabled without explicit consent.
- Qualification tests must include examples that fail each supported capability requirement; a health response or syntactically valid output alone must not establish task quality. Exact acceptance thresholds remain a decision prerequisite.
- Cost reporting tests distinguish known configured estimates from unknown costs and incomplete coverage. Savings tests require measured inputs and a declared comparison basis.
- Use mocked external network dependencies for routine tests and bounded loopback fixtures for workflow integration tests. Use explicit timeouts, bounded concurrency, and cleanup for every owned client, socket, worker, and subprocess. No load tests or continuous network polling.
- An opt-in live smoke test may establish the selected supported client/runtime/model combination. It must not replace deterministic failure-path tests or be reported as universal compatibility evidence.
- No implementation tests were run while writing this specification; the prior art was inspected as source.

## Out of Scope

- Implementing, deploying, publishing, or automatically activating the capabilities as part of this specification task.
- Replacing the coding client's agent loop or supplying general-purpose coding skills.
- Team administration and additional client routing adapters in the initial release.
- Universal model-quality claims, guaranteed savings, provider-invoice claims from configured prices, or assertions of interception outside the supported connected-client scope.
- Silent cloud fallback, automatic history opt-in, unscoped downloads, paid evaluations without authorization, or changes to unrelated processes and settings.
- Treating proposed MCP tools, interface designs, autonomous optimization, or exact release ordering as already approved.

## Further Notes

- This spec synthesizes the conversation; it does not resolve the nine open decision tickets. The decision map remains the canonical index for their resolutions.
- The requested ready-for-agent triage marker is applied to this synthesis artifact. It is not evidence that every feature is implementation-ready: release selection, evidence contracts, qualification thresholds, policy/budget details, insights, interaction design, and release acceptance details remain tracked decisions. An implementation agent must respect those prerequisites rather than silently choosing product answers.
- Publish through the established local Markdown tracker. The sibling map and its child tickets retain their wayfinder-only labels and open status.
- Source orientation used the inspected local history/insights worktree rather than assuming the dirty primary checkout represents the latest release. Recheck source baseline and compatibility before implementation.
- Optional focused skills, structured operation tools, and a compact status view were discussed as directions. Their final packaging belongs to the interaction-model decision.
