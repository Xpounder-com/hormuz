# Hormuz website

Static Next.js export for **https://usehormuz.github.io/**. This repository
remains the authoritative website source; the dedicated
[`usehormuz/usehormuz.github.io`](https://github.com/usehormuz/usehormuz.github.io)
repository pins a reviewed source commit for publication.
The existing Hormuz visual system is retained. This project site emphasizes
open-source documentation, demos, and community; enterprise information is
secondary and there is no checkout, hosted application, or form backend.

## Build and check

Use Node 24 (CI baseline), npm, and the committed lockfile:

```sh
cd website
npm ci --ignore-scripts --no-fund
npm test
python3 -m unittest discover -s tests -p 'test_*.py' -v
NEXT_TELEMETRY_DISABLED=1 npm run build
npm run typecheck
npm run verify
node scripts/serve-preview.mjs
```

Preview: `http://127.0.0.1:3100/`. The build output is `out/` and is
gitignored. Google-hosted Geist fonts are fetched at build time and self-hosted
in the export; visitors do not request the Google font service.

`basePath` is empty for the dedicated organization-root site. Use the shared
`sitePath` and `siteUrl` helpers for routes/assets/metadata; the origin and
route inventory live in `lib/site.mjs`.
The verifier checks every exported local link, source-document target, fragment,
canonical URL, OG image, download, and sitemap entry. It does not claim that an
external website will stay online or that search engines have indexed the site.

The root site owns `/robots.txt` and `/sitemap.xml`. Canonicals and social images
use the new origin. Neither the product repository nor its release, package,
or container identity changes with the website address.

## Deployment and rollback

`.github/workflows/website.yml` checks the root export on every PR and main push,
including changes to linked root/source documents. It then prepares compatibility
pages for the former `https://xpounder-com.github.io/hormuz/` address. Only main
can upload/deploy those redirects. Deployment retains narrowly scoped Pages
and OIDC permissions; PR builds cannot deploy. Product CI and branch protections
remain unchanged. Both repositories' Pages settings use **GitHub Actions**.

The dedicated website repository checks out an exact 40-character source commit
from this public repository, installs locked dependencies, and runs the website
tests, build, type check, and export verifier before publishing the root export.
It does **not** run `prepare:legacy`. No cross-repository write token is needed.
Update its source pin through a reviewed PR after the corresponding product
source change passes review and CI; source changes do not silently republish the
canonical website.

For the initial migration, publish and verify the new root site from the reviewed
migration commit **before** merging the product change that enables old-address
redirects. Check all nine routes, recordings, contact draft behavior, PDFs, PPTX,
mobile navigation, and metadata. Then merge through the protected workflow,
verify the old route redirects and downloads, and update inbound repository links.
An unavailable organization or unverified target is a cutover blocker, not a
reason to replace the working site early.

The legacy export preserves known route fragments, drops query strings (which
can contain private text), and provides a manual link plus an immediate
no-JavaScript refresh fallback. The immediate fallback follows
[Google's redirect guidance](https://developers.google.com/search/docs/crawling-indexing/301-redirects).
Static Pages cannot return custom HTTP 301 responses; these
are HTML redirects with canonical links. Unknown old routes show a safe link
to the new home instead of forwarding arbitrary destinations. Existing asset
and download URLs remain available. To check this export locally after a build:

```sh
npm run verify
npm run prepare:legacy
node scripts/serve-preview.mjs --legacy
```

This preview uses `http://127.0.0.1:3100/hormuz/`. Rebuild before previewing or
publishing the canonical root site again; `prepare:legacy` modifies only the
gitignored `out/` artifact, never source files or release artifacts.

To roll back the root site, restore a previously verified source pin through a
reviewed PR in the website repository. To restore the old full project site,
revert the migration through a reviewed product PR and its existing workflow.
Do not rewrite main or bypass protections. The original
Sites deployment is retained until the replacement is verified; this repository
does not silently delete it or mutate the immutable product release artifacts.

GitHub Pages is intended for static project sites; see its [usage limits](https://docs.github.com/en/pages/getting-started-with-github-pages/github-pages-limits).
If commercial transactions or a hosted enterprise service become the site's
primary purpose, select appropriate hosting before adding those features.

## Recordings and generated documents

With Hormuz dependencies installed, run `python scripts/record-demo.py` from
this directory (or the repository equivalent). It invokes the real CLI in a
credential-allowlisted child environment and records unmodified output/timings,
exit code, Python version, and source revision. The recorder enforces its deadline
while collecting output, reaps timed-out children, and checks staged, unstaged,
and untracked runtime source against HEAD before and after each run.
`export-demo-evidence.py` uses
a separate synthetic run and preserves only schema/content-checked events.
Both need permission to bind loopback ports. These scripts never create human
onboarding-study evidence.

Do not re-record casually: source revisions, digests, transcripts, and claim
boundaries must remain consistent. Re-run the tests and inspect the transcript
before publishing a replacement.

Buyer PDFs use `scripts/build-buyer-pdfs.py` with ReportLab. The editable deck
uses `scripts/build-buyer-deck.mjs` and the bundled `@oai/artifact-tool` runtime.
Set `RUNTIME_NODE`, `RUNTIME_NODE_MODULES`, and `RUNTIME_BIN_DIR` to paths supplied
by Codex's workspace-dependency loader when regenerating the deck. Generated
downloads are committed so a Pages build needs neither document tooling nor
access to Figma. Render and inspect every generated document before committing.

## Privacy and measurement

The contact form produces a local, reviewable email draft and a copy fallback.
It never claims submission/delivery. No form field is sent automatically or
saved to browser storage. Optional campaign attribution is unchecked by default.
There is no analytics SDK or ad pixel. See `marketing/MEASUREMENT.md` for the
manual measurement baseline and decisions required before enabling tracking.
GitHub Pages still receives requested URLs and query strings as the hosting
provider; optional email attribution does not prevent that initial request.

Figma handoff: https://www.figma.com/design/Ax2HWqdWzVnMANEOmB5Z4z
Public author: Mehrdad Zaker · zaker.mehrdad@gmail.com.
