# Hormuz website

Static Next.js export for **https://usehormuz.github.io/**. This repository
remains the authoritative website source; the dedicated
[`usehormuz/usehormuz.github.io`](https://github.com/usehormuz/usehormuz.github.io)
repository pins a reviewed source commit for publication.
The primary journey serves small and medium engineering, agency, and IT teams
using existing supported agents: start with free software, choose Cloud when
hosting is needed, or discuss a managed site for defined operating help. The
home page and `/plans/` make the three paths explicit; managed sites include
their associated Cloud workspace. Application, traffic, retention, capacity,
monitoring coverage, and activation are confirmed before paid checkout. The
budget and pace controls, `/evidence/`, and `/work/` remain part of the journey. The
primary `/demo/` shows the actual synthetic AI Work recording, with compaction
and policy mechanics in technical disclosures. Pricing, appliance
reservations, scoped onboarding, cancellation, and managed-site terms remain
reachable without changing their approved prices.
The single price catalog is `lib/pricing.json`: software $0, Cloud
$49.99/workspace/month, appliance $999, appliance with scoped onboarding $1,499,
and managed site $499/site/month.
Appliances are coming soon, with rollout planned for early 2027. The $49
one-time reservation deposit per appliance is fully refundable and credited
toward the purchase. Cancel anytime before fulfillment for a full refund.
All paid offers have direct Stripe checkout buttons on **Pricing & pay**,
the service cards, and the matching inquiry selection. Customers can pay without
submitting an inquiry. Cloud and managed-site billing starts at checkout;
scope, capacity, and activation must be confirmed before subscribing. Full-price
appliance buttons are for confirmed orders without a reservation deposit;
reserved buyers pay their specific Stripe balance invoice. Hardware delivery
requires qualification.
The static site does not provision accounts or verify payments.
Superseded checkout links are removed; inquiries use Formspree with an email fallback.
Managed sites include the associated Cloud workspace and one hour of remote
assistance per billing month, with a response within two business days.
See `marketing/COMMERCIAL_SETUP.md` for the support boundaries and activation gates.

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

For companion-widget changes, run the rendered regression check against the
built preview with an already-installed Playwright runtime and browser binaries:

```sh
node tests/companion.browser.mjs http://127.0.0.1:3100
```

If Playwright lives outside this package, set `PLAYWRIGHT_MODULE` to its module
specifier or absolute entry-point path. `COMPANION_BROWSERS=chrome,webkit` uses
installed Google Chrome and WebKit; the default is `chromium,webkit`. The check
exercises pointer previews without focus changes, Escape dismissal, visible touch
labels, desktop/phone layouts, metric-to-settings navigation, size controls,
session state, keyboard focus, folding from both entry points, and dismissal of
the optional privacy prompt without saving consent. It sends no provider calls,
uses QA-excluded page URLs, and does not install dependencies or publish a site.

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
The artifact contains the public `/site-source.json` pin. The validated revision
is passed as `NEXT_PUBLIC_HORMUZ_SOURCE_REVISION` to both build and export
verification; source-document links use that same immutable commit. Local builds
without this variable use main, while invalid or mutable values fail. Stable
download version labels are independent of the website source pin. A separate read-only
CI job verifies the live pin, the complete route inventory, metadata, and five downloads after
deployment, without giving verification code Pages or OIDC write permissions.
Update its source pin through a reviewed PR after the corresponding product
source change passes review and CI; source changes do not silently republish the
canonical website.

For the initial migration, publish and verify the new root site from the reviewed
migration commit **before** merging the product change that enables old-address
redirects. Check all known routes, recordings, contact draft behavior, PDFs, PPTX,
mobile navigation, and metadata. Then merge through the protected workflow,
verify the old route redirects and downloads, and update inbound repository links.
An unavailable organization or unverified target is a cutover blocker, not a
reason to replace the working site early.

The legacy export preserves known route fragments, drops query strings (which
can contain private text), suppresses the old URL as a referrer, and provides a
manual link plus an immediate no-JavaScript refresh fallback. The immediate fallback follows
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

The contact form submits to the owner's Hormuz Formspree form and acknowledges
receipt only after a positive service response. Entries are not saved to browser
storage. Optional campaign attribution is unchecked by default. X Ads measurement
loads only after visitor consent, only on the canonical origin, and never in
Safari or browsers on iPhone and iPad. Its lead event
follows acknowledged submission and contains no form entries. See
`marketing/MEASUREMENT.md` for exact events, consent, and verification boundaries.
GitHub Pages still receives requested URLs and query strings as the hosting
provider; optional email attribution does not prevent that initial request.

Google Analytics has a separate opt-in choice; existing X permission never enables
it. Public GA4 and Search Console identifiers live in
`lib/measurement-config.mjs`; empty values disable the corresponding setup.
The Google tag loads only on known canonical production routes after consent,
with advertising storage, Google signals, and personalization disabled. Website
analytics can operate in Safari without loading X. Keep enhanced measurement off
in the GA4 web stream so automatic form, search, and outbound-link events do not
expand the explicit event contract. Local previews and `?qa=1` never report.

Create a tagged X destination with:

```sh
npm run campaign:url -- --campaign=hormuz_technical_us_2026_09 --content=technical_demo_01
```

Use one stable campaign label and a distinct content label for each ad. These are
public labels, never personal information or credentials. The generator accepts
only canonical website routes and bounded labels. See the measurement guide for
event definitions and account-side activation checks.

Figma handoff: https://www.figma.com/design/Ax2HWqdWzVnMANEOmB5Z4z
Public author: Mehrdad Zaker · mehrdadz@neuralint.io.

## AI Work entry and evidence

`/work/` is a static entry page, not a cross-origin bearer-token application.
Set `HORMUZ_DASHBOARD_ORIGIN` at build time to the verified HTTPS gateway origin;
the entry opens its authenticated `/work` route. The legacy
`NEXT_PUBLIC_HORMUZ_DASHBOARD_ORIGIN` is still accepted. Unsafe paths, credentials,
query strings, static Pages origins, and conflicting settings fail the build.
When no destination is configured, the page explains how to use an existing
organization gateway and offers a workflow discussion. Never put tokens or
private paths or workspace credentials in these public settings.

`/evidence/` presents real provider-free executions with synthetic inputs,
original transcripts, source provenance, and qualification boundaries. It also
plays the actual authenticated journey against local synthetic identity, provider,
Stripe and signed GitHub fixtures. The current recording passed 12 checks with
three simulated provider responses and zero external network calls or real
payments. Its estimate is calculated from captured fixture usage and configured
synthetic rates; no amount is seeded. These
are functional control demonstrations, not customer savings or completion-time
results. New AI Work proof must come from actual recorded runtime execution;
never populate this page with invented output. The public AI Work receipt at
`public/downloads/ai-work-proof.json` must be byte-for-byte identical to the
executed `docs/evidence/ai-work-functional/receipt.json`; recopy it after any
regenerated execution before building. It records synthetic configured estimates
and local timings, not customer savings or production model quality.

Both execution receipts identify the same 27 gateway, billing, identity, CLI,
UI and runner files by SHA-256. Website tests enforce exact current source hashes
and byte-identical public recording, poster and browser-check copies. Reproduce
the browser journey from the source root with installed test dependencies,
Playwright and Chrome:

```sh
HORMUZ_QA_PYTHON=/path/to/project-venv/bin/python \
HORMUZ_QA_PLAYWRIGHT_MODULE=/path/to/playwright/index.mjs \
node website/scripts/ai-work-browser-qa.mjs
```

See [the browser validation guide](../docs/AI_WORK_BROWSER_VALIDATION.md) for
interception and qualification boundaries. Regenerate the browser run and gateway
receipt after source changes, then copy their public artifacts before building.

The cost/speed/outcome chooser on the public home page only explains the policy. Live
work creation and preference changes happen on the authenticated gateway.
Website analytics measure public evidence/entry clicks with bounded labels;
they do not report private work identities, descriptions, keys, or repositories.
