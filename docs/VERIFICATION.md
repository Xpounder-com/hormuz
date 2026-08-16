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

This is evidence for a single-node local repository contract. It is not evidence of encryption at rest, hosted multi-tenant isolation, KMS/BYOK, backup/restore, retention/legal hold, writer RBAC, automatic invalidation, context injection, or caching. Those gates remain open, and proposed ADRs 0002 and 0003 remain unaccepted.

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

### Governed context benchmark path

The bundled version-1 synthetic corpus and separated reference outcomes were generated from the checked-in deterministic generator and then verified byte-for-byte with its `--check` mode.

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
