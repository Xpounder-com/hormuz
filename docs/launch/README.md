# Hormuz launch package

This directory contains the evidence-grounded launch package for the Hormuz
v0.1.1 public open-source alpha. Every public asset is currently marked
**DRAFT — DO NOT PUBLISH**. Drafting is allowed before the quiet alpha;
publication is not.

The machine-readable source of truth is [claims-v1.json](claims-v1.json). It
binds each public claim to repository evidence, distinguishes implemented or
verified alpha behavior from roadmap statements and nonclaims, lists the
release gates that must close, and keeps the two commercial calls to action
blocked on owner-approved URLs.

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

1. Close the disclosure, community, live-provider, repository, and signed-OCI
   gates listed in the manifest.
2. Complete the five-person quiet-alpha gate in issue #110 and resolve every
   installation or security blocker.
3. Supply owner-controlled URLs for the AI Governance Review and paid-pilot
   application. The destination must explain its data handling and must not
   solicit credentials, prompts, responses, or proprietary customer data.
4. Review the final copy against the exact release evidence, update the
   manifest to the approved publication state in a focused pull request, and
   record owner approval.
5. Only then publish the landing page, article, social posts, or Show HN entry.

The package does not automate prospect selection, outreach, replies,
qualification, pricing, proposals, or publication. Those remain human-owned
decisions.
