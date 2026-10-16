# Personal-account transfer preparation

This branch stages the destination `mehrdadxzaker/hormuz`. Keep it unmerged
until a separately approved repository transfer: its public install commands,
support links, plugin metadata, and governance manifest intentionally name the
future destination. Preparing this branch changes no live ownership, rules,
App settings, package ownership, deployment, or billing.

## Changes prepared here

- Public install, support, issue-template, and vulnerability-reporting links
  use `mehrdadxzaker/hormuz`. Portable and Claude plugin metadata name the
  personal owner; local marketplace sources and shared skill paths stay local.
- The two tag-creation rule templates use the specific `User` actor
  `157386627` (`mehrdadxzaker`) rather than `OrganizationAdmin`, which GitHub
  does not support for personal repositories. Their creation restrictions and
  tag patterns are unchanged. Main-branch checks and version-tag immutability
  stay intact. See the [rules REST API](https://docs.github.com/en/rest/repos/rules).
- A [website companion patch](migrations/usehormuz-personal-checkout.patch)
  prepares the separate canonical publication repository. It is stored here
  for review, not applied to or published from that repository.

Historical issue references, evidence, attribution, and retired release assets
keep their original identity. In particular, an owner rename does not establish
that `ghcr.io/mehrdadxzaker/hormuz` exists. Keep existing
`ghcr.io/xpounder-com/hormuz` image references and digests until package access
has been explicitly checked. GitHub notes that package transfer/link behavior
depends on the registry in its [repository transfer guide](https://docs.github.com/en/repositories/creating-and-managing-repositories/transferring-a-repository).

## Website companion

The patch targets `usehormuz/usehormuz.github.io` main commit
`d8193b5b1be8094b9df56afd20e85fc545d8f9ab`. Recheck that base before applying:

```sh
git apply --check /absolute/path/to/usehormuz-personal-checkout.patch
git apply /absolute/path/to/usehormuz-personal-checkout.patch
node --test scripts/verification.test.mjs
```

It changes the checked-out repository to `mehrdadxzaker/hormuz` through a
validated workflow output while preserving source revision
`46449d2a3f05de09d7d0a18c9688e189a6c3a68e`. That revision still contains the
website; current router main does not. The published `site-source.json`
retains its original `Xpounder-com/hormuz` identity to match the snapshot's
embedded links and installation examples. Existing exact-pin, route, ownership,
and deployment checks are preserved. All 13 mocked publication tests passed
against the patched configuration; a live build and deployment must still be
verified during cutover.

Do not reuse the old `Xpounder-com/hormuz` slug: GitHub warns that doing so
removes the repository redirect. The canonical Pages repository and its
`usehormuz.github.io` domain stay where they are. This patch does not change
Pages ownership, DNS, Search Console verification, or the source snapshot.

The audit also found a legacy repository Pages site under Xpounder. GitHub
does not redirect transferred Pages sites. Inventory links to
`https://xpounder-com.github.io/hormuz/` and plan their redirect or retirement
separately; the canonical `usehormuz.github.io` publication does not prove those
legacy URLs will survive. Do not recreate the transferred repository slug to
host a redirect, because that would remove Git repository redirects.

## Later cutover

These are manual instructions for the separately approved maintenance window:

1. Confirm the destination name is available, admin access works, and open PRs
   and required checks have a stable head. Schedule a quiet period between
   releases. Pause separately deployed automation that depends on the old owner
   and record its current configuration. Retain Xpounder; do not delete the org.
2. Transfer the public repository, then verify its ID is still `1335333868`.
   Refresh this draft against the transferred main branch, review its exact
   diff, and merge only with passing required checks.
3. Recheck the actual GitHub rules after transfer. Update the two existing
   tag-creation rules with the prepared personal-user templates, preserving
   IDs and the immutable-tag/main protections. In the October 16 audit the
   creation rule IDs were `21746107` (candidate) and `21331448` (version).
   Confirm the owner is still user ID `157386627` before applying anything.
   Checked-in JSON alone does not update live rules.
4. Apply the companion patch in a separate publication PR after the destination
   exists. Keep the source SHA fixed, run its build and tests, then verify the
   deployed site and repository redirect. Publishing that companion is a
   separate action, not part of this preparation.
5. Update local clone remotes, installed plugin marketplace sources, and any
   external integrations that bind an owner name. Recheck token grants and
   installation access; retained secrets and webhooks do not prove that their
   external permissions still work. Requalify paused automation before resuming.
   The final October 16 repository-level and environment credential inventory
   is retained in the private cutover audit. Current main contains only
   non-deployment CI and requires none of those credentials. Preserve existing
   credentials without testing their values or restoring retired release
   workflows or Azure automation as part of this transfer.

GitHub Free supports rulesets for this **public** repository. The plan decision
for any private controller belongs to that repository's cutover review; no
upgrade is needed merely to prepare this public change. See
[ruleset availability](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-rulesets/about-rulesets).
