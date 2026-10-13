# Desktop infrastructure audit

Audit date: 2026-10-13. Source baseline:
`46449d2a3f05de09d7d0a18c9688e189a6c3a68e`.
Track the remaining read-only inventory and retirement proposal in
[#494](https://github.com/Xpounder-com/hormuz/issues/494).
The product sequence is recorded in the
[Electron desktop roadmap](ELECTRON_DESKTOP_ROADMAP.md).

## Decision for the first desktop

Recommend **zero mandatory Hormuz-hosted runtime services** for the first
installed desktop. Build and validate the complete UI simulation first, then
connect local inference and optional direct paid-provider execution.

The ordinary local setup must not require Render, Okta, a Hormuz account,
PostgreSQL, Stripe, a hosted workspace, Docker or Kubernetes. Once a qualified
engine and model are installed, local generation should work without those
services, a model catalog, an updater or remote analytics being available.

The application still needs a local backend: secure credential custody,
settings/model inventory, downloads, an owned llama.cpp process, streaming,
cancellation, eligibility and request accounting. Those responsibilities run on
the user's machine. A direct cloud connection uses the user's provider account
and the provider's API; it does not require a compulsory Hormuz-hosted hop.

Existing hosted resources are evaluated separately. This audit authorizes no
shutdown, deletion, restart, deployment, subscription cancellation, credential
change, service activation or new paid resource. Removing a dependency from the
new product is not evidence that an old service has no users or retained data.

## Verified Render resource state

A read-only authenticated Render CLI inventory on 2026-10-13 reported the
resources below. Public documentation records names and bounded configuration,
without private resource/account identifiers or credential values.

| Resource | Observed state | Declared size | Auto-deploy | Desktop disposition |
| --- | --- | --- | --- | --- |
| `hormuz-https-preflight` web service | Not suspended | One instance, 0.5 CPU / 512 MiB; attached 1 GB disk | Off | Existing hosted/pilot path; no first-desktop dependency. Evaluate consumers and retained state before proposing retirement. |
| `hormuz-desktop` web service | Suspended | One instance, 0.5 CPU / 512 MiB; attached 1 GB disk | Off | Retirement candidate after origin/callback/data/support inventory. Suspension is not deletion or proof of zero retained cost. |
| `hormuz-storage-qualify` web service | Suspended | One instance, 0.5 CPU / 512 MiB; attached 1 GB disk | Off | Qualification resource; retirement candidate after checking retained evidence/data and any recovery dependency. |
| `hormuz-external-pilot-postgres` | Available; not suspended | PostgreSQL 16; 0.1 CPU / 256 MiB; 1 GB storage | Not applicable | Optional legacy hosted database; exclude from desktop installation. Inventory logical databases and consumers before any instance-level action. |

These are control-plane state/configuration observations, not application
health, usage, customer or billing evidence. The deployed serving mode and
revision, actual workload, active tenants/sessions, database/disk contents,
current billed line items and commitments were not inspected. **Cost savings
are unknown.** Do not calculate savings from plan names or assume a suspended
service and its disk incur no charge.

## Existing hosted topology and evidence limits

[The Render gateway guide](../deploy/render/gateway/README.md) separates
maintenance, login/admin staging, workspace UI, provider-pilot and integrated
AI Work modes. `active` refuses model inference. The fixed provider-pilot
profile combines hosted identity with a bounded provider gateway, PostgreSQL
usage/evidence and encrypted session state on an attached disk. The separate
`ai-work` profile uses single-node SQLite stores for workspace/session/work and
optional billing/connector state. A mode's source implementation does not
prove that a live service runs that mode or is commercially qualified.

[The provider-pilot guide](RENDER_PROVIDER_PILOT.md) declares eight generation
slots and a bounded 1–4 PostgreSQL pool in its reference envelope. Render is a
remote identity/gateway host here, not a declared GPU/local-model host. This
profile makes no availability SLA or broad customer-production claim.

[One-click Mac sign-in history](MACOS_ONE_CLICK_SIGNIN.md) records that the
preflight service and the existing PostgreSQL instance supported an internal
pilot on 2026-10-01. It also records a second logical database inside that same
instance while the original remained intact. Those dated records help identify
possible consumers; they are not today's data/use inventory. Deleting the
database instance to remove one pilot could affect both logical databases.

The signed Mac client has its own bounded support and hosted-origin contract.
Current installed users, configured manual origins, cached sessions, IdP
callbacks and operator commitments must be inventoried before removing a
legacy endpoint. Preserve the shipped app and existing installation paths until
an explicit coexistence/migration and retirement plan is accepted.

## Dependency disposition

| Component | First local desktop | Disposition and boundary |
| --- | --- | --- |
| Electron + packaged Svelte UI | Required chosen interface | Develop the complete library/setup/chat/prompt-bar experience against simulated services before backend integration. |
| Local host/backend bridge | Required for real execution | One authority per lifecycle/state machine; renderer gets typed bounded operations. Users need no global Python, Node, Rust or development toolchain. Reuse qualified source selectively. |
| llama.cpp engine and weights | Required for local inference | Exact per-platform engine and curated model qualification; app-owned files/processes; fit/readiness/load/unload and recoverable removal. The existing Codex/Claude agent relay is not a llama.cpp model host. |
| Localhost/private IPC | Required when selected as transport | Private bounded app-to-engine transport with explicit process/endpoint ownership and cleanup. A localhost listener is not a public hosted gateway. Never expose credentials or an unrestricted process/filesystem bridge to renderer content. |
| OS credential custody | Required for saved cloud credentials | Qualified Keychain, Windows and Linux secure-store adapters; explicit unavailable/recovery state. Secrets remain outside renderer, logs and ordinary settings. |
| Local state and optional SQLite | Required storage responsibilities; technology selected later | Confirmed saved local projects/chats/files/instructions/preferences with disclosure, plus unsaved Temporary mode. #493 owns separate retention/export/delete/isolation/migration; content, metadata receipts and credentials have distinct owners. Do not copy all hosted stores into desktop setup. |
| Hugging Face or other model catalog/download host | Needed for online discovery/download only | Direct bounded metadata/download operations with provenance, license and integrity checks. No Render proxy required. Gated/private sources and marketplace loaders are separate later scope. No prompt upload is implied by downloading a model. |
| Direct paid-provider APIs | Optional cloud path | User-owned provider account/credential and qualified protocol/stream/cancel behavior. Provider costs are separate from Hormuz service subscriptions. No paid call or model load on typing. |
| Render services/disks | Not required | Optional existing managed/pilot support; hold new activation/expansion. Evaluate the observed resources as retirement candidates only after complete dependency/data/payment inventory. |
| PostgreSQL | Not required | Optional shared gateway storage. [Core storage](STORAGE.md) already makes PostgreSQL opt-in; [Personal Optimizer](PERSONAL_OPTIMIZER.md) direct mode needs no organization, PostgreSQL or Hormuz gateway. Preserve legacy stores and recovery authority. |
| Okta/OIDC/hosted sessions | Not required | Optional managed connection. Keep generic identity and existing credential/session contracts; do not require hosted enrollment for local model setup. Retire only selected callbacks/sessions after a separate accepted operations plan. |
| Hosted workspace/team administration | Not required | Deferred optional managed-service experience. Tenant authority and invitations belong to that path, not ordinary local first-run. |
| Stripe and hosted entitlements | Not required | Deferred optional service billing; retain existing payment/refund/subscription obligations. Public checkout buttons are neither payment proof nor proof that no customers exist. No new desktop billing model is approved here. |
| Public GitHub Pages site | Not required to execute installed inference | Keep discovery/docs/downloads and public support boundaries. Static Pages cannot serve OIDC callbacks, sessions or model gateway routes. Canonical site publication uses its own reviewed source pin. |
| GitHub Releases/update metadata | Distribution responsibility | Keep signed/checksummed release custody; select a static updater/download mechanism where suitable. No custom hosted updater is needed merely to launch desktop. Local generation must not wait for update service availability. |
| GitHub Actions and packaging/signing | Required engineering/distribution evidence | Preserve current gateway checks. Future desktop checks qualify exact installed targets, updates/migrations, trust, resources and ownership; shared UI does not prove all machines supported. |
| Docker/OCI/Compose/Kubernetes/HA references | Not an end-user desktop dependency | Existing self-hosted gateway/support and bounded proofs remain history/current contracts. Hold unrelated expansion. Do not erase accepted evidence or silently disable protected checks. |
| GitHub/Linear workflow connectors | Not required | Separate optional portfolio/work evidence program; no connector enrollment needed for request-time Auto. |
| Website analytics/contact/booking services | Not required | Keep outside desktop runtime. Website consent does not authorize desktop telemetry or reuse of marketing SDKs in chat. |
| Local benefit measurements/voluntary export | Optional | Existing personal feedback remains local and explicitly exported. No remote analytics service is required to decide an eligible model. |

## Hosted program triage

[#437](https://github.com/Xpounder-com/hormuz/issues/437) through
[#443](https://github.com/Xpounder-com/hormuz/issues/443) are deferred optional
managed-service work, outside the first desktop dependency graph. Keep their
original acceptance/history and all unresolved isolation/payment/production
gates. Deferral is not implementation completion, service shutdown or customer
contract cancellation.

| Issue | Deferred scope |
| --- | --- |
| #437 | Hosted product activation; no subscription/workspace requirement for local/direct-provider desktop. |
| #438 | Hosted workspace provisioning/admin, distinct from local first-run. |
| #439 | Multi-tenant hosted provider custody/routing; local OS credential custody remains required separately. |
| #440 | Hosted production workload/service-account API qualification; direct desktop provider conformance is narrower. |
| #441 | New hosted subscription/entitlement implementation; existing paid obligations remain intact. |
| #442 | Hosted billable request meter/overage design; Auto preview is not an inference/billable request. |
| #443 | Hosted serving capacity/recovery/support qualification; #494 owns the separate read-only rationalization inventory and proposal. No load test, capacity increase or deployment follows. |

The historical $99/workspace and request-overage proposal in these issues is
obsolete commercial scope. The current
[approved catalog and terms](../marketing/COMMERCIAL_SETUP.md) describe Cloud
at $49.99/workspace/month and managed site at $499/month including its workspace,
with no additional Hormuz usage/seat/software-edition/support tier charge.
[Live pricing](https://usehormuz.github.io/plans/) matched those amounts at the
audit read and conditions checkout on agreed scope/activation. This audit
changes no price, checkout, payment, subscription or offer. Revival of an old
hosted billing design requires a current commercial contract; a new paid
desktop license has not been decided.

Enterprise production/security gates and the independent onboarding/portfolio
pilot remain separate unresolved evidence programs. They are not prerequisites
to drawing or testing the simulated desktop interface. Reusing their accepted
custody/policy/source contracts does not create their broader claims.

## Read-only inventory still needed

Issue #494 should prepare a concrete keep/retire recommendation from bounded,
non-secret observations:

1. Current billed line items for all selected instances/disks/databases, actual
   utilization, service mode/revision and recurring/retention cost. Do not assume
   control-plane state implies a bill or a saving.
2. Consumer map: supported shipped client origins, aggregate active
   tenants/sessions/testers, custom domains, IdP callbacks, deploy hooks,
   workflow/protected-environment dependencies and explicit support promises.
   No raw user content or credential values need appear in public evidence.
3. Logical database/disk ownership, complete metadata-state classes and
   retention obligations: sessions, policy/custody/audit, usage/attempts/unknown
   reservations, work/connector/billing records and backups. Determine the
   recovery authority and exact application/database pair; an archive without
   recoverable key/ownership is insufficient.
4. Actual payment/subscription/reservation/refund commitments and any promised
   access, reviewed privately with the account owner. Neither a payment link
   nor a zero known-customer assumption establishes the result.
5. A named proposed retirement order, remaining consumer migration/support
   window, verified export/restore evidence, rollback limits and current quote.
   Treat shared database instances and each attached disk separately.

After that review, a **separate explicit operations work order** must name the
exact resources and authorize any admission closure, export/restore, endpoint
cutover, callback/session/credential retirement, suspension/deletion or billing
change. It must preserve applicable users/data/custody/payment commitments and
include cleanup and recovery checks. This planning audit does not satisfy that
gate, and no infrastructure operation is queued by it.

## CI and publication boundaries

Current normal CI's Render preflight/authentication container jobs build and
test local disposable containers without cloud deployment credentials. They
are not ongoing Render cloud service spend. Compose/Kubernetes/PostgreSQL/OCI
reference checks likewise establish their exact executable contracts; leaving
their source/tests intact does not make them desktop runtime dependencies.

A later reviewed conservative CI scope can run the appropriate UI/platform jobs
for isolated desktop-only changes while retaining gateway/shared-library gates
when those contracts change. Unknown/mixed paths continue to fail closed. Do
not disable protected checks merely to accelerate the UI roadmap.

Keep the public static site, docs, accepted operational evidence and existing
release assets. Product source changes do not automatically republish the
canonical GitHub Pages site. Future product/offer/copy publication has its own
source-pin and live verification process, separate from Render deployment.

## Outcome of this audit

The first desktop can be local and account-free, with optional direct cloud
models. Three web services and one PostgreSQL instance were inventoried
read-only; two web services are suspended, one is not suspended, and PostgreSQL
is available/not suspended. Their actual use, data obligations and costs still
need review. No service, data, account, bill, credential, production code,
customer release or acceptance count was changed.
