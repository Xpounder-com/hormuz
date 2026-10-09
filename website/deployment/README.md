# Dedicated root-site publication

These reviewed files bootstrap `usehormuz/usehormuz.github.io`. They are not an
additional workflow in the product repository and cannot deploy from this path.
The website source stays in `Xpounder-com/hormuz/website`.

## Repository contents

Copy `root-pages.yml` to `.github/workflows/website.yml`, plus
`verify-source-pin.mjs` and `verify-live-site.mjs` to `scripts/` in the dedicated
website repository. Add `site-source.json` with exactly these fields:

```json
{
  "repository": "Xpounder-com/hormuz",
  "revision": "<full 40-character reviewed source commit>"
}
```

The placeholder above is documentation, not a deployable revision. Set a real
commit after review and required CI. The validator rejects movable branches,
tags, abbreviated SHAs, alternate repositories, extra fields, and newline
injection. Existence is verified by the pinned checkout action. Review/CI
approval is a human publication gate, not something the JSON format can prove.
The validated pin is included in the public artifact as `/site-source.json`.
A separate, read-only post-deploy job verifies that pin, all sixteen canonical
routes, robots/sitemap metadata, and the five download signatures. It follows
no redirects and fails on stale source or unavailable content. Interactive
browser and document-layout QA remain separate checks.

Copy the existing Apache-2.0 license without changing product ownership. Use
GitHub Free, a public repository, Pages with GitHub Actions, and main-only Pages
deployment. Require a PR, resolved review threads, up-to-date branches, and the
`Website checks` status on future main updates; disallow force pushes and branch
deletion. Keep the deploy environment restricted to main. Do not add a cross-repo
PAT, broad write permissions, `pull_request_target`, or automatic publishing from
an unreviewed moving branch.

## Update and rollback

1. Review the source change and its exact commit's product CI/Website checks.
2. Open a normal PR that updates `site-source.json` in the publication repository.
3. Verify its Website checks and reviewed pin; merge through branch protection.
4. Verify all sixteen live routes, demo, contact draft, and all five buyer downloads.

For the initial migration only, the pin can reference the fully reviewed and
CI-passing migration PR head so the new site is published and verified before
the product merge switches the old project Pages to compatibility redirects.
Do not merge that cutover while the target is unavailable. Subsequent pins
normally use a reviewed main commit. A source commit is not a product release,
and website publication does not rebuild or relabel immutable product artifacts.

Roll back by restoring the last verified pin through a reviewed PR. See
[`website/README.md`](../README.md) for old-address rollback and privacy behavior.

## Hosted gateway entry

Set the publication repository variable `HORMUZ_DASHBOARD_ORIGIN` only to the
qualified HTTPS gateway origin. The static `/work/` page links to its
authenticated `/work` experience; it does not host the dashboard or store bearer
credentials. Without this variable the page explains the qualification path.
Consent is required before bounded campaign labels are passed to the gateway
confirmation page. Identity, checkout, provider access, signed outcomes, and
recovery qualification remain gateway responsibilities. A successful Pages
build proves publication of static content, not a live paid workspace. The read-only live verifier also checks the actual work
recording, execution-receipt link, and configured gateway destination; it does
not probe the private gateway or its customer data.
