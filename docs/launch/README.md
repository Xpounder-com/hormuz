# Hormuz launch package

> Archived v0.1.3/v1.0 recruitment material. Issue #110 is closed as
> superseded. Do not publish or use these drafts for v1.2.0 onboarding; the
> current boundary is documented in the root README and issue #307.

This directory contains the evidence-grounded tester-recruitment launch package
for the Hormuz v0.1.3 public open-source alpha. Every public asset is currently
marked **DRAFT — DO NOT PUBLISH**. Hormuz was not production-ready under this
package, and its historical external onboarding validation ended at 0/5
independent completions.

The machine-readable source of truth for this archived package is
[claims-v2.json](claims-v2.json). Its release identity, issue #110 status, 0/5
count, and `public_recruitment` fields are frozen compatibility fixtures; they
do not describe the current release or issue tracker. It binds each historical
public claim to repository evidence, distinguishes implemented or verified
alpha behavior from roadmap statements and nonclaims, lists the gates that
applied to that announcement, and records its public tester and
installation-report calls to action.

## Assets

- [Landing-page copy](LANDING_PAGE.md)
- [Terminal demonstration](TERMINAL_DEMO.md)
- [Architecture and security story](ARCHITECTURE_AND_SECURITY.md)
- [Technical article](TECHNICAL_ARTICLE.md)
- [X, LinkedIn, and Show HN drafts](SOCIAL_AND_SHOW_HN.md)
- [Human-controlled conversion and launch analytics](CONVERSION_AND_ANALYTICS.md)

## Verify the draft

```bash
python tools/verify_launch_assets.py
python -m unittest -v tests.test_launch_assets
```

The verifier is intentionally successful for a complete draft while returning
`"publishable": false`. It rejects unknown claims, missing evidence paths,
unsupported claim classes, unapproved template tokens, omitted safety labels,
and analytics drift.

## Historical publication sequence — do not execute

1. Reverify the closed disclosure, community, client/provider, repository,
   signed-OCI, and bounded deployment-reference gates listed in the manifest.
2. Confirm issue #110 remained open, report the external tester count honestly
   as 0/5, and do not count internal, maintainer-assisted, or synthetic runs.
   Public testing is self-service; evidence submission into the aggregate is
   invitation-only through a separately agreed private channel.
3. Review the final copy against the exact release evidence, update the
   manifest to the approved publication state in a focused pull request, and
   record owner approval.
4. Only then publish the bounded tester-recruitment landing page, article,
   social posts, or Show HN entry with the phrases **public alpha**, **not
   production-ready**, and **external onboarding validation pending**.
5. The plan would have continued issue #110 after publication until five
   independent completions, a returning user, and resolved plus independently
   retested blockers were proven.
6. Closing #110 was a prerequisite for validated-onboarding, beyond-alpha, or
   stronger commercial-readiness claims under that historical protocol.

The package does not automate prospect selection, outreach, replies,
qualification, pricing, proposals, publication, or tenant-data lifecycle
operations. Those remain human-owned decisions or future product work.
