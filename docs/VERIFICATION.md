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
- invalid capability, target shape, cursor, limit, reason, and organization-target combinations returned stable `400` or `403` JSON errors. A valid actor revocation invalidated both that employee's active sessions immediately;
- the real `hormuz sessions list` and `hormuz sessions revoke` commands exercised the authenticated loopback contract. Newly issued IDs use a `ses_` prefix so values cannot be parsed as CLI options; pre-v2 IDs beginning with `-` remain supported through `--target=<id>`;
- all 182 source tests passed with the installed Codex and official Claude Code compatibility paths enabled;
- the frozen 60-task context release profile remained green with precision `1.00`, recall `1.00`, useful-pack rate `1.00`, mean compression ratio `0.840593`, zero safety failures, and p95 in-process selection latency `0.196083 ms`; corpus SHA-256 remained `9822d592868202c7c7539bcdac7d4a5894c01f9e6dba7a434846516b67b32c17`;
- an isolated source distribution and wheel included the new API document, client, implementation, and tests. A clean Python 3.14 environment installed the exact wheel from outside the checkout, loaded Hormuz from `site-packages`, created session schema version 2, displayed both administrator CLI commands, and passed bytecode compilation;
- `git diff --check`, source bytecode compilation, frozen-corpus regeneration, and a source/wheel runtime scan for private-key, OpenAI, Anthropic, and GitHub credential values passed.

This closes the single-node administrator-revocation slice of issue #13, not the identity or enterprise-tenancy milestone. Real owner-selected IdP validation, live configuration reload, SCIM/event-driven deprovisioning, shared multi-node revocation, PostgreSQL tenant isolation, KMS custody, immutable session-event export, HA, backup/restore, and independent review remain open. Those shared persistence decisions still depend on proposed ADR 0002.

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

GitHub Actions runs four independent gates without provider credentials:

- the complete unit, context-governance, and loopback gateway suite on Python 3.11, 3.12, 3.13, and 3.14;
- deterministic corpus regeneration plus the 12-task governed-context regression profile, with machine-readable evidence retained as an artifact;
- source-distribution and wheel builds followed by installation of the wheel in a clean virtual environment;
- installed-client routing through local fake providers using pinned official Codex and Claude Code package versions.

The workflow grants only read access to repository contents, disables persisted checkout credentials, pins every GitHub Action to a reviewed commit SHA, and retains build artifacts for seven days. Dependabot is configured to propose updates to action and Python build dependencies; a client-version bump remains an intentional compatibility change because it can alter the provider protocol.

A separate weekly canary installs the latest published Codex and Claude Code packages in an ephemeral runner and exercises only the two fake-provider compatibility tests. It has no provider credentials, does not block ordinary pull requests, and is intended to surface upstream protocol drift before an employee upgrade does.

The publication candidate was also checked locally on August 15, 2026 with Codex `0.147.0` and Claude Code `2.1.233`, the then-current npm releases. Both routed successfully through Hormuz, and the complete 29-test suite passed with those executables selected first on `PATH`.
