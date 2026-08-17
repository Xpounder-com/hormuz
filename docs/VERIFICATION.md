# Verification record

This file records executable evidence for client/provider compatibility. It intentionally contains no provider credentials, prompts, responses beyond fixed test markers, or employee secrets.

## 2026-08-15

### Live OpenAI path

Environment:

- Installed client: OpenAI Codex CLI 0.139.0.
- Gateway: local Hormuz checkout on loopback.
- Provider: live OpenAI Responses API using a project-scoped key stored in ignored `.env.local`.
- Requested and routed model: `gpt-5.4-mini`.

Observed result:

- Codex returned the fixed marker `HORMUZ_NATIVE_MODEL_OK` with exit code 0.
- Hormuz logged `action=allowed`, `status=succeeded`, and `routed_model=gpt-5.4-mini`.
- Provider-reported usage recorded by Hormuz: 16,184 input tokens and 36 output tokens.
- Rate-card estimate recorded by Hormuz: 12,300 micro-USD ($0.012300).
- The employee process received only `HORMUZ_TOKEN`; the OpenAI key was loaded only into the Hormuz process.

The installed Codex version also probed `/v1/models` and received a non-blocking 404. Codex then used its bundled native-model metadata and completed the Responses API request successfully. See [CLIENTS.md](CLIENTS.md) for the catalog boundary.

### Live secret egress and provider-storage policy

A direct Responses API smoke request supplied a fake OpenAI-shaped credential in the input and instructed the model to repeat the value it received. Observed result:

- Hormuz returned `X-Hormuz-Policy-Decision: allowed+redacted` and `X-Hormuz-Redactions: 1`.
- The live model returned only `[REDACTED:HORMUZ_SECRET]`, proving the original fake value was transformed before provider inference.
- Hormuz recorded 26 input tokens, 14 output tokens, one redaction, and an estimated 82 micro-USD.

A second live request deliberately sent `store: true`. Hormuz's default provider policy overwrote it, the OpenAI response reported `store: false`, and the model returned `HORMUZ_PROVIDER_STORAGE_OK`. A `background: true` request returned `403 hormuz_provider_policy_denied` before the provider path.

Installed Codex was then rerun through the same forced-`store: false` gateway path. It returned `HORMUZ_STORE_FALSE_OK` with exit code 0; Hormuz recorded 16,184 input tokens, 28 output tokens, and an estimated 12,264 micro-USD. This proves the storage safeguard remains compatible with the existing Codex CLI workflow.

### Official Claude Code client path

Command gate:

```bash
HORMUZ_RUN_CLAUDE_CLIENT_TEST=1 python3 -m unittest -v
```

Observed result:

- The suite downloaded and ran the official `@anthropic-ai/claude-code` executable through `npx`.
- Claude Code used `ANTHROPIC_BASE_URL` to call Hormuz's `/v1/messages` endpoint.
- Hormuz authenticated the employee token, applied the Claude Code policy, replaced the credential, routed the Anthropic Messages request, streamed the response, and recorded usage.
- The fake upstream asserted that it received the configured provider key and never received the employee Hormuz token.
- All eight tests in that run passed, including the installed Codex client test.

This proves official Claude Code client/protocol compatibility without spending against or exposing a real Anthropic account. A live Anthropic provider call remains pending until `ANTHROPIC_API_KEY` is securely provisioned to the Hormuz service.

### Governed context-pack path

The explicit context-pack kernel was exercised from both the source checkout and a clean installation of `dist/hormuz-0.1.0-py3-none-any.whl`.

Observed result:

- authorization tests excluded wrong-organization, wrong-team, wrong-actor, wrong-repository, wrong-branch, over-clearance, provisional, future, and expired records before ranking;
- active authorized supersession replaced stale records while an expired superseder correctly left the prior record eligible;
- record ordering did not change the pack identity, while content, classification, policy version, and budget changes did;
- an oversized high-scoring record was skipped and a smaller matching record was selected without exceeding the budget;
- the sample CLI produced a single verified, source-linked context item and did not call a provider or write to the usage database;
- the complete source suite passed 37 tests locally after the context change, with the optional Claude Code executable test skipped in that local run.

### Generic OIDC JWT path

A local standards-shaped issuer served an OIDC discovery document and rotating RSA JWKS to the actual Hormuz authentication and HTTP server path. No external identity provider, employee token, or provider credential was used.

Observed result:

- a valid RS256 JWT access token with the configured issuer, audience, expiry, key ID, and subject reached `/v1/gateway/whoami` and resolved to the explicit actor, team, organization, clearance, and client policy identity;
- wrong-audience, expired, unmapped-subject, symmetric-algorithm, non-TLS remote-issuer, and inconsistent duplicate-actor configurations failed closed;
- a new signing key was accepted after one JWKS refresh, while repeated attacker-controlled unknown key IDs were rate-limited from causing repeated metadata fetches;
- the identity endpoint returned no JWT or OIDC subject;
- context-pack CLI tests proved a caller cannot request an organization or clearance beyond the configured identity;
- generated OIDC configurations use Codex command-backed bearer authentication and Claude Code `apiKeyHelper`, while invalid configuration-injection URLs are rejected;
- the complete local source suite passed 48 tests, with only the opt-in official Claude Code executable test skipped in that run.

This verifies Hormuz as a JWT resource server against a controlled issuer. It is not evidence of browser login, refresh-token custody, opaque-token introspection, SCIM, or a live third-party IdP; those remain explicitly outside this milestone.

### Persistent governed-context path

The local repository and CLI lifecycle were exercised with the checked-in sample in an isolated temporary directory, not the developer's usage ledger.

Observed result:

- `context-import` created one verified decision at storage version 1 and a repeated import was idempotent;
- `context-list` returned authorized provenance and lifecycle metadata without record content by default;
- `context-pack` read the repository, authorized candidates before decoding, selected the expected record, and made no provider request;
- `context-export` produced import-compatible content-bearing JSONL with mode `0600`, overwrite protection, and a SHA-256 checksum;
- `context-audit-export` produced only organization-scoped mutation metadata, with mode `0600`, overwrite protection, and no title, content, or source locator;
- the context database also had mode `0600` and was a separate schema/file from the metadata-only usage ledger;
- the checked-in sample's source SHA-256 matches the packaged `examples/sources/adr-0017.md` artifact rather than a placeholder hash;
- repository tests proved wrong tenant/team/actor/classification/repository/branch, future, expired, and provisional records were rejected before decode; intentionally inconsistent classification metadata also failed before decode;
- tests proved idempotency conflicts, full batch rollback on a late conflict, immutable source revisions, required verification evidence, content-integrity checks, supersession cycles/references, physical-delete controls, metadata-only mutation audit, and exactly one winner under concurrent optimistic updates;
- the complete source suite passed 61 tests locally, with only the opt-in official Claude Code executable test skipped.

This is evidence for the original single-node local repository contract. It is not evidence of encryption at rest, hosted multi-tenant isolation, KMS/BYOK, backup/restore, retention/legal hold, writer RBAC, source connectors, automatic promotion/decay, context injection, or caching. Those gates remain open, and proposed ADRs 0002 and 0003 remain unaccepted.

### Authenticated Context Pack REST path

The actual `GatewayServer` was exercised through `POST /v1/context/packs` with its loopback HTTP transport, static identity authentication, local governed-context repository, and fake provider servers running.

Observed result:

- organization, team, actor, and policy version came from authenticated server configuration rather than caller fields;
- a verified team record was selected within repository, classification, item, and token caps, while actor-private context was absent for a different authenticated actor;
- unknown identity fields, invalid lexical queries, invalid branch scope, control-character scope injection, over-budget, over-item-cap, over-clearance, and provisional requests failed with stable `400` or `403` codes before retrieval or provider work;
- missing credentials returned `401`; an injected repository failure returned sanitized `503` output and logs without its internal error detail;
- the per-actor local rate limit returned `429` plus `Retry-After`, while a different actor retained an independent allowance;
- every API response used `Cache-Control: no-store`;
- successful, empty, denied, and failed context requests made zero OpenAI/Anthropic requests and wrote zero provider-usage events;
- every successful context response committed a durable metadata-only read event before returning content; the event retained trusted actor/team/org scope, policy, pack ID, repository/branch, clearance, provisional flag, and aggregate counts without query, title, content, source locator/hash, or selected record IDs;
- an injected read-audit failure returned the same sanitized `503` storage envelope and no context pack, provider request, or usage event;
- opening a schema-version-1 context repository migrated it in place to version 2 while preserving governed records and mutation history;
- the context route rejected bodies above `64 KiB` before parsing and rejected
  Unicode line separators before they could forge metadata logs;
- authentication, rate-limit, and oversized-body tests require explicit
  connection closure when a denied POST body can remain unread;
- token-budget tests proved that emitted provenance, lifecycle, classification,
  and other item metadata are counted rather than only the prose body;
- storage tests proved an existing database file is tightened to mode `0600`
  before schema initialization and invalid codec output cannot mutate a record;
- the rebuilt source distribution contains the REST contract, context repository, and hashed provenance source; a clean Python 3.14 environment installed the rebuilt wheel with no broken requirements, loaded Hormuz from `site-packages`, initialized schema version 2, and emitted mutation/read audit events;
- the complete source suite passed 72 tests locally, with only the opt-in official Claude Code executable test skipped.

This verifies the additive REST contract, single-process policy enforcement, and local fail-closed durable read audit. It is not evidence of distributed rate-limit consistency, enterprise reader RBAC, automatic Codex/Claude injection, or accepted hosted tenancy.

### Codex and Claude Code MCP path

The checked-in `hormuz mcp` stdio process was exercised as a protocol server, through the actual local Context Pack API, from a clean installed wheel, and as a required/strict MCP server loaded by the official employee clients.

Observed result:

- legacy MCP initialization, tool listing, structured and text tool results, unknown methods/tools, stable execution errors, and cancellation behavior passed executable protocol tests;
- the `2026-07-28` discovery and per-request metadata contract returned complete discovery, list, and call results, rejected incomplete metadata, and returned the specified unsupported-version error;
- non-standard JSON constants, messages above `128 KiB`, unsafe scope values, caller-supplied identity fields, invalid branch scoping, plaintext non-loopback URLs, URL credentials/query/fragment/whitespace, responses above `16 MiB`, redirects, and non-JSON responses fail closed;
- a real MCP subprocess used an employee token from its inherited environment to call the actual authenticated `/v1/context/packs` route, selected only the expected repository/branch-scoped record, and returned no credential in stdout or stderr;
- the end-to-end call committed exactly one durable metadata-only context-read event before returning the pack and made no provider request or usage-ledger entry;
- the secret-free `mcp-config` outputs parse as Codex TOML and Claude Code JSON and do not require a local Hormuz server configuration or provider key;
- installed Codex CLI `0.139.0` completed its fake-provider generation with Hormuz configured as a required MCP server;
- official Claude Code `2.1.233` completed its fake-provider generation with `--strict-mcp-config`, and its debug trace confirmed that it loaded the Hormuz server;
- the complete source suite passed 89 tests locally, with only the separately rerun official Claude Code test skipped in the default command; that opt-in test then passed;
- the rebuilt source distribution includes `docs/MCP.md`, `hormuz/mcp.py`, and `tests/test_mcp.py`; a clean Python 3.14 environment installed the wheel, displayed both MCP commands, and completed initialize plus `tools/list` from the installed executable.

This is evidence for a real, model-controlled read-only context tool in both clients. It is not evidence of mandatory context injection, session-profile authentication inside the MCP adapter, shared rate limiting, automatic context invalidation, or a production hosted topology. Those remain separate gates.

### Governed context benchmark path (version 1, historical baseline)

Before trusted lifecycle snapshots were implemented, the bundled version-1 synthetic corpus and separated reference outcomes were generated from the checked-in deterministic generator and then verified byte-for-byte with its `--check` mode. The failed strict-profile result below is retained as historical evidence of the gap; version 2 supersedes it later in this document.

Observed result:

- the corpus contains 60 frozen tasks: ten each for bug fixes, features, refactors, incidents, onboarding, and policy questions, plus a balanced 12-task CI subset;
- authorization, expired-record, supersession, contradiction, changed-dependency, and malicious-context challenges each have ten primary task labels;
- every task binds its records to a synthetic repository revision and memory-snapshot digest, and the references bind the complete corpus by canonical SHA-256;
- the leakage review found zero exact normalized outcome or outcome-hash matches in task records;
- the governed baseline selected all labeled relevant records with zero cross-scope authorization leaks, zero expired/superseded selections, zero budget violations, and zero determinism failures;
- the regression profile passed on the 12-task CI subset with five iterations per task/baseline;
- the full release profile exited 2 as designed: precision was `0.50`, recall was `1.00`, and useful-pack rate was `0.50`; dependency-stale, malicious, and contradiction records remain selectable because automatic invalidation/quarantine/resolution is not implemented;
- full-history and ungoverned lexical baselines selected authorization or lifecycle hazards, demonstrating that the safety result is not a consequence of an inert corpus;
- the complete local source suite passed 96 tests, with only the separately gated official Claude Code executable test skipped;
- source and wheel distributions contain the generator, benchmark documentation, runner, and frozen artifacts; a clean Python 3.14 environment installed the wheel and passed the bundled 12-task regression profile outside the source checkout.

These are synthetic retrieval-contract results, not claims about employee productivity, model answer quality, accepted patches, hosted latency, or customer-data performance. See [CONTEXT_BENCHMARK.md](CONTEXT_BENCHMARK.md) for formulas and limitations.

### Browser OIDC and rotating human-session path

The accepted [ADR 0001](decisions/0001-oidc-login-and-session-architecture.md) path was exercised through the actual HTTP gateway, a standards-shaped loopback OIDC issuer, the actual session persistence service, and the same CLI client functions used by Codex and Claude Code auth helpers.

Observed result on August 15, 2026:

- authorization-code login used an external-browser URL, exact server callback, state, nonce, and PKCE S256; the fake token endpoint verified the original code challenge and confidential-client authentication;
- Hormuz verified the ID-token signature, issuer, client audience, expiry, nonce, and explicit subject mapping before authorizing enrollment;
- the browser completion body contained no Hormuz credential, and redemption required the independent terminal-held secret exactly once;
- opaque access and refresh credentials were independently random, client-bound, stored only as keyed hashes, and absent from the session database alongside the fake provider access token and OIDC client secret;
- access refresh rotated both credentials atomically; replay of a consumed refresh credential and a concurrent duplicate refresh revoked the winning credential family and recorded metadata-only security events;
- bad state/cookie, callback replay, wrong nonce, issuer mix-up, expired enrollment, token-endpoint outage, client mismatch, logout, and removed/invalid session credentials failed closed before provider work;
- the CLI login, helper refresh, and logout path completed with an injected secure-store test backend, while a transient real macOS Keychain write/read/delete also passed and left no test entry behind;
- `keyring` 25.7.0 was installed from the declared wheel dependency; unsupported, null, fail, plaintext, corrupt, and invalid-profile custody paths were rejected;
- the complete local source suite passed 118 tests with one separately gated official Claude Code test skipped; that official Claude Code test then passed independently, while the installed Codex compatibility test passed in the default suite;
- fresh source and wheel distributions contained the new protocol, persistence, CLI, documentation, and tests; a clean Python 3.14 environment installed the wheel, displayed `login`, `logout`, and `auth token`, and passed the bundled context regression profile.

This verifies the local, single-node protocol kernel and real macOS secure-store adapter. It is not a real enterprise IdP result, Windows/Linux secure-store runner evidence, SCIM or administrator revocation, KMS-backed/HA session persistence, immutable security-audit export, or a hosted deployment claim. Issue #13 remains open until those applicable acceptance gates are satisfied.

### Trusted context lifecycle and benchmark-v2 path

The additive context-pack lifecycle contract, SQLite schema version 3, CLI snapshot flow, authenticated REST route, deterministic benchmark generator, source package, and installed wheel were exercised on August 15, 2026.

Observed result:

- exact organization/repository/branch lifecycle snapshots were created idempotently, replaced only with the expected version, and produced exactly one winner under concurrent changed-snapshot writes;
- schema-version-1 and schema-version-2 context databases migrated in place to version 3 while preserving records and mutation history; legacy records received empty dependency and assertion fields;
- corrupted snapshot hashes, cross-organization CLI envelopes, malformed lifecycle metadata, missing dependency observations, changed dependency revisions/hashes, changed `git:` source revisions, and stale snapshot updates failed closed;
- lifecycle evaluation occurs only after authorization and only for lexical query matches, preventing unrelated exclusions or contradiction sources from appearing in a pack;
- bounded high-confidence policy-override, secret-exfiltration, and instruction-escalation patterns were quarantined across model-visible record fields before ranking;
- conflicting matched records sharing a structured assertion key were excluded and returned as `requires_resolution` with both authorized source references rather than silently merged;
- lifecycle audit events retained scope, versions, actor, policy, snapshot hash, and artifact count without artifact URIs/revisions, while pack-read events retained only lifecycle outcome and aggregate exclusion/contradiction counts;
- the actual authenticated Context Pack API applied the stored snapshot, returned only the safe relevant record, committed the metadata-only read event first, and made zero provider requests or usage-ledger writes;
- the generated version-2 corpus hash was `9822d592868202c7c7539bcdac7d4a5894c01f9e6dba7a434846516b67b32c17`, and `--check` reproduced both frozen files exactly;
- the full 60-task release profile passed with precision `1.00`, recall `1.00`, useful-pack rate `1.00`, mean compression `0.840593`, zero authorization/lifecycle/dependency/malicious/contradiction/budget/determinism failures, and p95 in-process selection latency `0.13975 ms` in the five-iteration local run;
- the complete local source suite passed 130 tests with one separately gated official Claude Code executable test skipped; that Claude Code test then passed independently, and a separate temporary installation of the pinned current Codex `0.147.0` and Claude Code `2.1.233` releases routed both clients through Hormuz successfully;
- a fresh source distribution and wheel contained the v1 historical corpus, v2 default corpus/references, lifecycle example, docs, CLI, and code; a clean Python 3.14 environment installed the wheel from outside the source checkout, reported context schema version 3, displayed both snapshot commands, and passed the bundled strict release profile.
- GitHub Actions run `31918373659` passed all seven publication jobs: Python 3.11–3.14, the full strict benchmark with preserved evidence, source/wheel build and installed CLI smoke, and pinned official Codex/Claude Code compatibility.

This verifies deterministic immediate evaluation of trusted lifecycle observations and the frozen synthetic retrieval contract. Issue #16 was closed after its complete acceptance checklist and remote release gate were verified. That closure is not evidence of source connectors, automatic verification/promotion/decay, resumable background revalidation, semantic prompt-injection detection, distributed tenancy, mandatory client injection, customer-data performance, or improved model/employee outcomes; issue #12 remains open for the applicable lifecycle capabilities.

### Context Pack v1 contract-completion path

The REST, MCP, JSONL CLI, repository-backed CLI, deterministic manifest, and access-first SQLite candidate path were exercised together on August 15, 2026.

Observed local result:

- every pack now names `policy_version`, `retrieval_version=lexical-v1`, and `render_version=json-v1`; all three participate in the deterministic manifest and pack identity;
- SQLite filters organization, visibility, classification, repository, and branch before content decode, while the shared pack kernel returns explicit exclusions only for identity-authorized lexical matches that are provisional, not yet effective, verified in the future, expired, dependency-stale, source-revision-stale, quarantined, or contradictory;
- cross-organization, cross-team, cross-actor, cross-repository, cross-branch, and over-classification candidates appeared in neither items nor exclusions, including through the authenticated REST route;
- the service returned stable `401`/`403` request-level outcomes for invalid identity or over-clearance, rejected pagination cursors as unknown input, enforced the `64 KiB` request ceiling, and retained the single token/item-bounded response contract;
- MCP timeout, redirect refusal, response-size limits, stable error mapping, identity-injection rejection, and explicit cancellation suppression remained green; both CLI input modes emitted the same `hormuz.context-pack.v1` contract;
- the complete source suite passed 131 tests with only the separately gated official Claude Code executable test skipped, and that Claude Code test passed independently;
- the frozen version-2 corpus reproduced hash `9822d592868202c7c7539bcdac7d4a5894c01f9e6dba7a434846516b67b32c17`; the five-iteration full release profile passed with precision `1.00`, recall `1.00`, useful-pack rate `1.00`, zero authorization/lifecycle/dependency/malicious/contradiction/budget/determinism failures, and p95 in-process selection latency `0.180125 ms`;
- isolated source and wheel builds passed; a clean Python 3.14 environment installed the wheel from outside the source tree, imported `hormuz.context` from site-packages, reported context schema version 3 and the expected pack/retrieval/render versions, displayed the packaged CLI contract, and passed the bundled release profile;
- a tracked-file scan found no private-key, OpenAI, Anthropic, or GitHub credential pattern, and the local `.env.local` remained ignored and unread.
- GitHub Actions run `31918906731` passed all seven publication jobs for implementation commit `4efe325`: Python 3.11–3.14, the full strict benchmark with preserved evidence, source/wheel build and installed CLI smoke, and pinned official Codex/Claude Code compatibility.

This closes the bounded model-facing pack-service contract, not record browsing, mandatory client injection, semantic/vector retrieval, distributed authorization, automatic lifecycle jobs, caching, or production tenancy. Context Pack v1 intentionally has no pagination because accumulating pages would bypass its pack budget; any future administrative discovery surface requires its own authorization-bound cursor contract.

### Versioned provider-accounting foundation

The provider-response parser, gateway outcomes, additive usage-ledger migration, audit export, CLI report, source distribution, and wheel were exercised on August 15, 2026.

Observed local result:

- OpenAI and Anthropic response fixtures retained normalized input, output, cache-read, cache-write, reasoning, and billable-token categories plus a strict allowlisted provider-native usage object without response content or unknown fields;
- the provider-returned actual model was persisted separately from requested alias and routed model, and model reports preferred that actual model when available;
- every accounted gateway outcome carried a cost basis, USD currency, and immutable rate-card version; successful provider results were labeled `estimated`, denials were `not_applicable`, and provider attempts without a credential were recorded as `not_available` rather than disappearing;
- an additive legacy-database migration preserved existing events, derived conservative billable-token values, labeled nonzero historical estimates `estimated_legacy`, and assigned `unversioned` rather than inventing a rate-card identity;
- report and audit tests proved billable tokens, estimate-only cost, unpriced request counts, cost bases, currencies, rate-card versions, actual model, and safe provider usage are visible while added prompt, response, matched-secret, and unknown provider fields remain excluded;
- two otherwise identical events retained separate `rates-v1` and `rates-v2` cost snapshots, proving a current configuration change does not rewrite historical estimates;
- the complete default source suite passed 139 tests with one separately gated official Claude Code executable test skipped; that official Claude Code test then passed independently, while the installed Codex path passed in the default suite;
- the frozen 60-task release benchmark remained green with precision `1.00`, recall `1.00`, useful-pack rate `1.00`, zero safety failures, and p95 in-process selection latency `0.162125 ms`;
- isolated source and wheel builds succeeded; the source distribution contained accepted DLP ADR 0004 and the new accounting tests, and a clean Python 3.14 environment installed the wheel and passed an installed-package model/rate/billable/estimate report smoke test;
- a tracked-file scan found no private-key, OpenAI, Anthropic, or GitHub credential pattern, and the ignored `.env.local` was not read.
- GitHub Actions run `31919702400` passed all seven publication jobs for implementation commit `db4269d`: Python 3.11–3.14, the full strict benchmark, source/wheel build and install, and pinned official Codex/Claude Code compatibility.

This is the request-level accounting and immutable-estimate foundation for issue #8, not provider billing ingestion or invoice reconciliation. OpenAI and Anthropic cost reports are aggregate across provider dimensions and do not universally assign a final invoiced cost to one Hormuz request. Team/person values therefore remain estimates unless the customer isolates provider accounting boundaries; Hormuz does not label an inferred allocation as final.

### Provider cost-report import and aggregate reconciliation

The bounded offline import parser, additive usage-ledger schema, exact-decimal snapshot store, and CLI reconciliation path were exercised on August 15, 2026 against the documented OpenAI organization Costs and Anthropic organization Cost Report response contracts.

Observed local result:

- OpenAI dollar-valued cost results retained project and line-item dimensions; Anthropic decimal-cent results retained workspace, description, cost type, model, service tier, token type, context window, and inference geo. The Anthropic fixture amount `123.78912` cents remained exactly `1.2378912` USD without binary floating-point conversion or micro-USD rounding;
- incomplete pagination, an inconsistent intermediate/final flag, an exact duplicate page, duplicate JSON members, non-standard numeric constants, unsupported currency, malformed amount, invalid bucket order, a forged normalized fingerprint, and a missing stored item failed closed. The normalized fingerprint was stable when the same buckets and items were returned with a different page size;
- the import stored only normalized billing metadata and a SHA-256 fingerprint. An unknown raw-field sentinel did not appear in SQLite, and import/reconciliation outputs explicitly reported `raw_payload_retained: false`;
- two concurrent imports of one organization/provider snapshot converged on one `pci_` import ID with one writer and one idempotent reader. Reimporting the same snapshot did not duplicate its cost items;
- newly recorded usage events carried the trusted event-time organization ID, and metadata-only usage audit output exposed that binding. Cross-organization request estimates did not enter reconciliation; pre-migration rows with no trustworthy organization binding remained null, were excluded, and were counted as legacy unattributed coverage;
- provider-reported aggregate cost and gateway request-time estimate remained separate decimal values. Positive, zero, and negative provider entries were included without guessing whether free-form line items were credits, discounts, or adjustments. Cache/batch/provider dimensions were preserved without repricing, while succeeded, failed, denied, and unpriced gateway counts remained separate;
- every offline provider response reported `provider_report_completeness: not_verifiable_from_response`, `coverage_status: partial_unverified_provider_scope`, `person_cost_basis: estimated`, and `variance_proves_gateway_bypass: false`. The CLI never labels aggregate variance as causal proof or final employee cost;
- all 194 source tests passed with both the installed Codex and official Claude Code executable compatibility paths enabled;
- the frozen 60-task governed-context release profile remained green with precision `1.00`, recall `1.00`, useful-pack rate `1.00`, mean compression ratio `0.840593`, zero authorization/lifecycle/dependency/malicious/contradiction/budget/determinism failures, and p95 in-process selection latency `0.183792 ms`; corpus SHA-256 remained `9822d592868202c7c7539bcdac7d4a5894c01f9e6dba7a434846516b67b32c17`;
- an isolated source distribution included the billing module, implementation document, and tests, while the wheel contained the runtime billing module and CLI. A clean Python 3.14 environment installed the wheel outside the checkout, loaded `hormuz.billing` from `site-packages`, preserved the fractional-cent fixture, displayed both billing subcommands, passed bytecode compilation, and passed the bundled strict benchmark;
- `git diff --check`, source bytecode compilation, and source/runtime credential-pattern scans passed. No provider administrator credential was created, read, or stored by the importer.

That checkpoint closed a local, offline reconciliation kernel for issue #8, not the issue or finance release gate. An offline provider response cannot prove which filters produced it or bind separately downloaded pages to their request cursors. Authenticated ingestion was outside that checkpoint and is evaluated separately below. Secure hosted administrator-credential custody, explicit provider project/workspace mappings, invoice/credit imports, configurable variance thresholds and exceptions, historical supersession policy, shared tenant storage/RBAC, retention, HA, and finance review workflows remain open. Aggregate provider cost still cannot be silently allocated as final team or employee spend.

### Authenticated provider cost ingestion

The operator-run authenticated collector, fixed provider query contracts, metadata-only source evidence, additive SQLite migration, and `billing fetch` CLI were exercised on August 16, 2026 against strict deterministic transports matching the current official [OpenAI organization Costs](https://developers.openai.com/api/reference/resources/admin/subresources/organization/subresources/usage/methods/costs) and [Anthropic Claude Platform Cost Report](https://platform.claude.com/docs/en/manage-claude/usage-cost-api) contracts.

Observed local result:

- OpenAI requests used only `https://api.openai.com/v1/organization/costs`, an admin bearer credential, inclusive/exclusive Unix bounds, daily buckets, the maximum 180-bucket page, and exact organization-wide `project_id` plus `line_item` grouping with no project or API-key filter. Anthropic requests used only `https://api.anthropic.com/v1/organizations/cost_report`, an Admin API key, `anthropic-version: 2023-06-01`, UTC RFC 3339 bounds, daily buckets, the maximum 31-bucket page, and exact `workspace_id` plus `description` grouping;
- both collectors followed the provider cursor through `has_more=false`, rejected repeated or malformed cursors, refused redirects and cross-origin responses, bounded each page to 16 MiB, bounded the complete report to 32 MiB and 100 pages, and retried only bounded transient status/network failures. Oversized, duplicate-member, non-standard-numeric, out-of-window, and otherwise invalid provider responses failed closed before persistence;
- the CLI accepted only the credential environment-variable name. The selected secret was passed in the provider header, never the URL or command arguments, and did not appear in stdout, stable error output, or SQLite. Provider error bodies and low-level exception causes were not reflected. Missing credentials failed before the usage database was created;
- authenticated source evidence bound the immutable import to a versioned provider API contract, UTC query window, and fixed organization query scope. Reconciliation reported `authenticated_query_pagination_complete` and `partial_authenticated_provider_endpoint_scope`; those labels attest only to the completed query, not a final invoice, provider freshness, or cost outside the documented endpoint scope;
- an empty authenticated window persisted and reconciled as exact zero. A later authenticated observation of an identical offline snapshot reused the existing import and cost items while adding distinct authenticated source evidence. Forged source/query mismatches failed before import;
- the positive-bucket legacy schema migrated additively to support authenticated empty windows without losing prior imports, and existing imports received only offline provenance unless authenticated evidence actually existed;
- 25 focused billing parser/client/store/CLI tests passed. The complete local source suite passed 290 tests with the opt-in official Claude Code executable test skipped; the installed Codex path remained enabled. Source bytecode compilation and a credential-pattern scan passed;
- the frozen 60-task governed-context release profile remained green with precision `1.00`, recall `1.00`, useful-pack rate `1.00`, mean compression ratio `0.840593`, zero authorization/lifecycle/dependency/malicious/contradiction/budget/determinism failures, and p95 in-process selection latency `0.168958 ms`; corpus SHA-256 remained `9822d592868202c7c7539bcdac7d4a5894c01f9e6dba7a434846516b67b32c17`;
- isolated source and wheel builds succeeded. A clean Python 3.14 environment installed the wheel outside the checkout, loaded `hormuz.billing_client` from `site-packages`, displayed `billing fetch --help`, passed installed-package bytecode compilation, and passed the bundled strict benchmark.

No `OPENAI_ADMIN_KEY` or `ANTHROPIC_ADMIN_KEY` was present in the verification environment. No live organization billing endpoint was called, and no inference credential was substituted. This checkpoint therefore proves the fixed authenticated request, pagination, parsing, secret-handling, persistence, and packaging behavior under deterministic official-contract transports. A customer-account live read, scheduled collection, secure hosted credential custody/rotation, Claude Enterprise Analytics support, Anthropic Priority Tier accounting, invoice/credit ingestion, finance exception workflow, shared tenant storage/RBAC, retention, HA, KMS, and independent review remain open. Issue #8 remains open, and aggregate provider cost remains ineligible for silent final team or employee allocation.

### Structured DLP detector and enforcement subset

The versioned rule configuration, recursive detector, additive security-ledger migration, OpenAI and Anthropic egress paths, and metadata-only evidence boundary were exercised on August 15, 2026.

Observed local result:

- high-confidence hyphenated US SSNs and Luhn-valid 13-to-19-digit payment-card candidates were redacted by default, invalid and Unicode-confusable numeric candidates were not classified, and conventional email syntax remained detect-only;
- bounded environment-backed company dictionaries supported provider and exact routed-upstream-model scopes plus detect, redact, deny, and fail-closed approval-required actions, while configured values stayed out of object representations, logs, errors, SQLite, and audit output;
- exact routed-model scope was enforced before egress, matching the owner-approved approval binding; the durable non-self approval grant/consumption workflow is deliberately not claimed by this subset;
- the OpenAI Responses and Anthropic Messages compatibility paths both inspected after model routing and before provider storage policy or serialization; redaction reached the fake provider only as `[REDACTED:HORMUZ_DLP]`, denial and approval-required made zero provider calls, and detect-only traffic was forwarded unchanged with an explicit detection header;
- security events committed before provider egress and stored only event-time scope, requested/routed model, policy version, rule/category/confidence/action/count metadata, and transformation counts. A simulated evidence-store outage returned `hormuz_dlp_evidence_unavailable` and made zero provider calls;
- an additive migration preserved legacy `security.secret` rows and added routed model, policy version, transformation count, event type, and a strict metadata-only finding envelope; a future content-bearing column was still excluded from export;
- the complete default source suite passed 153 tests with only the separately gated official Claude Code executable test skipped; that official Claude Code test then passed independently, while the installed Codex path passed in the default suite;
- the frozen 60-task context release profile remained green with precision `1.00`, recall `1.00`, useful-pack rate `1.00`, zero authorization/lifecycle/dependency/malicious/contradiction/budget/determinism failures, and p95 in-process selection latency `0.179375 ms`;
- isolated source and wheel builds succeeded; the source distribution contained the DLP decision, implementation docs, and detector/gateway tests, and a clean Python 3.14 environment installed the wheel from outside the source tree and loaded `organization-dlp-v1` with all three built-in rules.
- GitHub Actions push run `31920655595` and pull-request run `31920657597` both passed all seven publication jobs for implementation commit `695fff4`: Python 3.11–3.14, strict context benchmark, source/wheel build and install, and pinned official Codex/Claude Code compatibility.

This was the bounded deterministic detector/enforcement checkpoint for #10. At that checkpoint, the local grant/replay flow, opaque-media denial, and team/person DLP overlays remained open; the subsequent sections below close those bounded items. Source classification, provider-header and JSON-key inspection, semantic detection, context-cache invalidation, organization-representative detector evaluation, and enterprise deployment gates remain open.

### Replay-safe DLP approval workflow

The owner-approved transparent retry design, keyed fingerprint boundary, additive approval schema, approver API/CLI, both provider paths, and privacy evidence were exercised on August 15, 2026.

Observed local result:

- optional approval configuration requires a base64url key that decodes to exactly 32 random bytes, hides both key forms from configuration representations, treats the encoded form as an exact egress secret, and rejects enabled `require_approval` policy for an organization with no configured `dlp_approver`;
- the gateway canonicalized the final transformed JSON together with the provider operation and stored only a domain-separated HMAC-SHA-256 fingerprint plus bounded event-time metadata; protected dictionary values, prompts, and the fingerprint key were absent from SQLite, API/CLI results, audit output, logs, and errors;
- an opaque pending request bound organization, employee, client, provider, requested model, exact routed model, policy version, rule IDs, operation, and payload. A separately authenticated capability holder could inspect metadata and approve it, while missing capability, self-approval, cross-organization lookup, mutation, policy/provider/model/actor change, expiry, and key rotation failed closed;
- the same unchanged OpenAI Responses or Anthropic Messages retry atomically consumed one approved grant before egress without a client-supplied approval header. Concurrent retry produced exactly one consumed result, later replay created a new pending request, and an upstream failure could not restore a consumed grant;
- approval-store outage returned `hormuz_dlp_approval_unavailable` before a provider call. The same-approver decision endpoint was idempotent without extending expiry, while expired, consumed, or differently decided requests returned stable conflicts;
- provider-returned actual model remained separate usage metadata; a mismatch from the approved routed model produced a metadata-only `security.dlp.approval` `model_mismatch` event after egress;
- the real `hormuz dlp approval show` and `approve` CLI commands exercised the authenticated REST boundary. The client rejected redirects and non-loopback plaintext HTTP, and both existing AI-client request formats remained unchanged;
- the complete source suite passed all 160 tests with the installed Codex and official Claude Code executable paths both enabled;
- the frozen 60-task context release profile remained green with precision `1.00`, recall `1.00`, useful-pack rate `1.00`, mean compression ratio `0.840593`, zero safety failures, and p95 in-process selection latency `0.168458 ms`; corpus SHA-256 remained `9822d592868202c7c7539bcdac7d4a5894c01f9e6dba7a434846516b67b32c17`;
- an isolated source/wheel build included the approval implementation, API document, and tests; a clean Python 3.14 environment installed the wheel and loaded the approval CLI, fingerprint implementation, and empty approval schema successfully;
- a source scan excluding generated artifacts found no private-key, OpenAI, Anthropic, or GitHub credential pattern; ignored local environment files were not read;
- GitHub Actions push run `31921828391` and pull-request run `31921830188` both passed all seven publication jobs for implementation commit `fb41cd3`: Python 3.11–3.14, strict context benchmark, source/wheel build and install, and pinned official Codex/Claude Code compatibility.

This is a verified single-node approval workflow, not completion of issue #10 or an enterprise release. It has no approver queue/notification connector, rejection reason workflow, signed or externally immutable audit sink, shared PostgreSQL tenancy, KMS/BYOK key custody, HA failover, or independent security review. The exact client retry can also fail to match if a client changes any outbound field between attempts; Hormuz deliberately creates a new blocked request rather than weakening the approved binding.

### Provider-aware opaque-media boundary

The built-in provider-content classifier, fail-closed policy, both gateway paths, metadata-only evidence boundary, package artifacts, and installed wheel were exercised on August 15, 2026. The recognized request shapes follow the published [OpenAI Responses request schema](https://developers.openai.com/api/reference/resources/responses/methods/create), [Anthropic vision contract](https://platform.claude.com/docs/en/build-with-claude/vision), and [Anthropic PDF contract](https://platform.claude.com/docs/en/build-with-claude/pdf-support).

Observed local result:

- OpenAI `input_image`, `input_file`, `computer_screenshot`, computer-output screenshots, and image/file blocks nested in supported tool-output content were classified only in provider-semantic input positions; arbitrary metadata with a matching `type` value was not classified;
- Anthropic image, binary/URL/file document, direct file, container-upload, and supported nested tool/search-result content blocks were classified, while inline document `text` and `content` remained inspectable by the existing text DLP rules;
- the built-in `opaque_media` rule denied recognized uninspectable media before provider egress and wrote one metadata-only `security.dlp` event plus a non-billable denied usage outcome; Anthropic token-count denial wrote no provider-usage event because no inference request occurred;
- OpenAI and Anthropic denial tests made zero provider requests. The event and SQLite assertions retained only rule, category, confidence, action, count, event-time scope, and model/policy metadata; media data, URLs, file IDs, filenames, MIME values, and surrounding request content were absent;
- configuration accepts only `deny` or the explicit risk-acceptance value `off` for `opaque_media`; unsupported detect, redact, or approval actions fail at startup because Hormuz cannot safely inspect, transform, or fingerprint the referenced bytes;
- when the rule was explicitly off or provider-out-of-scope, the recognized media object passed through unchanged and was skipped by generic string scanning, avoiding both byte corruption and a false claim that embedded data had been inspected;
- media classification runs before bounded regex and dictionary matching, while the global JSON nesting limit is still validated first;
- all 169 source tests passed with both the installed Codex and official Claude Code compatibility paths enabled;
- the frozen 60-task context release profile remained green with precision `1.00`, recall `1.00`, useful-pack rate `1.00`, mean compression ratio `0.840593`, zero safety failures, and p95 in-process selection latency `0.174333 ms`; corpus SHA-256 remained `9822d592868202c7c7539bcdac7d4a5894c01f9e6dba7a434846516b67b32c17`;
- isolated source and wheel builds succeeded; the source distribution included the opaque-media implementation, documentation, configuration, and tests, and a clean Python 3.14 environment installed the final wheel from outside the source tree and returned `opaque_media`/`deny` for an OpenAI image block;
- bytecode compilation and `git diff --check` passed.
- GitHub Actions push run `31922716279` and pull-request run `31922717807` both passed all seven publication jobs for implementation commit `a39f7f7`: Python 3.11–3.14, strict context benchmark, source/wheel build and install, and pinned official Codex/Claude Code compatibility.

This closed the recognized provider-request media-shape gap, not issue #10 or the enterprise DLP program. At that checkpoint, team/person DLP overlays were not yet applied; the subsequent checkpoint below closes that bounded item. Hormuz still does not fetch and inspect URLs or provider file IDs, decode arbitrary base64 hidden in ordinary text, inspect archives, classify provider headers/JSON keys/source repositories, run semantic detectors, or provide customer-representative detector evaluation, KMS/BYOK custody, HA persistence, an externally immutable audit sink, or an independent security review. Explicitly disabling the rule accepts that uninspected media can reach the provider.

### Monotonic team and person DLP policy

The accepted ADR 0004 organization/team/person hierarchy, configuration parser, exact provider/model resolver, gateway cache, CLI diagnostics, both provider paths, approval validation, and metadata-only evidence boundary were exercised on August 15, 2026.

Observed local result:

- an organization rule remained the sole owner of its detector, dictionary values, category, confidence, base action, and maximum provider/model scope; team and actor overlays could reference only an enabled rule, declare a version, choose a strictly stronger action, and optionally narrow that scope;
- `detect < redact < require_approval < deny` was enforced at configuration and resolution boundaries. A stronger team action survived a weaker actor declaration, so a person layer could not relax the effective team result;
- unknown team/actor IDs, rules not enabled by the organization, actions equal to or weaker than the organization action, provider/model expansion, unsupported routed models, and team IDs shared by identities in multiple organizations failed configuration validation;
- the gateway resolved one effective rule per protected value for the exact routed provider/model, avoiding duplicate findings or counts across layers. In the integration path, Alice's OpenAI-scoped actor rule denied one email with zero provider calls while the Engineering team rule redacted Bob's and Alice's Anthropic requests before provider egress;
- environment-backed organization dictionaries were inherited by overlays without copying their values into configuration representations or the effective policy version;
- each active identity hierarchy produced a bounded deterministic `dlp-effective-v1:...` value from safe organization/team/actor layer metadata. Alice received the same binding across her provider requests and a different binding from Bob's team-only policy; DLP evidence contained no matched email value;
- an overlay selecting `require_approval` participated in the existing organization-approver validation, and the effective version flowed through the unchanged keyed approval and audit path;
- the gateway memoized immutable redactors by organization, team, actor, provider, and routed model, avoiding repeated policy hashing on the request path;
- `hormuz policy-check` returned the effective version and one safe rule/action/provider/model entry per organization rule without a provider call;
- all 176 source tests passed with both the installed Codex and official Claude Code compatibility paths enabled;
- the frozen 60-task context release profile remained green with precision `1.00`, recall `1.00`, useful-pack rate `1.00`, mean compression ratio `0.840593`, zero authorization/lifecycle/dependency/malicious/contradiction/budget/determinism failures, and p95 in-process selection latency `0.179167 ms`; corpus SHA-256 remained `9822d592868202c7c7539bcdac7d4a5894c01f9e6dba7a434846516b67b32c17`.
- isolated source and wheel builds succeeded; the source distribution contained the overlay implementation, configuration, documentation, and tests, and a clean Python 3.14 environment installed the wheel from outside the source tree, loaded Hormuz from `site-packages`, and returned the expected effective Engineering email-redaction rule from the installed CLI and library.
- GitHub Actions push run `31923726258` and pull-request run `31923728601` both passed all seven publication jobs for implementation commit `7143107`: Python 3.11–3.14, strict governed-context benchmark, source/wheel build and install, and pinned official Codex/Claude Code compatibility.

This closes the local configuration and enforcement slice for monotonic team/person DLP overlays. It does not establish dynamic RBAC policy administration, PostgreSQL tenant isolation, SCIM group synchronization, policy-change audit, distributed cache invalidation, or a hosted control-plane schema; those depend on the owner-pending enterprise tenancy decision. Issue #10 also remains open for source classification, arbitrary encoded text/archive decoding, provider-header and JSON-key coverage, semantic detector evaluation, multi-node approval operations, externally immutable audit, and independent security review.

### Opaque-media off-mode isolation

The explicit `opaque_media: off` risk acceptance and its interaction with ordinary inspectable sibling values were exercised on August 15, 2026.

Observed local result:

- a red-first domain regression reproduced an early-return path where the presence of an off-mode opaque-media rule incorrectly bypassed all ordinary credential and DLP inspection for the request;
- the domain fix makes the off-mode exception object-local: recognized opaque objects remain unchanged and are not falsely reported as byte-inspected, while inspectable sibling values continue through the ordinary transformation pipeline;
- both OpenAI Responses and Anthropic Messages gateway tests sent a recognized opaque image URL beside a hyphenated US SSN. Both provider calls succeeded with the original opaque URL, the SSN was redacted before egress, and provider captures contained no unredacted identifier;
- the two metadata-only `security.dlp` events recorded one `us_ssn` finding each, contained no opaque-media finding for the accepted objects, and neither the audit representation nor the SQLite database contained the SSN;
- the secure default denial path, Anthropic token-count no-charge path, and inspectable inline Anthropic text-document path remained green;
- all 177 source tests passed with both the installed Codex and official Claude Code compatibility paths enabled;
- the frozen 60-task context release profile remained green with precision `1.00`, recall `1.00`, useful-pack rate `1.00`, mean compression ratio `0.840593`, zero authorization/lifecycle/dependency/malicious/contradiction/budget/determinism failures, and p95 in-process selection latency `0.167417 ms`; corpus SHA-256 remained `9822d592868202c7c7539bcdac7d4a5894c01f9e6dba7a434846516b67b32c17`.
- isolated source and wheel builds succeeded; a clean Python 3.14 environment loaded `hormuz.redaction` from the installed wheel under `site-packages` and reproduced the object-local off-mode behavior with the opaque URL unchanged and the sibling SSN redacted;
- installed-package bytecode compilation and `git diff --check` passed. A source-code scan excluding documented placeholders and test fixtures, plus a scan of the wheel's runtime package, found no private-key, OpenAI, or Anthropic credential pattern.
- GitHub Actions push run `31924440725` and pull-request run `31924442617` both passed all seven publication jobs for implementation commit `d372731`: Python 3.11–3.14, strict governed-context benchmark, source/wheel build and install, and pinned official Codex/Claude Code compatibility.

This closes one fail-open composition defect; it does not expand the media-inspection claim. Hormuz still does not inspect accepted opaque bytes, fetch URLs or provider file IDs, decode arbitrary encoded values, or classify provider headers and JSON keys. Issue #10 remains open for those and the other enterprise DLP release gates.

### Tenant-scoped human-session administration

The accepted OIDC session architecture, session-store v1-to-v2 migration, authenticated administrator API, real CLI client, and both existing AI-client paths were exercised on August 15, 2026.

Observed local result:

- every newly redeemed human session was bound to its event-time organization, actor, team, clearance, and Codex or Claude Code client; changing a mapped team revoked the family and returned `401` before policy or provider work;
- a red-first persistence regression proved the prior issuer/subject-only schema could not safely list or revoke by tenant, employee, or team. Session-store schema version 2 added the explicit bindings and an indexed administrative scope;
- the v1-to-v2 migration preserved the database, added non-null legacy sentinels, revoked every active unbound legacy session, recorded a metadata-only `migration_identity_binding_required` event, and required re-login rather than guessing a tenant binding;
- `GET /v1/admin/sessions` required the explicit `session_admin` capability, derived organization scope only from the authenticated administrator, supported actor/team filtering plus bounded opaque cursor pagination, and returned no access credential, refresh credential, OIDC subject, provider token, or content;
- `POST /v1/admin/session-revocations` revoked one session, actor, team, or organization scope atomically inside the local store. Cross-tenant selectors matched nothing, retries were idempotent, and each affected session received a metadata-only event with decision actor, target scope, and one bounded reason code;
- `GET /v1/admin/session-events` required the same explicit capability, derived organization scope only from the authenticated administrator, and returned cursor-paginated evidence for logout, refresh replay, authorization-mapping removal, and administrative revocation. Exact actor/team/type and inclusive offset-aware `since` filters worked; unknown event types, naive timestamps, repeated fields, malformed cursors, and over-limit pages failed closed;
- newly recorded session events carried the trusted event-time organization/actor/team binding. Tenant queries excluded another organization's events and deliberately omitted migration or pre-v2 evidence without a trustworthy tenant binding. Responses contained no session credential, OIDC subject, provider key, prompt, response, or model payload;
- invalid capability, target shape, cursor, limit, reason, and organization-target combinations returned stable `400` or `403` JSON errors. A valid actor revocation invalidated both that employee's active sessions immediately;
- the real `hormuz sessions list`, `hormuz sessions events`, and `hormuz sessions revoke` commands exercised the authenticated loopback contract. Newly issued session and security-event IDs use `ses_` and `sev_` prefixes; pre-v2 session IDs beginning with `-` remain supported through `--target=<id>`;
- all 182 source tests passed with the installed Codex and official Claude Code compatibility paths enabled;
- the frozen 60-task context release profile remained green with precision `1.00`, recall `1.00`, useful-pack rate `1.00`, mean compression ratio `0.840593`, zero safety failures, and p95 in-process selection latency `0.175458 ms`; corpus SHA-256 remained `9822d592868202c7c7539bcdac7d4a5894c01f9e6dba7a434846516b67b32c17`;
- an isolated source distribution and wheel included the API document, client, implementation, and tests. A clean Python 3.14 environment installed the exact wheel from outside the checkout, loaded Hormuz from `site-packages`, created session schema version 2, exercised the empty tenant event query, displayed all three administrator CLI commands, passed bytecode compilation, and passed the bundled strict release benchmark;
- `git diff --check`, source bytecode compilation, frozen-corpus regeneration, and a source/wheel runtime scan for private-key, OpenAI, Anthropic, and GitHub credential values passed.

This closes the single-node administrator-revocation and local event-inspection slice of issue #13, not the identity or enterprise-tenancy milestone. The event API is a queryable local evidence ledger, not a signed or externally immutable audit sink. Real owner-selected IdP validation, live configuration reload, SCIM/event-driven deprovisioning, shared multi-node revocation, PostgreSQL tenant isolation, KMS custody, retention/SIEM delivery, immutable session-event export, HA, backup/restore, and independent review remain open. Those shared persistence decisions still depend on proposed ADR 0002.

### Evidence-driven governed-context lifecycle

The opt-in evidence contract, promotion policy, invalidation evaluator, durable local job kernel, CLI operator path, schema migration, and metadata-only audit boundary were exercised on August 16, 2026.

Observed local result:

- new records imported while lifecycle automation is enabled were required to start provisional; exact idempotent retries of pre-existing verified records remained compatible, and legacy verified records without Hormuz-managed evidence were not silently demoted solely because managed evidence was absent;
- immutable `hormuz.context-evidence.v1` events were tenant-, record-version-, and semantic-subject-bound. The raw external reference was hashed at import and was absent from the SQLite database and audit export; exact retries converged on one event and observation times more than five minutes in the future failed closed;
- configured positive evidence paths promoted records, while reverted commits, failed CI, rejected reviews, superseded ADRs, reopened incidents, withdrawn human confirmation, and rejected failed-attempt evidence returned records to provisional. Same-time opposing signals surfaced an explicit conflict, and `negative_knowledge` required its dedicated validation path;
- trusted source/dependency changes invalidated managed records, missing dependency observations deferred without destructive demotion, and returning to a matching snapshot recovered records only from still-current subject-bound evidence. Evidence for changed content was not reused;
- each durable job was bound to tenant, repository, branch, exact snapshot hash/version, policy-content hash, semantic record-set hash, and current-subject evidence-set hash. New evidence after an unchanged completed run created a new job; snapshot, record, or evidence changes superseded stale work;
- bounded SQLite leases allowed one active worker, expired leases resumed after a simulated crash, batches committed record mutations and cursor/counters atomically, and optimistic record versions prevented concurrent overwrite;
- only an identity with the explicit organization-scoped `context_promoter` capability could import evidence, import managed snapshots, or run revalidation. Lifecycle automation remains disabled in the example configuration by default;
- context schema versions 1, 2, and 3 migrated to schema version 4 without losing existing records or audit rows. The new evidence, job, change, and job-event tables were present after both source and clean-wheel initialization;
- all 213 source tests passed with Codex `0.147.0` and Claude Code `2.1.233` selected first on `PATH`, including their real installed-client fake-provider compatibility paths;
- the frozen 60-task release profile passed with precision `1.00`, recall `1.00`, useful-pack rate `1.00`, mean compression ratio `0.840593`, zero authorization, stale-lifecycle, dependency-stale, malicious, contradiction, budget, leakage, or determinism failures, and p95 in-process selection latency `0.1545 ms`. Corpus SHA-256 was `9822d592868202c7c7539bcdac7d4a5894c01f9e6dba7a434846516b67b32c17`; evidence artifact SHA-256 was `756940ef19e52eb2608cb9c5ef8628908cf90cc91e0015bc1b8a726931335b27`;
- isolated source and wheel builds succeeded. The source archive contained the lifecycle implementation, documentation, examples, and tests; a fresh Python 3.14 environment installed the wheel outside the checkout, loaded `hormuz.context_lifecycle` from `site-packages`, displayed both lifecycle commands, created schema version 4 with the evidence-set-bound job column, and passed the installed 12-task regression profile;
- source compilation and `git diff --check` passed. A high-confidence source and extracted-package scan found no private-key, OpenAI, Anthropic, GitHub, AWS, or Google credential pattern; the broader assignment scan returned only documented placeholders and an intentional test sentinel.

This is a real single-node lifecycle kernel, not an enterprise scheduler or automatic connector system. GitHub, GitLab, CI, review, ADR, and incident connectors; signed attestations; probabilistic or time-based decay; PostgreSQL/multi-node leasing; hosted operations; and automatic client injection remain open. Issue #12 therefore remains open, and the hosted isolation decision remains proposed in ADR 0002 rather than implemented by this checkpoint.

### Authenticated context lifecycle connector path

The provider-neutral evidence, snapshot, and revalidation transport was exercised locally on August 16, 2026. This checkpoint adds no source-specific event collector and does not treat workload authentication as proof that an asserted external event occurred.

- the gateway authenticated before reading lifecycle mutation bodies, required the explicit `context_promoter` capability, derived tenant scope from the identity, rejected cross-organization envelopes, and returned stable generic errors without reflecting attacker-controlled schema fields or storage exception text;
- a signed JWT from the generic workload OIDC path reached the lifecycle connector only through its configured issuer, audience, subject mapping, organization, and promoter capability;
- exact evidence retries returned the existing immutable event, exact snapshot retries preserved the existing version, stale record/snapshot versions returned `409`, oversized revalidation batches were denied by server policy, and ambiguous identical requests remained safe to retry;
- a real HTTP client submitted a trusted snapshot and merge/CI evidence, ran one bounded server-side job, and promoted the expected provisional record to verified without any OpenAI/Anthropic call or usage-ledger event;
- lifecycle mutation responses and metadata-only audit omitted raw evidence references and artifact identities. A high-confidence scan of the source and extracted wheel found no private-key, OpenAI, Anthropic, GitHub, AWS, or Google credential pattern;
- the remote CLI required no local server configuration, refused redirects and non-loopback plaintext HTTP, validated strict JSON and exact metadata-only response shapes, bounded request/response sizes, and exposed stable exit behavior for local-input versus remote-policy failures;
- the default source suite completed 224 tests: 223 passed and the explicitly opt-in Claude Code executable test skipped. The pinned official Codex `0.147.0` and Claude Code `2.1.233` compatibility tests then passed separately through local fake-provider endpoints;
- the frozen 60-task release benchmark passed with precision `1.00`, recall `1.00`, useful-pack rate `1.00`, zero authorization, stale-lifecycle, dependency-stale, malicious, contradiction, budget, leakage, or determinism failures, and p95 in-process selection latency `0.173542 ms`;
- isolated source and wheel builds succeeded. The source archive contained the connector API documentation, runtime modules, and tests; a clean Python 3.14 environment installed the exact wheel outside the checkout, loaded `hormuz.context_api` and `hormuz.context_lifecycle_client` from `site-packages`, displayed the three `hormuz lifecycle` commands, passed bytecode compilation, and passed the installed regression benchmark with zero safety failures;
- source compilation, frozen-corpus regeneration, and `git diff --check` passed.

This verifies a reusable authenticated connector boundary, not automatic GitHub/GitLab/CI collection, webhook signature validation, signed attestations, hosted scheduling, cross-node leases, or the truth of an external event. Issue #12 remains open for those source-specific and hosted lifecycle gates.

### Authenticated tenant usage administration

The tenant-scoped reporting API, CLI, read-audit boundary, and supporting usage-store isolation changes were exercised locally on August 16, 2026.

- `GET /v1/admin/usage` authenticated before reporting, required the explicit `usage_viewer` capability, derived organization only from the mapped identity, accepted bounded team/actor filters, and returned current-month team, person, model, client, provider, or organization aggregates without content or credentials;
- a deliberate collision reused the same actor and team IDs in two organizations. Administrator reports and the ordinary employee `/v1/gateway/usage` result returned only the authenticated organization; the other tenant's names, requests, and cost were absent;
- monthly policy totals, secret/DLP summaries, concurrent budget reservations, and local `status` reporting now include the organization key. Migrated unattributed usage and security rows remain null and are excluded rather than guessed; obsolete unscoped reservations are discarded during migration because they are short-lived and cannot be assigned safely;
- the first report page froze an exclusive UTC window and returned a bounded opaque cursor. A second page reused the same window; malformed and filter-mismatched cursors failed with stable `400` responses, and pagination ordering remained deterministic;
- every successful page committed a metadata-only `security.admin.usage_read` event containing the viewer, organization, grouping, frozen window, result count, and only SHA-256 digests of optional actor/team filters. An injected audit-store failure returned `503 usage_admin_unavailable` and no report rows;
- the config-independent `hormuz usage report` CLI exercised the real authenticated loopback endpoint. Its client rejected redirects and non-loopback plaintext HTTP, bounded credentials and responses, and required the exact versioned response envelope;
- the complete source suite ran 230 tests: 229 passed and the explicitly opt-in Claude Code executable test skipped. The exact CI-pinned Codex `0.147.0` and Claude Code `2.1.233` releases then passed their real installed-client fake-provider tests separately;
- the frozen 60-task release benchmark passed with precision `1.00`, recall `1.00`, useful-pack rate `1.00`, zero failed safety thresholds, and p95 in-process selection latency `0.254667 ms`; corpus SHA-256 remained `9822d592868202c7c7539bcdac7d4a5894c01f9e6dba7a434846516b67b32c17`;
- isolated source and wheel builds included the usage administrator client, shared report semantics, API documentation, and tests. A clean Python 3.14 environment installed the wheel outside the checkout, loaded Hormuz from `site-packages`, created the administrative-access table and organization-scoped security column, displayed `hormuz usage report --help`, passed bytecode compilation, and passed the installed regression benchmark with zero failed thresholds;
- `git diff --check`, source/test bytecode compilation, and high-confidence tracked-source and extracted-wheel scans for private-key, OpenAI, Anthropic, GitHub, AWS, and Google credential patterns passed.

This closes a real single-node usage-administration slice without accepting proposed ADR 0002. SQLite is not evidence for shared hosted tenancy, PostgreSQL row security, HA, externally immutable audit, SCIM, SIEM delivery, final invoice coverage, or complete provider-account usage. Person-level tokens and estimated spend remain consumption metadata, not employee-performance evidence.

### Bounded encoded-text DLP inspection

The recursive secret/DLP transformer, supported encoding boundary, OpenAI Responses path, Anthropic Messages path, package artifacts, and installed wheel were exercised locally on August 16, 2026.

- a red-first reproduction proved that a base64-encoded OpenAI-shaped credential previously produced zero detections and passed through unchanged; the new path decodes standard base64, URL-safe base64, and textual data URIs only when they form bounded, predominantly printable UTF-8;
- inspection is capped at 1 MiB per decoded value and three nested encoding layers. Oversized supported text and a fourth layer fail closed with a content-free validation error before provider work, while benign encoded text remains byte-for-byte unchanged;
- direct outer-value detection runs first, so an organization exact secret that is itself valid base64 remains protected as that exact secret rather than being misclassified as a container;
- redaction applies the existing credential or structured-DLP replacement inside decoded text and then safely re-encodes only the changed value. Detect, deny, and approval-required findings keep the original encoded payload and follow their existing action semantics;
- an OpenAI function-call output and an Anthropic tool-result text block each carried a distinct encoded fake provider credential through the full gateway. Both fake providers received only encoded replacement text, both gateway responses reported one redaction, and the two metadata-only security events retained protocol/rule/count data without either matched value;
- the oversized gateway case returned the stable provider-shaped `400` error, made zero provider calls, created no billable usage event, and did not reflect the encoded content;
- recognized provider image/file/document blocks remain outside this decoder and under the default-deny `opaque_media` boundary. Whitespace-wrapped base64, binary data, compression, archives, and other encodings remain explicitly unsupported;
- the complete source suite ran 237 tests: 236 passed and the separately gated official Claude Code test skipped. The CI-pinned Codex `0.147.0` and Claude Code `2.1.233` executables then passed their two real-client fake-provider tests independently;
- the frozen 60-task release benchmark passed with precision `1.00`, recall `1.00`, useful-pack rate `1.00`, zero failed thresholds, and p95 in-process selection latency `0.195 ms`; corpus SHA-256 remained `9822d592868202c7c7539bcdac7d4a5894c01f9e6dba7a434846516b67b32c17`;
- source and wheel builds contained the encoded-text implementation and tests. A clean Python 3.14 environment installed the exact wheel outside the checkout, loaded Hormuz from `site-packages`, transformed an encoded fake credential, compiled the installed package, displayed the relevant CLI help, and passed the bundled strict benchmark with zero failed thresholds;
- source/test bytecode compilation, deterministic corpus regeneration, `git diff --check`, and high-confidence source and extracted-wheel scans for private-key, OpenAI, Anthropic, GitHub, AWS, and Google credential patterns passed.

This closes one encoded-text bypass under accepted ADR 0004, not issue #10 or the enterprise DLP release gate. It does not inspect arbitrary binaries or archives, classify source repositories or provider headers/JSON keys, provide semantic DLP, invalidate a future content cache, operate approval state across nodes, or establish customer-representative detector quality and independent security review.

### JSON string-key DLP enforcement

The recursive secret/DLP transformer, representative OpenAI and Anthropic JSON-key paths, package artifacts, and installed wheel were exercised locally on August 16, 2026.

- a red-first reproduction proved that an OpenAI-shaped credential in a JSON metadata key previously produced zero detections and would have reached the provider unchanged;
- the new path applies the existing direct and bounded encoded-text detectors to every JSON string key without renaming it. Detect, deny, and approval-required actions retain their ordinary semantics, while a finding configured for redaction denies the complete request because replacing a schema or metadata key can corrupt meaning or collide with another key;
- low-level cases covered a direct fake credential, an encoded fake credential, a valid hyphenated SSN, a detect-only email, and an approval-required organization dictionary value. Every key remained byte-for-byte unchanged in memory, and routine findings retained only rule metadata and counts;
- an OpenAI metadata key, an Anthropic tool-schema property key, and an OpenAI SSN metadata key exercised both provider compatibility paths. All three requests returned provider-shaped `403` responses before egress, made zero provider calls, recorded three denied usage outcomes and metadata-only security events, and left matched values absent from responses, SQLite, and audit representations;
- the complete source suite ran 239 tests: 238 passed and the separately gated official Claude Code executable test skipped. The CI-pinned Codex `0.147.0` and Claude Code `2.1.233` executables then passed their two real-client fake-provider tests independently;
- the frozen 60-task release benchmark passed five iterations with precision `1.00`, recall `1.00`, useful-pack rate `1.00`, mean compression ratio `0.840593`, zero failed safety thresholds, and p95 in-process selection latency `0.167083 ms`; corpus SHA-256 remained `9822d592868202c7c7539bcdac7d4a5894c01f9e6dba7a434846516b67b32c17`;
- isolated source and wheel builds succeeded. The wheel SHA-256 was `956357efc8616f4d7475ca675ae3607a2ff18964321478ca9ccacc01c487ae90`; the source archive SHA-256 was `e9127681ba5734244cae0628a5c67f244fac1862e577c60d5b88c58b5caa2aa9`;
- a clean Python 3.14 environment installed the exact wheel outside the checkout, loaded `hormuz.redaction` from `site-packages`, denied an OpenAI-shaped credential in a JSON key without renaming it, compiled the installed package, displayed CLI help, and passed the installed strict 60-task benchmark with zero failed thresholds;
- source/test bytecode compilation, `git diff --check`, deterministic corpus verification, and high-confidence source and extracted-wheel scans for private-key, OpenAI, Anthropic, GitHub, AWS, and Google credential patterns passed.

This closes the known ordinary JSON-key bypass under accepted ADR 0004, not issue #10 or the enterprise DLP release gate. Caller-controlled provider headers, source classification, whitespace-wrapped or other unsupported encodings, compression, archives, media decoding, semantic evaluation, shared approval/KMS/HA operations, future cache invalidation, customer-representative detector quality, and independent security review remain open.

### Allowlisted provider-header DLP enforcement

The provider-header extraction boundary, recursive secret/DLP transformer, approval binding, OpenAI Responses and Anthropic Messages paths, package artifacts, and installed wheel were exercised locally on August 16, 2026.

- red-first gateway tests proved that all seven forwarded protocol/header slots could carry a fake credential to the fake providers with HTTP `200`, and that an approval-required company term in `OpenAI-Beta` bypassed the approval workflow entirely;
- the gateway now extracts only the exact caller-controlled values it will forward: `Accept` and `User-Agent` for both protocols, `OpenAI-Beta` for OpenAI, and `Anthropic-Version` plus `Anthropic-Beta` for Anthropic. Server-owned `Content-Type` and provider authorization credentials remain outside the caller envelope;
- the existing detector inspects those values, including supported encoded text, without mutating them. Detect-only values audit and forward unchanged, explicit deny blocks, approval-required uses the ordinary non-self workflow, and a credential or DLP finding configured for redaction denies the complete request because rewriting a feature/version header can change protocol semantics;
- direct and encoded fake credentials plus a valid hyphenated SSN exercised every forwarded slot. All nine enforced requests returned provider-shaped `403` responses, made zero provider calls, recorded denied usage and metadata-only security evidence, and left matched values absent from responses, SQLite, and audit representations;
- a detect-only email in `OpenAI-Beta` reached the fake OpenAI endpoint byte-for-byte with one detection and no redaction. An approval-required header term created no provider call, rejected changed header material with a different request ID, permitted one exact approved retry, then rejected replay; the protected value remained absent from durable evidence;
- the complete source suite ran 243 tests: 242 passed and the separately gated official Claude Code executable test skipped. The CI-pinned Codex `0.147.0` and Claude Code `2.1.233` executables then passed their two real-client fake-provider tests independently;
- the frozen 60-task release benchmark passed five iterations with precision `1.00`, recall `1.00`, useful-pack rate `1.00`, mean compression ratio `0.840593`, zero failed safety thresholds, and p95 in-process selection latency `0.154833 ms`; corpus SHA-256 remained `9822d592868202c7c7539bcdac7d4a5894c01f9e6dba7a434846516b67b32c17`;
- isolated source and wheel builds succeeded. The wheel SHA-256 was `8f79653aad8fbb9c607a8156b2d8886994f94a21b95580cbd94a6316e81a9162`; the source archive SHA-256 was `9ae826049bc16ddf2bf24ae7b15dbd446f1e6fea1188dcdab62366c0f24e83be`, and the source archive contained the implementation, tests, and boundary documentation;
- a clean Python 3.14 environment installed the exact wheel outside the checkout, loaded `hormuz.redaction` from `site-packages`, denied a fake credential supplied as unredactable provider material without mutating the request body, compiled the installed package, displayed CLI help, and passed the installed strict 60-task benchmark with zero failed thresholds;
- source/test bytecode compilation, `git diff --check`, deterministic corpus verification, and high-confidence source and extracted-wheel scans for private-key, OpenAI, Anthropic, GitHub, AWS, and Google credential patterns passed.

This closes the known allowlisted provider-header bypass under accepted ADR 0004, not issue #10 or the enterprise DLP release gate. Caller-controlled URL query parameters, source classification, whitespace-wrapped or other unsupported encodings, compression, archives, media decoding, semantic evaluation, shared approval/KMS/HA operations, future cache invalidation, customer-representative detector quality, and independent security review remain open.

### Provider URL-query DLP enforcement

The raw provider-query forwarding boundary, one-pass form decoder, recursive secret/DLP transformer, approval binding, OpenAI Responses and Anthropic Messages paths, package artifacts, and installed wheel were exercised locally on August 16, 2026.

- red-first gateway tests proved that direct and fully percent-encoded fake provider credentials in query names and values reached both fake providers with HTTP `200`, a percent-encoded detect-only email produced no DLP evidence, and an approval-required company term in the query bypassed approval entirely;
- Hormuz now extracts the exact raw query it will forward, form-decodes it once as strict UTF-8 for inspection, and supplies the decoded view to the same detector as provider-bound JSON and allowlisted headers. `%HH` encoding and `+`-encoded spaces are covered; non-UTF-8 percent bytes return a content-free `400` before provider work;
- raw query syntax is never mutated. Detect-only findings audit and forward the original query byte-for-byte, explicit deny blocks, approval-required uses the ordinary non-self workflow, and a credential or DLP rule configured for redaction denies the request because rewriting query syntax can change provider behavior;
- direct OpenAI and Anthropic credentials, fully percent-encoded credentials in a name and value, and a percent-encoded valid SSN all returned provider-shaped `403` responses, made zero provider calls, recorded denied usage and metadata-only security evidence, and left matched values absent from responses, SQLite, and audit representations;
- a fully percent-encoded detect-only email reached the fake OpenAI endpoint with the exact original raw request target, one detection, and no redaction. A query-bearing approval rejected changed raw material with a different request ID, permitted one exact approved retry, and rejected replay; query-free approval fingerprints retain their previous shape for compatibility;
- the complete source suite ran 249 tests: 248 passed and the separately gated official Claude Code executable test skipped. The CI-pinned Codex `0.147.0` and Claude Code `2.1.233` executables then passed their two real-client fake-provider tests independently;
- the frozen 60-task release benchmark passed five iterations with precision `1.00`, recall `1.00`, useful-pack rate `1.00`, mean compression ratio `0.840593`, zero failed safety thresholds, and p95 in-process selection latency `0.160000 ms`; corpus SHA-256 remained `9822d592868202c7c7539bcdac7d4a5894c01f9e6dba7a434846516b67b32c17`;
- isolated source and wheel builds succeeded. The wheel SHA-256 was `69fb7c45d1ce3eddd894529817053bcc6288505822e960b5a77eb0b823b36f04`; the source archive SHA-256 was `ca6431bfb97daf2a4237b8b2b1efdd8e12767923df166c9179bd90417ff23aee`, and the source archive contained the implementation, tests, approval contract, and boundary documentation;
- a clean Python 3.14 environment installed the exact wheel outside the checkout, loaded `hormuz.server` from `site-packages`, decoded a fully percent-encoded fake credential for inspection, denied it without changing the raw query, compiled the installed package, displayed CLI help, and passed the installed strict 60-task benchmark with zero failed thresholds;
- source/test bytecode compilation, `git diff --check`, deterministic corpus verification, and high-confidence source and extracted-wheel scans for private-key, OpenAI, Anthropic, GitHub, AWS, and Google credential patterns passed.

This closes the known ordinary provider-query bypass under accepted ADR 0004, not issue #10 or the enterprise DLP release gate. Provider-specific repeated query decoding, source classification, whitespace-wrapped or other unsupported encodings, compression, archives, media decoding, semantic evaluation, shared approval/KMS/HA operations, future cache invalidation, customer-representative detector quality, and independent security review remain open.

### Policy-filtered Claude Code model discovery

The authenticated model-catalog path, generated client configuration, installed Codex and official Claude Code compatibility, package artifacts, and clean wheel were exercised locally on August 16, 2026. The endpoint follows Anthropic's published [gateway model-discovery contract](https://code.claude.com/docs/en/llm-gateway-protocol#model-discovery); OpenAI's public Codex configuration reference documents custom Responses providers but no equivalent public catalog contract.

- red-first gateway tests proved that the authenticated `GET /v1/models?limit=1000` request returned `404` before implementation;
- the endpoint now accepts the exact documented query, authenticates either the bearer-token or `x-api-key` helper path, resolves the `claude-code` organization/team/person policy, and returns a deterministic list of at most 1,000 allowed Anthropic-route policy aliases whose IDs contain `claude` or `anthropic`;
- the response exposes neither upstream model names nor provider credentials, sends `Cache-Control: no-store`, makes no provider call, reserves no budget, and creates no usage event. Inference remains the authoritative current budget and routing check;
- missing authentication returned `401`; an identity not authorized for Claude Code returned `403`; missing, repeated, non-contract, unknown, and non-UTF-8 query values returned a stable content-free `400`;
- static-token, workload-OIDC, and rotating-session Claude configuration output now opts into discovery. Claude Code `2.1.233` called the real catalog method in a local macOS run and completed generation through the fake Anthropic provider. The blocking cross-platform client gate asserts generation routing; it deliberately does not require the optional picker prefetch because the same client skips that prefetch in noninteractive Ubuntu print mode. The exact authenticated discovery request, response, and side-effect boundary remain blocking deterministic gateway tests;
- installed Codex `0.139.0` still completed its Responses request through Hormuz, proving the Anthropic catalog surface did not break Codex's bundled-model-metadata path;
- the complete source suite ran 253 tests with both real-client gates enabled; all 253 passed with no skips;
- the frozen 60-task release benchmark passed five iterations with precision `1.00`, recall `1.00`, useful-pack rate `1.00`, mean compression ratio `0.840593`, zero failed safety thresholds, and p95 in-process selection latency `0.176417 ms`; corpus SHA-256 remained `9822d592868202c7c7539bcdac7d4a5894c01f9e6dba7a434846516b67b32c17`;
- isolated source and wheel builds succeeded. A clean Python 3.14 environment installed the wheel outside the checkout, loaded `hormuz.server` from `site-packages`, emitted discovery-enabled Claude configuration, compiled the installed package, and passed the installed strict 60-task benchmark;
- source/test bytecode compilation, `git diff --check`, and high-confidence tracked-source and extracted-wheel scans for private-key, OpenAI, Anthropic, GitHub, AWS, and Google credential patterns passed. The ignored `.env.local` remained outside the build and was not needed for any provider request.

This closes Hormuz's authenticated Claude Code model-discovery contract, not guaranteed picker presentation or general client catalog compatibility. Claude Code versions before 2.1.129, `--bare`, disabled nonessential traffic, a noninteractive path that omits the prefetch, or a failed discovery request use cached or built-in picker entries. Those presentation paths cannot bypass Hormuz's inference-time model, client, cap, budget, DLP, and provider-policy enforcement. Hormuz does not claim the response implements Codex's private model-catalog schema.

### Bounded automatic governed-context injection

The accepted ADR 0006 first slice, both provider renderers, policy/failure behavior, metadata lineage, official clients, package artifacts, and clean wheel were exercised locally on August 16, 2026. No live provider credential was used: official clients reached provider-compatible loopback fakes through the real Hormuz transport.

- automatic injection remained disabled by default. Organization/team/actor overlays intersected client/model allowlists, took minimum token/item caps, could strengthen optional to required, and could not enable an organization-off policy or weaken an organization-required policy;
- query extraction used at most 4,096 characters of direct latest-user text and ignored system/developer content, assistant output, tool results, images, and other non-text blocks. OpenAI rendering preserved top-level `instructions`; Anthropic rendering preserved `system` exactly; both emitted the same deterministic delimited user-priority JSON reference and avoided a duplicate identical block;
- gateway requests selected a verified organization-visible retry standard while a verified record from another team, a provisional record, and a high-confidence prompt-injection record were absent from both provider-bound payloads. Required mode denied tool-only and empty-pack requests before egress, and a context repository failure denied even optional mode with a stable content-free `503`;
- a selected record containing a fake provider credential entered the ordinary post-injection secret boundary and the fake provider received only `[REDACTED:HORMUZ_SECRET]`. Budget-reservation assertions matched the exact serialized mutated provider body plus the enforced output cap, proving injected bytes were included;
- usage audit schema version 2 retained mode/outcome/reason, pack and record IDs, policy/retrieval/render versions, repository revision, estimated rendered tokens, assembly time, and reuse state. It did not contain the direct query; the usage SQLite file did not contain the test query bytes. Team/person/model/client/provider reports added injected-request, required-denial, estimated-context-token, and distinct-pack aggregates, and the strict authenticated usage-report contract advanced to version 2;
- Codex `0.147.0` sent an ordinary Responses request and Claude Code `2.1.233` sent an ordinary Messages request. Each required policy, each fake upstream received its authorized context record, and each metadata-only usage event reported `injected`; neither client made an explicit MCP context call;
- all 262 source tests passed with both installed-client gates enabled and no skips;
- the frozen 60-task release benchmark passed five iterations with precision `1.00`, recall `1.00`, useful-pack rate `1.00`, mean compression ratio `0.840593`, zero authorization, stale-lifecycle, dependency-stale, malicious, contradiction, token-budget, leakage, or determinism failures, and p95 in-process selection latency `0.186416 ms`. Corpus SHA-256 remained `9822d592868202c7c7539bcdac7d4a5894c01f9e6dba7a434846516b67b32c17`;
- isolated source and wheel builds contained the injection implementation, contract documentation, and tests. A clean Python 3.14 environment installed the exact wheel outside the checkout, loaded `hormuz.context_injection` from `site-packages`, created the additive usage-lineage columns, compiled the installed package, and passed the bundled 60-task release profile; and
- source/test bytecode compilation, deterministic corpus regeneration, `git diff --check`, and high-confidence tracked-source and extracted-package scans for private-key, OpenAI, Anthropic, GitHub, AWS, and Google credential patterns passed; and
- [GitHub Actions run 31950749576](https://github.com/Xpounder-com/hormuz/actions/runs/31950749576) passed all seven jobs: Python 3.11–3.14, source/wheel build and install, the strict governed-context benchmark, and pinned Codex plus Claude Code compatibility.

This was executable evidence for verified organization/team/actor context on `POST /v1/responses` and `POST /v1/messages`, not closure of issue #5 or an enterprise release. Repository/branch grants and selectors were still open at this checkpoint and are addressed by the later repository-scoped checkpoint below. Tool-only continuation lineage, OpenAI compaction, prompt/context caching, broader client shapes and provider releases, customer-data evaluation, accepted-task cost/quality evidence, hosted tenancy, KMS, HA, and independent review remain open.

### Anthropic token-count context parity

The next accepted-ADR slice was exercised locally on August 16, 2026. No live provider credential was used; deterministic integration tests sent the request to the provider-compatible Anthropic fake through the real Hormuz transport.

- `POST /v1/messages/count_tokens` selected and rendered the same verified organization-visible Context Pack as Anthropic generation, preserved `system` exactly, routed the configured provider model, and returned the content-free pack identifier;
- a fake provider credential inside the selected record was redacted before egress. The provider received only `[REDACTED:HORMUZ_SECRET]`, the context-read audit committed, and the metadata-only DLP security total increased;
- required mode denied both tool-result-only and empty-pack token-count requests before provider egress. A context-store outage denied optional mode with the stable content-free `hormuz_context_unavailable` error and did not expose the internal exception;
- successful, policy-denied, store-failed, and opaque-media-denied token-count cases created no inference-usage rows and reserved no generation budget;
- all 264 source tests passed with official-client gates enabled and no skips. Installed Codex `0.139.0` and Claude Code `2.1.233` each completed ordinary generation through the gateway with an authorized pack, so the additive token-count path did not regress either client;
- the frozen 60-task release benchmark passed five iterations with precision `1.00`, recall `1.00`, useful-pack rate `1.00`, mean compression ratio `0.840593`, zero authorization, stale-lifecycle, dependency-stale, malicious, contradiction, token-budget, leakage, or determinism failures, and p95 in-process selection latency `0.161042 ms`. Corpus SHA-256 remained `9822d592868202c7c7539bcdac7d4a5894c01f9e6dba7a434846516b67b32c17`;
- isolated source and wheel builds succeeded. A clean Python 3.14 environment installed the exact wheel outside the checkout, loaded `hormuz.server` from `site-packages`, verified the token-count operation in the installed implementation, compiled the package, and passed the installed strict 60-task benchmark; and
- source/test bytecode compilation, deterministic corpus verification, `git diff --check`, and high-confidence runtime-source and extracted-wheel scans for private-key, OpenAI, Anthropic, GitHub, AWS, and Google credential patterns passed. The ignored `.env.local` remained outside the build and was not needed for any provider request.

This establishes token-count parity for a request containing direct current-user text. It does not bind a tool-only count to earlier context lineage, prove live Anthropic billing behavior, add OpenAI compaction, or close issue #5. Those remain explicit compatibility and product-decision gates.

### Repository-scoped automatic context injection

The accepted ADR 0006 repository-selection slice, effective policy overlays, supported-client configuration, trusted lifecycle validation, consumed-header DLP boundary, content-free evidence, source package, wheel, and installed package were exercised locally on August 16, 2026. No live provider credential or endpoint was used; both official clients reached provider-compatible loopback fakes through the real gateway.

- the organization repository grant defaults to an empty set. Exact `allowed_repositories` values intersect across organization/team/actor overlays, so a lower scope cannot add a repository the organization omitted. `max_classification` resolves to the narrowest policy cap and authenticated-identity clearance;
- Hormuz accepts at most one bounded safe `X-Hormuz-Repository`, `X-Hormuz-Branch`, and `X-Hormuz-Revision`. Repository is checked against the effective grant, revision requires branch, and revision must equal the trusted lifecycle snapshot for the exact organization/repository/branch. A selector never creates authorization or overrides trusted state;
- deterministic OpenAI and Anthropic gateway tests selected only the verified `acme/api`/`main` record at the trusted revision. Matching records from another repository, another branch, and a higher classification were absent. The fake providers received neither repository, branch, nor revision header;
- a stale revision in optional mode excluded repository records and continued with `repository_revision_mismatch`; ungranted and duplicate selectors in required mode denied before a repository read or provider call. The ungranted sentinel was absent from usage objects and SQLite;
- a valid consumed selector passed through the ordinary DLP kernel. Deny policy stopped egress with metadata-only evidence, and a non-self approval was bound to the exact consumed repository/branch map: changing branch could not consume the approved grant, while the exact retry consumed it once. Scope values were absent from approval/security evidence and never reached the provider;
- `client-config` emitted Codex custom-provider `http_headers` and Claude Code `ANTHROPIC_CUSTOM_HEADERS` for static, OIDC-helper, and secure-session configurations. Installed Codex `0.139.0` and official Claude Code `2.1.233` each sent those headers, received the authorized repository record, and completed generation through the fake provider without an explicit MCP context call;
- the usage event retained pack/record IDs, policy/retrieval/render versions, and only the trusted lifecycle revision. It retained no raw repository/branch/revision header, query, prompt, response, or context content. The separate context-read audit may retain the validated repository and branch as authorized scope IDs;
- all 296 source tests passed with both official-client gates enabled and no skips;
- the frozen 60-task release benchmark passed five iterations with precision `1.00`, recall `1.00`, useful-pack rate `1.00`, mean compression ratio `0.840593`, zero authorization, stale-lifecycle, dependency-stale, malicious, contradiction, token-budget, leakage, or determinism failures, and p95 in-process selection latency `0.153583 ms`. Corpus SHA-256 remained `9822d592868202c7c7539bcdac7d4a5894c01f9e6dba7a434846516b67b32c17`;
- isolated source and wheel builds succeeded. A clean Python `3.14.0` environment installed the built wheel outside the checkout, loaded Hormuz from `site-packages`, emitted a granted repository-scoped Codex configuration, compiled the installed package, and passed the installed strict benchmark with precision and recall `1.00`; and
- deterministic corpus verification, source/test bytecode compilation, `git diff --check`, and high-confidence runtime-source plus extracted-wheel scans for private-key, OpenAI, Anthropic, GitHub, and AWS credential patterns passed.

This closes the exact administrator-granted repository/branch/trusted-revision selector slice under issue #5, not the issue or the enterprise release. Automatic working-directory discovery, tool-only continuation lineage, OpenAI compaction, provider/Hormuz context caching, cross-node state, customer-data evaluation, accepted-task economics, hosted tenancy, KMS, HA, immutable audit, and independent review remain open. The provider-specific continuation identifier and TTL design remains a material owner decision and was not implemented in this checkpoint.

### Encoded compression/archive container denial

The next accepted-ADR-0004 slice was exercised locally on August 16, 2026. No archive was decompressed and no live provider credential was used. Deterministic tests passed signature-shaped payloads through the real gateway to provider-compatible loopback fakes.

- the bounded base64/base64url/data-URI decoder recognized ZIP, GZIP, BZIP2, XZ, 7z, RAR, TAR, Zstandard, and LZ4 using exact binary signatures or allowlisted declared media types before attempting UTF-8 classification;
- the secure-default `opaque_media` rule emitted one high-confidence `unsupported_media` finding and denied the original encoded value before egress. It did not decompress, decrypt, list, transform, or persist container content;
- unit coverage proved three-layer nested recognition, organization `off` as string-local risk acceptance, provider and exact routed-model scoping, continued sibling SSN redaction, and unchanged handling of benign encoded text and deliberately unclassified binary prefixes;
- OpenAI function output and Anthropic tool-result requests carrying the same encoded ZIP-signature payload both returned the existing provider-shaped DLP denial, made zero provider calls, and wrote only metadata-only security and denied-usage evidence. Neither the marker nor encoded payload entered responses, audit rows, or SQLite;
- all 267 source tests passed with official-client gates enabled and no skips. Installed Codex `0.139.0` and Claude Code `2.1.233` each completed ordinary generation through Hormuz, proving the fail-closed container path did not regress either supported employee client;
- the frozen 60-task release benchmark passed five iterations with precision `1.00`, recall `1.00`, useful-pack rate `1.00`, mean compression ratio `0.840593`, zero authorization, stale-lifecycle, dependency-stale, malicious, contradiction, token-budget, leakage, or determinism failures, and p95 in-process selection latency `0.160167 ms`. Corpus SHA-256 remained `9822d592868202c7c7539bcdac7d4a5894c01f9e6dba7a434846516b67b32c17`;
- isolated source and wheel builds succeeded. A clean Python 3.14 environment installed the exact wheel outside the checkout, loaded `hormuz.redaction` from `site-packages`, denied an encoded ZIP-signature payload, compiled the package, and passed the installed strict 60-task benchmark; and
- source/test bytecode compilation, deterministic corpus verification, `git diff --check`, and high-confidence runtime-source and extracted-wheel scans for private-key, OpenAI, Anthropic, GitHub, AWS, and Google credential patterns passed. The ignored `.env.local` remained outside the build and was not needed for any provider request.

This closed recognition and default denial for the named encoded container formats, not archive-content inspection or issue #10. At that checkpoint, whitespace-wrapped and unsupported encodings, unknown or obfuscated binary/container formats, provider file/URL fetching, source classification, semantic evaluation, cache invalidation, shared KMS/HA operations, externally immutable audit, organization-representative evaluation, and independent security review remained open.

### ASCII MIME-whitespace encoded-payload enforcement

The next accepted-ADR-0004 slice was exercised locally on August 16, 2026. No live provider credential was used. The source and installed-package checks used deterministic fake credentials and signature-shaped archive bytes.

- red-first detector coverage proved that a fake provider credential hidden in base64 separated by spaces, horizontal tabs, carriage returns, and line feeds was allowed before the change. The corresponding gateway reproduction proved that the same wrapping around a ZIP-signature payload reached both provider-compatible fakes with HTTP `200`;
- Hormuz now removes only those four ASCII MIME whitespace characters for bounded base64/base64url and data-URI decoding. Safe and detect-only values remain byte-for-byte unchanged; when a redaction changes decoded text, Hormuz emits canonical base64 with the original data-URI prefix and padding convention. Non-ASCII whitespace is deliberately not normalized;
- the existing 1 MiB decoded-value limit is paired with a 2,796,208-character wrapped-payload cap. Oversized declared payloads fail closed before decoding, and the nesting check now runs before text or archive classification so a recognized archive behind a fourth encoded layer cannot bypass the three-layer limit;
- unit coverage exercised ordinary strings, textual data URIs, redaction, benign unchanged forwarding, non-ASCII-whitespace non-classification, whitespace amplification, nested archives, object-local `opaque_media: off`, provider/model scope, and sibling DLP. The focused cross-boundary matrix also covered JSON keys, forwarded headers, provider queries, ordinary base64, oversized encoded text, and both provider paths;
- OpenAI function output and Anthropic tool-result requests carrying the same MIME-wrapped ZIP-signature payload both returned the existing provider-shaped DLP denial, made zero provider calls, and retained only metadata-only `opaque_media` findings and denied-usage outcomes. The marker and complete wrapped payload were absent from responses, audit representations, and SQLite;
- all 268 source tests passed with the installed-client gates enabled and no skips. Installed Codex `0.139.0` and Claude Code `2.1.233` each completed ordinary generation through their provider-compatible fake, proving that the stricter decoder did not regress either supported employee workflow;
- the frozen 60-task release benchmark passed five iterations with precision `1.00`, recall `1.00`, useful-pack rate `1.00`, mean compression ratio `0.840593`, zero authorization, stale-lifecycle, dependency-stale, malicious, contradiction, token-budget, leakage, or determinism failures, and p95 in-process selection latency `0.158500 ms`. Corpus SHA-256 remained `9822d592868202c7c7539bcdac7d4a5894c01f9e6dba7a434846516b67b32c17`;
- isolated source and wheel builds succeeded. A clean Python 3.14 environment installed the exact wheel outside the checkout, loaded `hormuz.redaction` from `site-packages`, redacted MIME-wrapped fake credential text, denied a MIME-wrapped ZIP-signature payload, compiled the package, and passed the installed strict benchmark; and
- source/test bytecode compilation, deterministic corpus verification, and `git diff --check` passed.

This closes ASCII MIME-whitespace normalization for supported base64 forms, not arbitrary transfer decoding or issue #10. Non-ASCII whitespace, percent/hex and other encodings, unknown or obfuscated binary/container formats, archive-content inspection, provider file/URL fetching, source classification, application-specific repeated query decoding, semantic and organization-specific evaluation, cache invalidation, shared KMS/HA operations, externally immutable audit, and independent review remain open.

### Content-free organization DLP detector evaluation

The next accepted-ADR-0004 slice was exercised locally on August 16, 2026. No provider credential or live provider endpoint was used. The content-bearing inputs were synthetic or test-local and the emitted evidence was aggregate only.

- red-first checks established that neither the `hormuz.dlp_evaluation` domain module nor `hormuz dlp evaluate` command existed before this slice;
- the new offline command loads one enabled organization rule and one exact configured upstream protocol/model scope, then runs that detector through the same bounded provider-aware `SecretRedactor` kernel in detect mode. It does not call a provider or gateway, change policy, create an approval, or open the usage, security, context, or session stores;
- strict UTF-8 JSONL validation rejects duplicate members, unknown case fields, non-standard constants, invalid labels, empty corpora, excessive detector nesting, more than 10,000 cases, and inputs over 25 MiB. Content-bearing payloads are hidden from object representations and bounded validation/detector errors;
- schema `hormuz.dlp-evaluation.v1` records deterministic detector version `hormuz-deterministic-v1`, package/runtime versions, safe rule and scope metadata, an administrator-controlled corpus version, aggregate finding/case counts, confusion matrix, and derived metrics. It explicitly records that payloads, matched values, case IDs, and corpus hashes are absent and that policy promotion is manual;
- direct and CLI tests proved one true positive, true negative, false positive, and false negative; null-safe metric calculation; encoded organization-dictionary detection; provider/model-scope rejection; no dictionary value or environment-name disclosure; content-free invalid-corpus and detector-failure errors; no partial evidence on failure; overwrite refusal; and private `0600` output;
- the checked-in synthetic installation fixture produced one true positive and one true negative. That is format/package evidence only and is not represented as an organization-representative evaluation or evidence that the low-confidence email rule should move beyond detect-only;
- all 277 source tests passed with installed-client gates enabled and no skips. Codex `0.139.0` and the cached official Claude Code `2.1.233` package each completed ordinary generation through Hormuz and provider-compatible loopback fakes;
- the frozen 60-task release benchmark passed five iterations with precision `1.00`, recall `1.00`, useful-pack rate `1.00`, mean compression ratio `0.840593`, zero authorization, stale-lifecycle, dependency-stale, malicious, contradiction, token-budget, leakage, or determinism failures, and p95 in-process selection latency `0.154833 ms`. Corpus SHA-256 remained `9822d592868202c7c7539bcdac7d4a5894c01f9e6dba7a434846516b67b32c17`;
- isolated source and wheel builds included the evaluator, documentation, tests, and synthetic fixture. A clean Python `3.14.0` environment installed the exact wheel outside the checkout, loaded `hormuz.dlp_evaluation` from `site-packages`, ran the packaged evaluator to a `0600` aggregate report, compiled the installed package, and passed the installed strict 60-task benchmark; and
- source/test bytecode compilation, deterministic corpus verification, `git diff --check`, and high-confidence runtime-source, extracted-wheel, and tracked-environment-file credential scans passed.

This completes the reusable content-free evaluation mechanism, not the customer-specific ADR gate or issue #10. An organization must still freeze and review a representative corpus, set acceptable false-positive/false-negative thresholds, approve any action change, and retain that decision through its security process. Source-path classification, semantic detection, unknown encodings/containers, archive-content inspection, application-specific repeated query decoding, multi-node approval operations, cache invalidation, shared KMS/HA, externally immutable audit, and independent security review remain open.

### Bounded nested provider-query DLP enforcement

The accepted-ADR-0004 provider-query boundary was exercised locally on August 16, 2026. No live provider credential or endpoint was used; deterministic requests reached provider-compatible loopback fakes through the real Hormuz transport.

- red-first unit coverage proved that the existing helper stopped after one form-decoded view, leaving a further percent-encoded representation uninspected;
- Hormuz now applies one strict UTF-8 form decode followed by percent-only decoding through at most three distinct views. A `+` produced from `%2B` remains a literal plus on later passes, invalid UTF-8 at any pass fails closed, and a fourth changing layer returns a stable content-free `400` before provider work;
- all decoded views represent one raw query. The same rule visible in multiple views contributes the maximum per-view count once, preserving intermediate exact-value detection without inflating routine evidence;
- nested OpenAI- and Anthropic-shaped fake credentials returned the existing provider-shaped secret denials with one finding each and zero provider calls. Neither value entered the response, security evidence, or SQLite;
- a doubly encoded detect-only email reached the fake OpenAI endpoint with the exact original raw query, one detection, and no transformation. Existing query-bearing approval tests continued to bind the exact raw query, reject changed material, permit one exact approved retry, and reject replay;
- all 302 source tests passed with both official-client gates enabled and no skips. Installed Codex `0.139.0` and Claude Code `2.1.233` each completed their ordinary provider-compatible gateway path;
- the frozen 60-task release benchmark passed five iterations with precision `1.00`, recall `1.00`, useful-pack rate `1.00`, mean compression ratio `0.840593`, zero authorization, stale-lifecycle, dependency-stale, malicious, contradiction, token-budget, leakage, or determinism failures, and p95 in-process selection latency `0.158292 ms`. Corpus SHA-256 remained `9822d592868202c7c7539bcdac7d4a5894c01f9e6dba7a434846516b67b32c17`;
- isolated source and wheel builds succeeded. A clean Python `3.14.0` environment installed the exact wheel outside the checkout, loaded Hormuz from `site-packages`, produced two bounded views for a nested fake credential, denied the one logical source with count one, compiled the installed package, displayed the installed CLI, and passed the installed strict benchmark with zero failed thresholds; and
- source/test bytecode compilation, deterministic corpus verification, `git diff --check`, and high-confidence runtime-source plus extracted-wheel scans for private-key, OpenAI, Anthropic, GitHub, AWS, and Google credential patterns passed.

This closes the ordinary bounded nested-percent query bypass, not issue #10 or the enterprise DLP release gate. Hormuz does not model application-specific parsing beyond the three decoded views, and source classification, semantic detection, unsupported encodings and container contents, provider file/URL retrieval, organization-representative threshold approval, future cache invalidation, shared approval/KMS/HA operations, externally immutable audit, and independent review remain open.

### Content-free application observability

The owner-approved content-free default was exercised locally on August 16, 2026 across the built-in HTTP access logger, provider-connection error boundary, both provider transports, installed clients, package artifacts, and clean wheel. No live provider credential or endpoint was used.

- the audit identified that verbose mode inherited a raw HTTP request-target logger. That target can contain provider query values, OAuth authorization codes/state, or an attacker-controlled path even when databases and structured audit events remain content-free;
- Hormuz now logs only the allowlisted canonical route or a fixed dynamic template/`unknown`, method, query-presence flag, status, and response-size metadata when available. The fallback server/protocol logger also discards caller-controlled formatting details;
- adversarial gateway tests placed distinct markers in an OpenAI query, prompt, unknown path, OAuth callback code/state, simulated internal provider exception, and upstream credential environment-variable name. None appeared in captured debug/error logs or public error bodies. The provider query still reached the fake provider unchanged in the detect-only case, proving privacy did not come from dropping compatibility data;
- provider-unavailable responses now use one fixed public message. Missing-credential responses no longer disclose the configured environment-variable name;
- all 304 source tests passed with no skips while installed Codex `0.139.0` and official Claude Code `2.1.233` each completed their ordinary provider-compatible Hormuz path;
- the frozen 60-task release benchmark passed five iterations with precision `1.00`, recall `1.00`, useful-pack rate `1.00`, mean compression ratio `0.840593`, zero authorization, stale-lifecycle, dependency-stale, malicious, contradiction, token-budget, leakage, or determinism failures, and p95 in-process selection latency `0.154708 ms`. Corpus SHA-256 remained `9822d592868202c7c7539bcdac7d4a5894c01f9e6dba7a434846516b67b32c17`;
- isolated source and wheel builds succeeded. The final install-tested local wheel SHA-256 was `71380516bc0d6fb1066ff4287d4d97b061e49550ba7bb036ac2e15633f774d65`; the source archive is not self-hashed inside this embedded record because changing the record necessarily changes that archive;
- a clean Python `3.14.0` environment installed that exact wheel outside the checkout, loaded `hormuz.server` from `site-packages`, confirmed the packaged canonical logger, displayed the installed CLI, and passed the bundled regression benchmark; and
- deterministic corpus verification, source/test bytecode compilation, local Markdown-link validation, `git diff --check`, and high-confidence runtime-source plus extracted-wheel scans for private-key, OpenAI, Anthropic, GitHub, AWS, Google, Slack, and Hormuz-session credential patterns passed.

This verifies Hormuz's application-level observability boundary, not every deployment component. Reverse proxies, load balancers, service meshes, crash reporters, packet captures, and provider-side storage must independently disable raw URL/body/header capture. The governed-context repository and its private content export remain intentionally content-bearing and require the still-open hosted encryption, KMS, retention, deletion, tenancy, backup, and independent-review gates.

### Content-free storage schema conformance

The structural persistence backstop for the owner-approved content-free default was exercised locally on August 16, 2026.

- `hormuz.content-free-schema.v1` enumerates the exact permitted columns for nine gateway usage/security/billing tables, one session-security table, and five governed-context audit tables;
- fresh usage, session, and context stores matched their manifests exactly, while the existing legacy usage and session migrations plus context schema versions 1, 2, and 3 continued to reach the accepted schemas without losing their prior migration coverage;
- adding `prompt` to usage telemetry, `raw_query` to session-security audit, or `source_content` to context-read audit caused the corresponding store to refuse its next startup with only `content_free_schema_incompatible`;
- canonical context records/lifecycle snapshots and session credential/authentication-state tables are explicitly absent from the telemetry manifest, preventing the product from misrepresenting intentionally content-bearing or secret-bearing storage as content-free;
- audit export remains independently allowlisted, so an unknown physical column introduced after process initialization still does not enter JSONL output before restart-time drift detection;
- all 309 source tests passed. The opt-in official-client cases were then run explicitly with installed Codex `0.139.0` and pinned official Claude Code `2.1.233`; both completed provider-compatible requests through Hormuz and loopback fakes;
- the frozen 60-task release benchmark passed five iterations with precision `1.00`, recall `1.00`, useful-pack rate `1.00`, mean compression ratio `0.840593`, zero authorization, stale-lifecycle, dependency-stale, malicious, contradiction, token-budget, leakage, or determinism failures, and p95 in-process selection latency `0.162667 ms`. Corpus SHA-256 remained `9822d592868202c7c7539bcdac7d4a5894c01f9e6dba7a434846516b67b32c17`;
- isolated source/wheel builds succeeded. A clean Python `3.14.0` environment installed the wheel outside the source checkout, loaded `hormuz.content_free` from `site-packages`, initialized all three store types, observed all 15 manifested tables, compiled the installed package, displayed the installed CLI, and passed the installed regression benchmark; and
- deterministic corpus verification, source/test bytecode compilation, local Markdown-link validation, `git diff --check`, and high-confidence runtime-source plus extracted-wheel scans for private-key, OpenAI, Anthropic, GitHub, AWS, Google, Slack, and Hormuz-session credential patterns passed.

This manifest prevents silent column drift; it does not replace value validation, database access controls, encryption, retention/deletion, immutable export, or deployment-component logging policy. The hosted database topology and KMS boundary remain owner-pending enterprise decisions.

### Process health and bounded graceful draining

The process-level health and shutdown slice was exercised locally on August 16, 2026 through the real HTTP server, provider-compatible loopback fakes, signal callback, package artifacts, and a clean installed wheel. No live provider credential or endpoint was used.

- unauthenticated `GET /health/live` and `GET /health/ready` returned exact `hormuz.health.v1` content-free responses with `Cache-Control: no-store`. The compatibility `/health` path retained its ready-state `status=ok` and feature metadata;
- after readiness withdrawal, `/health/live` remained `200`, `/health/ready` and `/health` returned `503 draining` plus `Retry-After: 1`, and later implemented application work returned a fixed `503 gateway_draining` before authentication, request-body processing, policy, storage, or provider work;
- request admission and drain transition are atomic. A provider request admitted before the transition completed successfully, while a later request was rejected and produced no provider or usage-ledger side effect;
- the `SIGTERM` callback now starts `shutdown()` on one helper thread rather than the serving thread, repeated signals are idempotent, and the configured `listen.shutdown_grace_seconds` is bounded from 1 through 300 with a default of 30;
- in-flight accounting waits only for parsed requests admitted before drain. A held provider request remained visible until completion, while an idle HTTP/1.1 keep-alive socket counted as zero and could not block listener shutdown or `server_close()`;
- grace expiry returned a failed process exit and logged only the remaining request count. The health query marker, rejected prompt, provider credentials, and request content were absent from the fixed health/drain responses;
- all 316 source tests passed with no skips. Installed Codex `0.139.0` and pinned official Claude Code `2.1.233` each completed their ordinary provider-compatible request through Hormuz;
- the frozen 60-task release benchmark passed five iterations with precision `1.00`, recall `1.00`, useful-pack rate `1.00`, mean compression ratio `0.840593`, zero authorization, stale-lifecycle, dependency-stale, malicious, contradiction, token-budget, leakage, or determinism failures, and p95 in-process selection latency `0.168208 ms`. Corpus SHA-256 remained `9822d592868202c7c7539bcdac7d4a5894c01f9e6dba7a434846516b67b32c17`;
- isolated source/wheel builds succeeded;
- a clean Python `3.14.0` environment installed that exact wheel outside the source checkout, loaded `hormuz.server` from `site-packages`, compiled the installed package, displayed the 30-second grace through `doctor`, and passed the installed regression benchmark. The source archive contained the operations contract, config, implementation, and regression tests; and
- source/test bytecode compilation, deterministic benchmark execution, local Markdown-link validation, `git diff --check`, and high-confidence runtime-source plus extracted-wheel scans for private-key, OpenAI, Anthropic, GitHub, AWS, Google, Slack, and Hormuz-session credential patterns passed.

This verifies a shallow single-process liveness/readiness and graceful-drain foundation, not production deployment issue #11. Plain HTTP remains limited to loopback or a separately hardened private TLS boundary. Deep dependency policy, shared load-balancer coordination, container/reference deployment, multi-node persistence, HA/failover, backup/restore, RPO/RTO, deployment-component telemetry review, and independent security review remain open.

### Signed private-container release contract

The tag, source, privilege, signing, provenance, package, evidence, and rollback contract was exercised locally on August 16, 2026 without creating a tag, registry package, signature, attestation, or GitHub release.

- strict release inputs bind an annotated `vX.Y.Z` tag to the identical `pyproject.toml` version, full event ref/SHA, exact `Xpounder-com/hormuz` repository, and a commit reachable from `main`. Temporary real Git repositories proved acceptance of an annotated main-history tag and rejection of a lightweight or unmerged tag;
- publication permissions are split across verification, package/signing, and GitHub-release jobs. All Actions are pinned to immutable commits, no `latest` tag exists, and version/revision aliases must resolve to the verified digest;
- the live repository default was changed from write-capable Actions tokens to `read`, and workflow-token pull-request approval was disabled. Jobs that need additional authority must now request it explicitly;
- public-good Sigstore metadata disclosure is fail-closed unless `HORMUZ_SIGSTORE_RELEASE_APPROVAL=sigstore-public-transparency-v1` records owner acceptance. No such approval or remote release was assumed by this checkpoint;
- official action contracts were reviewed at their pinned commits. Digest-verified actionlint `v1.7.12` plus digest-verified ShellCheck `v0.11.0` reported zero workflow, expression, or embedded-shell errors;
- all 329 source tests passed with the installed Codex path enabled; the environment-gated official Claude Code case was the sole local skip. The ordinary CI compatibility job continues to install and exercise both pinned clients, while a real tag workflow requires the Claude path inside its complete suite;
- the frozen 60-task strict release benchmark passed five iterations, and deterministic corpus verification retained SHA-256 `9822d592868202c7c7539bcdac7d4a5894c01f9e6dba7a434846516b67b32c17`;
- isolated source and wheel builds succeeded. A fresh Python 3.13 environment installed the wheel outside the checkout and displayed the installed CLI;
- the version-labeled reference image passed the restricted non-root/read-only/capability-drop/health/authentication/persistence/credential-response/SIGTERM smoke contract;
- an unpublished OCI archive built successfully for both `linux/amd64` and `linux/arm64`, with per-platform BuildKit attestation manifests;
- digest-verified Trivy `v0.74.0` found zero high or critical OS/Python findings, including unfixed findings, and emitted a CycloneDX `1.7` SBOM with 43 components; and
- source/test bytecode compilation and `git diff --check` passed.

This proves the local and static automation contract, not a signed image or registry release. A real tag run must still prove Actions package permission, private repository linkage, OIDC certificate identity, public transparency behavior, remote digest aliases, Cosign signature and SLSA-attestation verification, and final release assets. Tag governance, protected release review, package retention/access, customer-registry/KMS options, TLS, shared persistence, HA, backup/restore, RPO/RTO, and independent security review remain open.

### Safe model-metadata boundary

A focused failure-first security review of model routing metadata was exercised locally on August 16, 2026. No live provider credential or endpoint was used.

- fallback routing previously allowed an arbitrary caller-supplied requested-model value to survive policy selection and reach logs plus the `X-Hormuz-Requested-Model` response header. Control characters could construct an unsafe header value, while non-Latin text could terminate the request thread during header encoding;
- request-time model identifiers now require a bounded ASCII identifier before policy evaluation, provider work, persistence, logging, or response headers. Unsafe values return a fixed `400 invalid_request` response;
- configured route aliases and upstream model identifiers use the same 512-character grammar, preventing an administrator-controlled route from recreating the header/log boundary at startup;
- adversarial tests covered CRLF, Unicode, and overlong identifiers. Each produced zero provider calls and zero usage-ledger events, and the CRLF marker was absent from response headers;
- the complete 331-test source suite passed in `102.680` seconds. The environment-gated official Claude Code case was the sole local skip; and
- source/test bytecode compilation and `git diff --check` passed.

This closes the identified model-metadata injection and request-thread failure path, not the independent security-review gate. Other provider-derived or deployment-generated metadata, reverse-proxy behavior, TLS, shared persistence, KMS, HA, backup/restore, and the broader enterprise review remain open.

### Bounded provider-request metadata

A focused failure-first review of caller-controlled provider-request metadata was exercised locally on August 16, 2026 through real Hormuz HTTP transport and provider-compatible OpenAI and Anthropic loopback fakes. No live provider credential or endpoint was used.

- red-first cases proved that a folded `OpenAI-Beta`, folded `Anthropic-Version`, 1,025-byte `User-Agent`, and non-ASCII `Anthropic-Beta` each crossed Hormuz, reached the fake provider, and received HTTP `200` before the change;
- the exact allowlist—`Accept` and `User-Agent` for both protocols, `OpenAI-Beta` for OpenAI, and `Anthropic-Version` plus `Anthropic-Beta` for Anthropic—now passes one gateway-owned limit of at most 1,024 bytes of visible ASCII plus horizontal tab;
- folded, control-bearing, non-ASCII, or overlong values return a fixed provider-shaped `400 invalid_request` before DLP, policy accounting, provider work, or usage persistence. The adversarial marker was absent from downstream headers and bodies, with zero provider calls and zero usage events;
- ordinary OpenAI non-streaming and Anthropic streaming requests remained compatible, and safe credential/DLP-bearing allowlisted headers still reached the existing fail-closed detector and metadata-only evidence path;
- the complete 336-test source suite passed in `104.537` seconds. The environment-gated official Claude Code case was the sole local skip; and
- source/test bytecode compilation and `git diff --check` passed.

This closes the identified application-level provider-request metadata path, not every ingress component. A reverse proxy, load balancer, service mesh, or WAF must independently reject malformed request metadata before logging or forwarding it. TLS ingress, shared persistence, HA, KMS, backup/restore, and independent security review remain separate gates.

### Bounded provider-response metadata

A focused failure-first review of the provider-response boundary was exercised locally on August 16, 2026 through real Hormuz HTTP transport and a provider-compatible loopback fake. No live provider credential or endpoint was used.

- red-first tests proved that folded `Content-Type`, provider request ID, processing-time, and rate-limit values could cross into downstream response metadata, while arbitrary printable provider model and unbounded request-ID values could enter the content-free usage ledger;
- Hormuz now applies gateway-owned header-name, visible-ASCII, length, identifier, and duplicate rules before provider metadata reaches `send_header` or storage. Unsafe fields are omitted and counted through a content-free diagnostic without retaining their values;
- the usage parser and store independently require safe bounded provider model and request identifiers, preventing a future caller from bypassing the transport boundary;
- adversarial CRLF/folded-header, Unicode, content-like, duplicate, and overlong values were absent from downstream metadata and the usage audit. The provider body still reached the caller unchanged, proving that compatibility did not depend on dropping the response;
- a valid OpenAI request ID continued to reach both the employee response and usage ledger for provider reconciliation;
- the complete 333-test source suite passed in `103.093` seconds. The environment-gated official Claude Code case was the sole local skip; and
- source/test bytecode compilation and `git diff --check` passed.

This closes the identified application-level provider-response metadata path, not production deployment issue #11. Reverse proxies, TLS termination, service meshes, provider-side content, shared persistence, HA, KMS, backup/restore, and independent security review remain separate gates.

### Bounded provider-usage accounting parser

A focused failure-first review of the provider-response accounting parser was exercised locally on August 16, 2026 through parser-level adversarial inputs, real Hormuz HTTP relay, and provider-compatible OpenAI and Anthropic loopback fakes. No live provider credential or endpoint was used.

- red-first tests proved that the pre-change SSE and non-stream accumulators could retain at least one byte beyond their intended ceilings, while a newline-free SSE event had no application-owned bound. A size-bounded but deeply nested SSE value also raised `RecursionError` through the gateway accounting path;
- the accounting parser now caps its input buffer at 1 MiB for one SSE line and 10 MiB for one non-stream JSON response. It discards an oversized line until the next newline, ignores malformed/deep accounting values, recovers for later valid events, and releases its transient buffers at completion;
- the real gateway relayed an oversized SSE line byte-for-byte to the employee, then parsed the later terminal Anthropic usage and recorded the ordinary estimated event. The bound therefore protects internal metadata parsing without truncating the provider response;
- complete valid input/output usage from a non-stream response or terminal stream event is now required for `cost_basis=estimated`. Initial/provisional, missing, incomplete, oversized, or malformed successful-response usage produces an explicit `not_available` event with zero estimated cost, so reporting surfaces it as unpriced rather than free;
- ordinary OpenAI non-streaming and Anthropic streaming usage, native metadata allowlisting, actual-model capture, cache/reasoning categories, and provider-compatible response relay remained intact;
- the complete 343-test source suite passed in `105.927` seconds. The environment-gated official Claude Code case was the sole local skip; and
- source/test bytecode compilation and `git diff --check` passed.

This closes the identified per-request accounting-buffer and false-zero-estimate paths, not provider invoice completeness or complete process-wide resource governance. A later checkpoint added parsed-request capacity and a total provider-response relay deadline; pre-parse connection/header limits, provider-side behavior, workload egress, final invoice/credit reconciliation, shared persistence, HA, KMS, disaster recovery, and independent security review remain open gates.

### Parsed-request capacity and total provider-response deadline

A failure-first single-process resource review was exercised locally on August 16, 2026 through real Hormuz HTTP transport and continuously trickling OpenAI- and Anthropic-compatible loopback streams. No live provider credential or endpoint was used.

- the pre-change configuration and admission path had no request-capacity ceiling: every syntactically parsed request was counted as active unless draining had begun. The pre-change provider timeout was a socket-inactivity timeout; a provider sending one byte every 10 milliseconds kept the response alive past the five-second test-client timeout;
- `listen.max_concurrent_requests` now defaults to `128`, accepts only `1` through `10000`, and defaults safely for existing configuration files. One condition-protected check admits or rejects each parsed non-health request before authentication, body reading, policy, storage, or provider work;
- saturation returns a fixed content-free `503 gateway_busy`, `Retry-After: 1`, and connection closure. Liveness remains `200`; health probes consume no application slot; readiness reports `503 busy` at capacity and recovers to `200 ready` after the admitted request releases its slot;
- `upstream_timeout_seconds` now governs one wall-clock deadline from provider open through single-read response acquisition and downstream relay. Each provider read and downstream write is tightened to the remaining interval, preventing a continuous trickle from resetting the limit indefinitely;
- deadline expiry before downstream headers returns a fixed provider-shaped `504 gateway_upstream_timeout`. Expiry after response start closes the partial provider-compatible response. Both paths release the budget reservation and admission slot and record an accounted failure; missing terminal usage remains explicitly `not_available` and unpriced. Logs and responses did not contain the adversarial prompt or invalid credential marker;
- focused configuration, saturation, readiness, drain, deadline, installed-operator diagnostics, and ordinary OpenAI/Anthropic compatibility tests passed. An interim broader gateway, CLI, and authentication run passed 131 tests in `92.680` seconds with the existing environment-gated Claude case skipped; and
- the final complete 348-test source suite passed in `111.488` seconds with that same single skip;
- isolated source and wheel builds succeeded. The exact clean-install-tested wheel SHA-256 was `fb1f380372144fd642425994cfdcaad4a45d3eefa5bf40ac1ca9f2c5b1df3ed8`; the source archive is not self-hashed inside this embedded record because changing the record changes that archive;
- a clean Python 3.12 environment outside the checkout imported `hormuz.server` from `site-packages`, displayed the effective `128`-request capacity and `600`-second response deadline through installed `doctor`, compiled the installed package, and passed the installed strict 60-task benchmark with precision, recall, and useful-pack rate `1.00`, zero safety failures, mean compression `0.840593`, and governed p95 selection latency `0.166791 ms`; and
- deterministic corpus verification retained SHA-256 `9822d592868202c7c7539bcdac7d4a5894c01f9e6dba7a434846516b67b32c17`. Source/test bytecode compilation, `git diff --check`, and high-confidence tracked-source plus extracted-wheel credential scans passed.

This closes parsed-application-request concurrency and provider-response relay duration for one Hormuz process, not production deployment issue #11. TCP connections and threads before parsing, a complete ingress-header deadline, operating-system DNS resolution, reverse-proxy/WAF slow-client controls, cross-replica capacity, TLS ingress, shared persistence, HA, backup/restore, RPO/RTO, KMS, and independent security review remain open.

### Pre-parse connection capacity and total request-header deadline

A failure-first transport-boundary review was exercised locally on August 16, 2026 through real TCP sockets against the Hormuz HTTP server. No live provider credential or endpoint was used.

- source inspection confirmed that Python's `ThreadingHTTPServer` created a new worker before Hormuz's parsed-request admission check, while request-line and header reads had no application-owned deadline. Incomplete or continuously trickled headers could therefore retain unbounded workers;
- `listen.max_connections` now defaults to `256`, accepts only `1` through `10000`, and defaults safely for existing configuration files. The accepted-connection slot is acquired before the standard library creates a handler thread and is released exactly once on ordinary completion, parse failure, disconnect, deadline, or worker error;
- a connection arriving at the ceiling is hard-closed before handler creation because an incomplete request cannot safely receive a shaped HTTP response. The event is rate-limited and fixed to `connection_capacity_exhausted` with only the numeric configured ceiling and suppressed-event count; no client address, request bytes, credential, provider call, or usage row is retained;
- `listen.request_header_timeout_seconds` now defaults to `15`, accepts only `1` through `120`, and is an absolute wall-clock deadline rather than socket inactivity. One server-wide watchdog covers accepted sockets and re-arms for each keep-alive request, so a client sending bytes every 100 milliseconds cannot extend the deadline indefinitely;
- deadline expiry records one `request_header_deadline_exceeded` event containing only its batch count, shuts down the sockets, releases their connection workers and slots, and permits a later readiness probe. An adversarial header marker was absent from logs, responses, provider requests, and usage storage;
- focused configuration, installed-operator diagnostics, capacity, continuous-trickle, keep-alive re-arm, recovery, health, drain, parsed-request concurrency, keep-alive shutdown, and shutdown-idempotence tests passed;
- the complete 353-test source suite passed in `117.844` seconds. The environment-gated official Claude Code case was the sole local skip;
- source/test bytecode compilation and `git diff --check` passed;
- isolated source and wheel builds succeeded. The exact clean-install-tested wheel SHA-256 was `faa5044a2da02981c47c65a92cd3bff1b78c53fbb05f05a679cd80f55d74fda6`; the source archive is not self-hashed inside this embedded record because changing the record changes that archive;
- a clean Python 3.12 environment outside the checkout imported `hormuz.server` from `site-packages`, displayed the effective `256`-connection ceiling, `15`-second request-header deadline, `128`-request capacity, and `600`-second provider-response deadline through installed `doctor`, and compiled the installed package;
- the installed strict 60-task release benchmark passed with precision, recall, and useful-pack rate `1.00`, zero authorization, lifecycle, dependency, contradiction, malicious-context, determinism, or token-budget failures, mean compression `0.840593`, and governed p95 selection latency `0.158958 ms`; and
- deterministic corpus verification retained SHA-256 `9822d592868202c7c7539bcdac7d4a5894c01f9e6dba7a434846516b67b32c17`. High-confidence tracked-source and extracted-wheel scans found no private-key, OpenAI, Anthropic, GitHub, AWS, Google, Slack, or Hormuz-session credential pattern. Remote CI evidence follows after publication.

This closes unbounded accepted connection workers and complete request-header duration for one Hormuz process, not production deployment issue #11. A total ingress-body deadline, kernel accept-backlog policy, per-source limits, cross-replica coordination, reverse-proxy/WAF controls, TLS ingress, shared persistence, HA, backup/restore, RPO/RTO, KMS, and independent security review remain open.

### Total request-body deadline and exact-length ingestion

A failure-first ingress-body review was exercised locally on August 16, 2026 through real TCP sockets against the Hormuz HTTP server. No live provider credential or endpoint was used.

- source inspection found one blocking `rfile.read(Content-Length)` with no application-owned total deadline. The failure-first configuration, diagnostic, and real-socket tests were red because no body-deadline setting or enforcement path existed; the completed test now sends body bytes every 100 milliseconds and proves they cannot extend the one-second test deadline;
- a second red test announced seven bytes, sent the complete valid JSON object `{}`, and half-closed the client write side. Before the change, Hormuz accepted the short body as valid JSON and reached application validation despite the unmet `Content-Length` contract;
- `listen.request_body_timeout_seconds` now defaults to `30`, accepts only `1` through `600`, and defaults safely for existing configuration files. Its absolute deadline starts when `POST` or `PUT` headers finish and is recomputed as the maximum wait for every bounded body-read step, so continuous trickle cannot extend it;
- deadline expiry returns fixed `408 request_body_timeout` with `Connection: close`. Its rate-limited `request_body_deadline_exceeded` diagnostic contains only the configured numeric timeout and suppressed-event count. The adversarial body marker was absent from logs and the response, and no provider request or usage row was created;
- early EOF now returns fixed `400 incomplete_request_body` with `Connection: close` before JSON expansion, policy, provider work, or usage accounting;
- focused configuration/default/bounds, installed-operator diagnostics, continuous-trickle, keep-alive re-arm, early-EOF, capacity-release, recovery, content-free logging, provider non-invocation, and usage non-accounting tests pass; and
- the deadline ends after exact body receipt and the prior socket mode is restored, preserving normal OpenAI, Anthropic, context, lifecycle, approval, and session request handling. Provider response streaming remains governed by its separate total response deadline;
- the complete 357-test source suite passed in `123.470` seconds. The environment-gated official Claude Code executable case was the sole local skip;
- isolated source and wheel builds succeeded. The exact clean-install-tested wheel SHA-256 was `07937679afe655689f92a52f99a2ff78a64b57739052ed381c2b653e81f836f0`; the source archive is not self-hashed inside this embedded record because changing the record changes that archive;
- a clean Python 3.12 environment outside the checkout imported Hormuz from `site-packages`, displayed the effective `256`-connection ceiling, `15`-second header deadline, `30`-second body deadline, `128`-request capacity, and `600`-second provider-response deadline through installed `doctor`, displayed CLI help, and compiled the installed package;
- the installed strict 60-task release benchmark passed with precision, recall, and useful-pack rate `1.00`, zero authorization, lifecycle, dependency, contradiction, malicious-context, determinism, or token-budget failures, mean compression `0.840593`, and governed p95 selection latency `0.271708 ms`; and
- deterministic corpus verification retained SHA-256 `9822d592868202c7c7539bcdac7d4a5894c01f9e6dba7a434846516b67b32c17`. Source/test bytecode compilation, `git diff --check`, and high-confidence tracked-source plus extracted-wheel and extracted-source-archive credential scans passed. Remote CI evidence follows after publication.

This closes total application-owned request-body duration and exact announced-length ingestion for one Hormuz process, not production deployment issue #11. Kernel accept-backlog policy, per-source limits, cross-replica coordination, reverse-proxy/WAF enforcement, operating-system DNS behavior, TLS ingress, shared persistence, HA, backup/restore, RPO/RTO, KMS, and independent security review remain open.

### Explicit kernel accept-backlog hint

A failure-first listener-activation review was exercised locally on August 16,
2026 without a provider request or credential.

- source and runtime inspection confirmed that Hormuz inherited the standard library's fixed backlog value `5` and exposed no configuration or operator diagnostic. The failure-first tests were red because `ListenConfig` had no `accept_backlog` field and activation could not apply an owner-selected value;
- `listen.accept_backlog` now defaults to `256`, accepts only `1` through `65535`, and defaults safely for existing configuration files. The default aligns with the separate default accepted-connection ceiling but remains independently configurable;
- `GatewayServer.server_activate` assigns the resolved value before delegating to the standard socket activation path. The activation-level regression test uses an isolated socket double and proves the exact configured value reaches `listen()` rather than being assigned after activation;
- installed-operator diagnostics expose the effective accept backlog next to the connection, request, header, body, and provider-response controls;
- the final focused configuration, diagnostic, and activation set passed 3 tests in `1.009` seconds; and
- the complete 358-test source suite passed in `125.575` seconds. The environment-gated official Claude Code executable case was the sole local skip;
- isolated source and wheel builds succeeded. The exact clean-install-tested wheel SHA-256 was `f5e18e8a0f3f2d34f2d40935898ef84f19fa0ea14d47dd0f4bf95d8e329e8902`; the source archive is not self-hashed inside this embedded record because changing the record changes that archive;
- a clean Python 3.12 environment outside the checkout imported Hormuz from `site-packages`, displayed the effective accept backlog of `256` alongside the existing connection and deadline controls through installed `doctor`, displayed CLI help, and compiled the installed package;
- the installed strict 60-task release benchmark passed with precision, recall, and useful-pack rate `1.00`, zero authorization, lifecycle, dependency, contradiction, malicious-context, determinism, or token-budget failures, mean compression `0.840593`, and governed p95 selection latency `0.163208 ms`; and
- deterministic corpus verification retained SHA-256 `9822d592868202c7c7539bcdac7d4a5894c01f9e6dba7a434846516b67b32c17`. Source/test bytecode compilation, `git diff --check`, and high-confidence tracked-source plus extracted-wheel and extracted-source-archive credential scans passed. Remote CI evidence follows after publication.

This closes application ownership of the listening socket's accept-backlog hint,
not production deployment issue #11 or a portable kernel-queue guarantee. The
operating system may cap or reinterpret the hint, manages SYN queues and global
network limits independently, and still requires an outer ingress policy.
Per-source and trusted-proxy controls, TLS ingress, cross-replica coordination,
shared persistence, HA, backup/restore, RPO/RTO, KMS, and independent security
review remain open.

### Fail-closed configuration schema and policy references

A failure-first configuration-boundary review was exercised locally on August 16, 2026 without a provider request or live credential.

- sixteen red cases proved that the root, listener, upstream map/provider, static identity, authentication/OIDC, OIDC issuer/subject, model route, policy map/body, team/actor policy reference, and cross-organization team-scope paths previously accepted unknown or ineffective configuration;
- every Hormuz-owned configuration object now rejects unknown fields. One shared validator reports only the fixed schema path and never reflects the rejected key; lifecycle, DLP-rule, and capability unknowns follow the same non-reflective diagnostic rule;
- model/fallback references retain their existing route validation. Team and actor policy scopes must now resolve to configured identities, and a team policy is rejected if that team ID occurs in more than one configured organization, preventing a typo from silently removing a restriction or a shared name from crossing an organization boundary;
- the accepted configuration is explicitly immutable for one process. `doctor` is a full candidate preflight, and the documented safe change path is a readiness-gated replacement revision with the previous image/configuration pair retained for rollback. Hormuz does not claim `SIGHUP`, in-place live reload, signed configuration, or deployment-coordinated rollback;
- four focused regression methods covering all sixteen cases passed. The complete 362-test source suite passed in `124.594` seconds; the environment-gated official Claude Code executable case was the sole local skip;
- source/test bytecode compilation and `git diff --check` passed. Isolated source and wheel builds succeeded with the CI-pinned `build==1.3.0`; the exact clean-install-tested wheel SHA-256 was `6e3d1943dcc2dfc00adf13ea160a4d07efdcb012d9c69d26bfe3508b525e9d1a`;
- a clean Python 3.13 environment outside the checkout imported Hormuz from `site-packages`, passed installed `doctor`, rejected a synthetic unknown root key with exact non-reflective output, and compiled the installed package;
- the installed strict 60-task release benchmark passed with precision, recall, and useful-pack rate `1.00`, zero authorization, lifecycle, dependency, contradiction, malicious-context, determinism, leakage-review, or token-budget failures, mean compression `0.840593`, and governed p95 selection latency `0.175625 ms`; and
- deterministic corpus verification retained SHA-256 `9822d592868202c7c7539bcdac7d4a5894c01f9e6dba7a434846516b67b32c17`. High-confidence tracked-source, extracted-wheel, and extracted-source-archive scans found no private-key, OpenAI, Anthropic, GitHub, AWS, Google, Slack, or Hormuz-session credential pattern. Remote CI evidence follows after publication.

This closes application-owned unknown-field and policy-reference validation at startup, not production deployment issue #11. Published schema versioning, configuration signing and approval, live secret rotation, orchestrated rollout/rollback, shared persistence, HA, backup/restore, KMS/BYOK, and independent security review remain open.

### Remote provider HTTPS enforcement

The provider-upstream configuration boundary was exercised locally on August 16, 2026 without a live provider credential or remote provider request.

- red-first configuration tests proved that both provider slots accepted non-loopback plaintext HTTP, embedded URL credentials, queries, and fragments before the change;
- OpenAI and Anthropic base URLs now pass the same strict structural URL validator and require HTTPS whenever the hostname is not a literal loopback address or `localhost`;
- deterministic development remains possible through IPv4, IPv6, and `localhost` loopback fakes, while remote hostnames and non-loopback addresses cannot receive a provider credential over HTTP;
- focused OpenAI non-streaming and Anthropic streaming requests completed through the real gateway and loopback providers with the existing employee/provider credential separation intact;
- the complete 334-test source suite passed in `102.465` seconds. The environment-gated official Claude Code case was the sole local skip; and
- source/test bytecode compilation and `git diff --check` passed.

This closes the identified configured plaintext provider-egress path, not production network policy. DNS integrity, platform certificate trust, private endpoints, workload egress allowlists, proxy/service-mesh policy, TLS ingress, shared persistence, HA, disaster recovery, KMS, and independent review remain open gates.

### Provider redirect refusal

The provider-generation redirect boundary was exercised locally on August 16, 2026 through real Hormuz HTTP transport, its configured OpenAI and Anthropic credentials, and two provider-compatible loopback origins. No live provider credential or endpoint was used.

- the red-first test proved that the standard provider transport followed a `302` to the second origin and treated its fixed fake response as a successful `200`;
- Hormuz now uses one redirect-refusing provider transport for both OpenAI and Anthropic, keeping each server-held credential and provider-bound request at the configured origin;
- OpenAI Responses and Anthropic Messages redirects each returned a provider-shaped fixed `502 gateway_upstream_redirect`, did not reflect `Location`, and produced zero requests at the redirect target;
- each refused attempt created exactly one failed, `not_available` cost-basis usage event. The events retained identity, provider/model, policy, status, rate-card, DLP-count, and context-lineage metadata only; neither request/response content nor the redirect target entered routine telemetry;
- ordinary OpenAI non-streaming, Anthropic streaming, and fixed content-free upstream-failure paths remained compatible in focused tests;
- the complete 335-test source suite passed in `103.365` seconds. The environment-gated official Claude Code case was the sole local skip; and
- source/test bytecode compilation and `git diff --check` passed.

This closes automatic application-level provider redirect following, not production egress governance. DNS integrity, platform certificate trust, forward-proxy and service-mesh behavior, workload egress allowlists, TLS ingress, shared persistence, HA, KMS, disaster recovery, and independent security review remain open gates.

## Reproduce locally

The default suite uses only loopback fake providers:

```bash
python3 -m unittest -v
```

The live OpenAI check requires an ignored credential file or secret-manager injection. Start Hormuz with credentials in its environment, configure Codex using [CLIENTS.md](CLIENTS.md), request a fixed marker, then verify metadata with:

```bash
hormuz --config hormuz.json status --json
```

Never add real provider or employee credentials to this record.

## Automated publication gate

Ordinary GitHub CI runs five independent gate families without provider credentials:

- the complete unit, context-governance, and loopback gateway suite on Python 3.11, 3.12, 3.13, and 3.14;
- deterministic corpus regeneration plus the 60-task governed-context release profile, with machine-readable evidence retained as an artifact;
- source-distribution and wheel builds followed by installation of the wheel in a clean virtual environment;
- installed-client routing through local fake providers using pinned official Codex and Claude Code package versions; and
- pinned-base/hash-locked container build, restricted-runtime smoke, CycloneDX SBOM, and a fail-on-any-high-or-critical vulnerability gate.

Ordinary CI grants only read access to repository contents, disables persisted checkout credentials, pins every GitHub Action to a reviewed commit SHA, and retains build artifacts for seven days. The separate release workflow re-runs the applicable gates on an annotated version tag, splits source, package/signing, and GitHub-release permissions, and retains its verification and signed-release evidence longer. It remains blocked before publication until the owner-approved Sigstore transparency repository variable is present. Dependabot is configured to propose updates to action and Python build dependencies; a client-version bump remains an intentional compatibility change because it can alter the provider protocol.

A separate weekly canary installs the latest published Codex and Claude Code packages in an ephemeral runner and exercises only the two fake-provider compatibility tests. It has no provider credentials, does not block ordinary pull requests, and is intended to surface upstream protocol drift before an employee upgrade does.

The publication candidate was also checked locally on August 15, 2026 with Codex `0.147.0` and Claude Code `2.1.233`, the then-current npm releases. Both routed successfully through Hormuz, and the complete 29-test suite passed with those executables selected first on `PATH`.
