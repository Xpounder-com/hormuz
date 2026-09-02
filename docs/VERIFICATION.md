# Verification record

This file records executable evidence for client/provider compatibility. It intentionally contains no provider credentials, prompts, responses beyond fixed test markers, or employee secrets.

## v1.1 portfolio-intelligence contract gate

The accepted v1.1.0 design, exact v1.0.0 baseline manifest, documentation set,
and additive-compatibility guard are verified with:

```bash
python3 tools/verify_portfolio_intelligence_contract.py
python3 -m unittest -v tests.test_portfolio_intelligence_contract tests.test_portfolio_wire_contract
```

The verifier rejects duplicate or unknown contract fields, baseline-fixture
drift, removed or changed v1 schemas and errors, changed v1 authentication or
request, ordering, pagination, idempotency, or retry requirements; boolean
schema versions; drifted identity, lifecycle, time, evidence-threshold, or
self-scope rules; multiple active primary use cases per attempt; missing content
exclusions; automatic policy application; and drift in the accepted decision
record. The source-distribution gate requires the contract, verifier, fixture,
and documentation to ship together.

Review regression checks additionally freeze the complete manifest
compatibility block, all four role-specific scopes, every KPI driver/guardrail/
dimension list, per-entity mutability/content mapping, the content allowlist
(including disjointness from exclusions), and recommendation types. Newly
added current-manifest entries must satisfy the current executable manifest
validator before additive comparison. The immutable historical fixture remains
digest-bound independently of later validator versions.

Contract-control schema versions, attribution counts, and page sizes must be
actual integers, not boolean or floating-point aliases. Boolean acceptance,
causality, provider-authority, and automatic-application flags reject numeric
aliases. These checks apply to the planning contract; they do not change the
digest-frozen wire bundle's JSON Schema numeric semantics.

The [wire bundle](portfolio-intelligence-wire-v1.json) defines and digest-binds
all 26 planned payloads. Tests exercise 52 synthetic minimal/populated examples,
every required top-level field, unknown and nested content fields, scalar and
collection bounds, invalid dates, missing references, and unsupported schema
keywords. These are structural examples, not live domain or authorization
proof. The standalone command bootstraps its adjacent source root; it needs
the declared runtime dependencies but not an installed/editable Hormuz package
or caller-provided `PYTHONPATH`. Source-archive verification must run it from
an unpacked archive in that configuration.

Passing this gate proves only an internally consistent plan and an additive
v1.0.0 compatibility boundary. It does not prove a portfolio repository,
migration, connector, budget, outcome association, scorecard, authorization
view, recommendation, human pilot, causal effect, HA/SLA, or certification.
Those remain ordered issues in
[epic #226](https://github.com/Xpounder-com/hormuz/issues/226).

## V1 usage-repository composition gate

Issue #213 is a feature-free interface and factory boundary. Its checks freeze
the exact 21 public operation names, parameters, defaults, and annotations of
both usage adapters; retain the public protocol alias; and require concrete
SQLite/PostgreSQL adapters to satisfy the repaired protocol structurally.
The three audit-chain operations formerly nested under `monthly_usage_bounds`
are protocol members. Unknown keywords and invalid argument types must fail
the static negative fixtures.

```bash
python3 -m pip install --require-hashes -r requirements/persistence-typecheck.lock
python3 -m mypy --strict --follow-imports=silent --disallow-any-unimported \
  --no-incremental --python-version 3.11 \
  hormuz/store_router.py tests/typing_fixtures/usage_repository.py
python3 -m unittest -v tests.test_usage_repository_boundary \
  tests.test_repository_composition tests.test_usage_repository_characterization
```

The hash-pinned type checker runs in the existing required package job and is
not a runtime dependency. It checks the factory and structural conformance of
the imported real adapter interfaces; it does not claim the entire legacy
codebase is strict-typed. The source archive includes the lock and fixtures.

The shared `_usage_repository_contract.py` scenario exercises all 21 operations
before and after refactoring: both tenants, six reporting dimensions,
actor/team/tenant-filtered reads, explicit monthly bounds, reservation refresh/release/denial,
atomic attempt finalization and retry rejection, unknown/stale holds, audit
export, checkpoint replay/conflict, anchor aging, and recovery epochs. Parity
normalizes generated event IDs, event/commit clocks, and exact integral
PostgreSQL `numeric` aggregates; it never rounds amounts or removes policy,
tenant, token, cost-basis, coverage, state, or schema fields.

With `HORMUZ_TEST_POSTGRES_DSN` pointed at a disposable PostgreSQL database,
run `tests.test_postgres_usage`, `tests.test_postgres_request_attempts`,
`tests.test_postgres_audit_chain`, `tests.test_postgres_migration_rls`, and
`tests.test_postgres_pooling`. CI runs these against an isolated installed wheel.
The full suite retains gateway, policy, exact-client, schema/migration,
concurrency, audit, and recovery regressions. A skipped PostgreSQL suite is
not backend parity evidence.

Read checks cover empty and populated stores. SQLite uses a real read-only
connection and compares complete dumps before/after. PostgreSQL compares all
ten v1 ledger/migration tables before/after and runs non-locking reads in
`READ ONLY` transactions. Chain verification keeps its existing `FOR SHARE`
snapshot lock and needs a normal PostgreSQL transaction; SQL `READ ONLY`
continues to reject it. `audit_chain_head` still initializes an empty epoch,
so it is intentionally not classified as read-only. These differences are
characterized, not changed.

Factory tests cover construction ordering, exact forwarding of configuration,
environment, read-only intent and a borrowed pool, failure propagation, and
unchanged legacy factory behavior. No portfolio implementation, table,
migration, wire field, command, version bump, or release is provided by this
gate. Close #213 only after review, package/client checks, and exact-main CI;
#214 and the downstream portfolio issues remain separate gates.

## v1.1.0 registry implementation and transition

The [#214/#215 transition guide](REGISTRY_TRANSITION.md) records the exact
released-v1 source baseline, compatibility inventory, stop/migrate/restart
boundary, and safe rollback decision. The historical version-1 preflight
remains frozen; version 2 describes actual registry migration verification,
never final-candidate acceptance.

`tests.test_sqlite_registry_transition` and
`tests.test_postgres_registry_transition` test actual SQLite 5 / PostgreSQL 9
migrations (then cumulative current migrations), failure injection after real DDL, rollback/retry, unchanged
v1 rows/unknown holds, fresh released-v1 rejection of partial/newer state,
and isolated old-pair backup/restore. Populated current-candidate restores on
both backends preserve registry history, idempotent results, continuation
cursors, and pre/post-upgrade unknown holds without replacing the original.
PostgreSQL tests also characterize that
already-running readiness checks do not continuously revalidate the schema.
All migration/writer processes must be quiesced; rolling upgrades are not
proven. The existing required PostgreSQL job runs these tests from an
isolated current wheel against a separately installed digest-pinned v1 source
archive. Release-dependent local skips are not transition evidence.

The source archive must contain both plans, guides, verifier, baseline manifest,
and backend fixtures. `test_sqlite_portfolio_registry`,
`test_postgres_portfolio_registry`, and `test_portfolio_api_cli` cover actual
create/version/archive/binding, strict parsing, role checks before access,
forced RLS, append-only state, concurrent idempotency/CAS, frozen cursors and
safe audit. The required PostgreSQL job executes both registry adapters from
the isolated installed wheel. The registry-era suite owns 80 live PostgreSQL
cases; attribution adds six transition, ten adapter and seven native gateway
cases, for 103 total owned cases.
Populated policy/custody recovery and exact final signed OCI/Compose transitions
remain #214 final-candidate gates. Do not close #214 on registry-only evidence.

## v1.1.0 attribution implementation and transition

The [#214/#216 transition](ATTRIBUTION_TRANSITION.md) implements the accepted
optional admission contract and SQLite 5-to-6 / PostgreSQL 9-to-10 transitions.
The frozen version-1 plan remains historical; version 2 and the strict verifier
pin the real registry predecessor while preserving the released-v1/portfolio
contracts and keeping final-candidate acceptance false.

Six transition tests per backend exercise actual DDL rollback/retry, all
populated registry tables, unchanged v1 rows and unknown holds, partial/newer
state refusal by actual registry and released-v1 binaries, isolated old-pair
restore and current-pair forward recovery with all attribution tables populated.
Set `HORMUZ_TEST_REGISTRY_PYTHON` and `HORMUZ_TEST_V1_PYTHON` to the independently
installed digest-pinned predecessors, plus disposable `HORMUZ_TEST_POSTGRES_DSN`
and matched `HORMUZ_TEST_PG_CONTAINER`. CI supplies all four; local skips are
not evidence. The unpacked source kit must contain both plans and every helper.
Empty policy/custody tables do not prove populated-domain recovery.

The required job also runs both attribution adapters, pure admission/schema
tests, CLI/API tests and OpenAI/Anthropic JSON/streaming/compact no-egress proofs
from the isolated installed wheel. Feature acceptance requires reviewed PR
evidence and exact merged-main CI; #214 final-candidate and #225 external
validation remain open. No unit, migration or synthetic provider test is an
independent customer session.

## Live pinned-client release gate

The manual `Live BYO-provider client conformance` workflow and
`tools/verify_live_client_conformance.py` are the repeatable real-provider
gate. They require exact Codex `0.147.0` and Claude Code `2.1.233` executables,
dedicated operator-attested provider credentials, explicit OpenAI and
Anthropic model IDs, and an acknowledgement that the run incurs provider
cost. Provider credentials are scoped to the gateway process and removed from
both client environments.

The tool observes only allowlisted post-policy metadata immediately before
egress, then validates strict v2 usage/security events after each real
streaming response. Its schema-v1 artifact records client hashes, tenant
identity, model routing, policy/cap/redaction outcomes, token counts,
configured-rate-card cost, and booleans for provider-request-ID presence and
pre-egress checks. Prompts, responses, request IDs, keys, identity tokens, and
debug logs are prohibited. The runner binds evidence to an exact clean Git
`HEAD` and refuses to overwrite an existing artifact. See
[LIVE_CLIENT_CONFORMANCE.md](LIVE_CLIENT_CONFORMANCE.md) for the command,
workflow secret boundary, strict evidence contract, unsupported features, and
nonclaims.

A one-provider run is explicitly partial. The completed
[#115](https://github.com/Xpounder-com/hormuz/issues/115) gate is bound to
[workflow run 32884601758](https://github.com/Xpounder-com/hormuz/actions/runs/32884601758):
one `scope: complete` artifact containing both real providers on exact public
`main` revision `49e04d2cc9a4bbc00362414145ebbe5fc15e7c35`. Provider-free CI
cannot substitute for that evidence. The result does not establish
provider-invoice reconciliation, every client feature, traffic bypassing
Hormuz, or enterprise production readiness.

## Signed Mac controlled-pilot gate

The separate `hormuz.macos-pilot-qualification` v1 contract composes the exact
notarized archive with its distribution proof and Apple summary, clean
Gatekeeper installation on Apple Silicon and Intel Macs without developer
tools, signed Keychain/session update and rollback behavior, the pinned Codex
and Claude Code `401` semantics, and a real hosted `external_pilot` gateway.
That gateway evidence requires HTTPS, Okta, server-only provider credentials,
PostgreSQL durability and tenant RLS, durable sessions, early streaming,
upstream closure and zero replay on cancellation, header/first-byte/total
latency samples, a one-hop policy-bounded same-protocol failover, worker and
pool-wait monitoring, recovery, and a support path. Independent security and
accessibility reviews are separate required records.

Run the content-free synthetic contract check with:

```bash
python tools/verify_macos_pilot_evidence.py \
  tests/fixtures/macos_pilot/complete-synthetic-v1.json \
  --archive tests/fixtures/macos_pilot/Hormuz-0.1.0-notarized.zip \
  --distribution-proof tests/fixtures/macos_pilot/distribution-proof-v2.json \
  --notarization-summary tests/fixtures/macos_pilot/notarization-v1.json \
  --previous-archive tests/fixtures/macos_pilot/Hormuz-0.0.9-notarized.zip \
  --previous-distribution-proof tests/fixtures/macos_pilot/previous-distribution-proof-v2.json \
  --previous-notarization-summary tests/fixtures/macos_pilot/previous-notarization-v1.json \
  --allow-synthetic-fixture
```

The fixture always returns `ready_for_controlled_external_pilot: false`. A real
pass must run on macOS with authenticated GitHub CLI access, independently
recheck both archives with codesign, stapler, and Gatekeeper, require Apple team
`R267LZMUTY`, authenticate the recorded default-branch workflow runs, download
their final unexpired Actions artifacts, and byte-bind each supplied archive,
proof, and notarization summary to the corresponding artifact members. The
two distribution workflows must complete by the aggregate snapshot. The
clean-machine, lifecycle, and official-client records must exact-match a
candidate-bound artifact from the authenticated macOS operations workflow.
That artifact also binds the exact gateway source commit and deployment run;
the deployment must finish before Mac operations start, and clean-machine
starts must fall within the operations run and precede its artifact creation.
Incomplete operations gates may retain
`macos_operational_evidence_url: none` rather than inventing a run URL;
complete gates require the authenticated URL and artifact. Hosted gateway
records must exact-match authenticated deployment and qualification artifacts.
Their URLs may remain `none` while the gateway gate is incomplete, but both
distinct authenticated URLs are required once its controls otherwise qualify.
Every download is streamed under its authenticated
size and an absolute cap. A passing result still supports only a controlled
single-region pilot. It does not prove external-human usability,
multi-region availability, zero downtime, an availability or latency SLA, or
customer production readiness. See
[MACOS_PILOT_QUALIFICATION.md](MACOS_PILOT_QUALIFICATION.md) for the sequence,
evidence boundary, and remaining operational gates.

## Active post-publication onboarding-validation milestone

The v1.0.0 invited-reviewer procedure and strict content-free aggregate are
defined in [EXTERNAL_ONBOARDING.md](EXTERNAL_ONBOARDING.md). Every counted
session binds to the immutable promoted source archive at digest
`sha256:2c3b16c1742ee76032a33f3714492a8d8515c5291d4d57520441882cd8bc5b5a`.
The evidence contract allows only opaque participant/session IDs, coarse
environment enums, completion states, elapsed seconds, fixed demo observations,
failure codes, bounded guidance/friction metadata, issue/advisory references,
and content-free attestations. It has no fields for identities, feedback text,
prompts, responses, credentials, customer data, local paths, logs, or
screenshots, and Hormuz contains no hidden collection path.

Run the contract tests and synthetic-fixture check with:

```bash
python -m unittest -v tests.test_external_onboarding_evidence
python tools/verify_external_onboarding_evidence.py \
  tests/fixtures/external_onboarding/complete-synthetic-v1.json \
  --allow-synthetic-fixture
```

Synthetic evidence always reports `validated_human_onboarding: false`. Actual
evidence requires an off-repository distinct-human attestation, five to ten
preregistered participants, at least five independent installation/demo
completions across all four reviewer personas, a later returning-user session,
and correction plus automated regression and independent retest of every
blocker. Recruitment is active; the honest count remains 0/5 initial
completions and zero returning users. Internal and synthetic runs do not count.
Closing #110 remains mandatory before any validated-human-onboarding claim, but
it is not a v1.0.0 release dependency. The validator does not prove the
off-repository identity attestation or replace #115's separately completed
live-provider evidence.

The older [QUIET_ALPHA.md](QUIET_ALPHA.md) protocol and
`hormuz.quiet-alpha-evidence` v1 validator remain archived v0.1.3 compatibility
material. They cannot satisfy the active v1.0.0 milestone.

## Durable-data inventory boundary

The self-hosted v1 data boundary is documented in
[DURABLE_DATA.md](DURABLE_DATA.md) and its strict versioned inventory. Verify
that every SQLite and PostgreSQL table is registered, operator-created
artifacts are named, prompt and response bodies remain outside the claimed
durable stores, and external-system deletion is not overclaimed with:

```bash
python tools/verify_durable_data_inventory.py
python -m unittest -v tests.test_durable_data_inventory
```

The inventory is a product-boundary assertion, not proof of universal erasure.
Customer database and backup operators own export, retention, backup, restore,
and deletion in their controlled infrastructure. Provider, IdP, KMS, Object
Lock, backup, client-local, and observability lifecycles remain separate
systems. Operator-sealed custody envelopes are opaque encrypted artifacts;
Hormuz does not inspect or promise exclusion of request content from their
operator-supplied plaintext.

## Provider-free five-minute path

The public checkout exposes one configuration-free product tour:

```bash
hormuz demo
```

It starts a disposable loopback provider simulator and the real Hormuz
gateway, then proves an allowed request, model fallback plus output cap,
pre-egress secret redaction, pre-egress policy denial, and strict metadata-only
usage/security evidence. The regression test intercepts every outbound socket
connection and rejects any destination other than `127.0.0.1`. The command
also verifies that its synthetic request content, response content, identity
credentials, provider credential, original secret, and redaction replacement
are absent from the durable audit events before deleting the temporary SQLite
ledger.

The blocking `Python 3.11` through `Python 3.14` Linux CI matrix runs this
documented command immediately after installation. Each job records the
installation-step wall time in GitHub Actions and the command prints its own
measured demonstration time. The isolated wheel and `linux/amd64` OCI gates
also run the command, so a source-only import path cannot satisfy the
quickstart release gate. These are provider-free product-path proofs, not live
OpenAI/Anthropic compatibility or a production deployment claim.

## Zero-network policy-administrator path

The separate administrator demonstration exercises local policy authoring and
read-only analysis without PostgreSQL, a policy-administrator credential, a
provider credential, or any network call:

```bash
hormuz policy demo
```

It creates and validates a local baseline and candidate, compares their
normalized policy semantics, creates two explicit request scenarios, and
evaluates those scenarios against a disposable SQLite ledger whose current
usage is exactly zero. The observed behavior changes are a model denial and an
output-token cap; no synthetic budget consumption is inserted. The command
does not stage, activate, or roll back a policy.

Temporary artifacts are deleted by default. To retain a newly created
owner-only directory for inspection, run:

```bash
hormuz policy demo --output ./policy-demo
```

The directory is mode `0700`, every artifact is mode `0600`, and an existing
path is never overwritten. The managed `policy apply`, `policy history`, and
`policy rollback` commands printed at the end are guidance only and are not
executed. This is evidence for the policy-administration UX milestone, not for
completion of the enterprise v1 release gate.

## v1 internal-repeatability gate

The exact-digest protocol is defined in
[POLICY_ADMIN_USABILITY.md](POLICY_ADMIN_USABILITY.md). Verify its versioned
contract and deliberately non-promotable fixture with:

```bash
python -m unittest -v tests.test_v1_internal_repeatability_evidence
python tools/verify_v1_internal_repeatability_evidence.py \
  tests/fixtures/v1_internal_repeatability/complete-synthetic-v1.json \
  --allow-synthetic-fixture
```

After candidate freeze, `tools/run_v1_internal_repeatability.py` validates the
manifest/archive binding and runs the fixed six-stage workflow five times. Each
run gets a fresh child virtual environment, workspace, and zero-usage SQLite
database; the candidate process receives no provider or policy-admin
credential and runs with Python socket creation denied. The aggregate retains
only candidate/task digests, timestamps, stage and exit status, isolation
attestations, and the bounded expected comparison/evaluation facts.

All five exact-candidate runs must pass. Promotion rejects a different digest,
the deferred human-evidence schema, synthetic evidence, incomplete runs, and
any widened claim boundary. Passing proves deterministic internal offline
workflow repeatability only.

## Separate deferred independent administrator study

The external protocol and strict content-free aggregate are defined in
[POLICY_ADMIN_USABILITY.md](POLICY_ADMIN_USABILITY.md). Verify the contract and
its deliberately non-counting synthetic fixture with:

```bash
python -m unittest -v tests.test_policy_admin_usability_evidence
python tools/verify_policy_admin_usability_evidence.py \
  tests/fixtures/policy_admin_usability/complete-synthetic-v2.json \
  --allow-synthetic-fixture
```

Contract v2 accepts only a frozen `v1.0.0` source-archive candidate because it
ships the complete protocol, configuration, examples, command help, and
validator kit; the package gate inspects those members in the built archive.
The validator binds the candidate's source commit, freeze time, and SHA-256
digest, and requires exactly five current-candidate offline participants before
three current-candidate PostgreSQL participants. It computes unaided completion, the
15- and 25-minute thresholds, allowed published-guidance use, shipped offline
asset and expected compare/evaluate bindings, and exact apply/history/rollback
version, digest, and generation relationships. It also requires
preregistration, complete started-session inclusion, no-replacement
attestations, non-overlapping intervals for the same participant, all five
offline sessions to qualify before the first PostgreSQL session starts, and
aggregate generation no earlier than any computed session end or more than
five minutes ahead of the validator clock. Sessions retain a bounded collection of every
linked finding, with managed-state blockers restricted to PostgreSQL runs.
PostgreSQL runs also require unique opaque run scopes, tenant-isolation
attestations, and matching guarded apply and rollback values. Open blockers
prevent readiness. Corrections require an
automated regression whose Actions source commit and canonical CI workflow are
attested and bound to the exact corrected candidate, an exact
digest/source/freeze-time match to the candidate being gated, attested
correction-commit ancestry, and a later independent retest. A broad workflow
change requires every participant in the affected track to rerun against the
new digest.

Synthetic and internal results never satisfy this deferred human study. The
current count remains 0/5 offline and 0/3 PostgreSQL. This is separate from the
installation/demo evidence tracked by issue #110: the same people may
participate in both, but neither result may be relabeled as the other. The
legacy human-evidence schema is not accepted by the v1 candidate manifest or
promotion command and must not be reported as completed from the internal
sandbox result.

## Launch-claim boundary

The tester-recruitment drafts and their schema-v2 claim ledger live under
[`docs/launch`](launch/README.md). Validate them with:

```bash
python tools/verify_launch_assets.py
python -m unittest -v tests.test_launch_assets
```

The draft verifier returns `"publishable": false` by design. It requires every
asset to carry the do-not-publish marker, binds implemented and verified-alpha
claims to closed issues plus repository evidence, labels roadmap and nonclaim
statements separately, rejects unapproved links or template tokens, and freezes
the privacy-bounded launch measures. It does not query GitHub or prove that an
issue remains closed; final publication requires a fresh remote review and
recorded owner approval. Issue #110 remains an explicitly pending
post-publication validation milestone at 0/5, not a prerequisite for the
bounded tester-recruitment announcement.

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

This dated record proves official Claude Code client/protocol compatibility
without spending against or exposing a real Anthropic account. At the time of
this 2026-08-15 run, live Anthropic evidence was still pending; the later
same-revision OpenAI and Anthropic result is recorded in
[#115](https://github.com/Xpounder-com/hormuz/issues/115) and
[workflow run 32884601758](https://github.com/Xpounder-com/hormuz/actions/runs/32884601758).

### Generic OIDC JWT path

A local standards-shaped issuer served an OIDC discovery document and rotating RSA JWKS to the actual Hormuz authentication and HTTP server path. No external identity provider, employee token, or provider credential was used.

Observed result:

- a valid RS256 JWT access token with the configured issuer, audience, expiry, key ID, and subject reached `/v1/gateway/whoami` and resolved to the explicit actor, team, organization, clearance, and client policy identity;
- wrong-audience, expired, unmapped-subject, symmetric-algorithm, non-TLS remote-issuer, and inconsistent duplicate-actor configurations failed closed;
- a new signing key was accepted after one JWKS refresh, while repeated attacker-controlled unknown key IDs were rate-limited from causing repeated metadata fetches;
- the identity endpoint returned no JWT or OIDC subject;
- generated OIDC configurations use Codex command-backed bearer authentication and Claude Code `apiKeyHelper`, while invalid configuration-injection URLs are rejected;
- the complete local source suite passed 48 tests, with only the opt-in official Claude Code executable test skipped in that run.

This verifies Hormuz as a JWT resource server against a controlled issuer. It is not evidence of browser login, refresh-token custody, opaque-token introspection, SCIM, or a live third-party IdP; those remain explicitly outside this milestone.

## Reproduce locally

The default suite uses only loopback fake providers:

```bash
python3 -m unittest -v
```

The historical live OpenAI record above predates the repeatable two-provider
release harness. New evidence should use
[the live client conformance command](LIVE_CLIENT_CONFORMANCE.md), which emits
a strict content-free artifact. For ordinary operator inspection, Hormuz's
versioned usage summary remains available through:

```bash
hormuz --config hormuz.json status --json
```

Never add real provider or employee credentials to this record.

## AWS KMS and S3 Object Lock conformance

The generic custody implementation has local adapter, gateway, package, and
contract evidence. It is not yet recorded as live AWS-account certification.
The opt-in `tests/test_aws_custody_live.py` gate requires a customer-controlled
AWS workload identity, distinct customer-managed KMS keys, and a dedicated
Object-Lock-enabled test bucket. It verifies real KMS data-key and re-encryption
operations, then retains one metadata-only `COMPLIANCE` object and checks its
SSE-KMS and retention metadata. The test is skipped by ordinary CI and requires
an explicit acknowledgement because the object cannot be cleaned up before its
retention date. See [CUSTODY.md](CUSTODY.md) for the exact command and safety
boundary.

## Ceph RGW self-hosted Object Lock conformance

OpenBao Transit plus Ceph RGW Tentacle 20.2.3 is the first **verified
self-hosted reference** for the exact tested custody/retention and
rotation/recovery behaviors, not unrestricted production certification. The
exact-pair live evidence is published in
[#95](https://github.com/Xpounder-com/hormuz/issues/95). The opt-in
`tools/verify_ceph_rgw_custody_conformance.py` harness requires a local,
operator-provisioned Linux Cephadm lab, loopback RGW/OpenBao endpoints, a
dedicated Object-Lock-enabled bucket, and explicitly attested running RGW and
OpenBao containers. It refuses a container whose required release, repository
digest, or platform differs from the target documented in
[CEPH_RGW_CONFORMANCE.md](CEPH_RGW_CONFORMANCE.md).

After first checking OpenBao data-key operations and Object Lock configuration,
the harness first proves the credential can delete an unprotected control
version and extend retention. It then writes two encrypted metadata-only audit
artifacts, reads one back and verifies the Hormuz audit chain, asserts
`COMPLIANCE` retention, attempts (and requires denial of) retention reduction
and version deletion, and checks a separate object has a legal hold. This
distinguishes RGW enforcement from a merely underprivileged test credential. It
writes a strict content-free evidence record only if every assertion passes.
The published #60 record covers the earlier pinned Ceph-only target; #95
publishes the current paired content-free evidence. Normal CI additionally
tests the harness's validation and evidence shape against fakes. A single-host
pass does not protect against a host-root administrator deleting disks or
volumes, and does not establish HA, recovery, or production immutability.

## Automated publication gate

GitHub Actions runs independent gates without provider credentials:

- the complete core unit and loopback gateway suite on Python 3.11, 3.12, 3.13, and 3.14;
- the provider-free documented quickstart on every release-gated Python version, the isolated wheel, and the candidate `linux/amd64` image;
- source-distribution and wheel builds followed by a clean-wheel inspection and isolated gateway-start boundary test;
- PostgreSQL migration and repository compatibility against the pinned service image, including tenant custody authority and one/two-person approval behavior;
- a disposable `pg_dump` custom-format / `pg_restore` recovery drill against digest-pinned source, recovery, and quarantine PostgreSQL containers;
- a disposable physical PostgreSQL WAL/PITR drill that requires a named recovery target, proves target exclusion, and fails closed without required WAL;
- non-root OCI reference-runtime smoke testing with externally mounted inputs;
- CycloneDX SBOM generation and a fix-aware OCI vulnerability gate for the exact local candidate image;
- installed-client routing through local fake providers using pinned official Codex and Claude Code package versions.

The workflow grants only read access to repository contents, disables persisted checkout credentials, pins every GitHub Action to a reviewed commit SHA and the scanner image to an immutable digest, and retains build artifacts for seven days. Dependabot is configured to propose updates to action and Python build dependencies; a client-version bump remains an intentional compatibility change because it can alter the provider protocol.

Repository-side enforcement is a separate checked contract. Run
`python tools/verify_repository_governance.py` and follow
[`REPOSITORY_GOVERNANCE.md`](REPOSITORY_GOVERNANCE.md) to validate the pinned
Action owners, public-fork safety boundary, required check identities, protected
`main`, immutable version tags, and the explicit pre-public/public phases.

A separate weekly canary installs the latest published Codex and Claude Code packages in an ephemeral runner and exercises only the two fake-provider compatibility tests. It has no provider credentials, does not block ordinary pull requests, and is intended to surface upstream protocol drift before an employee upgrade does.

The package job also builds the core and separated experiment with Apache 2.0
metadata and license files, installs every core extra into a fresh environment,
and fails closed on an unexpected distribution, license identity, or missing
license artifact. It validates the versioned content-free disclosure report and
its visibility/verdict lifecycle. The checked-in `public_transition_verified`
verdict records the completed transition; a pre-transition
`ready_for_public_transition` verdict would record authorization only. See
[PUBLIC_DISCLOSURE.md](PUBLIC_DISCLOSURE.md) for the audited surfaces,
limitations, and transition-verification boundary.

The live BYO-provider workflow is manual-only, uses the protected
`live-provider-conformance` environment, grants `contents: read`, serializes
runs to prevent overlapping spend, and scopes provider secrets only to the
conformance step. It is not a scheduled canary and never runs for a pull
request.

The publication candidate was also checked locally on August 15, 2026 with Codex `0.147.0` and Claude Code `2.1.233`, the then-current npm releases. Both routed successfully through Hormuz, and the complete 29-test suite passed with those executables selected first on `PATH`.

## PostgreSQL custody-control authority

The `PostgreSQL compatibility` job runs
`tests/test_postgres_custody_control.py` against the digest-pinned PostgreSQL
service image. The tests prove one-time tenant bootstrap, stable OIDC
issuer/subject authority without inference entitlement, forced tenant RLS,
separate runtime/policy/custody roles, content-free initial-enrollment handles,
one approval for routine operations, two distinct active administrators for
destructive operations, expiry, replay denial, last-administrator protection,
append-only approvals/events, and transaction rollback when evidence validation
fails.

This is a control-plane persistence and authorization proof. It does not
execute KMS operations, expose or store plaintext, change customer IAM, provide
the all-administrator-loss break-glass mechanism, prove a production database,
or establish end-to-end custody readiness. See
[CUSTODY_CONTROL.md](CUSTODY_CONTROL.md) for the exact boundary.

## OCI reference runtime

The executable reference image gate is:

```bash
./tools/verify_oci_reference.sh
```

It builds the source candidate, verifies the fixed numeric non-root identity
and liveness health check, proves a configuration is not embedded, starts the
gateway under a read-only root filesystem with configuration and SQLite data
mounted from outside the image, validates the versioned liveness/readiness
contracts, and requires a clean SIGTERM exit. It uses fixed placeholders and
does not call a model provider. Its narrow deployment boundary and remaining
nonclaims are in [OCI.md](OCI.md).

## V1 deployment-contract validation

The accepted profile, ownership, state-inventory, and recovery-objective
contract is checked with:

```bash
python tools/verify_deployment_contract.py
```

The verifier rejects unknown or duplicate JSON fields, changed platform or
authentication support, a broadened Compose claim, a missing durable/cache
state class, changed child-gate ownership, RPO above 300 seconds, internal RTO
above 3,600 seconds, suppression of complete end-to-end recovery time, or any
attempt to label the reference objectives a customer SLA. Package CI retains
only a content-free summary containing counts, thresholds, and Boolean scope
labels. It does not claim that issues #103 through #107 have already executed
their operational proofs.

## Single-VM Compose pilot reference

The `Single-VM Compose pilot reference` job runs
`tools/verify_compose_profile.sh` on a clean GitHub-hosted Ubuntu AMD64 VM. It
pulls the exact published Hormuz and PostgreSQL digests, starts one non-root
read-only gateway and one private persistent database, and uses only an
internal fake provider. It proves authenticated loopback ingress, fallback,
output capping, pre-egress redaction, a configuration-enforced deny, durable
metadata-only PostgreSQL evidence, gateway restart, configuration replacement
and rollback, logical backup/restore, secret non-disclosure, and clean
container/network/volume removal.

The verifier validates a strict `hormuz.compose-reference-proof` v1 summary and
uploads only that content-free file. Rendered models, logs, container
inspection, request observations, and the raw logical backup are temporary
inputs to the proof and are deleted. The job contacts no OpenAI or Anthropic
endpoint. Its exact operator workflow and nonclaims are in the
[Compose profile](../deploy/compose/README.md).

## Kubernetes + Helm multi-replica reference

The `Kubernetes + Helm multi-replica reference` job invokes:

```bash
HORMUZ_TEST_POSTGRES_DSN="$PROTECTED_DISPOSABLE_POSTGRES_DSN" \
  python tools/verify_multi_replica_operation.py run-state-proof \
    --postgres-image 'postgres@sha256:57c72fd2a128e416c7fcc499958864df5301e940bca0a56f58fddf30ffc07777' \
    --source-commit "$GITHUB_SHA" \
    --output "$RUNNER_TEMP/hormuz-multi-replica-state.json"

HORMUZ_KUBERNETES_PROOF_ACK=I_UNDERSTAND_THIS_IS_A_DISPOSABLE_KUBERNETES_REFERENCE_PROOF \
HORMUZ_KUBERNETES_EVIDENCE_DIR=/protected/new/output-directory \
HORMUZ_MULTI_REPLICA_STATE_EVIDENCE="$RUNNER_TEMP/hormuz-multi-replica-state.json" \
HORMUZ_SOURCE_COMMIT="$GITHUB_SHA" \
  ./tools/verify_helm_profile.sh
```

The runner verifies exact SHA-256 values before executing Kind `v0.32.0`,
kubectl/Kubernetes `v1.36.1`, Helm `v3.21.4`, and the Cilium `1.20.1` chart.
It creates one control plane and two workers from the exact Kind node-image
digest with the default CNI disabled, then installs the exact digest-pinned
Cilium agent and generic operator. Cilium is the first tested CNI, not a
Hormuz or chart dependency.

The proof packages and installs the vendor-neutral chart with the exact signed
Hormuz image digest, an internal ClusterIP, two replicas on distinct workers,
customer-fixture PostgreSQL pinned to its Linux AMD64 child-manifest digest
through an existing immutable Secret, and standard default-deny NetworkPolicy.
The cluster pulls both public workload images directly by immutable digest and
does not substitute mutable tags or use Kind's host-side image importer.
It proves authenticated ingress, fake-provider
routing/capping/redaction, metadata-only evidence persistence, denied ingress
and egress, readiness-gated immutable configuration/Secret replacement and
rollback, and sustained synthetic traffic synchronized across one observed Pod
deletion and distinct ready replacement. It secret-scans gateway and preflight
logs before each revision transition or deletion and after replacement, then
proves clean removal. It contacts no model provider or external IdP.

The same job now binds that deployment proof to a focused eight-test PostgreSQL
state proof. The named tests cover shared atomic budgets, immutable policy
activation, tenant isolation, request-attempt reservations, concurrent audit
chains, custody approval and barrier coordination, duplicated synchronization,
stale acknowledgements, and partition fencing. In Kind, the verifier
additionally holds one request at the fake provider during a normal Pod
termination and one during a forced Pod deletion. It measures restrictive
rollout, rollback, readiness withdrawal, in-flight drain, and replacement
convergence. The forced-loss attempt must
become `outcome_unknown`, keep one uncertain reservation, and correspond to
exactly one provider egress.

Only three strict mode-`0600` content-free summaries are uploaded:
`hormuz.kubernetes-reference-proof` v1,
`hormuz.multi-replica-state-proof` v1, and
`hormuz.multi-replica-operation-proof` v1. The final summary binds the first
two by SHA-256 and records the exact commit, image and chart digests, fixed
event sequence, observed timings, state counts, and nonclaims.
Generated credentials, configuration, rendered resources, logs, fake traffic,
and raw database observations stay temporary and are deleted. The proof makes
no PostgreSQL leader-failover, RPO, RTO, zone-failure, broad CNI-portability,
browser-session, provider exactly-once, customer-infrastructure, or universal
customer-SLA claim. See the exact boundary in the
[Kubernetes profile](../deploy/kubernetes/README.md).

## PostgreSQL HA failover reference

The `PostgreSQL HA failover reference` job invokes:

```bash
HORMUZ_POSTGRES_HA_PROOF_ACK=I_UNDERSTAND_THIS_IS_A_DISPOSABLE_POSTGRESQL_HA_REFERENCE_PROOF \
HORMUZ_POSTGRES_HA_EVIDENCE_DIR=/protected/new/output-directory \
HORMUZ_SOURCE_COMMIT="$GITHUB_SHA" \
  ./tools/verify_postgres_ha_reference.sh
```

The native Linux AMD64 runner verifies every downloaded binary, chart, and
operator manifest against an exact SHA-256. It deploys the exact Linux AMD64
CloudNativePG `1.30.0` operator image and three exact PostgreSQL `16.15`
instances in disposable Kind. Required anti-affinity places the database
instances on three tainted workers; the two Hormuz replicas are proven to run
on two different non-database workers. The Hormuz chart never renders a
PostgreSQL resource and receives only a restricted generic PostgreSQL DSN from
an existing immutable Secret.

The positive failure injects an unexpected active-primary worker pause. Every
gateway replica must withdraw readiness and enforce bounded concurrent storage
denials with no provider egress. The proof requires a changed primary, matching
primary Lease and read/write endpoint, gateway reconnection without process
replacement, a former primary that rejoins as a replica, preserved policy,
budget/request-attempt/evidence/custody/audit state, tenant isolation, one
preserved uncertain attempt and reservation, and zero automatic provider
replay. Immediately after the primary pause, the fake provider records an
in-flight request and then closes its connection without returning a response.
The client transport outcome must remain ambiguous and the pre-egress attempt
and reservation must remain intact for later reconciliation.

The negative failure pauses the active primary and one replica. During a
minimum 30-second observation window, failover quorum must refuse promotion,
the read/write endpoint must have no ready address, both gateways must remain
unready and deny provider egress, and the sole surviving replica must not be
presented as a writable primary. Recovery is accepted only after both lost
workers return and all three instances plus the existing gateway processes are
ready.

The job uploads only a strict mode-`0600`, content-free
`hormuz.postgresql-ha-reference-proof` v1 summary. Generated credentials,
DSNs, manifests, raw state snapshots, fake traffic, and the cluster remain
temporary. This is the first exact self-contained PostgreSQL HA reference, not
a managed-provider certification, broad Kubernetes/CNI claim, production
storage proof, backup/retention proof, RPO/RTO result, disaster-recovery
rehearsal, or customer SLA.

## OCI supply-chain evidence

The `OCI supply-chain evidence` job invokes:

```bash
./tools/verify_oci_supply_chain.sh
```

It builds its own local reference-image candidate, generates a CycloneDX SBOM,
and scans that same candidate with Trivy `0.74.0` pinned by immutable OCI image
digest. The job uploads the SBOM, raw Trivy JSON, and a versioned normalized
summary for seven days whether the gate passes or fails. The summary binds the
candidate image ID, pinned scanner identity, and SHA-256 hashes of both raw
artifacts.

The command fails only if a `HIGH` or `CRITICAL` vulnerability includes a
scanner-reported non-empty `FixedVersion`. Lower-severity and unfixed findings
remain in the raw report as review evidence. Missing, malformed, unsupported,
or candidate-mismatched scanner/SBOM data fails closed; this is a scanner
evidence failure, not a statement that the image is vulnerability-free. The
scanner binary is pinned, but its advisory database is refreshed at scan time.
There is no registry publication, signing, attestation, exception workflow, or
customer-content scanning in this individual gate.

## OCI reproducibility and signed release

The `OCI reproducibility` job invokes:

```bash
./tools/verify_oci_reproducibility.sh
```

It performs two independent no-cache `linux/amd64` builds with the reviewed
wheel hashes, pinned frontend/base, source commit timestamp, and BuildKit
timestamp rewriting. The verifier requires byte-identical OCI archives, one
AMD64 manifest, complete blob hashes, and exact version/revision labels. CI
retains only the content-free summary, not the archives.

`.github/workflows/release-oci.yml` is a separate tag-only publication gate.
It requires an annotated protected tag matching the package version and exact
release workflow identity, rebuilds and compares the digest, publishes first
under an immutable commit locator, runs the runtime and supply-chain gates
against that digest, and then creates keyless Cosign signature, CycloneDX, and
bounded SLSA v1 attestations. Before any Sigstore operation, a strict validator
requires a public repository and allowlisted, release-bound, secret-free SBOM
and provenance shape. The image signature uses public Rekor. The two
attestations are timestamped Fulcio identities attached to public GHCR and are
not uploaded to Rekor. Their complete registry-visible metadata is
allowlist-validated before attachment. Only after exact issuer, workflow/tag
identity, digest, image-signature transparency, and attestation verification
pass does the workflow add the semantic-version registry tag.

The final `hormuz.oci-release-evidence` summary hashes every retained evidence
file and records the registry-portable digest contract, AMD64 boundary,
mirroring requirements, exact signer, and explicit Rekor/attestation
boundaries. GHCR is the first publication registry, not the product contract.
The workflow requires the published package to remain public and records
that visibility in release-evidence schema v2. The workflow artifact contains
only allowlisted summaries, not raw SBOM,
provenance, vulnerability, or Cosign verification payloads. This workflow does not
certify Compose, Kubernetes, a customer mirror, public TLS, HA, or recovery.

## PostgreSQL logical backup and restore

The `PostgreSQL logical recovery drill` job invokes:

```bash
./tools/verify_postgres_backup_restore.sh
```

It builds no application image and calls no provider. The command creates only
fixed metadata-only fixture records in disposable source, recovery, and
quarantine PostgreSQL 16.14 containers selected by immutable image digest. It
uses `pg_dump` custom format for the source and `pg_restore` from that same
pinned image. A corrupted archive is restored only into quarantine and must
fail; a target that cannot be verified from restricted Hormuz roles is never
promoted. The valid recovery target must exactly match the source metadata
fingerprint—including finance-attempt sidecars and their audit-chain
entries—before the tool writes an artifact.

On a successful run, CI retains only the strict, content-free
`hormuz.postgresql-recovery-drill-summary` v2 `summary.json` for seven days.
It includes the database image/version, dump hash and size, record counts,
fingerprints, passed checks, and measured durations; it excludes the archive,
connection strings, roles, database names, policy documents, records, and
credentials. A failing run emits no dump or intermediate state artifact. This
is evidence for a disposable logical exercise only. It does not establish
PITR, production RPO/RTO, cloud backup, customer-data recovery, encryption,
HA/failover, automatic promotion, or DR certification.

## PostgreSQL point-in-time recovery

The same `PostgreSQL recovery drills` CI job also invokes:

```bash
HORMUZ_POSTGRES_PITR_ACKNOWLEDGEMENT=I_UNDERSTAND_DISPOSABLE_POSTGRESQL_PITR \
  ./tools/verify_postgres_pitr_recovery.sh
```

The opt-in wrapper starts only specifically named and Docker-labelled
PostgreSQL 16.14 containers selected by immutable image digest. It creates a
physical base backup of fixed metadata-only Hormuz state, commits a marker
after that backup, creates a named recovery point, commits a later marker, and
waits for the exact switched WAL files. The recovered copy must replay the
pre-target state, exclude the post-target state, promote from the named target,
and pass Hormuz's restricted runtime/control verification against the original
metadata fingerprint. Separate recoveries with an unreachable target and an
empty WAL archive must terminate non-zero without promotion.

CI retains only `hormuz.postgresql-pitr-recovery` v1 `summary.json` for seven
days. It contains the pinned database identity, boolean checks, and durations;
it excludes container names, ports, database names, roles, connection strings,
marker values, fixture state, archive files, and credentials. This proves a
single local disposable WAL/PITR mechanism only. It does not establish
production backup retention, customer restore, production RPO/RTO, encryption,
HA/failover, managed-database operations, or DR certification.

## PostgreSQL interruption and pool recovery

The same `PostgreSQL recovery drills` CI job also invokes:

```bash
./tools/verify_postgres_interruption_recovery.sh
```

The opt-in wrapper creates one specifically named and Docker-labelled
disposable PostgreSQL 16.14 container from the pinned image and fixes one
loopback host port to it for the container lifetime. The verifier rejects any
other target before it can issue an interruption command. It runs
one managed-policy gateway with restricted runtime and policy-control roles,
confirms a governed request, abruptly stops the database, and requires both a
content-free not-ready response and pre-egress governed-request failure. It
then restarts the same container and requires the same gateway process and
open `PostgresConnectionPool` to become ready and relay a **new** request
without provider replay. The fixture also checks retained usage evidence and
an empty second tenant after recovery.

CI retains only `hormuz.postgresql-interruption-recovery` v1 `summary.json`
for seven days. Its strict schema contains the pinned database identity,
boolean checks, and durations; it excludes container names, connection
strings, credentials, fixture policy, request/response content, and provider
payloads. This proves one single-instance, disposable stop/restart behavior.
It does not establish PostgreSQL HA/failover, multi-instance recovery,
production RPO/RTO, automatic provider replay, or incident-response readiness.
