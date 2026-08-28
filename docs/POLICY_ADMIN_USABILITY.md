# Independent policy-administrator usability gate

This protocol measures whether the v1 policy-administration workflow is usable
and state-correct without private guidance. It is the human candidate gate in
[issue #173](https://github.com/Xpounder-com/hormuz/issues/173). Current
qualifying evidence is **0/5** offline participants and **0/3** PostgreSQL
participants. Repository tests and the synthetic fixture do not change either
count.

Issue #110 remains a separate public-alpha onboarding gate. The same person may
participate in both studies, but each study retains its own aggregate and proves
a different claim.

## Qualifying participants

Pre-register exactly five people for the offline cohort and exactly three for
the PostgreSQL cohort before testing a candidate artifact. A person may be in
both cohorts, but that person's measured session intervals must not overlap.
Do not replace a failed participant with a later success or omit a run against
the current candidate.

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
- they use only documentation, examples, and `--help` shipped with the frozen
  candidate.

Public material outside the candidate, private notes, hints, screen sharing,
spoken command suggestions, and corrections from a facilitator disqualify the
run. Record the assistance count even when it is nonzero; do not hide the run.

## Frozen v1.0.0 candidate boundary

The qualifying artifact is one frozen Hormuz Python source distribution named
for target version `v1.0.0`. Before any measured session, record its exact
source commit, UTC freeze time, and SHA-256 digest. The archive co-locates the
protocol, `config.example.json`, both saved-task examples, the command
implementation that produces `--help`, and the evidence validator. CI inspects
the built archive rather than only trusting `MANIFEST.in`.

The candidate is not yet the final release. If and only if the gate passes,
promote the exact tested archive and digest to `v1.0.0`. Do not rebuild,
repackage, rename content inside the archive, regenerate metadata inside it, or
substitute a byte-equivalent claim based only on the same source commit. A
different byte stream is a new candidate with a new digest and must satisfy the
affected gate again. External release-page metadata may be added during
promotion only when the archived bytes remain unchanged.

A wheel may be installed and the signed OCI image may be used during setup, but
neither is accepted as the session's `artifact_kind` in contract v2: those
formats do not currently contain the complete participant kit. Expanding the
accepted formats requires packaging the same immutable kit and revising the
contract; it cannot be inferred from an otherwise successful run.

### Candidate custody and unchanged-byte promotion

Repository release immutability must be enabled before a candidate is frozen
and must remain enabled through publication. The release steward dispatches
the `Freeze v1.0.0 candidate` workflow from protected `main` only after the
exact commit has a successful post-merge CI run and its package version is
exactly `1.0.0`. A rerun attempt is rejected, and only one freeze workflow run
is permitted for a source commit. Recovering from a failed freeze therefore
requires a new source commit and a newly computed candidate digest.

The freeze workflow installs the exact pure-Python frontend and backend wheels
from `requirements/v1-source-build.lock` with SHA-256 enforcement and without
dependency resolution. It then invokes the source-distribution build exactly
once with build isolation disabled. It validates the archive contents, computes
its SHA-256 digest, and writes
the strict `hormuz.v1-candidate-manifest` contract. The manifest records the
source commit, UTC freeze time, archive name and size, workflow run, digest,
and the facts that overwriting and promotion-time rebuilding are forbidden.
The manifest is written atomically with mode `0600` and refuses an existing,
symlink, or special-file destination.

Candidate custody uses a draft GitHub Release and a non-semver,
digest-addressed tag such as
`candidate-v1.0.0-` followed by the 64 lowercase SHA-256 hex characters. This
avoids creating the final tag before the human gate passes and cannot trigger
the semantic-version OCI workflow. Copy the exact value from
`custody.release_tag` in the manifest rather than typing it.
The workflow attaches exactly two assets without `--clobber`: the source
archive and its manifest. It then downloads both assets into a new directory
and verifies their local and GitHub-reported digests. Existing candidate
releases or assets are never replaced.

Give every participant the exact archived source distribution and manifest.
Before installation, verify the archive locally and compare the result with
`candidate.artifact_digest` in the manifest:

```bash
shasum -a 256 hormuz-1.0.0.tar.gz
```

Every session and the aggregate must carry that same `sha256:` value. Changing
even one archived byte creates a different candidate digest. Evidence bound to
the prior digest remains blocker history but cannot count for the changed
candidate; run the affected cohort again against a newly frozen draft.

After a real aggregate passes the validator, the repository owner checks out
the manifest's exact frozen source commit and runs its checked-in promotion
command from a clean Hormuz worktree. The command rejects a different commit,
a commit outside fetched protected `main`, any tracked or untracked worktree
change, and an output directory inside that checkout:

```bash
tools/promote_v1_candidate.sh \
  --candidate-tag CANDIDATE_TAG_FROM_MANIFEST \
  --evidence /private/path/policy-admin-usability-evidence.json
```

Use `--output /private/path/promotion-proof` only when the owner needs to keep
the owner-only downloaded artifacts and mechanical proof. The directory must
not already exist. Without `--output`, temporary material is removed.

The command downloads the draft assets and validates the real evidence against
their exact digest before it creates anything. It also queries the recorded
Actions run and requires a successful first attempt of the canonical freeze
workflow on protected `main`, at the manifest's source commit, with the freeze
time and both release-asset creation times inside that run. An asset replaced
after the freeze run therefore fails even if it has the expected name. The
digest-addressed custody tag must also remain a lightweight pointer to that
source commit. The tag is re-fetched and revalidated during every later
promotion phase; the no-bypass tag-immutability ruleset also covers these
digest-addressed candidate tags. The command then creates and pushes the
protected annotated `v1.0.0` tag at that commit, waits for the existing signed
OCI release workflow to succeed for the exact tag and commit, and downloads the
draft assets again.
An existing final tag is accepted only when it is annotated, targets that
commit, was created no earlier than the validated aggregate, and its annotation
binds both the archive digest and gate-evidence digest. Only after this second
verification does the command associate the existing draft with `v1.0.0` and
publish it. It downloads the published assets once more, requires GitHub to
report the release immutable, and verifies GitHub's release attestations for
both assets.

There is no build or asset-upload operation in the promotion command. A
partially completed promotion may be resumed only when the existing annotated
tag, source commit, release assets, evidence digest, and OCI run still match.
Any mismatch fails closed. The digest-addressed custody tag remains a pointer
to the tested source commit; the final release is associated only with the
protected `v1.0.0` tag.

## Setup boundary

Installation and environment setup finish before the measured task. Verify
that the supplied archive matches the frozen candidate SHA-256 digest, then
install from that archive. The participant starts with a clean working
directory containing
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

Each completed offline session records the SHA-256 digests of the exact shipped
baseline and scenario-suite files plus a bounded, content-free summary of the
observed comparison and evaluation contracts. The verifier binds those values
to the kit shipped with itself: one semantic change at
`policies.organization.max_output_tokens` from `16000` to `4000`, one changed
`output-cap` scenario, both decisions allowed, and output caps of `16000` and
`4000`. It also requires the canonical baseline, candidate, and suite
identities, `usage_basis: current`, and an attestation that current SQLite usage
was zero. Stage labels alone cannot qualify a session.

Complete and evaluate the entire offline cohort before starting any measured
PostgreSQL session. All five offline sessions must qualify, at least four must
finish within 15 minutes, none may exceed 25 minutes, and no offline blocker may
remain open. The earliest PostgreSQL `started_at` must be at or after the latest
computed offline session end.

If the offline cohort exposes a blocker, correct it, freeze a new candidate with
a new digest, run the required automated regression, and repeat the affected
offline cohort before provisioning measured PostgreSQL sessions. Prior runs
remain in the aggregate as blocker history but cannot count toward the new
candidate. This ordering avoids spending PostgreSQL setup time on a candidate
whose basic documentation or command workflow has already failed.

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

`wrong_policy_state`, `authentication_bypass`, and `history_inconsistency` are
PostgreSQL-only blocker reasons because the offline task neither authenticates
an administrator nor changes or inspects managed policy state. Offline compare
or evaluation failures use the published-guidance, misleading-success, or
content-exposure reasons as applicable.

An open blocker always prevents the gate from passing. A resolved blocker must
name a correction commit, the exact corrected candidate source commit,
immutable corrected-candidate digest, successful GitHub Actions regression run with an
explicit `success` conclusion, and a later qualifying retest session. The
regression record names the Actions source commit and canonical
`.github/workflows/ci.yml` path, and the release steward attests that the run is
bound to that commit and workflow. Its source commit must equal the corrected
candidate source commit. The corrected digest, source commit, and freeze time
must exactly equal the top-level candidate being gated; a correction,
regression, and retest against a different artifact cannot clear a blocker on
an older candidate. Before accepting the aggregate, the release steward
verifies and attests that the correction commit is an ancestor of the corrected
candidate source commit. The retest may be one of the pre-registered current
cohort runs. If the correction broadly changes a track's workflow, every member
of that track must rerun after the corrected candidate was frozen.

## Content-free aggregate

The strict `hormuz.policy-admin-usability-evidence` v2 aggregate records only:

- target version `v1.0.0`, candidate artifact kind and digest, source commit,
  and freeze time;
- preregistration, complete-session inclusion, and no-replacement attestations;
- pseudonymous participant/session IDs, track, stage outcomes, measured
  seconds, and candidate digest;
- author/reviewer, private-walkthrough, and assistance-count metadata;
- bounded documentation/example/`--help` usage plus up to 20 finding IDs and
  their friction categories per session;
- exact shipped offline-asset digests and bounded comparison/evaluation
  identities, counts, booleans, semantic path, and public numeric outcomes;
- public issue or opaque private-advisory references;
- a unique opaque run scope and tenant-isolation attestation for each
  PostgreSQL session;
- guarded apply/rollback attestations and values, expected and observed policy
  version IDs, content digests, generations, and the predecessor, activation,
  and rollback lifecycle event types for PostgreSQL; and
- correction commit, exact corrected candidate source commit/artifact digest,
  source-history verification attestation, automated regression source/workflow
  binding, and retest linkage.

Exact field allowlists leave no place for names, email addresses, policy JSON,
request content, prompts, responses, credentials, logs, screenshots, local
paths, hostnames, or free-form notes. Keep the participant-to-person mapping and
raw intake outside Git, Actions, candidate/release artifacts, and the aggregate.

Use the checked-in fixture only to exercise the contract:

```bash
python tools/verify_policy_admin_usability_evidence.py \
  tests/fixtures/policy_admin_usability/complete-synthetic-v2.json \
  --allow-synthetic-fixture
```

Validate real evidence without the synthetic flag:

```bash
python tools/verify_policy_admin_usability_evidence.py \
  /private/path/policy-admin-usability-evidence.json
```

Exit `0` means the frozen candidate is eligible for unchanged promotion, exit
`1` means a structurally valid real aggregate remains incomplete, and exit `2`
means the evidence contract itself is invalid. A successful result reports
`status: eligible_for_unchanged_promotion`, the exact candidate digest,
`target_version: v1.0.0`, and
`claim_scope: administrator_workflow_usability_and_state_correctness`.
Promotion must use that reported digest; the validator never authorizes a
rebuild. Explicitly allowed synthetic evidence exits `0` only to show that the
fixture is structurally valid; its JSON result always has
`eligible_for_v1_0_0_promotion: false`.

Schema v2 replaces the pre-evidence schema v1 lifecycle, which incorrectly
modeled the tested object as an already published release. The validator
rejects schema v1 rather than silently assigning its fields the new candidate
semantics. No real human evidence existed when v2 was introduced.

The validator accepts only a regular file of at most 1 MiB, rejects symlinks
and special files without blocking on them, and treats malformed or
unrepresentable JSON numbers as contract errors. `generated_at` may be no more
than five minutes ahead of the validator's UTC clock, allowing bounded clock
skew without permitting future sessions to pass early.

## Nonclaims

The validator can enforce structure, thresholds, exact candidate/correction
identity, candidate-freeze and session-end chronology, offline-before-PostgreSQL
ordering, required attestations, metadata
relationships, and the synthetic-evidence boundary. It cannot independently
prove the off-repository cohort registration or identity mapping, that every
started run was submitted, that a person was not privately coached, the
recorded offline outputs were personally observed, the attested PostgreSQL
tenant isolation, that a participant actually supplied the recorded
active-version guard, the attested Git ancestry, or the stated
source/workflow binding and contents of a referenced Actions run without
separately inspecting those systems. Passing this gate proves only the bounded
administrator workflow's usability and apply/history/rollback state
correctness for the tested candidate. It does not prove complete enterprise
readiness, production security or availability, disaster recovery,
live-provider behavior, customer demand, market validation, or the separate
issue #110 claim.
