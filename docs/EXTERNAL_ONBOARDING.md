# External onboarding validation for Hormuz v1.0.0

This is the active, post-release validation tracked by
[issue #110](https://github.com/Xpounder-com/hormuz/issues/110). Current human
evidence is **0/5 independent initial completions** and **0 returning users**.
Internal repetitions, maintainers, AI agents, sandboxes, and synthetic fixtures
do not count.

The bounded question is whether independent developers, security reviewers,
platform engineers, and engineering administrators can install the exact
Hormuz v1.0.0 source archive and complete `hormuz demo` using only material
inside that archive and command help. Passing supports only an independent
installation and provider-free-demo usability claim. It does not prove the
separate policy-administrator workflow, PostgreSQL state correctness,
production readiness, security certification, customer demand, or market fit.

## Volunteer

Add a thumbs-up reaction or the single word `Interested` to
[issue #110](https://github.com/Xpounder-com/hormuz/issues/110). Do not post an
email address, participant ID, session result, employer, customer information,
configuration, credential, prompt, response, path, hostname, log, or screenshot.

A maintainer will schedule a session only after both sides establish a mutually
agreed private channel. Public interest does not count as a participant or a
completion. Participation uses synthetic data and requires no OpenAI or
Anthropic account.

## Exact released artifact

Every counted session uses the same immutable source archive that was promoted
without rebuilding to v1.0.0:

- target version: `v1.0.0`
- source commit: `2fc0605252e41f731c85cc9146fbff6eb3b34669`
- archive: `hormuz-1.0.0.tar.gz`
- archive size: `895460` bytes
- archive SHA-256:
  `sha256:2c3b16c1742ee76032a33f3714492a8d8515c5291d4d57520441882cd8bc5b5a`
- manifest SHA-256:
  `sha256:85774aa45a8b30be88d1cb1a7b543222cc1396523aec31c17de07470b09d56b2`
- immutable [custody release](https://github.com/Xpounder-com/hormuz/releases/tag/candidate-v1.0.0-2c3b16c1742ee76032a33f3714492a8d8515c5291d4d57520441882cd8bc5b5a)
- stable [v1.0.0 release](https://github.com/Xpounder-com/hormuz/releases/tag/v1.0.0)

The stable release is intentionally metadata-only. The immutable custody
release remains the canonical location for the exact tested archive and its
manifest. A GitHub-generated tag archive, a checkout of `main`, a wheel, an OCI
image, or a locally rebuilt source archive is not the same evidence object.

Changing any byte creates a new digest and invalidates affected evidence. Do
not rebuild, repackage, or substitute a same-commit archive.

## Cohort and independence

Before the first session, preregister five to ten people off-repository and
assign each person one of four personas: `developer`, `security`, `platform`,
or `engineering_admin`. The final cohort must cover all four personas and must
include every started session. Do not replace an unsuccessful participant or
omit a blocked run.

A counted initial session requires all of the following:

- the participant is a real person distinct from every other participant;
- they did not author or review Hormuz's onboarding workflow;
- they received no private walkthrough or command hints;
- after timing starts, they use only the shipped README, shipped documentation,
  and `--help` from the exact archive;
- installation and the six-check provider-free demo pass;
- the demo reports zero external provider calls and three loopback calls; and
- only the content-free fields in the evidence contract are retained.

Public or private help may be given after a person becomes stuck, but that run
remains recorded and does not count as independent. A later successful run on
a corrected artifact is a retest, not a rewrite of the failed session.

At least five distinct initial participants must qualify. At least one of those
successful participants must independently run the same artifact again on a
later UTC date and pass the demo. A returning session may reuse the existing
installation; it still uses a new session ID.

## Facilitator setup before timing

The facilitator downloads and verifies the two immutable assets. This setup is
not participant assistance and is completed before the task is handed over.

```bash
curl --fail --location --proto '=https' --tlsv1.2 \
  --output hormuz-1.0.0.tar.gz \
  https://github.com/Xpounder-com/hormuz/releases/download/candidate-v1.0.0-2c3b16c1742ee76032a33f3714492a8d8515c5291d4d57520441882cd8bc5b5a/hormuz-1.0.0.tar.gz

curl --fail --location --proto '=https' --tlsv1.2 \
  --output hormuz-v1.0.0-candidate-manifest.json \
  https://github.com/Xpounder-com/hormuz/releases/download/candidate-v1.0.0-2c3b16c1742ee76032a33f3714492a8d8515c5291d4d57520441882cd8bc5b5a/hormuz-v1.0.0-candidate-manifest.json

shasum -a 256 hormuz-1.0.0.tar.gz
shasum -a 256 hormuz-v1.0.0-candidate-manifest.json
```

Reject the session setup unless the names, sizes, source commit, and both hashes
match this document and the manifest. Use Python 3.11 through 3.14. Remove any
OpenAI, Anthropic, Hormuz administrator, or customer credential from the
participant environment. Dependency installation may use the package index;
the demo itself must make no external provider call.

Generate opaque IDs privately and keep the person-to-participant mapping out of
Git, GitHub issues, Actions, release artifacts, and aggregate evidence:

```bash
python3 -c 'import uuid; print("eop:" + str(uuid.uuid4()))'
python3 -c 'import uuid; print("eos:" + str(uuid.uuid4()))'
```

Do not disclose commands, point to a particular README heading, demonstrate the
workflow, or preinstall Hormuz. The available Python interpreter and ordinary
operating-system setup may be prepared before timing. Download and digest
verification are supply-chain setup, not measured installation time.

## Neutral participant task

Start the installation timer when the participant receives the exact archive
and this goal:

> Install the supplied Hormuz v1.0.0 source archive in a clean virtual
> environment, then run its provider-free demonstration. Use only material
> shipped inside the archive and the installed command's help. Confirm whether
> all six checks pass, whether external provider calls are zero, and whether
> loopback simulator calls are three.

Stop the installation timer when the `hormuz` command is available in the clean
environment. Start the demo timer immediately before the participant invokes
the demo and stop it when the command exits. Record whole seconds. Do not record
terminal output.

The expected successful demo observations are bounded public constants:

- six `PASS` lines;
- zero external provider calls; and
- three loopback provider-simulator calls.

The participant may report a problem publicly through the
[installation form](https://github.com/Xpounder-com/hormuz/issues/new?template=installation.yml)
or
[documentation form](https://github.com/Xpounder-com/hormuz/issues/new?template=documentation.yml).
Security-sensitive behavior must use the
[private advisory path](https://github.com/Xpounder-com/hormuz/security/advisories/new).
Never include participant IDs or private session metadata in those reports.

## Content-free evidence

With explicit consent, the facilitator records only the strict
`hormuz.external-onboarding-evidence` v1 fields:

- exact released artifact digest and source identity;
- opaque participant and session IDs;
- UTC start time, persona, coarse environment enums, and Python minor version;
- a boolean attestation that the participant did not author or review the
  onboarding workflow;
- installation/demo statuses and whole-second timings;
- assistance level and bounded shipped-material/`--help` lookup counts;
- one fixed failure code;
- the three fixed demo observations when the demo passes;
- bounded friction categories and public issue or opaque private-advisory
  references; and
- attestations that content, credentials, identities, mappings, paths, and free
  text are absent.

The contract has no field for names, handles, email addresses, employer,
company, feedback prose, policy or request content, prompts, responses,
credentials, tokens, local paths, hostnames, logs, or screenshots. Keep raw
intake and identity mappings outside the repository and delete them according
to the separately agreed participant-retention boundary.

Run the non-counting structural fixture with:

```bash
python tools/verify_external_onboarding_evidence.py \
  tests/fixtures/external_onboarding/complete-synthetic-v1.json \
  --allow-synthetic-fixture
```

Validate real owner-held evidence without the synthetic flag:

```bash
python tools/verify_external_onboarding_evidence.py \
  /private/path/external-onboarding-evidence.json
```

The CLI exits `0` for passing real evidence or an explicitly allowed synthetic
contract check, `1` for structurally valid but incomplete real evidence, and
`2` for malformed, unsafe, or unapproved synthetic input. The evidence file
must be a bounded regular file; symlinks and special files fail closed.

Synthetic evidence always reports `validated_human_onboarding: false`. Internal
or AI-generated repetitions do not count even when their bounded fields look
identical to a human run.

## Findings, correction, and retest

Every friction category other than `none` links to a public issue. A security
finding may instead use an opaque private-advisory reference. The aggregate
stores no feedback text.

These findings block completion:

- published guidance cannot complete installation or the demo;
- output misleadingly reports success;
- the demo makes an external provider call; or
- content, credentials, tokens, customer data, or personal identity are
  exposed.

The validator derives mandatory blocker treatment from the session outcome;
the finding cannot downgrade it to `none`. `install_dependency`,
`command_not_found`, `demo_policy`, and `documentation` failures require
`published_guidance_failure`; `demo_network_boundary` requires the matching
blocker; `demo_evidence` requires either `published_guidance_failure` or
`misleading_success`; and `security` requires
`content_or_credential_exposure`. `unsupported_platform` and `other_bounded`
may remain nonblocking when the bounded finding supports that classification.
For failed sessions, the equivalent installation, command-discovery,
documentation, demo, network-boundary, evidence, and security friction
categories carry the same requirement even if the supplied failure code is
broader.

A blocker cannot be marked resolved against the unchanged failing artifact.
Correction requires a new immutable artifact and digest, an automated
regression bound to the correction commit, and a fresh independent retest. The
origin session must end before the corrected artifact is frozen. If the
correction broadly changes installation or the demo workflow, every
preregistered participant with an initial session on the failing digest must
independently rerun the corrected digest and pass; additional participants do
not replace anyone in that affected cohort. A participant's assigned persona
is immutable across artifact digests. Prior evidence remains blocker history
and cannot count for the new digest.

## Completion and nonclaims

Issue #110 is complete only when the real aggregate passes, every started
session is included, five to ten distinct humans are attested off-repository,
all four personas are covered, at least five independent initial sessions
qualify, a successful participant returns on a later date, and no onboarding
blocker remains open.

Passing proves only the tested v1.0.0 installation and provider-free demo were
usable by this bounded independent cohort. It does not prove the separate
[policy-administrator usability protocol](POLICY_ADMIN_USABILITY.md),
PostgreSQL apply/history/rollback state correctness, provider compatibility,
production security or availability, disaster recovery, enterprise readiness,
customer demand, or market validation.
