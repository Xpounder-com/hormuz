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
