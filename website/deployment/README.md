# Dedicated root-site publication

These reviewed files bootstrap `usehormuz/usehormuz.github.io`. They are not an
additional workflow in the product repository and cannot deploy from this path.
The website source stays in `Xpounder-com/hormuz/website`.

## Repository contents

Copy `root-pages.yml` to `.github/workflows/website.yml` and
`verify-source-pin.mjs` to `scripts/verify-source-pin.mjs` in the dedicated
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
4. Verify all nine live routes, demo, contact draft, and all four buyer downloads.

For the initial migration only, the pin can reference the fully reviewed and
CI-passing migration PR head so the new site is published and verified before
the product merge switches the old project Pages to compatibility redirects.
Do not merge that cutover while the target is unavailable. Subsequent pins
normally use a reviewed main commit. A source commit is not a product release,
and website publication does not rebuild or relabel immutable product artifacts.

Roll back by restoring the last verified pin through a reviewed PR. See
[`website/README.md`](../README.md) for old-address rollback and privacy behavior.
