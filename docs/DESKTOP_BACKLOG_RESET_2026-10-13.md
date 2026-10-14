# Hormuz backlog reconciliation — 2026-10-13

Scope: all 32 open issues and two open PRs in `Xpounder-com/hormuz` at this
audit boundary, plus seven initial planning issues, eight follow-up UI,
local-data/infrastructure issues, seven focused account-cleanup children and two
Research Review issues created under
[desktop epic #479](https://github.com/Xpounder-com/hormuz/issues/479).
The subsequent local cleanup #504 is complete within its audited scope: 113
old merged checkouts and 19 test-export folders retired with exact recovery,
while unique work, retained state and release assets remain preserved. This
extends cleanup coverage without beginning application implementation.
Adjacent repositories, user-owned dirty checkouts, shipped products and
implementation branches are not deleted or reset by this sweep.

Source baseline is `46449d2a3f05de09d7d0a18c9688e189a6c3a68e`.
Published v1.8.0 is the notarized Apple Silicon Mac release; Windows is an
unsupported preview. Current main contains the AI Work 1.9.0 source candidate.
Old native v1.4–v1.9 and portfolio v1.3 milestone names were planning sequences,
not acceptance of the similarly numbered published releases.

## Disposition of the complete original open set

| Issue | Track | Reconciled disposition |
| --- | --- | --- |
| #9 | Enterprise | Open conditional production-readiness/independent review gate; scope does not include every future desktop/hosted issue. |
| #10 | Enterprise | Open conditional organizational DLP gate; existing egress controls still apply to exposed managed paths. |
| #11 | Enterprise | Open parent; completed bounded reference children #100/#101/#102/#103/#104/#105/#108 reconciled; #106 not planned and #107 still open. |
| #13 | Enterprise | Open conditional generic browser/session qualification; no organization enrollment requirement for local setup. |
| #107 | Enterprise | Open deferred OCI/deployment/database upgrade gate; separate from desktop updates. |
| #214 | Portfolio | Open final-candidate/transition gate; future program release TBD; exact historical plans retained. |
| #219 | Portfolio | Open trusted GitHub ingress; not a desktop Auto dependency. |
| #220 | Portfolio | Open trusted Linear ingress; accepted context design is not live connector proof. |
| #225 | Portfolio | Open independent organization pilot; no completion inferred. |
| #226 | Portfolio | Open continuing epic; obsolete active v1.3 release commitment removed. |
| #307 | Validation | Open exact v1.2 independent onboarding study; artifact and dated 0/1 counts preserved. |
| #329 | Desktop history | Superseded by #479, closed not planned; accepted shared contracts retained. |
| #331 | Desktop history | Separate native Windows shell superseded by #482/#340, closed not planned. |
| #337 | Desktop | Re-scoped event-driven refresh/power integration; source checkpoint retained. |
| #338 | Desktop | Early project/bar/context/Auto UI fixtures feed #492; final runtime integration follows later. Old source evidence retained. |
| #339 | Desktop | Re-scoped app/backend/model ownership and recovery; source foundations retained. |
| #340 | Desktop | Exact installed project/history/context/setup/Auto journeys for every declared Windows/Linux/Mac row; Windows evidence cannot qualify the other platforms. |
| #341 | Runtime | Existing external-agent relay qualification retained; conditional dependency for an exposed agent path, not llama.cpp model hosting. |
| #342 | Runtime | Optional measured Rust optimizer migration deferred; preserve the shipping helper. |
| #343 | Desktop history | Separate GTK UI superseded by #482/#340, closed not planned. |
| #344 | Desktop history | Swift C UI bridge outcome superseded by #480/#482, closed not planned; narrow API/custody/ownership requirements transferred. |
| #345 | Desktop history | Swift UI migration outcome superseded by #482/#340/#346, closed not planned; shipped Mac coexistence preserved. |
| #346 | Desktop | Packages, trusted updates, project-data/settings migration and rollback. |
| #347 | Desktop | Re-scoped bounded Electron process-tree and separate UI/inference footprint; obsolete exact-one-process rule removed. |
| #369 | Validation | Open; merged implementation/protected checks reconciled, independent technical-lead review still outstanding. |
| #437 | Hosted | Open, deferred optional managed product; no desktop activation dependency. Historical commercial proposal requires reconciliation. |
| #438 | Hosted | Open, deferred hosted workspace/admin activation; local projects need no server tenant. |
| #439 | Hosted | Open, deferred multi-tenant custody/routing; local OS custody remains required separately. |
| #440 | Hosted | Open, deferred hosted production API gate; direct desktop provider conformance stays active. |
| #441 | Hosted | Open, deferred subscription/entitlement implementation; existing payment obligations preserved. |
| #442 | Hosted | Open, deferred meter/overage proposal; old $99 terms are historical, typing preview is not billable. |
| #443 | Hosted | Open, deferred serving-capacity/recovery work; independent cleanup #494–#501 evaluates and executes bounded authorized resource retirement. |

Original acceptance and evidence are retained in issue bodies. Superseded
outcomes are **not planned**, never relabeled completed. Re-scoped desktop
issues have new active dependency/acceptance sections with old native sections
explicitly archived. Existing completed #330/#332–#336 remain completed within
their accepted source/reference scopes; this sweep does not reopen them.

## New work and milestone policy

README cleanup is included in #479's planning/documentation changes: lead with
the upcoming desktop project workspace, bar, model setup/Auto and Review; label
available versus planned capabilities, preserve existing runnable quickstart
anchors and link detailed gateway/operational guides. The draft documentation
rewrite does not close a UI/runtime gate or publish a desktop release.

The user then confirmed a full project workspace with chats, files, instructions
and model/Auto preferences. #481 now owns the whole glossy visual system. Six
UI issues add #487 navigation/bar handoff, #488 projects/chats/history/search,
#489 files/instructions/context, #490 onboarding/model library/connections,
#491 inherited preferences/Auto, and #492 integrated simulated UI approval.

#480 now has two checkpoints: A records concrete
[UI-facing backend boundaries](DESKTOP_BACKEND_BOUNDARIES.md) before UI work;
B freezes actual local host/data/support/migration acceptance after #492. A
defines saved/Temporary lifetime, imported text-copy/context scope, privacy/cap
inheritance, chat pins, Send/recovery, bounded setup and Research Review without
implementing or completing #480. No new issue or UI/backend dependency cycle is
required. After #492, #480 B freezes the runtime contract, then #482
integrates the approved UI. #493 local project persistence/context, #483 local
engine setup and #485 governed paid baselines advance in parallel. #484 then
integrates real pure Auto and explicit model intent. #502 adds bounded simulated
Research Review journeys before #492; optional #503 real orchestration follows
qualified single-response #493/#483/#485/#484 contracts and is required only
when real Review is exposed. Failed reviewers, disagreement and stale revision
approvals remain visible; model agreement does not authorize implementation.

#494–#501 form independent `track:cleanup`, milestone 16, rather than deferred
Hosted Pro delivery. The user authorized bounded cleanup of proved-unused Hormuz
scope; current operations and retained holds are recorded in the
[account ledger](DESKTOP_ACCOUNT_CLEANUP.md). UI completion does not wait for
cleanup or infer its completion from issue labels or suspended compute.
Reuse #337/#338/#339/#340/#346/#347 as integration/release gates, with separate
early and final #338 checkpoints. Adapter evidence contributes to later #340;
#340 closure is not an adapter prerequisite.

The old ephemeral-only first scope is superseded by disclosed saved local
projects and unsaved Temporary chat, with a separate content ownership,
context/export/delete/isolation contract. Original source/acceptance history
remains intact. See the [UI coverage plan](DESKTOP_UI_PLAN.md) and
[infrastructure assessment](DESKTOP_INFRASTRUCTURE_AUDIT.md),
[Research Review contract](RESEARCH_REVIEW_MODE.md) and
[account cleanup ledger](DESKTOP_ACCOUNT_CLEANUP.md).

Delivery is in named stages in [the desktop roadmap](ELECTRON_DESKTOP_ROADMAP.md).
New desktop, optional Rust and Hosted Pro milestones have version TBD. The
portfolio milestone also has future version TBD. Legacy native milestones are
marked historical and retired after their active work is moved or superseded;
milestone retirement is not platform/release acceptance.
The roadmap is sufficient to begin UI simulation. Backend work follows #492
approval and a named owner, selected finish, exact model/provider/protocol and
storage/context/time limits, with declared platform qualification rows.

## PR and evidence handling

- #458 remains an open draft native integration proposal. Stage #480 must
  inventory its useful fixes/contracts, latest exact head/review/CI and obsolete
  rendering scope before deciding how to split, retain or retire it. Its
  proposed GTK/Swift UI is not an Electron prerequisite or merged-main proof.
- #460 remains an open dependency-maintenance PR. This planning sweep does not
  review, merge or dismiss its code changes.
- #369's post-merge checkpoint records protected head/main checks at
  `8d338eb57a45eceaa89f76f950f5c3fde15684a1`; independent technical-lead
  acceptance is still open. See [the recorded checkpoint](https://github.com/Xpounder-com/hormuz/issues/369#issuecomment-5754824669).
- Closed #106 is a deferred/not-planned tenant-authority scope, not implemented
  destructive authority. The completed enterprise references do not certify
  arbitrary customer environments.

The primary local checkout was dirty and preserved. Read-only source audits used
current-main Git objects; roadmap documentation is prepared in an isolated
branch. Application/backend work remains planning and simulation; no real model
download, paid-provider request, customer release or desktop support expansion
is established.

Separately authorized cleanup on 2026-10-13 suspended the remaining active Render
pilot; all three inventoried web services are now suspended. It removed two
obsolete GitHub environment secrets and disabled the
live pilot workflow. PostgreSQL and disks remain retained; archive/export/restore
and data/custody/recovery decisions are pending. This is bounded operations evidence,
not complete account retirement, proved zero cost or replacement of paused
legacy hosted access. Original shipped artifacts, code contracts and acceptance
history remain intact; consult the cleanup ledger for remaining holds.
