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

### OIDC browser-login discovery preflight

The browser-login deployment check was tightened on August 20, 2026. `hormuz doctor` now validates the selected issuer's discovery document before a browser-login deployment is considered ready: it requires authorization-code support, PKCE `S256`, an ID-token signing algorithm configured by Hormuz, and the configured token-endpoint client-authentication method. It continues to reject unsafe discovery endpoints and missing signing keys.

Observed local result:

- the standards-shaped loopback issuer passed the preflight and the doctor output identifies the number of issuers whose browser-login capabilities were checked;
- negative fake-IdP cases for unsupported authorization-code flow, grant type, PKCE method, ID-token signing algorithm, and token-endpoint authentication method all failed closed with explicit non-secret errors;
- an omitted `token_endpoint_auth_methods_supported` field followed the OIDC default for `client_secret_basic` and passed only for that configured method;
- the complete local source suite passed 532 tests in 141.955 seconds, with three separately gated tests skipped.

This is a local discovery-contract checkpoint, not validation of an owner-selected external IdP. It does not register a real client, prove its redirect URI or employee login, or replace the remaining #13 gates for real IdP, durable multi-node session infrastructure, SCIM/administrator controls, and audited operations.

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

This closes the single-node administrator-revocation and local event-inspection slice of issue #13, not the identity or enterprise-tenancy milestone. The event API is a queryable local evidence ledger, not a signed or externally immutable audit sink. Real owner-selected IdP validation, live configuration reload, SCIM/event-driven deprovisioning, shared multi-node revocation, PostgreSQL repository integration, KMS custody, retention/SIEM delivery, immutable session-event export, HA, backup/restore, and independent review remain open. ADR 0002 now supplies the accepted shared-persistence direction and schema foundation; it does not close these implementation gates.

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

This historical checkpoint closed a real single-node usage-administration slice
without deciding the then-proposed ADR 0002. The current schema-v2 checkpoint
adds an opt-in PostgreSQL accounting repository separately; it does not turn
this older SQLite observation into hosted-tenancy, HA, externally immutable
audit, SCIM, SIEM delivery, final invoice coverage, or complete provider-account
usage evidence. Person-level tokens and estimated spend remain consumption
metadata, not employee-performance evidence.

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
- schema `hormuz.dlp-evaluation.v1` recorded deterministic detector version `hormuz-deterministic-v1` at this checkpoint, package/runtime versions, safe rule and scope metadata, an administrator-controlled corpus version, aggregate finding/case counts, confusion matrix, and derived metrics. It explicitly recorded that payloads, matched values, case IDs, and corpus hashes were absent and that policy promotion was manual;
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

- `hormuz.content-free-schema.v2` enumerates the exact permitted columns for nine gateway usage/security/billing tables, one session-security table, and five governed-context audit tables; v2 adds only the resolved model alias needed by short-lived per-model budget reservations;
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

### Bounded configuration artifact integrity

A failure-first configuration-artifact review was exercised locally on August 16, 2026 without a provider request or live credential.

- red tests proved that duplicate root and nested members used Python's last-value-wins behavior, `NaN`/positive or negative `Infinity` reached the ordinary schema, invalid UTF-8 escaped as an unhandled decoder exception, oversized files were fully decoded, and no deployment-supplied exact digest could be enforced;
- `GatewayConfig.load` now reads at most 1 MiB, decodes one strict UTF-8 JSON document, rejects duplicate members at every nesting level, rejects non-standard numeric constants, and permits at most 64 structural levels and 100,000 decoded nodes. Huge integer conversion, excessive nesting, malformed JSON, and invalid encoding use fixed non-reflective failures;
- allowed numeric policy fields additionally reject finite-looking JSON exponents that overflow the runtime float representation, so a rate or budget cannot become infinity;
- every accepted file receives the SHA-256 of its exact bytes. `--expected-config-sha256` or `HORMUZ_CONFIG_SHA256` can require an independently retained 64-character lowercase digest on every config-backed CLI path, including `serve` and `doctor`;
- an invalid expected value fails before file access, while a mismatch fails before environment-secret resolution, policy construction, database initialization, OIDC discovery, or listener creation. `doctor` reports the accepted exact digest for an authorized operator without returning configuration content;
- the final six focused failure/integrity methods passed in `0.012` seconds. The broader configuration, CLI, OIDC, and gateway-health set passed 63 tests in `5.840` seconds;
- the final complete 391-test source suite passed in `124.450` seconds. The environment-gated official Claude Code executable case was the sole local skip; source/test bytecode compilation and `git diff --check` passed. The additional regression deterministically verifies that decoder recursion maps to the fixed structural-limit diagnostic across supported Python releases; and
- the frozen 60-task strict release benchmark retained corpus SHA-256 `9822d592868202c7c7539bcdac7d4a5894c01f9e6dba7a434846516b67b32c17`, precision/recall/useful-pack rate `1.00`, mean compression `0.840593`, zero authorization/lifecycle/dependency/malicious/contradiction/budget/determinism/leakage failures, and governed p95 in-process selection latency `0.157417` ms; and
- an isolated source archive and wheel build from the working tree succeeded with the reviewed local build toolchain. Exact-commit reproducibility and installed-package evidence follow after the implementation is committed.

This closes a bounded parser and artifact-identity gap, not configuration approval. A SHA-256 is neither a signature nor proof of who approved the file; an actor that can replace both file and expected digest bypasses it. Signer identity, protected approval, configuration retention, cross-replica rollout, live secret rotation, shared persistence, HA, backup/restore, KMS/BYOK, and independent security review remain open under issues #11, #17, and #9.

### Exact-source reproducible Python distributions

A failure-first package-release review was exercised locally on August 16, 2026 without a provider request or credential.

- two ordinary isolated builds of the same source produced different wheel and source-archive hashes because archive timestamps and generated member metadata followed build time;
- supplying the commit timestamp through `SOURCE_DATE_EPOCH` made the two wheels byte-identical, while the two source archives still differed at both the gzip and uncompressed-tar layers. Their path sets and generated package metadata content matched, isolating the remaining difference to archive metadata;
- `pyproject.toml` pins the reviewed backend pair, but the first reproducibility checkpoint still installed `build==1.3.0` without a hash and let isolated PEP 517 builds redownload backend inputs. Workflow and lock tests were red for that unresolved supply-chain path;
- `deploy/build/requirements.lock` now contains the complete Python 3.11+ cross-platform universal-wheel closure for `build==1.3.0`, Windows-conditional `colorama==0.4.6`, `packaging==26.3`, `pyproject-hooks==1.2.0`, `setuptools==84.0.0`, and `wheel==0.48.0`. `colorama` is installed unconditionally to avoid platform-marker ambiguity and is inert outside Windows. Every entry is exact and has one SHA-256 that was checked against the downloaded wheel and official PyPI release metadata;
- every ordinary CI editable/package path, the latest-client canary, and tag verification install that lock with `--require-hashes --only-binary=:all:`. Editable installs and the two-build gate disable build isolation, so no second backend resolver is authorized;
- `scripts/reproducible_build.py` accepts only the full checked-out `HEAD` SHA, derives the build epoch from that commit, exports tracked source through `git archive`, validates `pyproject.toml` and the lock from each exported tree, verifies every installed distribution version, and creates two independent source and output trees;
- canonicalization accepts only one-root regular-file/directory source archives within bounded member and byte limits. It rejects absolute/traversal paths, duplicate paths, multiple roots, links, and device-like members with fixed diagnostics before creating output;
- safe members receive sorted order, numeric owner/group zero, empty owner names, the commit timestamp, stable executable/non-executable modes, cleared PAX metadata, and a deterministic filename-free gzip wrapper. The raw wheel and source-archive hashes from both builds must match before one pair is published;
- schema `hormuz.reproducible-distributions.v2` in `hormuz-distribution-reproducibility.json` deterministically records the exact source SHA, commit epoch, build-lock SHA-256, six distribution versions, artifact filenames, byte sizes, and artifact SHA-256 digests. It has no generated time or local path; and
- ordinary CI and the tag verification workflow both use this gate, while contract tests prohibit a direct one-pass `python -m build` regression;
- the 18 focused canonicalization, lock, installed-toolchain, and release-contract tests passed, including metadata normalization, traversal/link/duplicate/multiple-root refusal, lock completeness and hash syntax, wrong/missing installed-version refusal, deterministic manifest structure, no-isolation enforcement, and all workflow bindings; and
- the complete 370-test source suite passed in `124.676` seconds. The environment-gated official Claude Code executable case was the sole local skip; source and test bytecode compilation, dependency integrity, and `git diff --check` also passed.

This proves a fail-closed exact-source package-reproducibility contract under a reviewed, hash-custodied Python build toolchain. It does not prove offline or internally mirrored build-wheel availability, runtime locking for arbitrary downstream environments outside the repository workflows and reference container, byte-identical OCI layers across builders, or an observed signed registry release. Those and the remaining TLS, shared persistence, HA, backup/restore, KMS/BYOK, and independent-review requirements keep issues #11 and #9 open.

### Hash-locked workflow runtime and test resolution

A failure-first runtime-resolution review was exercised locally on August 17, 2026 without a provider request or credential.

- before this checkpoint, ordinary matrix, context-benchmark, client-compatibility, release-verification, and latest-client-canary jobs installed the exact build frontend but then ran editable Hormuz installation with dependency resolution enabled. The isolated wheel smoke likewise installed the wheel with dependency resolution enabled. Those paths could therefore select newer runtime or transitive packages than the reviewed container closure;
- two new workflow-contract methods, including separate release and canary subtests, were red because none of those jobs installed the runtime lock or prohibited dependency resolution;
- the first GitHub run then exposed a second, platform-conditional defect that the local Python 3.12 check could not: on Python 3.11, `jaraco.context==6.1.2` requires `backports.tarfile`, but the universal resolver had omitted it. Pip correctly failed closed under `--require-hashes` rather than downloading the unreviewed transitive dependency;
- the canonical runtime lock now includes the complete Python 3.11-only closure: `backports-tarfile==1.2.0`, `importlib-metadata==9.0.0`, and `zipp==4.1.0`, each guarded below Python 3.12 and bound to reviewed wheel and source-archive SHA-256 hashes. A focused contract test preserves the exact versions, markers, and backport artifact hashes;
- ordinary source/test, context-benchmark, client-compatibility, tag-verification, and latest-client-canary jobs now install `deploy/container/requirements.lock` with `--require-hashes --only-binary=:all:` before installing Hormuz with `--no-build-isolation --no-deps --editable .`, then require `python -m pip check` to pass;
- the package job's fresh wheel environment independently installs the same lock, installs the exact built wheel with `--no-deps`, and runs that environment's `pip check` before any CLI smoke command;
- contract tests require all of those bindings and forbid the earlier resolver-enabled editable command. The complete 14-test release-contract module passed, and Ruby's YAML parser accepted all three changed workflows;
- a fresh Python 3.12 environment installed the exact build lock and exact multi-platform runtime lock, built the editable package without isolation or dependency resolution, and reported `No broken requirements found` from `pip check`; and
- a fresh Python 3.11.14 environment installed the exact build lock and corrected runtime lock, including `backports-tarfile==1.2.0`, `importlib-metadata==9.0.0`, and `zipp==4.1.0`; editable Hormuz installation used `--no-deps`, `pip check` reported `No broken requirements found`, and the installed CLI started successfully; and
- the corrected complete local source suite passed 394 tests in `124.396` seconds. The environment-gated official Claude Code executable case was the sole expected local skip, and `git diff --check` passed.

This proves that repository-owned Python CI, tag verification, scheduled client compatibility, and isolated wheel-smoke paths do not authorize runtime dependency resolution during Hormuz installation. It does not provide an offline/internal wheel mirror, lock arbitrary downstream `pip install hormuz` environments, or make the intentionally latest-version canary deterministic. The separate official-client closure is proven below; the other items remain explicit supply-chain and deployment boundaries.

### Integrity-locked official-client compatibility

A failure-first official-client supply-chain review was exercised locally on August 16, 2026 without a provider request or credential.

- before this checkpoint, the ordinary CI and tag-verification jobs pinned Codex `0.147.0` and Claude Code `2.1.233` only at the two direct package names. Both used `npm install --no-package-lock --no-save`, so all platform packages and other package metadata were resolved live during each run under an unpinned Node/npm toolchain;
- three focused release-contract assertions initially failed because no tracked npm manifest or lock existed and both workflows still used that live installation path. A separate canary assertion passed, preserving its intentionally dynamic latest-version role;
- `deploy/clients/package.json`, `package-lock.json`, and `.npmrc` now bind Node `24.19.0`, npm `11.17.0`, both direct clients, and all 14 supported platform-native optional packages. Lockfile v3 records the exact official-registry tarball and SHA-512 integrity for all 16 package records;
- `scripts/client_lock_contract.py` requires the exact manifest fields, whole-lock SHA-256, complete package set, versions, platform markers, optional edges, official registry, SHA-512 syntax, and reviewed direct-package integrities. It rejects linked or bundled packages, new transitive edges, and every lifecycle-script set except the Claude Code wrapper installer;
- ordinary CI and tag verification validate that contract before installation, require the exact Node/npm versions at runtime, run `npm ci` with lifecycle scripts disabled, and then execute only `@anthropic-ai/claude-code/install.cjs`. The integrity-locked script links or copies the already installed platform binary; it is not permitted to change the package closure;
- tag verification exercises the two clients in a separate read-only job from artifact production. Publishing requires both the artifact-verification and client-compatibility jobs, so client execution cannot modify release artifacts while a compatibility failure still prevents publication;
- the content-free `hormuz.pinned-client-lock.v1` evidence binds the Node/npm versions, official registry, package count, lock SHA-256, direct names, versions and integrities, and the sole explicit lifecycle script without a path, prompt, response, identity, credential, or generated time;
- three focused validator tests cover the tracked evidence, dependency/package/registry drift, integrity drift, and script drift. Five additional release-contract and canary checks cover manifest shape, every package record, scripts-disabled installation, toolchain enforcement, workflow isolation, and preservation of the latest-client canary; and
- a fresh local install from the tracked lock under Node `24.19.0` and npm `11.17.0` first demonstrated the fail-closed boundary: Codex started while Claude Code refused to start until the reviewed wrapper installer ran. After that explicit step, the exact clients reported `codex-cli 0.147.0` and `2.1.233 (Claude Code)` and both passed their real executable-to-Hormuz integration cases against local fake upstreams in `4.082` seconds; and
- the complete source suite passed 400 tests in `124.466` seconds. The environment-gated official Claude Code executable case was the sole expected skip in the ordinary environment; that exact executable case passed in the separate locked-client environment above.

This proves the reviewed, integrity-locked official-client closure and executable gateway compatibility on the exercised local host, plus fail-closed workflow contracts for GitHub-hosted Linux. It does not provide an offline or internal npm mirror, add publisher signatures beyond npm integrity and source review, exercise every supported operating-system package, make the separate latest-version canary deterministic, or prove the workflows green until their published runs complete. Issues #11 and #9 remain open with those boundaries and the broader release and enterprise gates.

### Exact-source reproducible OCI image

A failure-first container-reproducibility review was exercised locally on August 17, 2026 without a provider request or credential.

- the focused test first failed because no exact-source OCI rebuilding module existed;
- two ordinary clean `linux/amd64` BuildKit OCI exports of the same source, same commit epoch, pinned base, and hash-locked dependency set then produced different manifest and config digests. Layer comparison isolated the difference to pip-generated `.pyc` files: the Dockerfile's runtime `PYTHONDONTWRITEBYTECODE` setting did not prevent pip from explicitly compiling bytecode during installation;
- the Dockerfile now exposes `SOURCE_DATE_EPOCH` to that installation step, causing deterministic hash-based bytecode compilation. A second pair of clean local builds then had identical manifests, configs, layers, and complete validated OCI layouts;
- the first published gate at `d486ae0a700d6f1bd7842ee501f95799c0fc2894` passed two local and two GitHub builds independently, but direct artifact comparison correctly disproved cross-host equality. The sole filesystem delta was an empty `/root/.cache/rosetta` directory created while Docker Desktop emulated `linux/amd64`; config history also differed because local BuildKit `v0.29.0` and GitHub BuildKit `v0.32.2` serialize `HEALTHCHECK` history differently;
- the follow-up contract removes emulator-created root-cache state, pins workflow builders to reviewed `moby/buildkit:v0.32.2` image digest `sha256:28a898719c18a33f4e8000685287fa36fd0dd9560c6440227d3a732d79bb41d8`, and makes the gate reject any active builder not using the `docker-container` driver and exact `v0.32.2` release;
- `scripts/reproducible_image.py` requires the selected full SHA to equal checked-out `HEAD`, derives the epoch from that commit, safely exports tracked source into two isolated contexts, and invokes BuildKit with no cache, no pull, no publication, and run-specific provenance/SBOM attestations disabled;
- the bounded validator accepts only the expected OCI layout, one `linux/amd64` manifest, regular safe files, strict unambiguous JSON, verified descriptor digests/sizes, matching source/version/runtime-user configuration, and consistent layers/root-filesystem diff IDs. Unexpected, unreferenced, unsafe, oversized, or excessive inputs fail with fixed diagnostics that do not reflect input content;
- the complete file sets, sizes, and SHA-256 hashes of both layouts must match before the gate atomically publishes a sorted deterministic OCI tar and schema `hormuz.reproducible-oci.v1` manifest into an initially empty non-symlink directory;
- the content-free manifest binds the source SHA and epoch, platform, reviewed builder driver/image/version, Dockerfile/base/lock digests, OCI index/manifest/config/layer digests, and final artifact name/hash/size without a workspace path, source content, prompt, response, identity, credential, or generated time;
- seven focused failure, builder, layout, equality, archive, evidence, Dockerfile, and workflow contract tests pass. The broader package/release subset contains 25 passing tests. The complete 384-test source suite passed in `125.298` seconds; the environment-gated official Claude Code executable case was the sole local skip;
- deterministic corpus verification retained SHA-256 `9822d592868202c7c7539bcdac7d4a5894c01f9e6dba7a434846516b67b32c17`. The strict 60-task release benchmark passed with governed precision, recall, and useful-pack rate `1.00`, zero authorization, lifecycle, dependency, malicious-context, contradiction, budget, determinism, or leakage failures, mean compression `0.840593`, and governed p95 selection latency `0.156` ms; and
- ordinary CI and tag verification run the two-build gate and retain its image tar plus evidence. The signed release build remains separate and keeps its provenance and SBOM generation enabled.

At that checkpoint, this proved same-source byte equality for the exercised unsigned `linux/amd64` BuildKit payload but did not yet prove `linux/arm64` equality, equality across every BuildKit implementation or host, offline availability of the pinned base and PyPI inputs, reproducibility of run-specific signatures or attestations, an independent rebuild service, or an observed signed registry release. Issues #11 and #9 remained open with TLS, shared persistence, HA, backup/restore, KMS/BYOK, tag governance, and independent review still unproven.

### Dual-architecture exact-source OCI follow-up

The bounded OCI gate was extended and independently verified for `linux/arm64` on August 17, 2026 without a provider request or credential.

- failure-first coverage initially rejected an otherwise valid arm64 OCI layout and showed that ordinary and tag workflows invoked the gate only once. The implementation now accepts exactly `linux/amd64` or `linux/arm64`, preserves amd64 as the compatible default, and retains one schema `hormuz.reproducible-oci.v1` manifest and canonical tar in a distinct directory per platform;
- all seven focused platform, layout, equality, evidence, and workflow-contract tests passed. The complete local source suite passed 400 tests in `125.845` seconds; the environment-gated official Claude Code executable case was the sole expected skip;
- exact branch commit `138eb1505586a3c210a62a15ea4211c149891e45` passed two local builds per platform under the reviewed digest-pinned BuildKit `v0.32.2`. Push run `31995734188` and pull-request run `31995737445` then each passed all eight jobs, including the dual-platform gate, runtime smoke, SBOM generation, and high/critical vulnerability enforcement. PR #19 reported 16 of 16 combined checks green;
- the retained push artifacts were byte-identical to the local exact-commit rebuild. The amd64 tar and manifest SHA-256 digests were `a7c1934ad8c62a3a180a977c218eb3e410ba3d95e7b6bd4920a384b753041d0d` and `eaeafcc5df5861cac13d82f27b0617cb8340d3bafa7f63f50ef727b35906bee1`; the arm64 tar and manifest digests were `4569829683550ca398e9b77310dd51fd7feb568a164b958158dbb2fb176d2d94` and `545f53df73ede9615ce47c96ae8c0f117d75a06e7892fe81f124b03514ae2b5a`; and
- GitHub's pull-request commit `a631478c894f6bfa8e4abf751059f59cf29e1b2a` had the same tree as the branch head. Independent local merge rebuilds were byte-identical to the retained pull-request artifacts: amd64 tar and manifest `97aa2e2f6c746f142c0e96a0d6a32a7b300d91b9ad0f9a285255596706928a3f` and `913379a9832ce482ee2d8fb1cdc4a9d67efe356e408f2a685532bdf051effc4a`; arm64 tar and manifest `96f8a2c7b24d35a1734107959a96d761277eef5852eb5e6fadd0c8310bdcabca` and `052e1eda91fac2db0cd03fb6b7423a1cd6bcfadff9c5abf8fe97ffa4e489b6ec`.

This closes the previously documented arm64 gap only inside the unsigned payload-reproducibility contract. It proves neither equality between architectures nor every builder or host. Offline build-input availability, deterministic signature and attestation envelopes, an independent rebuild service, an observed signed registry release, tag governance, customer/KMS custody, retention and rollback operations, and independent review remain open under issues #11 and #9.

### Anchored audit-chain export

A failure-first audit-evidence review was exercised locally on August 17, 2026.
The focused tests initially failed because the chain module and verification
command did not exist.

- usage/security and governed-context audit exports can now select canonical
  schema `hormuz.audit-chain.v1` without changing the existing raw JSONL
  compatibility default;
- every wrapper binds the original metadata-only event, one-based sequence,
  predecessor, domain-separated event digest, and resulting chain digest. The
  export reports an external anchor consisting of schema, count, final chain
  SHA-256, and exact-file SHA-256;
- file output uses owner-only permissions and a synchronized same-directory
  temporary file before no-clobber or explicit replacement publication, so a
  failed writer does not publish partial evidence;
- `hormuz audit-verify` runs without gateway configuration and requires an
  externally retained lowercase chain head and event count. An exact-file
  SHA-256 can additionally bind serialization;
- the strict bounded verifier rejects altered events, prefix/suffix deletion,
  reordering, duplication, wrong external anchors, noncanonical or ambiguous
  JSON, missing terminal newlines, unsafe symlinks, oversized records/files,
  and excessive counts with fixed non-reflective failures; and
- focused pure-format, usage CLI, and non-empty governed-context CLI tests pass,
  including checks that governed content and retrieval queries remain absent
  from the chained evidence; the 50 focused tests passed in `0.208` seconds;
  and
- the complete 377-test source suite passed in `124.363` seconds. The
  environment-gated official Claude Code executable case was the sole local
  skip.

This proves integrity and gap detection for an exported sequence only when the
anchor is retained independently. It does not hash-chain events at database
commit time, reveal records deleted before export, authenticate an attacker-
replaceable adjacent anchor, create one continuous sequence across stores or
exports, sign with KMS/BYOK, stream to immutable retention, or prove restore and
legal-hold operations. Issue #17 remains open for those enterprise controls.

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

### Versioned threat-model release gate

The internal threat-model contract was exercised locally on August 17, 2026
from exact commit `32aa07bb227b76ec209cf17b4dcfb332ea4843d2`.

- the red-first test failed because no threat-model contract module existed;
- the committed `hormuz.threat-model.v1` model records 9 assets, 7 trust
  boundaries, and 18 threats covering all six STRIDE categories and all seven
  incident scenarios named in issue #9;
- the model explicitly reports 1 mitigated, 12 partially mitigated, and 5 open
  threats, `independent_review.status = "pending"`, and
  `enterprise_release_ready = false`;
- strict validation rejects duplicate or unknown fields, non-standard JSON
  numbers, invalid and broken identifiers, missing STRIDE or incident coverage,
  unsupported status values, path-escaping or missing evidence references, and
  a falsely completed independent review;
- the canonical model SHA-256 is
  `e54c7893d8400c43b221d9207e1f4e4b8fb7da511964622b44322a0917c8c05e`;
- the 10 focused threat-model tests passed, and the combined 27-test threat-
  model and release-contract suite passed in `0.212` seconds;
- the complete 410-test source suite passed in `124.514` seconds. The
  environment-gated official Claude Code executable case was the sole local
  skip;
- two independent exact-source builds produced byte-identical artifacts: wheel
  SHA-256 `0cf33632a33e8558c7d84707d1f26ffecb0e4767aaa950b30ab4fa5f508724cf`
  (336,504 bytes), source-distribution SHA-256
  `d71aca7d36ed324fabe54f85b643555adc6c352c4380aae46e3f40cfedd40210`
  (634,335 bytes), and reproducibility-manifest SHA-256
  `c1113992faa3c4558ee2f5584886b94b0ba1b032f8e6c40499f8b4a5405ec11d`;
  the source distribution contains the model, validator, and threat-model
  documentation; and
- clean-package dependency checking, workflow YAML parsing, and
  `git diff --check` passed.

CI and the tag-release workflow validate this model and retain content-free
evidence. This is a machine-verifiable internal engineering threat model, not
an independent security review, penetration test, external risk acceptance, or
proof that the controls marked partial or open are complete. Issue #9 remains
open, and this checkpoint does not authorize enterprise release.

### Versioned compatibility support gate

The compatibility contract was exercised locally on August 17, 2026 from exact
implementation commit `6259000d9423cd8bd4b132f8ab05a374e9e58d27`.

- the red-first test failed because no compatibility-contract module existed;
- the committed `hormuz.compatibility-matrix.v1` matrix has 16 entries across
  clients, Python runtimes, provider protocols, identity, persistence, and
  deployment: 8 are exact release-tested surfaces, 3 are locally protocol-
  tested, 1 is development-only, 3 are unsupported, and PostgreSQL is pending
  an owner decision;
- the canonical matrix SHA-256 is
  `8051eb171a5fbd26d54e80dd1eaaf305f6ab9500dc2df7faa0f198b9fea3a926`;
- validation binds Codex `0.147.0`, Claude Code `2.1.233`, Node.js `24.19.0`,
  npm `11.17.0`, CPython `3.11` through `3.14`, project version `0.1.0`, the
  implemented provider routes, and container base
  `python:3.14.6-alpine3.23` to their repository sources and evidence;
- all evidence references must resolve inside the repository, optional
  selectors must exist in the referenced UTF-8 file, and duplicate members,
  non-standard JSON numbers, unknown fields, invented support levels, changed
  locked versions, missing test scope/evidence/limitations, and alpha production
  claims fail closed;
- the emitted `hormuz.compatibility-evidence.v1` schema contains only versions,
  hashes, counts, and support-level metadata. It is created privately with mode
  `0600`, refuses overwrite, and reports zero verified real-IdP, production-
  persistence, and production-deployment profiles;
- all 12 focused compatibility tests passed, and the combined 39-test
  compatibility, threat-model, and release-contract suite passed in `0.243`
  seconds;
- the complete 422-test source suite passed in `124.387` seconds. The
  environment-gated official Claude Code executable case was the sole local
  skip;
- two independent exact-source builds produced byte-identical artifacts: wheel
  SHA-256 `2eac74f2f130c4d43e65c5eec2bf08ad5e23fdad8b291656f74434c1093a2c6d`
  (336,629 bytes), source-distribution SHA-256
  `d5dda304e2f6f75626127b0641f720ccc54e5ebef68202eb210a6cc68412d6df`
  (646,465 bytes), and reproducibility-manifest SHA-256
  `d214b3d0c55b68d6501ec30c04dfad544aa9a1e15516889dc2eb7675de55b34b`;
- the source distribution contains the matrix, validator, operator document,
  and tests. A clean isolated environment installed the exact wheel after the
  hash-locked runtime closure, passed `pip check`, and ran the installed CLI;
  and
- workflow YAML parsing, source/test bytecode compilation, and
  `git diff --check` passed.

Ordinary CI and tag verification enforce the same matrix and retain content-free
evidence. `release_tested` means only the exact version and environment named in
the matrix. It does not certify other client versions or operating systems, live
provider behavior, a real IdP, PostgreSQL, TLS/HA, backup/restore, disaster
recovery, or enterprise release readiness. Issue #9 remains open and no
acceptance checkbox is satisfied by this internal contract alone.

### Repository-local incident-drill gate

The incident-drill contract was exercised locally on August 17, 2026 from exact
implementation commit `434a9acc32ac69efc8a854d853dc797dafb4c481`.

- the red-first test failed because no incident-drill contract module existed;
- the strict `hormuz.incident-drills.v1` catalog binds all seven issue #9
  scenarios to one exact repository test each: provider timeout, IdP token-
  endpoint outage, refresh-credential replay and family revocation, tenant-
  scoped session administration, source/dependency invalidation, pre-provider
  budget denial, and governed-context physical deletion guards;
- validation rejects duplicate or unknown fields, non-standard JSON numbers,
  missing or duplicate scenarios, broad, missing, or duplicate test bindings,
  unsafe runbook references, unresolved headings, invalid role identifiers,
  and any claim that a local simulation completed production exercises;
- the actual runner executed 7 tests and passed all 7. Its private mode-`0600`,
  545-byte `hormuz.incident-drill-evidence.v1` artifact has SHA-256
  `9f321eb414f54b3252de06a7ea154dd8eed3f98cb93ffae883fd20b27fc6188a`;
  the artifact contains the catalog digest and aggregate counts, not scenario
  prose, test identifiers, prompts, responses, identities, or credentials;
- the canonical catalog SHA-256 is
  `069c88b872e6dba22ee6b6b9f84ba864255647bc2afdc2806a847701b18f2c54`;
- all 8 focused incident-contract tests passed, and the combined 47-test
  incident, compatibility, threat-model, and release-contract suite passed in
  `5.119` seconds;
- the complete 430-test source suite passed in `129.525` seconds. The
  environment-gated official Claude Code executable case was the sole local
  skip;
- two independent exact-source builds produced byte-identical artifacts: wheel
  SHA-256 `554fd6b6fcad2fbc6006ffd81d9e6b58cabf3ab8e74f502211fcafe1d1c40cbc`
  (336,810 bytes), source-distribution SHA-256
  `4dc9244f6d76fb3abc7fc76bb1ec48122ecf0ff361a89fdcf4af1b00defa5590`
  (655,641 bytes), and reproducibility-manifest SHA-256
  `463cf70c1d33e8ae0489717e992bf01a12d21b644b2a0922f7e362c41df3111d`;
- the source distribution contains the catalog, validator/runner, role-based
  runbook, and tests. A clean Python 3.14 environment installed the exact wheel
  after the hash-locked runtime closure, passed `pip check`, and ran the
  installed CLI; and
- workflow YAML parsing, source/test bytecode compilation, and
  `git diff --check` passed.

Ordinary package CI and tag verification now execute the same seven regressions
and retain content-free aggregate evidence for seven and thirty days,
respectively. This proves repository-local control behavior only. Live provider
and IdP exercises, shared persistence and multi-region failure, complete tenant
deletion and legal holds, named on-call assignments, incident severities,
response/recovery targets, external communications, and independent review
remain open. Issue #9 remains open and this checkpoint does not authorize an
enterprise release.

### Content-free latency SLI instrumentation

The usage-timing contract was exercised locally on August 17, 2026 from exact
implementation commit `dc848ec9d65ec96d1e30ff84090c0dab0acfb5b2` without a
live provider request or credential.

- the first system-Python attempt stopped before product code because PyJWT was
  unavailable. Re-running in the hash-locked project environment made the new
  client and store tests red because neither the latency opt-in nor bounded
  timing fields existed;
- newly accounted requests now persist nullable non-negative SQLite integers
  for gateway, synchronous-policy, and attempted-provider timings. Historical
  rows remain null, requests without an upstream attempt have no provider
  observation, and injected-context histograms reuse the existing assembly
  timing only when context was actually injected;
- the ordinary tenant-scoped usage response remains the exact schema-v2
  envelope and row shape. `include=latency` explicitly selects schema v3,
  adds four cumulative fixed-bucket histograms, and binds that selection into
  the cursor so page sequences cannot silently change contracts;
- strict client validation rejects unknown histogram fields, non-monotonic
  buckets, count mismatches, non-finite averages, and values outside the SQLite
  integer range. A synthetic maximum 100-row v3 response was 218,592 bytes,
  41.7 percent of the client's 512 KiB limit;
- the first remote Python 3.14 run exposed a brittle pre-existing assertion
  that expected exactly one injected-context redaction even though the contract
  permits multiple occurrences and requires each to be redacted. The failed job
  observed four reported redactions; its later non-disclosure assertion did not
  execute because the exact-count assertion stopped the test first. The
  regression now requires at least one redaction, exact agreement between the
  response header and the replacement count sent upstream, and complete absence
  of the original credential; it passed 20 repeated Python 3.14 loopback runs;
- CLI JSON exposes complete histograms, while its tabular form labels p95
  histogram-bucket upper bounds rather than presenting exact percentiles or SLO
  targets. The timing columns contain no prompt, response, query, credential,
  filename, source text, network address, or caller-controlled label and remain
  outside the stable audit-export v2 shape;
- the focused store, usage-client, and CLI suite passed 68 tests in `0.280`
  seconds. The final complete source suite passed 433 tests in `128.242`
  seconds; the environment-gated official Claude Code executable case was the
  sole expected local skip. Source/test/script bytecode compilation and
  `git diff --check` passed;
- the frozen 60-task release benchmark passed with precision, recall, and
  useful-pack rate `1.00`, mean compression `0.840593`, and zero authorization,
  stale, dependency-stale, malicious, contradiction, budget, determinism, or
  leakage-review failures; and
- two independent exact-source builds produced byte-identical artifacts: wheel
  SHA-256 `f380aded0d08f31b5b73a26beae664246c48acab0a57493f5262b49b4c1e161d`
  (339,596 bytes), source-distribution SHA-256
  `3112e68a8e42debc80857f26affc878e37e169fe85ea5631391b21e2ad2f9977`
  (662,640 bytes), and reproducibility-manifest SHA-256
  `6fa83cbd2abd88802af9541ba4bce8b2a077097b33cb854bfd1311d99f7397f6`.
  A fresh Python 3.14 environment installed the hash-locked runtime closure and
  exact wheel, passed `pip check`, and exposed both latency CLI flags.

This supplies content-free SLI inputs for accounted generation routes only. It
does not set availability or latency targets, alert or page an owner, measure
pre-authentication failures or bypass traffic, export to an external collector,
or prove end-to-end client latency. Shared persistence, HA, externally retained
telemetry, numeric SLOs, named ownership, real infrastructure load, and
independent review remain open under issues #11 and #9.

### Secure session-backed MCP authentication

The MCP session-profile boundary was exercised locally on August 17, 2026 from
exact implementation commit `c3da3b3e1b8fb42bd35a4b8ad1bb5c81d5def115`
without a live provider, identity provider, or credential.

- six failure-first expectations demonstrated that the adapter did not accept a
  profile, generate profile-based Codex or Claude configuration, or resolve a
  credential provider before the change. The implemented focused client and
  configuration set then passed all 15 tests;
- `--profile` and `--credential-env` are mutually exclusive. Human profile mode
  requires HTTPS except for an explicitly permitted loopback development URL;
  the original inherited `HORMUZ_TOKEN` mode and custom safe environment names
  retain their generated configuration and request behavior;
- every context request invokes the existing secure-session resolver instead of
  capturing one access credential at MCP process startup. A two-request
  regression observed two different bearer values, while the session layer's
  existing tests cover unexpired reuse, near-expiry rotation under a
  cross-process profile lock, expiry, replay-family revocation, gateway binding,
  and secure-store failure;
- missing, expired, revoked, malformed, non-UTF-8, secure-store, and refresh
  failures collapse to the fixed model-visible `context_auth_unavailable`
  boundary. Internal exception text, profile names, refresh state, and
  credential values are not returned;
- generated Codex TOML and Claude Code JSON contain only the gateway URL,
  profile label, timeout, and ordinary MCP process settings in profile mode.
  They contain no access credential, refresh credential, inherited credential
  variable, or provider key;
- the broader MCP, session broker, session store/configuration, credential-store,
  and CLI set passed 104 tests in `17.792` seconds. The complete source suite
  passed 440 tests in `129.987` seconds; the environment-gated official Claude
  Code executable case was the sole expected local skip. Source/test bytecode
  compilation and `git diff --check` passed;
- the source distribution and wheel built successfully. An isolated Python
  3.14 environment installed the exact wheel from `site-packages`, passed
  `pip check`, generated both profile configurations, and completed MCP
  initialize plus `tools/list` through the installed executable. The wheel was
  340,504 bytes with SHA-256
  `3cdc6861acc4c998e0f50023c67f3d1cb3761b2a5e4e67b3db8857f6d8101fd1`;
  the source archive was 676,328 bytes with SHA-256
  `00577401d1c7768d80ef0733a8778cbc584c12bf2aa3f41518dcecf8108d17cf`;
  and
- frozen corpus verification retained SHA-256
  `9822d592868202c7c7539bcdac7d4a5894c01f9e6dba7a434846516b67b32c17`.
  The 60-task release profile passed with governed precision, recall, and
  useful-pack rate `1.00`, p95 retrieval latency `0.145334` milliseconds, and
  zero authorization, lifecycle-stale, dependency-stale, malicious,
  contradiction, token-budget, or determinism failures.

This proves additive adapter wiring, secret-free client configuration, and the
local session-refresh composition. It does not prove enrollment against a real
IdP, a blocking cross-platform OS-keyring deployment, shared multi-node
revocation, SCIM, KMS custody, or the pending enterprise persistence topology.
Those remain separate release gates.

### Installed-client Keychain profile and governed-context call

The profile path was then exercised locally on macOS 26.2 arm64 with the exact
locked Codex CLI `0.147.0`, Claude Code `2.1.233`, and Node.js `24.19.0`.

- each test configured the real single-node session broker, created a bounded
  client-specific opaque session inside that deterministic local fixture, and
  stored it through the allowlisted real macOS Keychain backend under a unique
  transient profile;
- the child Codex and Claude Code processes inherited no static Hormuz identity
  credential and no OpenAI or Anthropic provider key. Their native auth helper
  obtained inference access from the Keychain profile, while the gateway alone
  authenticated upstream with its provider-owned test credential;
- the fake OpenAI model emitted the native Codex Responses namespace call for
  `mcp__hormuz` / `hormuz_get_context`. The fake Anthropic model emitted Claude
  Code's flat `mcp__hormuz__hormuz_get_context` tool-use block. Each stock client
  invoked the actual stdio adapter with the same profile and returned the
  selected `hormuz.context-pack.v1` result to its next provider request;
- the pack contained only the verified team-authorized fixture record, and the
  context repository committed exactly one metadata-only `context.read` event
  under actor `alice` for each run. Provider requests contained the provider
  credential, never the Hormuz access credential, and neither client printed a
  Hormuz credential;
- Claude Code was granted only the read-only Hormuz MCP tool. No shell, file, or
  other built-in tool was enabled. Automatic context injection stayed disabled
  in both tests so a tool-only continuation did not imply or preempt the pending
  continuation-binding decision; and
- both focused tests passed in `2.980` seconds under the exact Node.js runtime.
  Their `finally` paths and unittest cleanup each delete the transient Keychain
  profile, and a postcondition verified that no entry remained.

The complete source suite then passed 442 tests in `130.725` seconds with only
the three documented opt-in installed-client cases skipped. Source/test bytecode
compilation, `git diff --check`, and the strict compatibility contract passed.
The frozen 60-task corpus retained SHA-256
`9822d592868202c7c7539bcdac7d4a5894c01f9e6dba7a434846516b67b32c17`;
its release profile passed with governed precision, recall, and useful-pack rate
`1.00`, p95 assembly latency `0.148708` milliseconds, and zero authorization,
lifecycle-stale, dependency-stale, malicious, contradiction, token-budget, or
determinism failures.

Two independent distributions built byte-identically from implementation
commit `94865405efb66fd7f3637aa881d60a4b7d16cf0c` under the exact locked build
closure. The wheel was 340,596 bytes with SHA-256
`4365b4dd6fcacfd357f8c6770eeeec7860a268129e56533f68fdf1eea2075896`;
the source archive was 673,314 bytes with SHA-256
`4f2d7dc3afb5ca2afde0e7722831e5c7919be8b4e43830a804e60f60f7693d1a`.
A fresh Python 3.12.12 environment outside the checkout installed the exact
wheel and hash-locked runtime closure, loaded Hormuz from `site-packages`,
reported no broken requirements, compiled the installed package, generated
both secret-free profile MCP configurations, and completed MCP initialize plus
`tools/list` through the installed executable.

This closes the prior local question of whether an installed official client
can actually complete a profile-authenticated governed-context call. It does
not prove browser enrollment against a real IdP, a hosted or production
Keychain deployment, Linux Secret Service, Windows Credential Manager,
blocking CI coverage for this opt-in macOS case, shared revocation, SCIM, KMS,
automatic-injection continuation lineage, or any live provider behavior. The
fixture intentionally creates its short-lived session inside the local broker
instead of substituting a fake plaintext credential store.

## Reproduce locally

The default suite uses only loopback fake providers:

```bash
python3 -m unittest -v
```

After installing the integrity-locked clients under `deploy/clients`, the
macOS Keychain profile proof is opt in:

```bash
PATH="$PWD/deploy/clients/node_modules/.bin:$PATH" \
HORMUZ_RUN_PROFILE_CLIENT_TEST=1 \
HORMUZ_RUN_CLAUDE_CLIENT_TEST=1 \
python3 -m unittest -v \
  tests.test_gateway.GatewayIntegrationTests.test_installed_codex_calls_context_with_keychain_profile \
  tests.test_gateway.GatewayIntegrationTests.test_official_claude_calls_context_with_keychain_profile
```

The test refuses to run outside macOS or without the explicit opt-in flags.
It writes only transient test sessions to Keychain and deletes them on success
or failure.

The live-provider check requires an ignored credential file or secret-manager
injection. The reusable content-free command and its claim boundary are defined
in [PROVIDER_CONFORMANCE.md](PROVIDER_CONFORMANCE.md). For OpenAI, start the
loopback example with the provider credential only in the Hormuz process, then
run:

```bash
hormuz provider-conformance \
  --provider openai \
  --gateway http://127.0.0.1:8791 \
  --allow-insecure-http \
  --credential-env HORMUZ_CONFORMANCE_TOKEN \
  --model openai-live-luna \
  --max-output-tokens 16 \
  --output /tmp/hormuz-openai-conformance.json
```

Using the separate 64-token redaction example on port `8792`, verify the fixed
synthetic-secret path without accepting operator content:

```bash
hormuz provider-conformance \
  --provider openai \
  --gateway http://127.0.0.1:8792 \
  --allow-insecure-http \
  --credential-env HORMUZ_CONFORMANCE_TOKEN \
  --model openai-live-luna \
  --probe secret-redaction \
  --max-output-tokens 64 \
  --output /tmp/hormuz-openai-redaction-conformance.json
```

With the same gateway running, verify the pinned stock client through the
reusable isolated harness:

```bash
hormuz client-conformance \
  --client codex \
  --gateway http://127.0.0.1:8791 \
  --allow-insecure-http \
  --credential-env HORMUZ_CONFORMANCE_TOKEN \
  --model openai-live-luna \
  --executable deploy/clients/node_modules/.bin/codex \
  --expected-version 0.147.0 \
  --expected-executable-sha256 134063e133f0b4244fa3b251acf973d4fe4b4aeeacbdc135211bf480f59f1477 \
  --output /tmp/hormuz-codex-conformance.json
```

Never add real provider or employee credentials to this record.

One bounded OpenAI observation completed on local evening August 19, 2026. The
request traversed Hormuz `POST /v1/responses`, the policy alias routed to
`gpt-5.6-luna`, the fixed marker and provider usage were verified, and Hormuz
recorded 20 input tokens, 10 output tokens, 30 billable tokens, and an estimated
`$0.000016` cost under the example rate card. The generated evidence was mode
`0600`; exact-value scans found neither credential in it or the gateway log, and
the marker was absent from the evidence. The checked-in content-free artifact is
[`evidence/provider-conformance-openai-2026-08-19.json`](../evidence/provider-conformance-openai-2026-08-19.json).
This does not claim Anthropic live conformance, provider SLA/retention/residency,
or production readiness; the combined compatibility flag remains false.

The same live gateway was then exercised by the pinned stock Codex CLI `0.147.0`
with `OPENAI_API_KEY` explicitly removed from the client environment. Codex
received only the generated Hormuz employee credential, exited zero, and
returned its fixed marker. Hormuz recorded 12,332 input tokens, 9 output tokens,
12,341 billable tokens, 2,029 milliseconds gateway latency, and an estimated
`$0.002477` cost while enforcing the 16-token organization output cap. Exact
credential scans of Codex stdout, stderr, and the gateway log were negative. The
content-free observation is
[`evidence/codex-openai-live-2026-08-19.json`](../evidence/codex-openai-live-2026-08-19.json).

The reusable harness repeated that fixed client path with pinned Codex `0.147.0`
and its operator-approved resolved-executable SHA-256,
and recorded a 1,527 millisecond client invocation. It used a sanitized
environment, empty private workspace, non-persistent read-only Codex execution,
disabled shell, multi-agent, and web-search tools, and the
dedicated final-message file rather than matching console output. Its
content-free evidence is
[`evidence/client-conformance-codex-openai-2026-08-19.json`](../evidence/client-conformance-codex-openai-2026-08-19.json).
This adds a repeatable operator command, not a wider provider or production
support claim.

The fixed synthetic-secret probe then passed through live OpenAI at the same
local-evening checkpoint. Hormuz returned `allowed+redacted` with exactly one
redaction; OpenAI returned only the sanitized placeholder and reported 21 input,
37 output, 21 reasoning, and 58 billable tokens. Measured latency was 1,570
milliseconds. Exact provider-key, employee-credential, and synthetic-value scans
of the generated evidence plus gateway log were negative. The content-free
artifact is
[`evidence/provider-redaction-conformance-openai-2026-08-19.json`](../evidence/provider-redaction-conformance-openai-2026-08-19.json).
This is not an organization-specific detector evaluation, complete DLP proof,
or live Anthropic observation.

### Percent and hexadecimal JSON-string DLP inspection

The ordinary JSON/tool-output decoder, evaluation kernel, both provider paths,
package artifacts, threat contract, and bounded residual-risk documentation
were exercised locally on August 20, 2026.

- red-first regressions proved a fully percent-encoded OpenAI-shaped credential
  and a hexadecimal Anthropic-shaped credential were classified as `allow`; the
  corresponding gateway requests reached both fake providers with HTTP `200`;
- detector version `hormuz-deterministic-v2` now recursively inspects printable
  UTF-8 revealed by `%HH` or hexadecimal text, including optional `0x`, under
  the existing 1 MiB decoded-value and three-layer limits. Percent decoding
  deliberately preserves `+` as a literal character;
- safe and detect-only encoded values remain byte-for-byte unchanged. Hidden
  findings configured for redaction deny rather than rewriting ambiguous
  syntax; deny and approval semantics remain unchanged;
- mixed-view tests prove a direct detect-only email cannot hide a stronger
  encoded credential, visible findings are subtracted from decoded views rather
  than double-counted, and a visible redaction remains transformable when the
  percent escape reveals no additional protected content;
- unit coverage includes the shortest valid four-byte organization dictionary
  value, optional upper-case `0x` input, three successful nested layers, a
  denied fourth layer, separate percent/hex size limits, and unchanged safe
  forwarding;
- OpenAI function output and Anthropic tool-result requests carrying the two
  encoded fake credentials returned provider-shaped HTTP `403`, made zero
  provider calls, recorded one content-free denied security event each, and
  left raw plus encoded values absent from responses, event representations,
  and SQLite;
- the offline DLP evaluator detected the same organization dictionary value in
  base64, percent, and hexadecimal cases while retaining only aggregate counts
  and the new detector version;
- the complete suite ran 466 tests successfully with three documented opt-in
  installed-client/profile tests skipped. The focused redaction, evaluator,
  and gateway slice ran 141 tests successfully with the same three skips;
- all seven repository-local incident drills passed. The strict threat and
  compatibility contracts passed while retaining `enterprise_release_ready:
  false`, five open threats, no verified production persistence/deployment,
  and independent review pending;
- the frozen 60-task release benchmark retained corpus SHA-256
  `9822d592868202c7c7539bcdac7d4a5894c01f9e6dba7a434846516b67b32c17`,
  precision, recall, and useful-pack rate `1.00`, mean compression `0.840593`,
  zero safety-threshold failures, and governed p95 selection latency
  `0.152791` ms; and
- isolated source and wheel builds succeeded. The exact install-tested wheel
  SHA-256 was
  `3b77c2b4ebb4482c59b7d43aeb8e8ab6f56f80310960f6a4cac8208af146d9a3`.
  The source archive is not self-hashed inside this embedded record because
  changing the record changes that archive. It contained the implementation,
  tests, and security docs;
  an external exact-wheel target loaded `hormuz.redaction`, asserted detector
  v2, denied the percent-encoded synthetic credential, and compiled.

This closes two ordinary JSON-string encoding bypasses under accepted ADR 0004,
not issue #10 or the enterprise DLP gate. Non-UTF-8/unknown transfer encodings,
application-specific decoding, archive contents, provider-referenced file
contents, source classification, semantic detection, organization-specific
threshold approval, shared approval/KMS/HA, immutable retention, future cache
invalidation, and independent review remain open.

### OpenAI direct-query compaction governance

The OpenAI compaction route, automatic governed-context policy, post-injection
DLP, budget reservation, provider usage accounting, compatibility contract, and
bounded residual documentation were exercised locally on August 20, 2026.

- red-first gateway tests proved that `POST /v1/responses/compact` was classified
  as an unsupported injection operation: required mode still reached the fake
  provider, a direct-query request lacked the governed pack, and shared output
  logic added the generation-only `max_output_tokens` field. Anthropic token
  counting likewise received an unsupported `max_tokens` field;
- the gateway now treats direct current-user compaction as a supported automatic
  context operation. It uses the existing OpenAI user-priority renderer, leaves
  `instructions` unchanged, consumes the same authorized repository selectors,
  and runs the fully rendered provider body through secret/DLP inspection;
- a verified organization record containing a configured fake provider secret
  produced one redaction after injection. The provider-compatible fake received
  the authorized reference and placeholder but not the secret; the client
  response, content-free usage event, and SQLite bytes also excluded both the
  secret and raw query;
- compaction usage retained the Context Pack and selected-record lineage plus
  the fake provider request ID and reported 90 input, 25 output, 4 reasoning,
  and 115 total tokens. A compaction-only opaque input in required mode returned
  stable HTTP `403` with `no_eligible_query`, made zero provider calls, and
  recorded only content-free denied metadata;
- Hormuz no longer sends `max_output_tokens` to compaction or `max_tokens` to
  Anthropic token counting. For accounted compaction it still reserves the
  effective 100-token policy allowance locally in addition to serialized input,
  then replaces the reservation with actual provider usage;
- the complete suite ran 468 tests successfully with three documented opt-in
  installed-client/profile tests skipped. The focused compaction, token-count,
  and compatibility slice ran 15 tests successfully;
- all seven repository-local incident drills passed. The strict threat contract
  retained 18 threats, five open threats, seven covered incident scenarios,
  independent review pending, and `enterprise_release_ready: false`;
- the strict compatibility contract retained 16 entries, exact Codex `0.147.0`
  and Claude Code `2.1.233` pins, no verified production persistence or
  deployment profile, and `enterprise_release_ready: false`; and
- the frozen 60-task release benchmark retained corpus SHA-256
  `9822d592868202c7c7539bcdac7d4a5894c01f9e6dba7a434846516b67b32c17`,
  precision, recall, and useful-pack rate `1.00`, zero authorization, stale,
  malicious, contradiction-selection, determinism, or token-budget failures,
  and governed p95 selection latency `0.178917` ms; and
- isolated source and wheel builds succeeded. A clean Python 3.14 environment
  installed the built wheel and hash-locked runtime dependencies outside the
  checkout, loaded `hormuz.server` from `site-packages`, asserted the compaction
  operation and reservation branch, compiled the installed package, displayed
  CLI help, passed `pip check`, and passed the installed strict 60-task
  benchmark with zero failed thresholds. Exact final-commit distribution
  digests belong in the external PR/CI evidence rather than this self-changing
  source record.

A follow-up content-free live conformance checkpoint completed later on August
20, 2026:

- red-first tests captured OpenAI `cache_write_tokens` being dropped (`0 != 5`)
  and the new compact probe being rejected as `invalid_probe` before the
  implementation changed;
- the OpenAI usage allowlist now preserves provider-reported cache-write input
  as a separate cost category while normalized billable volume remains input
  plus output because OpenAI cache categories are input subsets;
- the opt-in OpenAI-only probe sends one fixed package prompt, requires a valid
  Hormuz Context Pack header and injected context marker, validates the returned
  `response.compaction` shape plus provider usage, and retains none of the raw
  prompt, context, pack ID, provider request/response IDs, or opaque compaction;
- the live request routed `openai-live-luna` to `gpt-5.6-luna`, returned
  `allowed+context-injected`, and reported 436 input, 390 output, zero cache
  read/write/reasoning, and 826 billable tokens in 4,154 milliseconds. Hormuz
  estimated 555 micro-USD under `openai-public-2026-08-20-v1`;
- the provider omitted an actual-model field from its compact response, so the
  evidence omits `actual_model` instead of manufacturing it from the route;
- the evidence file was mode `0600`. Exact-value scans found no provider key in
  the evidence, gateway log, usage database, or context database, while fixed
  prompt, governed context, Context Pack ID, and opaque compaction markers were
  absent from the checked-in evidence;
- 23 focused usage/provider-conformance tests and the focused loopback gateway
  compaction test passed. The complete suite then ran 471 tests successfully
  with the same three documented opt-in installed-client/profile skips; and
- the strict compatibility contract passed with 16 entries and
  `enterprise_release_ready: false`. The content-free observation is
  [`evidence/provider-compaction-conformance-openai-2026-08-20.json`](../evidence/provider-compaction-conformance-openai-2026-08-20.json).

This closes a policy-composition bypass for direct-query compaction under
accepted ADR 0006, not issue #5 or provider certification. OpenAI does not
define a hard output-cap field for compaction, so the effective cap is a local
reservation allowance rather than a provider-enforced per-request ceiling.
Previous-response-only, opaque-compaction-only, and tool-only continuation
binding still requires a separate owner decision. Blocking release coverage
still uses a provider-compatible loopback fake; the one fixed live OpenAI
compaction observation does not establish installed Codex compaction, live
Anthropic, cache policy, provider SLA, or enterprise readiness.

### PostgreSQL tenant-isolation feasibility

A standalone, opt-in PostgreSQL feasibility verifier was exercised locally on
August 20, 2026. It accepts only an immutable `postgres@sha256:...` reference,
requires that exact image to exist locally, forbids image pulling, launches a
disposable container without networking, ports, or host mounts, and removes the
container before producing verified evidence.

Observed result:

- the exact digest resolved to PostgreSQL server version `16.14`;
- the synthetic runtime role was a non-owner, non-superuser without
  `BYPASSRLS`;
- both synthetic tables had RLS enabled and forced;
- missing tenant context, the forced table owner without context, and a reused
  session after transaction-local context cleared each returned zero rows;
- each of two synthetic tenants saw only its own record, and an explicit
  cross-tenant read returned zero rows;
- a cross-tenant insert was denied by RLS and a composite tenant foreign key
  denied a cross-tenant reference;
- eight verifier/evidence unit tests passed, including unknown launch cleanup,
  final-startup readiness,
  invalid mutable image,
  mismatched proof, unexpectedly allowed write, failed cleanup, Docker Desktop
  immutable-ID fallback, private output, and overwrite-refusal paths;
- the generated evidence was mode `0600`, retained no SQL output or ephemeral
  credential, and a final Docker inspection found no matching container;
- the complete source suite passed 479 tests with three documented opt-in
  installed-client/profile tests skipped; all seven repository-local incident
  drills passed; the strict threat contract retained five open threats and
  independent review pending; and the strict compatibility contract retained
  `pending_owner_decision`, zero verified production-persistence profiles, and
  `enterprise_release_ready: false`;
- the frozen 60-task governed-context release profile retained precision,
  recall, and useful-pack rate `1.00`, zero safety-threshold failures, and
  governed p95 selection latency `0.151` ms.

The checked-in content-free observation is
[`evidence/postgres-rls-feasibility-2026-08-20.json`](../evidence/postgres-rls-feasibility-2026-08-20.json).
Reproduce it only when the exact digest is already cached:

```bash
python scripts/postgres_rls_feasibility.py \
  --output /tmp/hormuz-postgres-rls-feasibility.json
```

This historical feasibility result predated product-owner acceptance and the
schema-v1 implementation below. By itself it did not prove an accepted schema,
repository implementation, migrations, connection-pool behavior, production
concurrency, backup/PITR, restore, deletion, residency, KMS, HA, or independent
security review. The later checkpoint supersedes its historical
`pending_owner_decision` compatibility status without changing
`production_supported: false`.

### Accepted PostgreSQL schema-v2 accounting slice

After the product owner accepted ADR 0002 option A on August 20, 2026, the
PostgreSQL boundary was implemented and exercised locally against the same
immutable PostgreSQL `16.14` image digest.

Observed result:

- `hormuz storage migrate` applied packaged schema version 2 from an empty
  database and reapplied it idempotently; `hormuz storage verify` bound the
  ordered migration name and SHA-256 to the database ledger;
- the schema created tenant, workspace, project, team, principal,
  external-identity, role, capability, team-membership, usage, provider-cost,
  usage-read-audit, and budget-reservation tables with tenant-leading keys and
  composite tenant foreign keys;
- the migration owner and application runtime roles were distinct. The runtime
  was non-superuser, had no role memberships, lacked `CREATEDB`, `CREATEROLE`,
  and `BYPASSRLS`, owned no tenant table, could not create in the Hormuz schema,
  and could not read the migration ledger or truncate/change constraints and
  triggers;
- verification required every tenant table to retain the exact bidirectional
  tenant policy, forced RLS, the exact tenant-immutability function and trigger,
  migration-owner ownership, and the exact accounting column sets. A
  deliberately permissive replacement policy, a temporary runtime-to-owner
  membership, and an unexpected usage column were each detected and rejected;
- an application tenant transaction bound tenant, principal, client, and
  authorization version only for that transaction and rejected an owner or
  privileged connection. Missing context and the same connection after commit
  each saw zero rows;
- one reused runtime connection switched between two synthetic tenants without
  leakage. Each tenant saw one row, an explicit cross-tenant read saw zero, RLS
  denied a cross-tenant insert, the composite foreign key denied a cross-tenant
  reference, and the forced-RLS owner could not change a tenant key;
- two independent PostgreSQL accounting stores each wrote and read only their
  selected tenant; usage reports, bounded gateway-coverage summaries, and
  metadata-only usage-read audit were exercised against the real database;
- two concurrent writers attempted 600-token reservations against the same
  tenant-month with 1,000 tokens available after recorded usage. Exactly one
  reservation committed, one was denied, and release removed the winner;
- duplicate provider-cost imports converged on one snapshot and aggregate
  reconciliation preserved exact provider decimal cost separately from
  request-time estimated micro-USD;
- the optional Psycopg `3.3.4` binary driver has a separate hash-locked Linux
  integration closure, and the package exposes content-free migration and
  verification CLI commands without accepting a DSN on the command line;
- the focused PostgreSQL, compatibility, threat-model, and release-contract
  slice passed. The final complete source suite passed 507 tests with three
  documented opt-in installed-client/profile skips; and
- the compatibility matrix now reports two `development_only` persistence
  surfaces, zero pending owner decisions, and zero production-persistence
  profiles. The threat model retains five open threats, independent review is
  pending, and `enterprise_release_ready` remains false.

The checked-in content-free observation is
[`evidence/postgres-foundation-integration-2026-08-20.json`](../evidence/postgres-foundation-integration-2026-08-20.json).
Reproduce it with the separately documented optional driver and exact cached
image:

```bash
python scripts/postgres_foundation_integration.py \
  --output /tmp/hormuz-postgres-foundation.json
```

This is real schema, migration, role, tenant-isolation, usage/cost persistence,
and shared budget-concurrency evidence. `hormuz serve` can opt into that
accounting backend while sessions, DLP approvals, and governed context remain
SQLite-backed. Cutover backfill, dynamic authorization versions, connection
pooling, multi-node revocation, backup/PITR, tenant restore/deletion, KMS, HA,
production operations, and independent security review stay open under issues
#6, #7, #11, #17, and #9.

### Per-model capacity enforcement

Provider-neutral per-model monthly capacity was exercised locally on August
20, 2026 without making a provider request:

- organization, team, and employee policies independently limited input-plus-
  output tokens and estimated USD spend for an exact governed model alias;
- organization and team policies also imposed per-employee model allowances,
  while concurrent reservation tests proved one employee's usage does not
  consume another employee's allowance;
- routing tests proved an unapproved requested model cannot evade capacity by
  falling back, because the resolved destination alias is checked and reserved;
- model isolation tests proved a reached allowance for one alias does not block
  another alias, and legacy reservation tables migrated with a nullable alias;
- `policy-check` exposed every applicable model-capacity scope without provider
  work, while strict configuration parsing rejected unknown aliases, empty
  entries, unknown fields, invalid limits, and identities lacking an effective
  maximum-output bound;
- the content-free telemetry manifest advanced to
  `hormuz.content-free-schema.v2`; its only new field is the governed alias on a
  short-lived budget reservation;
- the complete source suite passed 486 tests with the same three documented
  opt-in installed-client/profile skips; all seven repository-local incident
  drills passed; and the strict threat and compatibility contracts retained
  `enterprise_release_ready: false`, five open threats, independent review
  pending, and zero verified production persistence profiles; and
- the frozen 60-task, five-iteration governed-context release profile retained
  precision, recall, and useful-pack rate `1.00`, zero safety-threshold
  failures, and governed p95 selection latency `0.149` ms.

This closes atomic per-model enforcement for the current single-node SQLite
deployment. It does not establish shared enforcement across replicas, actual
provider-invoice caps, or production tenancy. Those remain dependent on the
owner-approved persistence topology, hosted reconciliation, and the existing
enterprise release gates.

### Versioned billing-reconciliation exception policy

The aggregate provider-cost reconciliation path was extended and exercised
locally on August 20, 2026 without a live provider request or credential:

- an optional organization policy now binds exact maximum absolute USD
  variance, relative basis-point variance, unpriced request count, legacy
  unattributed request count, unscoped provider-item count, and authenticated-
  source requirements to an explicit version and canonical SHA-256;
- enabled configuration without an explicit version or at least one rule,
  floating-point USD thresholds, unknown fields, out-of-range values, excessive
  decimal precision, inconsistent raw variance, and invalid count/source facts
  fail closed;
- relative variance uses the absolute provider-reported cost as its denominator,
  admits exact threshold equality, treats two zero totals as zero basis points,
  and emits `variance_basis_unavailable` when provider cost is zero but variance
  is not;
- reconciliation schema version 2 emits stable `not_evaluated`, `clear`, or
  `review_required` status and reason codes. `--fail-on-review` emits the full
  result before returning exit `3`, and returns exit `2` before database access
  if the policy is disabled;
- all 77 focused billing/configuration tests passed. The complete source suite
  passed 491 tests in `136.435` seconds with the same three documented opt-in
  installed-client/profile skips;
- all seven repository-local incident drills passed. The strict compatibility
  contract retained product stage `alpha`, zero verified production persistence
  profiles, and `enterprise_release_ready: false`; the threat contract retained
  five open threats and independent review pending; and
- the frozen 60-task, five-iteration governed-context release profile retained
  corpus SHA-256
  `9822d592868202c7c7539bcdac7d4a5894c01f9e6dba7a434846516b67b32c17`,
  precision, recall, and useful-pack rate `1.00`, zero safety-threshold failures,
  and governed p95 selection latency `0.150375` ms. Source/test/script bytecode
  compilation, `git diff --check`, and a high-confidence tracked-file credential
  scan also passed.

This is deterministic, automation-safe exception classification over one
aggregate snapshot. It is not scheduled provider polling, hosted administrator-
key custody, a persistent authenticated reviewer/case workflow, invoice/credit
finalization, or final team/person chargeback. Aggregate variance remains
unresolved evidence and does not by itself prove that traffic bypassed Hormuz.

### PostgreSQL desired-state identity and multi-instance session slice

The second ordered PostgreSQL migration slice was exercised locally on August
20, 2026:

- packaged schema version 3 adds exact-column, forced-RLS identity-projection,
  principal-projection, enrollment, session, consumed-refresh, and
  session-security-event tables with immutable tenant keys;
- the runtime role was reduced to read-only access for tenants, teams,
  principals, external identities, roles, capabilities, memberships, and
  desired-state projection metadata. It retains the previously accepted
  tenant-scoped workspace/project DML and write access to the gateway-owned
  accounting/session tables currently migrated;
- `hormuz identities sync` used the schema-owner deployment path to project
  configuration without provider, employee, or database credential values. An
  unchanged second sync made zero organization/principal changes;
- the runtime startup verifier matched each configured organization's canonical
  projection fingerprint. Changing only an actor's configured OIDC subject
  mapping incremented that principal's authorization version and revoked the
  affected active session;
- tenant-bound enrollment is inferred when one issuer maps to one organization
  and requires an explicit CLI organization only for a multi-organization
  issuer. Enrollment IDs, OAuth state, access credentials, and refresh
  credentials carry a keyed 96-bit hexadecimal routing tag rather than the raw
  organization ID;
- two independent PostgreSQL repository instances completed one enrollment and
  authenticated the resulting credential across instances. Under two
  concurrent refreshers, exactly one rotated, one detected replay, and the
  current credential family was revoked. A transaction advisory lock binds the
  race to the exact tenant and keyed refresh hash;
- the digest-pinned PostgreSQL `16.14` integration applied and verified
  migration versions `1`, `2`, and `3`, retained the prior accounting and RLS
  results, and wrote content-free evidence to
  `evidence/postgres-foundation-integration-2026-08-20.json`;
- 110 focused configuration, SQLite-session, PostgreSQL, CLI, compatibility,
  and threat-model tests passed; the complete source suite passed 514 tests in
  `137.852` seconds with the same
  three documented opt-in installed-client/profile skips; and
- source/test/script bytecode compilation and `git diff --check` passed before
  the full suite.

This is bounded local multi-instance persistence evidence, not a production
storage claim. Real IdP validation, SCIM, DLP-approval and governed-context
migration, connection pooling, KMS custody/rotation, backup/PITR and restore,
HA/failover, retention/export/delete operations, deployment rollback, and
independent security review remain open. Issue #6 and draft PR #19 therefore
remain open.

### PostgreSQL policy projection and multi-instance DLP approval slice

The third ordered PostgreSQL migration slice was exercised locally on August
20, 2026:

- packaged schema version 4 adds exact-column, forced-RLS policy-projection,
  secret/DLP-event, approval-request, and approval-event tables with immutable
  tenant keys. The runtime role can read but not mutate policy projections and
  can write only tenant-scoped gateway security/approval state;
- `hormuz policies sync` used the schema-owner deployment path to store a
  tenant-specific canonical policy document and SHA-256. It retained model,
  budget, route, DLP, and context-injection policy metadata while excluding
  provider/identity credentials, resolved secret and dictionary values, the
  approval fingerprint key, prompts, responses, matched values, filenames, and
  source content. An unchanged second sync made zero organization changes;
- a gateway runtime using the non-owner role matched every configured policy
  projection. A changed model/output/DLP policy failed closed with
  `policy_projection_stale` until the owner sync applied the candidate;
- two independent PostgreSQL security repositories shared pending decisions,
  metadata-only security events, and approval audit. Cross-tenant request lookup
  returned only `approval_request_not_found`, and the requesting actor could not
  self-approve even when separately given the approver capability;
- a transaction advisory lock serialized two concurrent exact retries. Exactly
  one consumed the approved grant; the other remained blocked behind a new
  pending request. A changed routed model did not consume the exact grant, and
  an actual-provider-model mismatch created a metadata-only audit event;
- the digest-pinned PostgreSQL `16.14` integration applied and verified
  migration versions `1` through `4`, retained the accounting, identity/session,
  and tenant-isolation results, rejected unexpected accounting and security
  columns, and replaced the checked-in evidence at
  `evidence/postgres-foundation-integration-2026-08-20.json`;
- 108 focused PostgreSQL, approval, compatibility, threat-model, CLI, and
  content-free-schema tests passed. The complete source suite passed 518 tests
  in `137.004` seconds with the same three documented opt-in installed-client
  skips; and
- source/test/script bytecode compilation, `git diff --check`, and a local
  source/wheel build passed. The built wheel and source archive both included
  the policy/security repositories and migration `0004_policy_approvals.sql`.

This proves bounded shared PostgreSQL behavior, not a production persistence or
policy-administration system. Existing SQLite approvals are deliberately not
backfilled or honored after cutover. Policy rollout currently requires an
owner-run coordinated sync/replacement sequence; there is no multi-version
activation, change-approval API, or automatic rollback. Governed context,
SCIM, representative DLP evaluation, approval notifications/queue UX,
connection pooling, KMS custody/rotation, backup/PITR and restore, HA/failover,
retention/export/delete operations, and independent security review remain
open. Issue #6, issue #10, and draft PR #19 therefore remain open.

### Gateway-only policy projection and release-contract slice

The second issue #20 boundary slice was exercised locally on August 20, 2026:

- `hormuz.policy-projection.v2` retains model routes, model and budget limits,
  fallback behavior, secret controls, and DLP/approval policy while excluding
  deprecated context-injection configuration. Changing only that legacy
  configuration leaves the projection fingerprint unchanged;
- an existing version-1 projection fails replacement-runtime startup with the
  established `policy_projection_stale` error until an owner runs
  `hormuz policies sync`. This is a document migration only; PostgreSQL schema
  version 4 is unchanged;
- the machine-readable threat register now covers gateway identity, policy,
  DLP, routing, provider relay, accounting, and provider-native cache risk. Its
  required policy-rollout drill binds to the stale-projection startup test;
- the data-deletion drill now proves tenant isolation of usage, security, and
  reservation records without claiming that a deprecated context-record test
  establishes an organization deletion workflow;
- ordinary and tag release workflows no longer run or retain the deprecated
  context benchmark. Context tests remain in the complete source suite as
  compatibility evidence, and the public compatibility matrix labels the live
  context-compaction observation historical and experimental;
- focused policy-projection, threat-model, incident-drill, compatibility, and
  release-contract tests passed; and
- the complete source suite passed 519 tests in `137.068` seconds with the same
  three documented opt-in installed-client/profile skips. `git diff --check`
  also passed.

This is a source-level product-boundary and upgrade-contract checkpoint. It is
not versioned live policy administration, authorized activation, coordinated
replica rollout, or rollback; those remain issue #21. The deprecated context
configuration, commands, routes, schema columns, and tests remain readable and
executable until a separately versioned removal and sunset decision.

### Immutable policy-version and activation-store slice

The first issue #21 implementation slice was exercised locally on August 20,
2026:

- packaged PostgreSQL migration 5 adds tenant-scoped immutable policy versions,
  an atomic active-version pointer, and append-only policy events. Version IDs
  are the `hpv_v1_` prefix plus the canonical projection SHA-256;
- version rows retain the validated secret-free projection, author, timestamp,
  and a structural summary containing only changed section names and a count.
  Free-form comments, prompts, responses, resolved secret values, and matched
  DLP values are not part of the history schema;
- runtime grants are exact: policy versions and events allow only `SELECT` and
  `INSERT`; the active pointer allows `SELECT`, `INSERT`, and `UPDATE` but not
  deletion. Additional database triggers reject policy-version and event
  mutation even by the schema owner;
- two independent runtime repositories staged one policy idempotently,
  activated two versions with compare-and-swap semantics, observed the same
  active snapshot, and rolled back to the previously active version at
  activation sequence 3;
- a caller without `policy_admin` was rejected before database work, and a
  tenant-B administrator could not discover or activate tenant A's version;
- the digest-pinned PostgreSQL `16.14` integration applied and verified
  migrations 1 through 5 and emitted the exact checked-in content-free artifact
  at `evidence/postgres-foundation-integration-2026-08-20.json`; and
- 24 focused PostgreSQL/configuration tests passed. The complete local suite
  passed 520 tests in `137.917` seconds with 3 documented opt-in client skips;
  source/test/script bytecode compilation and `git diff --check` also passed.

This closes only the durable policy-version and atomic-pointer foundation.
There is no public policy administration API/CLI yet, and provider requests do
not yet read the active pointer. Issue #21 remains open for authenticated
staging, activation, rollback, request-time exact-version evaluation, usage
lineage, concurrent-reader tests at the gateway boundary, and the operator
runbook.

### Authenticated request-time policy administration slice

The second issue #21 implementation slice was exercised locally on August 20,
2026:

- a caller with `policy_admin` can export a canonical secret-free projection,
  stage it through the CLI or authenticated HTTP API, inspect the active
  version, activate with compare-and-swap semantics, and roll back with an
  explicit expected-current version. Invalid, cross-tenant, oversized, and
  non-canonical documents fail before activation;
- materialization reconstructs only the gateway's supported policy surface.
  Custom secret environment names, DLP dictionaries, approval keys, and
  deployment upstreams must already be provisioned in the target gateway;
  resolved secret values and request or response content never enter a policy
  version;
- every provider request reads the tenant's active pointer, reuses only the
  immutable materialized version, and evaluates routing, limits, secret/DLP
  controls, approval policy, and budgets against that exact version. Failure to
  read or materialize an active version fails closed before a provider call;
- each usage record and content-free accounting audit carries the exact
  `governance_policy_version`. The content-free manifest is version 4 and the
  accounting audit event schema is version 4;
- packaged PostgreSQL migration 6 adds the non-null usage lineage column and
  migration 7 adds the controlled identity type. The digest-pinned PostgreSQL
  `16.14` integration applies migrations 1 through 7,
  exercised staged and active versions across independent repositories, and
  emitted schema
  `hormuz.postgres-policy-administration-integration.v8` with exact-version
  accounting proof;
- policy-store outage, active-version enforcement, exact usage lineage,
  administration authorization, CLI transport, rollback input, configuration
  reconstruction, incident-drill, compatibility, persistence, and
  content-free-contract tests passed; and
- the complete source suite passed 531 tests in `140.356` seconds with the same
  three documented opt-in installed-client/profile skips.

This proves the first usable governed rollout path on the PostgreSQL backend.
Version 0.2 deliberately uses one `policy_admin` capability for staging,
activation, and rollback; two-person change approval and administrator
notifications remain later hardening. Production deployment, independent
security review, connection pooling, KMS custody/rotation, backup/PITR and
restore, HA/failover, and a tested multi-process rollout under real load remain
open and must not be inferred from this bounded checkpoint.

### Scoped usage-report RBAC slice

The issue #6 usage-visibility slice was exercised locally on August 20, 2026:

- configuration accepts exactly one scoped report capability per identity:
  self, current-team aggregate, finance aggregate, or organization
  administrator. The existing `usage_viewer` remains a compatibility alias for
  the organization-administrator scope; unrelated policy, identity, DLP, and
  session capabilities do not grant usage visibility;
- the authenticated HTTP route derives effective filters server-side. A member
  sees only their own usage, a manager cannot request person rows or actor
  filters and receives only current-team aggregates, and finance cannot request
  person/team rows or actor/team filters. An organization administrator retains
  person-level drill-down;
- constrained responses carry an explicit `access` object in schema version 4
  (or 5 with latency), while organization-administrator responses retain their
  exact version-2/3 shapes. The bundled CLI validates both response contracts
  rather than treating an enforced filter as an unfiltered administrator report;
- SQLite and PostgreSQL usage-read audit methods independently re-authorize the
  effective filters and reject a supplied scope that does not match the mapped
  identity. The PostgreSQL negative contract executes before database I/O; and
- 35 resolver, configuration, SQLite-audit, PostgreSQL-pre-I/O, and CLI-client
  tests passed in `0.072s`. The real loopback session/gateway suite then passed
  all 15 tests in `15.816s`, including member, manager, finance, policy-admin,
  pagination, audit, legacy administrator behavior, and the scoped CLI path.

This establishes a bounded usage-report authorization boundary, not an HR
directory, group synchronization system, production role-management UI,
SCIM deprovisioning workflow, workforce-performance system, immutable audit
service, or complete provider-account telemetry. Those remain separate issue
#6/#7 and operational gates.

## Automated publication gate

Ordinary GitHub CI runs seven independent gate families without provider credentials:

- the complete unit, compatibility, and loopback gateway suite on Python 3.11, 3.12, 3.13, and 3.14;
- two independent exact-commit source-distribution and wheel builds under pinned packaging inputs, raw-byte equality with a content-free digest manifest, and installation of the verified wheel in a clean virtual environment;
- installed-client routing through local fake providers using pinned official Codex and Claude Code package versions;
- pinned-base/hash-locked container build, restricted-runtime smoke, CycloneDX SBOM, and a fail-on-any-high-or-critical vulnerability gate;
- strict versioned threat-model validation covering all STRIDE categories and the issue #9 incident scenarios, with content-free evidence retained for audit;
- strict versioned compatibility validation binding exact client, Python, provider-protocol, identity, persistence, package, and OCI claims to repository evidence while preserving unsupported and owner-pending boundaries; and
- exact repository-local execution of all seven incident scenarios through a strict versioned drill catalog, with private content-free aggregate evidence and false production-readiness flags retained in ordinary and tag verification.

Ordinary CI grants only read access to repository contents, disables persisted checkout credentials, pins every GitHub Action to a reviewed commit SHA, and retains build artifacts for seven days. The separate release workflow re-runs the applicable gates on an annotated version tag, splits source, package/signing, and GitHub-release permissions, and retains its verification and signed-release evidence longer. It remains blocked before publication until the owner-approved Sigstore transparency repository variable is present. Dependabot is configured to propose updates to action and Python build dependencies; a client-version bump remains an intentional compatibility change because it can alter the provider protocol.

A separate weekly canary installs the latest published Codex and Claude Code packages in an ephemeral runner and exercises only the two fake-provider compatibility tests. It has no provider credentials, does not block ordinary pull requests, and is intended to surface upstream protocol drift before an employee upgrade does.

The publication candidate was also checked locally on August 15, 2026 with Codex `0.147.0` and Claude Code `2.1.233`, the then-current npm releases. Both routed successfully through Hormuz, and the complete 29-test suite passed with those executables selected first on `PATH`.
