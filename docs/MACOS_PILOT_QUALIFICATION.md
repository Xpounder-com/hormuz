# Signed Mac pilot qualification

This protocol decides whether one exact notarized Hormuz Mac archive is ready
for a small, controlled external pilot. It composes distribution, clean-machine
installation, Keychain lifecycle, official-client authentication recovery,
hosted provider behavior, operational recovery, and independent review. A pass
does not publish the archive, invite a customer, create an availability SLA, or
count as external-human validation.

The retained contract is `hormuz.macos-pilot-qualification` v1. Its verifier is
`tools/verify_macos_pilot_evidence.py`. The existing v1.0.0 external-onboarding
study remains separate: this protocol always reports `0` initial and `0`
returning completions and cannot change the `0/5 initial` or `0/1 returning`
ledger.

## Gate order

Run the gates in this order. Stop at the first failure, retain only the bounded
status allowed by this contract, fix the underlying problem, and repeat the
affected gate on a new build when artifact bytes change.

1. **Freeze two reviewed builds.** The protected `Mac signed distribution`
   workflow must run from the repository's exact default-branch commit. Retain
   its notarized ZIP, `distribution-proof.json`, and `notarization.json`. The
   current build and its immediately preceding notarized build are both needed
   to test update and rollback. A local or feature-branch notarization is useful
   rehearsal evidence but cannot satisfy the default-branch provenance fields.
2. **Verify exact artifact custody.** Recompute the SHA-256 and byte size after
   downloading the artifact. They must match `distribution-proof.json`; the
   proof and notarization-summary file digests must match the aggregate. The
   distribution proof must show a universal `arm64`/`x86_64` Developer ID
   signature, hardened runtime, no entitlements, system-only dependencies, a
   stapled ticket, Gatekeeper acceptance, and the same permanent bundle and team
   identity. The verifier independently extracts both archives and reruns
   `codesign`, stapler, and Gatekeeper assessment, requiring Apple team
   `R267LZMUTY`; hand-authored JSON cannot replace those platform checks. It
   also authenticates each distribution run through `gh api`, requiring a
   successful default-branch `Mac signed distribution` workflow-dispatch run at
   the proof's source commit. It downloads the uniquely named, unexpired Actions
   artifact for that run and byte-binds the retained notarized ZIP, distribution
   proof, and notarization summary to its members. The proof build must equal the
   workflow run-number/run-attempt build identity. Apple acceptance must contain
   zero issues and at least two ticket entries.
3. **Exercise clean machines.** Use one Apple Silicon Mac and one Intel Mac
   without developer tools. Download through the intended delivery channel so
   normal quarantine is present. Confirm Gatekeeper accepts the archive,
   install `Hormuz.app` in `/Applications`, and launch it. A VM that changes
   architecture or bypasses normal quarantine does not replace either run.
4. **Exercise signed Keychain and session lifecycle.** With the real pilot IdP,
   sign in, restart the app, lock and unlock macOS, refresh the session, sign
   out, and revoke the server-side session. Confirm the revoked session is
   denied. Reinstall the same build, update from the retained previous build to
   the candidate, and roll back to the previous build. At every step verify the
   intended Keychain behavior and that no credential file appears. The
   candidate evidence binds `update_to_build` to the archive under review.
5. **Repeat official-client `401` qualification through the signed helper.** The
   pinned Codex client must receive one `401`, refresh once, replay once, and
   complete with zero provider egress on the rejected turn and one provider
   egress after recovery. The pinned Claude Code client must receive one `401`,
   refresh once, avoid automatic replay, and complete only after one explicit
   next request. Both records bind to the notarized archive digest and attest
   that the native Keychain helper supplied the Hormuz session.
6. **Qualify the hosted gateway.** Use a separately deployed
   `external_pilot` profile with HTTPS, real Okta login, server-only provider
   credentials, PostgreSQL durability and tenant RLS, durable sessions,
   monitoring, a recovery drill, and a published support path. Prove that the
   first streaming chunk arrives before provider completion, cancellation
   closes upstream work, records an outcome-unknown attempt, and causes zero
   replay, and header/first-byte/total latency samples are retained. Prove one
   policy-bounded same-protocol failover with a one-hop limit and a durable
   failover link. Each request may have at most one fallback attempt, and the
   durable failover-link count must equal the extra attempt count. Monitor worker
   saturation and PostgreSQL pool wait. The current Render
   authentication staging profile has inference disabled and cannot satisfy
   this gate. A real pass also authenticates distinct deployment and recovery
   runs through GitHub's API and requires both to be successful default-branch
   runs of `.github/workflows/external-pilot-qualification.yml` at the recorded
   gateway commit. That operational workflow does not exist yet, so the current
   repository remains fail-closed for real pilot qualification.
7. **Complete independent review.** An independent reviewer must close both the
   security and accessibility reviews through a public issue or opaque private
   review reference. A self-review or an unreferenced `passed` value is rejected.
8. **Resolve every blocker.** The aggregate may retain only fixed blocker enums.
   `ready_for_controlled_external_pilot` is true only when the list is empty and
   every preceding gate qualifies.

## Content-free evidence boundary

The aggregate contains only exact artifact identifiers, timestamps, bounded
environment enums, fixed counts, booleans, public Actions/issue URLs, and opaque
private-review IDs. Its schema has no field for a name, email address, employer,
customer, prompt, response, provider request ID, token, credential, hostname,
local path, log, screenshot, or free-form feedback. Unknown fields, duplicate
JSON members, non-finite numbers, symlinks, changing files, and oversized input
fail closed.

Real qualification evidence must carry the permanent `com.xpounder.hormuz`
bundle identity and Apple Developer team `R267LZMUTY`. The checked-in synthetic
identity is accepted only while `evidence_kind` remains
`synthetic_test_fixture`; changing that marker cannot promote the fixture into
a qualifying artifact. The distribution-proof schema matches the exact
v2 `distribution-proof.json` emitted by the protected signing workflow,
including its `executable_version_verified`, `source_commit`, and
`workflow_run_url` fields. The aggregate binds those workflow-origin fields for
both the candidate and the retained previous archive; operator-entered
provenance cannot substitute for the protected workflow proof.
Real qualification reruns the macOS platform checks against both archive byte
streams, authenticates the recorded GitHub workflow runs, and verifies that the
exact retained files occur in each run's final Actions artifact. Expired,
duplicate, malformed, oversized, encrypted, path-bearing, or extra artifact
members fail closed. The gateway record also carries an evidence-kind
discriminator; the synthetic gateway domain is rejected when the aggregate
claims real pilot qualification.

`provider_attempt_record_count` is content-free operational evidence. It does
not contain request or response content. Provider credentials remain only in
the hosted gateway's managed secret boundary; neither the signed Mac app nor
the aggregate receives them.

## Verification command

Keep the real aggregate outside the repository. Run this on macOS 14 or later
with `gh` authenticated to GitHub and able to read Actions artifacts, then
download both exact notarized ZIPs,
distribution proofs, and notarization summaries into a private directory and
run:

```bash
python3 tools/verify_macos_pilot_evidence.py \
  /private/path/macos-pilot-qualification.json \
  --archive /private/path/Hormuz-0.1.0-notarized.zip \
  --distribution-proof /private/path/distribution-proof.json \
  --notarization-summary /private/path/notarization.json \
  --previous-archive /private/path/Hormuz-0.0.9-notarized.zip \
  --previous-distribution-proof /private/path/previous-distribution-proof.json \
  --previous-notarization-summary /private/path/previous-notarization.json
```

Exit status `0` means the real aggregate is ready for a controlled external
pilot. Status `1` means the evidence is structurally valid but one or more
gates remain incomplete. Status `2` means the evidence or artifact binding is
malformed or unsafe.

The repository carries a deliberately fake archive and complete synthetic
shape so CI can execute every branch of the contract:

```bash
python3 tools/verify_macos_pilot_evidence.py \
  tests/fixtures/macos_pilot/complete-synthetic-v1.json \
  --archive tests/fixtures/macos_pilot/Hormuz-0.1.0-notarized.zip \
  --distribution-proof tests/fixtures/macos_pilot/distribution-proof-v2.json \
  --notarization-summary tests/fixtures/macos_pilot/notarization-v1.json \
  --previous-archive tests/fixtures/macos_pilot/Hormuz-0.0.9-notarized.zip \
  --previous-distribution-proof tests/fixtures/macos_pilot/previous-distribution-proof-v2.json \
  --previous-notarization-summary tests/fixtures/macos_pilot/previous-notarization-v1.json \
  --allow-synthetic-fixture
```

That command exits successfully only to prove validator mechanics. Its result
always says `ready_for_controlled_external_pilot: false` with reason
`synthetic_fixture`. The fake `.zip` is plain fixture data and is never a Mac
application or distributable artifact.

## Result boundary

A passing real aggregate supports only this statement:

> The exact signed and notarized Hormuz Mac archive passed the recorded
> controlled-pilot installation, session, client-authentication, hosted-gateway,
> recovery, security, accessibility, and support gates.

It does not establish multi-region availability, zero-downtime operation, an
availability or latency SLA, provider invoice accuracy, broad client
compatibility, external-human usability, customer production readiness, or
commercial demand. Those claims require their own evidence and decisions.
