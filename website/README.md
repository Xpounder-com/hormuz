# Hormuz project website

Static Next.js export deployed at **https://xpounder-com.github.io/hormuz/**.
The existing Hormuz visual system is retained. This project site emphasizes
open-source documentation, demos, and community; enterprise information is
secondary and there is no checkout, hosted application, or form backend.

## Build and check

Use Node 24 (CI baseline), npm, and the committed lockfile:

```sh
cd website
npm ci --ignore-scripts --no-fund
npm test
NEXT_TELEMETRY_DISABLED=1 npm run build
npm run typecheck
npm run verify
node scripts/serve-preview.mjs
```

Preview: `http://127.0.0.1:3100/hormuz/`. The build output is `out/` and is
gitignored. Google-hosted Geist fonts are fetched at build time and self-hosted
in the export; visitors do not request the Google font service.

`basePath` is fixed at `/hormuz`; **native anchors are not automatically
rewritten by Next.js**. Use `sitePath` and `siteUrl` for routes/assets/metadata.
The verifier checks every exported local link, source-document target, fragment,
canonical URL, OG image, download, and sitemap entry. It does not claim that an
external website will stay online or that search engines have indexed the site.

`/hormuz/robots.txt` is supplied for portability, but a project cannot control
the origin-root `/robots.txt` with this repository. Per-page robot metadata and
the project sitemap are present. No organization-root repository was created.

## Deployment and rollback

`.github/workflows/website.yml` builds on relevant PRs and main pushes. Only
main can upload/deploy a Pages artifact. Deployment has narrowly scoped Pages
and OIDC permissions; PR builds cannot deploy. Product CI and branch protections
remain unchanged. GitHub repository Pages settings must use **GitHub Actions**.

To update: open a normal PR, review claims and rendered pages, wait for all
required checks plus Website checks, then merge through the permitted workflow.
The main-branch workflow publishes that validated static export. Verify the live
home, demo, Docs, contact, and downloads after deployment before changing inbound
links or announcing the update.

To roll back: use a reviewed revert PR for the website changes and let the same
workflow deploy it. Do not rewrite main or bypass protections. The original
Sites deployment is retained until the replacement is verified; this repository
does not silently delete it or mutate the immutable product release artifacts.

GitHub Pages is intended for static project sites; see its [usage limits](https://docs.github.com/en/pages/getting-started-with-github-pages/github-pages-limits).
If commercial transactions or a hosted enterprise service become the site's
primary purpose, select appropriate hosting before adding those features.

## Recordings and generated documents

With Hormuz dependencies installed, run `python scripts/record-demo.py` from
this directory (or the repository equivalent). It invokes the real CLI in a
credential-allowlisted child environment and records unmodified output/timings,
exit code, Python version, and source revision. `export-demo-evidence.py` uses
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

Figma handoff: https://www.figma.com/design/Ax2HWqdWzVnMANEOmB5Z4z
Public author: Mehrdad Zaker · zaker.mehrdad@gmail.com.
