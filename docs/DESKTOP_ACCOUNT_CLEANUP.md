> Dated operational evidence. Current resource state must be reverified; the active product is the [local router](ROUTER_SCOPE.md). The operational cleanup issues remain open.

# Hormuz account cleanup

Scope confirmed 2026-10-13: evaluate **all Hormuz-related accounts and resources**
and clean up those proved unused, while preserving other projects, shared
accounts, users, data, custody and payment obligations. Parent:
[#494](https://github.com/Xpounder-com/hormuz/issues/494).
The work uses `track:cleanup` and the separate
[cleanup milestone](https://github.com/Xpounder-com/hormuz/milestone/16).

The user's instruction authorizes cleanup within that scope; do not ask for a
blanket confirmation again. It does not turn an unknown consumer, retained
record or shared credential into an unused resource. Prepare a concrete ledger
and runbook, execute the authorized operations once their conditions are met,
and identify only an actual resource-specific missing authority or unresolved
irreversible data/retention boundary. The user confirms no hosted users/testers
or paid commitments and authorizes retirement of the unused pilot. This
document records the verified operations below and the still-open cleanup work;
it does not claim every account or retained resource has been retired.

UI exploration and the simulated project/chat/model experience can start
independently. Account cleanup is not a dependency of
[#487–#492](https://github.com/Xpounder-com/hormuz/issues/487).
The [infrastructure audit](DESKTOP_INFRASTRUCTURE_AUDIT.md) records why the first
local desktop needs no mandatory hosted runtime.

## Current ledger and evidence gaps

Local build/dependency/test debris is a separate cleanup slice in
[#504](https://github.com/Xpounder-com/hormuz/issues/504). Its
[verified local checkpoint](DESKTOP_LOCAL_CLEANUP.md) records removal of 699
approved generated folders, retirement of 113 merged checkouts and archival/
retirement of 19 test-export folders. All 31 protected checkouts and exact
source/asset recovery remain. The earlier 11.4 GiB and later observed capacity
change are local results, not Render/account savings or retirement of tracked
Docker/test contracts. The audited #504 disposition is complete; the account
cleanup program remains open.

Record exact private locators only in the operator ledger. Public conclusions
use names/resource classes and aggregate results; never credential values,
private IDs or personal account names.

| Area | Current observed evidence | Recommended disposition | What still establishes unused/ready |
| --- | --- | --- | --- |
| Render web services/disks | All three are now suspended: `hormuz-https-preflight`, `hormuz-desktop` and `hormuz-storage-qualify`. The preflight service was suspended through the API and read back on Oct13. All auto-deploy off; each attached 1 GB disk remains retained. Declared compute was one 0.5 CPU/512 MiB instance per service. | Compute pause verified; keep suspended while selected archives/recovery and disk-retirement work finish in #495. | Actual billing and retained storage cost, disk data/retention, verified export/recovery and final deletion scope. Suspension is not disk deletion or a measured cost result. |
| Render PostgreSQL | `hormuz-external-pilot-postgres` available/not suspended, PostgreSQL 16, 0.1 CPU/256 MiB, 1 GB. Source records multiple logical databases in the instance. | Optional legacy store; decide per logical database before whole instance. | Current logical database/role/client map, usage/attempt/custody/audit obligations, complete restore/retention and actual cost. |
| Okta/hosted identity | Source records Hormuz hosted-login app, callback registrations and pilot sessions. No live usage/ownership inventory completed. | Retire only unused Hormuz application/client/callback/session scope; preserve shared organization and unrelated users. | Current consumers, signed/manual client origins, active access/support and retained recovery needs. |
| Stripe/commercial offers | Public catalog and Payment Links configured; old $99/overage proposal is historical. No live payment/subscription/refund inventory completed. | Evaluate obsolete offers and webhook authority; preserve merchant, financial history and existing agreements. | Actual subscriptions/reservations/refunds/disputes, remaining paid periods, account sharing and compatible public terms. Sales-link deactivation is not subscription cancellation. |
| Website analytics/contact/booking | Source config enables Hormuz GA/Search Console/X measurement, Formspree and a Calendar appointment schedule. Current account-side use/cost unknown. | Keep useful acquisition/support; retire unused property/form/schedule/campaign scope after replacement/retention review. | Shared Google/X/calendar boundaries, upcoming bookings/submissions, alternate contact, active campaigns and actual billed plan. |
| GitHub distribution/automation | Repository secret metadata lists two Docker Hub-related names; values unread. Verified environment-secret counts after cleanup: `external-pilot-qualification` 0, `github-pages` 0, `live-provider-conformance` 2, `macos-distribution` 5, `macos-pilot-operations` 0, `v1-release-custody` 1. External-pilot deploy-hook and rehearsal secrets were removed, read-back empty; its workflow is `disabled_manually`, with source retained. | Pilot deployment authority pause/removal verified; ordinary CI, Pages and signing remain untouched. Continue remaining consumer/credential review in #500. | Remaining environment permissions/consumers and any provider/account-side revocation or orphan hooks. Metadata/read-back does not expose or verify secret values. |
| Provider/KMS/catalog credentials | Source has qualified direct-provider paths and optional custody/connectors; model catalog is proposed. No live credential-consumer/usage/retention inventory completed. | Keep direct-provider/local custody/catalog access; retire only unused dedicated Hormuz credentials/resources. | Shared consumers, unknown attempts, recovery/decryption holds, current authorization and affected integrations. No account-wide key revocation. |
| Vercel | Accessible connector returned zero teams and zero Hormuz-name project matches in its authenticated scope. | No discovered candidate in that scope; record a bounded negative result. | This does not prove global absence, no direct personal projects or absence in another account/team. Expand only if an owner/source reference identifies a relevant scope. |

The no-user/tester/no-paid-commitment conclusion is the user's explicit
confirmation, not an invented account-side usage or payment query. Historical
financial records, current identity/provider activity, data contents and billing
were not independently inspected. **Savings are unknown** until actual
before/after billing and retained storage/support costs are known. PostgreSQL
remains available/not suspended; all service disks remain retained.

## Verified cleanup checkpoint — 2026-10-13

| Operation | Verified result | Remaining boundary |
| --- | --- | --- |
| Suspend the unused `hormuz-https-preflight` web service | API operation succeeded; read-back confirms suspended. The other two Hormuz web services were already suspended, so all three are suspended now. | No service/disk deletion, archive/recovery closure or invoice saving asserted. PostgreSQL was not suspended or deleted. |
| Remove external-pilot deployment authority | `HORMUZ_RENDER_DEPLOY_HOOK_URL` and `HORMUZ_FAILOVER_REHEARSAL_KEY` removed from `external-pilot-qualification`; environment read-back is empty. | Account-side credential consumers/revocation and any other pilot authority are separate remaining checks. No secret values were printed. |
| Pause external-pilot qualification automation | The external-pilot deployment/qualification workflow is `disabled_manually`; its source remains retained. | Ordinary CI, Pages, signing, releases and their retained credentials were not changed. |

These are completed bounded operations, not closure of #494 or every cleanup
child. Archive/recovery, retained database/disks, identity/provider/account
scope and actual cost verification continue in the linked work orders.

## Focused cleanup work orders

The seven children under #494 contain current inventory checkpoints, proposed
resource operations, consumer/retention holds, execution records and
post-operation verification.

| Work order | Required result | Keep/shared boundary |
| --- | --- | --- |
| [#495 — Render services and attached disks](https://github.com/Xpounder-com/hormuz/issues/495) | Retain the verified all-services-suspended checkpoint; reconcile domains/hooks/cost and disk state; preserve verified owner-controlled export/restore before selected final deletion. | Do not close a shared Render account/workspace or confuse compute with disk/data cost. Suspended resources remain suspended while archive/retention holds are unresolved. |
| [#496 — PostgreSQL archive and retirement](https://github.com/Xpounder-com/hormuz/issues/496) | Inventory logical databases/roles/clients/state, preserve required evidence and unknown reservations, verify exact application/database recovery pair, then retire selected databases or the whole instance only when all consumers/holds close. | One unused logical database does not authorize deleting its shared instance. A snapshot without recoverable custody/authority is insufficient. |
| [#497 — Okta and pilot access](https://github.com/Xpounder-com/hormuz/issues/497) | Reconcile callbacks, clients/sessions and shipped origins; transition endpoints first; retire selected enrollment/callback/client/session scope in order and verify remaining sign-in works. | Preserve shared identity organization/users and generic OIDC implementation. Keep any remaining recovery authority explicit. |
| [#498 — Stripe offers and payment obligations](https://github.com/Xpounder-com/hormuz/issues/498) | Preserve the user's no-paid-commitment confirmation; reconcile historical financial records, merchant sharing and public catalog; retire obsolete sales/price/webhook scope with public copy continuity. Record any actual cancellation/refund separately if discovered. | Keep financial history and shared merchant. No repricing or account-wide deletion is inferred from a new desktop plan. |
| [#499 — Website acquisition/contact/booking](https://github.com/Xpounder-com/hormuz/issues/499) | Inventory current measurement/campaign/form/schedule use and cost; retain or replace useful channels; preserve inquiries/bookings/retention; retire only unused scopes and verify live contact/consent/links. | Preserve shared Google/X/calendar accounts and unrelated campaigns. Source pixel presence is not an active paid campaign. |
| [#500 — GitHub pilot authority and delivery hygiene](https://github.com/Xpounder-com/hormuz/issues/500) | Retain verified external-pilot environment cleanup and disabled workflow evidence; complete remaining consumer/permission review; verify publishing/signing/checks and no orphan hooks. | Keep canonical Pages, historical redirects/releases/checksums, required checks, signing/notary custody still used and independent support evidence. Local Render container CI is not running cloud spend. |
| [#501 — Provider/KMS/catalog authority](https://github.com/Xpounder-com/hormuz/issues/501) | Build a named consumer/permission/retention map for dedicated provider tokens, custody keys, cloud/KMS resources and optional catalog/connector credentials; retire only proved-unused Hormuz scope after active/unknown attempts and decryption/recovery holds resolve. | Keep direct paid-model accounts, user OS custody and credentials used by other products. Do not delete a KMS key needed to decrypt retained archives; public model downloads do not require inventing a new account/proxy. |

Optional KMS/AWS/connector imports in source are not evidence of live resources
or paid accounts. Inventory actual owner-selected integrations rather than
creating or activating them to test cleanup.

## Readiness and execution record

For every candidate, record:

1. **Ownership and scope:** named operator/consumer classes, current exact
   resource, dedicated versus shared authority, and proposed keep/pause/retire
   operations. Private locator and credential references stay private.
2. **Unused evidence:** explicit recent-use observation window, source/client/
   workflow references, active tester/customer/payment/support dependencies
   and missing access. Zero known consumers is different from a verified empty
   consumer set; quiet traffic alone does not close a recovery obligation.
3. **Data and custody:** complete retained state classes, unknown-spend holds,
   retention owner/location, verified export/restore where needed, recoverable
   key/authority and irreversible deletion limits. Do not read or publish raw
   user content merely to inventory it.
4. **Runbook:** ordered endpoint/admission/consumer transition, graceful
   process handling, callbacks/hooks/credentials, exact resource operations,
   bounded verification and stop/rollback conditions. New temporary paid
   recovery resources are not created casually.
5. **Completion evidence:** operation timestamp/result, final provider resource
   state, affected public/client/workflow checks, no remaining orphan authority,
   retained access/recovery evidence and later invoice reconciliation. Unknown
   costs remain explicitly unknown.

For a retired hosted path, normally settle consumer/payment/retention scope,
verify recovery, transition enrollment and public/shipped references, gracefully
stop or suspend selected compute, then retire callbacks/session/service-specific
authority and delete only ready named resources. Keep shared database, identity,
merchant and distribution boundaries explicit. Parallelize independent metadata
checks with bounded requests; do not run load tests to prove a service unused.

An intentionally retained or deferred resource needs an owner and resume/removal
condition. A cleanup child is not completed by adding a label, identifying a
candidate, switching off auto-deploy or suspending compute while its agreed
data/consumer/authority cleanup remains unfinished.

## Public and financial continuity

Removing an optional hosted dependency from desktop must not silently remove
the existing support/contact path, public download, paid service period,
reservation/refund promise or recoverable history. The current Cloud/managed
catalog and any remaining agreements govern their own scope; a new desktop
license/subscription has not been approved here.

Website/config/privacy/offer changes are prepared together, published through
the dedicated canonical GitHub Pages repository's reviewed source pin, and
verified live after publication. A source commit is not canonical publication.
Financial records and remaining customer agreements stay distinct from sales
links and optional hosting implementation. Keep accepted operational proofs
and reusable code even when their old live resources are retired.
