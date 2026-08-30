# Contributing to Hormuz

Hormuz is an open-source public alpha. Contributions that make the gateway
safer, easier to understand, or easier to verify are welcome. The project does
not yet claim enterprise production readiness, broad platform support, or
compatibility beyond the boundaries in [SUPPORT.md](SUPPORT.md).

## Choose the right public path

- Search the [issue tracker](https://github.com/Xpounder-com/hormuz/issues)
  before opening a report.
- Use the issue chooser for installation failures, reproducible bugs, feature
  requests, and documentation problems.
- Follow [SECURITY.md](SECURITY.md) for a suspected vulnerability. Never put a
  vulnerability, credential, prompt, response, identity token, customer name,
  private hostname, or customer data in a public issue.
- Follow [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) in every project space.

## Development setup

The verified source-development path is CPython 3.11 through 3.14 on the
GitHub-hosted Ubuntu runner. Other environments may work, but are not release
gates for the first alpha.

From a clean checkout on a POSIX shell:

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --editable .
python -m hormuz --help
python tools/verify_secret_inventory.py
python -m unittest -v
```

The default suite uses loopback fake providers and must not require an OpenAI
or Anthropic credential. PostgreSQL, OpenBao, Ceph, AWS, and live-provider
checks are separately documented opt-in proofs; do not enable them casually or
substitute a fake pass for their live acceptance criteria.

To verify only the public contribution and support surfaces:

```bash
python tools/verify_public_community_paths.py
python -m unittest -v tests.test_public_community_paths
```

## Make a focused change

1. Start from current `main` and link the change to one issue with a verifiable
   outcome.
2. Keep unrelated formatting, refactors, and feature work out of the pull
   request.
3. Add tests for behavior changes and update the nearest user-facing document.
4. Record the exact commands run and the result. State skipped live checks and
   remaining nonclaims explicitly.
5. Use synthetic values in tests and evidence. A value being revoked does not
   make it suitable for Git history, Actions output, or an uploaded artifact.

## Contract and security changes

Hormuz has versioned policy, evidence, request-attempt, custody, and health
contracts. A public or durable field must not be added, removed, renamed, or
reinterpreted without the required schema version, compatibility fixture,
migration behavior, rollback boundary, and documentation.

Changes to authentication, authorization, provider egress, redaction, secret
custody, audit retention, budgets, or tenant isolation need focused negative
tests in addition to a successful path. Explain what fails closed and what the
change still does not prove.

## Pull-request review

Complete the pull-request template. A reviewer should be able to determine:

- the one outcome being delivered;
- the evidence that proves it;
- whether a public or durable contract changed;
- whether sensitive material can enter source, logs, artifacts, images, or
  evidence;
- the exact support and production-readiness boundary after the change.

### Technical-lead merge policy

Every PR needs a technical-lead evaluation of the exact proposed commit and
its linked issue before merge. Passing CI or resolving review threads alone
is not that evaluation.

1. Read the issue's acceptance criteria, dependencies, and claim boundaries;
   distinguish a prerequisite checkpoint from final issue or release closure.
2. Review the diff and surrounding code for correctness, compatibility,
   authorization, tenant isolation, privacy, failure/retry behavior, and
   maintainability. Evaluate each finding on evidence, not severity labels alone.
3. Verify meaningful tests, required CI, and package or migration evidence
   appropriate to the change. Record skipped checks and residual risks.
4. Fix substantiated blockers, re-review the changed commit, and record the
   findings and their disposition on the PR.
5. For an owner-approved work order, merge the reviewed head once its gates
   pass; the owner's standing policy does not require another routine merge
   confirmation. Unresolved product/security decisions, expanded scope,
   missing required approvals, or failed protection gates still block merging.
6. Verify the resulting `main` commit and its checks, then reconcile linked
   issues against their actual acceptance evidence. A merged slice is not a
   completed release or evidence that its downstream features exist.

Use normal branch protections without bypass. Merge authorization does not
authorize release publication, deployment, destructive cleanup, or activation
of a conditional roadmap gate.

Hormuz does not currently require a contributor license agreement or a special
commit-signing scheme. By submitting a contribution, you agree that it may be
distributed under the repository's Apache-2.0 license.
