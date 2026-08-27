# Independent policy-administrator usability gate

This protocol measures whether the v1 policy-administration workflow is usable
and state-correct without private guidance. It is the human release gate in
[issue #173](https://github.com/Xpounder-com/hormuz/issues/173). Current
qualifying evidence is **0/5** offline participants and **0/3** PostgreSQL
participants. Repository tests and the synthetic fixture do not change either
count.

Issue #110 remains a separate public-alpha onboarding gate. The same person may
participate in both studies, but each study retains its own aggregate and proves
a different claim.

## Qualifying participants

Pre-register exactly five people for the offline cohort and exactly three for
the PostgreSQL cohort before testing a release artifact. A person may be in
both cohorts. Do not replace a failed participant with a later success or omit a
run against the current artifact.

The final aggregate explicitly attests that both cohorts were preregistered
before testing, every started session is included, and no participant was
replaced. Exact session counts are necessary but not sufficient: any false or
missing attestation leaves the gate incomplete. The aggregate generation time
must be at or after each session's computed end time (`started_at` plus
`duration_seconds`).

A participant is independent only when all of these are true:

- they did not author or review the workflow, this protocol, or the relevant
  command implementation;
- they received no private walkthrough before the run;
- the facilitator provides zero interventions after timing starts; and
- they use only documentation, examples, and `--help` shipped with the tested
  release.

Public material outside the release, private notes, hints, screen sharing,
spoken command suggestions, and corrections from a facilitator disqualify the
run. Record the assistance count even when it is nonzero; do not hide the run.

## V1 artifact boundary

The qualifying v1 evidence artifact is the published Hormuz Python source
distribution (`hormuz-<version>.tar.gz`). That archive co-locates the protocol,
`config.example.json`, both saved-task examples, and the evidence validator. CI
inspects the built archive rather than only trusting `MANIFEST.in`.

A wheel may be installed and the signed OCI image may be used during setup, but
neither is accepted as the session's `artifact_kind` in contract v1: those
formats do not currently contain the complete participant kit. Expanding the
accepted formats requires packaging the same immutable kit and revising the
contract; it cannot be inferred from an otherwise successful run.

## Setup boundary

Installation and environment setup finish before the measured task. Verify and
record the immutable source-archive SHA-256 digest, then install from that
archive. The participant starts with a clean working directory containing
copies of the shipped
`config.example.json`,
`examples/policy-admin-usability-baseline.json`, and
`examples/policy-admin-usability-scenarios.json`. Rename the configuration copy
to `hormuz.json`; do not alter the example baseline or suite. Before handoff,
initialize its fresh SQLite usage database and verify that current usage is
zero. If the example configuration's static identity token is needed only for
that setup command, use a synthetic setup value and remove it before timing.

The facilitator may provision files, PostgreSQL, and a scoped administrator
credential before timing. They may not demonstrate commands, point to a
specific help page, or explain the workflow. Installation may be measured in a
separate study, but its time is never included in this gate's
`duration_seconds`.

## Offline task card

Start the timer when the participant receives this ordered task. Stop after the
evaluation result has been checked.

1. Create `candidate.json` from Hormuz's `standard` policy template using
   `hormuz.json`.
2. Change only `policies.organization.max_output_tokens` from `16000` to
   `4000`.
3. Validate `candidate.json`.
4. Compare it semantically with
   `policy-admin-usability-baseline.json`; confirm that the output-cap path is
   changed from `16000` to `4000`. A comparison exit status of `1` means a
   difference was found, not that comparison failed.
5. Load and validate the shipped saved suite
   `policy-admin-usability-scenarios.json` through the spaced `policy scenarios`
   command tree.
6. Evaluate that suite against the local baseline and local candidate. Confirm
   that the one request remains allowed and its candidate output cap is
   `4000`. Evaluation exit status `1` means behavior changed as intended, while
   exit `2` means evaluation failed.

This path uses local policy files and SQLite. It needs no provider or
policy-administrator credential and must make no provider call. The disposable
SQLite database starts with zero current usage; the observed change is the
output policy, not manufactured budget consumption.

The offline cohort passes only when all five complete unaided, at least four
finish in 900 seconds or less, and every participant finishes in 1,500 seconds
or less.

## PostgreSQL task card

For each of three independent participants, provision a separate isolated
tenant with one active baseline policy and a distinct reviewed candidate. Give
each run a unique opaque `run_scope_id`; the aggregate records that identifier
and an isolation attestation, never the actual tenant identifier. Provide the
participant an administrator credential through the documented credential
boundary. Never place the credential or its environment-variable value in the
evidence aggregate.

Start timing after PostgreSQL, configuration, the baseline, candidate, and
credential are ready. The participant must:

1. authenticate as a policy administrator and inspect the active baseline;
2. apply the candidate with an `--if-active` guard matching the inspected
   baseline version;
3. use the versioned JSON status to verify the active candidate version ID,
   content digest, and activation generation, including that generation
   advanced by one;
4. inspect bounded JSON history and verify the predecessor plus candidate
   activation metadata;
5. perform the default one-step generation rollback with an `--if-active`
   guard matching the active candidate version; and
6. verify through status and history that the predecessor version ID and digest
   are active again, rollback created the next generation, and the history
   events match both observed state transitions.

Rollback is a new activation and does not erase history. Repeating rollback may
toggle to the version just left; this task performs one rollback only. All
three participants must complete and the validator must independently match
expected and observed version IDs, content digests, generations, and history
metadata. The aggregate records whether each guard was used and its version
value; a PostgreSQL session cannot qualify unless both guards match the active
version expected at that step.

## Findings and blockers

Every recorded friction category other than `none` links to a public issue.
Authentication-bypass and content/credential-exposure findings may instead use
an opaque private security-advisory reference. A session carries a bounded,
sorted collection of finding IDs and friction categories so multiple findings
from one run are retained rather than collapsed or omitted. Each finding links
back to that session. The aggregate contains no feedback text. These findings
block the affected gate:

- the workflow cannot be completed through published guidance;
- output misleadingly reports success;
- apply or rollback selects the wrong version, digest, or generation;
- a non-administrator can perform an administrator action;
- status and history disagree; or
- policy, request, credential, token, or personal content is exposed.

An open blocker always prevents the gate from passing. A resolved blocker must
name a correction commit, the exact corrected release source commit, immutable
corrected-release digest, successful GitHub Actions regression run with an
explicit `success` conclusion, and a later qualifying retest session. The
regression record names the Actions source commit and canonical
`.github/workflows/ci.yml` path, and the release steward attests that the run is
bound to that commit and workflow. Its source commit must equal the corrected
release source commit. The corrected digest, source commit, and publication
time must exactly equal the top-level release being gated; a correction,
regression, and retest against a different artifact cannot clear a blocker on
an older release. Before accepting the aggregate, the release steward verifies
and attests that the correction commit is an ancestor of the corrected release
source commit. The retest may be one of the pre-registered current cohort runs.
If the correction broadly changes a track's workflow, every member of that
track must rerun after the corrected release was published.

## Content-free aggregate

The strict `hormuz.policy-admin-usability-evidence` v1 aggregate records only:

- the release version, artifact kind and digest, source commit, and publication
  time;
- preregistration, complete-session inclusion, and no-replacement attestations;
- pseudonymous participant/session IDs, track, stage outcomes, measured
  seconds, and release digest;
- author/reviewer, private-walkthrough, and assistance-count metadata;
- bounded documentation/example/`--help` usage plus up to 20 finding IDs and
  their friction categories per session;
- public issue or opaque private-advisory references;
- a unique opaque run scope and tenant-isolation attestation for each
  PostgreSQL session;
- guarded apply/rollback attestations and values, expected and observed policy
  version IDs, content digests, generations, and the predecessor, activation,
  and rollback lifecycle event types for PostgreSQL; and
- correction commit, exact corrected release source commit/artifact digest,
  source-history verification attestation, automated regression source/workflow
  binding, and retest linkage.

Exact field allowlists leave no place for names, email addresses, policy JSON,
request content, prompts, responses, credentials, logs, screenshots, local
paths, hostnames, or free-form notes. Keep the participant-to-person mapping and
raw intake outside Git, Actions, release artifacts, and the aggregate.

Use the checked-in fixture only to exercise the contract:

```bash
python tools/verify_policy_admin_usability_evidence.py \
  tests/fixtures/policy_admin_usability/complete-synthetic-v1.json \
  --allow-synthetic-fixture
```

Validate real evidence without the synthetic flag:

```bash
python tools/verify_policy_admin_usability_evidence.py \
  /private/path/policy-admin-usability-evidence.json
```

Exit `0` means the real gate passed, exit `1` means a structurally valid real
aggregate remains incomplete, and exit `2` means the evidence contract itself
is invalid. Explicitly allowed synthetic evidence exits `0` only to show that
the fixture is structurally valid; its JSON result always has
`ready_for_v1_policy_admin_claim: false`.

The validator accepts only a regular file of at most 1 MiB, rejects symlinks
and special files without blocking on them, and treats malformed or
unrepresentable JSON numbers as contract errors. `generated_at` may be no more
than five minutes ahead of the validator's UTC clock, allowing bounded clock
skew without permitting future sessions to pass early.

## Nonclaims

The validator can enforce structure, thresholds, exact release/correction
identity, session-end chronology, required attestations, metadata
relationships, and the synthetic-evidence boundary. It cannot independently
prove the off-repository cohort registration or identity mapping, that every
started run was submitted, that a person was not privately coached, the
attested PostgreSQL tenant isolation, that a participant actually supplied the
recorded active-version guard, the attested Git ancestry, or the stated
source/workflow binding and contents of a referenced Actions run without
separately inspecting those systems. Passing this gate proves the bounded
administrator tasks, not live-provider behavior, enterprise availability,
disaster recovery, or the separate issue #110 claim.
