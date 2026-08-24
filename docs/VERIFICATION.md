# Verification record

This file records executable evidence for client/provider compatibility. It intentionally contains no provider credentials, prompts, responses beyond fixed test markers, or employee secrets.

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

A one-provider run is explicitly partial. Closing issue #115 requires one
successful artifact containing both real providers at the same source
revision; provider-free CI cannot substitute for it.

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

A separate weekly canary installs the latest published Codex and Claude Code packages in an ephemeral runner and exercises only the two fake-provider compatibility tests. It has no provider credentials, does not block ordinary pull requests, and is intended to surface upstream protocol drift before an employee upgrade does.

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
attestations are timestamped Fulcio identities attached only to private GHCR
and are not uploaded to Rekor. Only after exact issuer, workflow/tag identity,
digest, image-signature transparency, and attestation verification pass does
the workflow add the semantic-version registry tag.

The final `hormuz.oci-release-evidence` summary hashes every retained evidence
file and records the registry-portable digest contract, AMD64 boundary,
mirroring requirements, exact signer, and explicit Rekor/attestation
boundaries. GHCR is the first private registry, not the product contract. The
workflow artifact contains only allowlisted summaries, not raw SBOM,
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
fingerprint before the tool writes an artifact.

On a successful run, CI retains only the strict, content-free
`hormuz.postgresql-recovery-drill-summary` v1 `summary.json` for seven days.
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
