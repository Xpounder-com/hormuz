# Hormuz launch package

This directory contains the evidence-grounded tester-recruitment launch package
for the Hormuz v0.1.3 public open-source alpha. Every public asset is currently
marked **DRAFT — DO NOT PUBLISH** until the owner completes the final
post-change copy review. Hormuz is not production-ready, and external
onboarding validation is pending at 0/5 independent completions.

The machine-readable source of truth is [claims-v2.json](claims-v2.json). It
binds each public claim to repository evidence, distinguishes implemented or
verified alpha behavior from roadmap statements and nonclaims, lists the
release gates that must close, and binds the announcement to public tester and
installation-report calls to action. Commercial conversion remains deferred
until onboarding is independently validated.

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

## Publication sequence

1. Reverify the closed disclosure, community, client/provider, repository,
   signed-OCI, and bounded deployment-reference gates listed in the manifest.
2. Confirm issue #110 remains open, report the external tester count honestly
   as 0/5, and do not count internal, maintainer-assisted, or synthetic runs.
   Public testing is self-service; evidence submission into the aggregate is
   invitation-only through a separately agreed private channel.
3. Review the final copy against the exact release evidence, update the
   manifest to the approved publication state in a focused pull request, and
   record owner approval.
4. Only then publish the bounded tester-recruitment landing page, article,
   social posts, or Show HN entry with the phrases **public alpha**, **not
   production-ready**, and **external onboarding validation pending**.
5. Continue issue #110 after publication until five independent completions, a
   returning user, and resolved plus independently retested blockers are proven.
6. Treat closing #110 as a prerequisite for validated-onboarding, beyond-alpha,
   or stronger commercial-readiness claims—not for the initial announcement.

The package does not automate prospect selection, outreach, replies,
qualification, pricing, proposals, publication, or tenant-data lifecycle
operations. Those remain human-owned decisions or future product work.
