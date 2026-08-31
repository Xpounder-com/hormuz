# Website publication checks

Local checks completed August 30, 2026 on the website publication branch,
based on main `932024e5bb5d9250a20ef7c815bac2487746d086`. This is website and
marketing-packet verification, not a product-release qualification record.

## Automated results

| Check | Observed result |
| --- | --- |
| `npm test` | 12 passed: project paths, contact encoding/validation, optional attribution, recording provenance, synthetic evidence, claims, Markdown links/fragments, workflow coverage, privacy, and sticky-ancestor regression |
| `python3 -m unittest discover -s tests -p 'test_*.py' -v` in `website/` | 7 passed: recorder timing, silent/partial-output deadlines and child reaping, staged/unstaged/untracked/ignored source rejection, and clean-source cases |
| `NEXT_TELEMETRY_DISABLED=1 npm run build` | Static export succeeded |
| `npm run typecheck` | Passed |
| `npm run verify` | 9 pages, 342 local link occurrences, 17 source-document targets passed; canonical URLs, metadata, sitemap, and downloads present |
| `npm audit --audit-level=high` | 0 vulnerabilities reported for the locked dependencies at check time |
| `python -m unittest -q` | 795 tests ran; OK with 64 prerequisite-dependent skips, including live/platform checks; loopback-listener permission was required |
| `python tools/verify_public_community_paths.py` | Passed, including unchanged support-matrix boundaries |
| `python tools/verify_repository_governance.py` | Passed; new Pages publication boundary regression-tested |
| `python tools/verify_launch_assets.py` | `passed_draft`; historical launch packet remains non-publishable |
| Final PPTX overflow check | Passed; no canvas overflow detected |
| `git diff --check` | Passed |

The initial restricted-sandbox test attempt could not bind the suite's local
fake servers. The final run above used loopback permission. No product-runtime
implementation or immutable release artifact was changed.

## Browser and document review

- All nine public routes were inspected at 1280px and 390px widths; each had
  one primary heading and no document-wide horizontal overflow.
- Mobile menu navigation worked. The terminal recording played to the actual
  six-PASS transcript and exposed replay and full-output controls.
- Empty required fields prevented draft creation. A synthetic inquiry produced
  the correct recipient, subject, and body; copy feedback explicitly said
  nothing was sent. Editing an input cleared the stale draft.
- Campaign attribution was absent by default and appeared in the draft only
  after selecting the visible opt-in checkbox. No email was sent.
- All five PDF pages and all seven slides of the exported PPTX were rendered
  and individually reviewed. The editable Figma handoff was visually reviewed.
- After the review corrections, the documentation sidebar remained at 28px
  from the viewport top after section navigation at a scroll offset of 1833px.
  The shared main element clips decoration without becoming a scroll container.
- The hardened recorder ran both real demos successfully into a disposable
  directory. Public recording bytes were left unchanged; the product source
  tree still matches the original recorded revision.

Review corrections also require the Pages workflow to remain present, run its
checks for every PR/main push (including linked-document changes), and clarify
that the host receives query strings even when application analytics is off.

## Deployment handoff

The PR and GitHub Actions run are the source of truth for the deployed commit,
required remote checks, and Pages status. After publication, verify the live
home, demo, documentation, contact flow, and all four downloads anonymously.
Only then update the repository homepage URL. Keep the old site recoverable.

Not established here: mailbox delivery, independent human onboarding, customer
outcomes, paid demand, search indexing, full accessibility certification,
production deployment fitness, or a service/SLA commitment. Tracking stays off.

## Root-hostname migration — August 31, 2026

Prepared on `mehrdad/usehormuz-pages`. These are local migration checks, not a
claim that `usehormuz.github.io` is registered, deployed, or indexed. The original
August 30 evidence above remains historical and has not been replaced.

| Check | Observed result |
| --- | --- |
| `npm test` | 19 passed, including fixed source pins, root preview, all nine compatibility redirects, fragment-only forwarding, preserved assets, and existing privacy/provenance checks |
| Website Python tests | 9 passed, including stale PDF URL-annotation and PPTX slide/notes checks without adding document tooling to CI |
| Static build, type check, and export verification | Passed: 9 routes, 342 local link occurrences, 17 source-document targets, root robots/sitemap, new canonicals and social-image URLs |
| `npm audit --audit-level=high` | 0 vulnerabilities reported at check time |
| Repository governance validator and tests | Passed; 45 governance tests; existing privileged Pages job and release boundaries unchanged |
| Buyer PDFs | All 5 pages rendered and individually reviewed; correct author/date and 26 link annotations checked with a PDF parser |
| Editable buyer deck | All 7 source and final slides individually reviewed; imported and edited existing objects; fidelity and overflow checks passed; original theme part byte-identical; only website references and revision dates changed |
| `git diff --check` | Passed |

In the browser, all nine local root routes had one primary heading, the expected
new canonical URL, no broken images, and no document-wide overflow at 1280px.
The root quickstart link reached `/docs/#quickstart`. The recording loaded at
the root path, played through all six PASS lines, and exposed Replay. Empty
required contact fields produced no draft; a synthetic inquiry produced the
correct recipient and reviewable body; editing a field cleared the stale draft.
No email was opened or sent, and no analytics or tracking was added.

The local legacy preview forwarded a synthetic `/hormuz/docs/` bookmark to
`https://usehormuz.github.io/docs/#quickstart` with its fragment retained and
query string removed. All 14 public files (including `.nojekyll`) stayed
byte-identical between the root export and the compatibility export. The new
remote hostname still returned GitHub's no-site 404 at this check; this proves
the forwarding behavior, not successful publication. The old live homepage
still served its original canonical URL anonymously.

The migration preserves the existing design, project identity, recorded-demo
bytes, license, maturity statements, and immutable release artifacts. Figma
requires no redesign for this address-only change. The presentation/PDF editing
workflow was used to preserve the existing buyer materials while updating links.

Publication remains gated on the approved organization being created, protected
remote checks, and live verification of the new root site (including mobile
navigation and downloads) before merging the old-address redirect cutover.
Old public URLs have not been changed by these local checks. Deployment evidence
and the exact source pin belong in the migration PR and publication repository.
## Publication safeguards — 2026-08-31

Follow-up review before the root-site publication added an explicit
`no-referrer` policy to the compatibility pages. The destination remains fixed,
fragments are retained, and query strings are not forwarded.

The dedicated publication workflow now includes its validated public source pin
in the artifact and uses a separate read-only post-deploy job to verify the live
pin, all nine canonical routes, robots/sitemap metadata, and four download
signatures. The privileged Pages job still executes only the pinned deployment
action; verification receives no Pages or OIDC write permission.

Local checks after these changes: 23 Node tests and 9 Python tests passed;
static build, type check, and export verification passed (9 pages, 342 local
links, 17 source targets). Workflow YAML parsing and permission-boundary checks
passed. Negative cases cover stale/invalid source pins, redirects, network
failure, missing routes, incorrect canonicals, HTML in place of downloads, and
incomplete metadata. These are local checks, not a claim that the new host is
already live. Live browser and document-layout QA remain separate gates.
