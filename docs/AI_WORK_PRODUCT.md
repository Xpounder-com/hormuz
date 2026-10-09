# Hormuz AI Work: product and operating contract

Hormuz lets a team keep its supported API agents, give each job a durable identity,
choose a cost/speed/outcome priority, and approve how much its captured work may
spend. The gateway enforces those boundaries before paid inference, records
retries and reuse, and learns from sourced observations of the team's own work.
The customer dashboard is the authenticated gateway `/work` page.

This source change implements that experience. It does not establish a released
installer, a qualified hosted deployment, paying demand, or measured savings.
The public entry is a connection/qualification path; public sign-up alone does
not activate inference or a paid workspace.

## Acceptance contract

| Customer action | Implemented behavior | Qualification boundary |
| --- | --- | --- |
| Open AI work | Existing verified workspace/console session or authorized API identity; browser changes require exact Origin and CSRF | Production OIDC, canonical/custom domain, DNS/TLS and account enrollment must be qualified |
| Connect existing agents | Responses, Chat Completions and Messages requests attach an owned work ID; credentials remain separate from job headers | Native client versions, actual provider tools/streaming and chosen models must pass deployment qualification |
| Set a boundary and priority | Authorized workspace/repository monthly plans and owned job lifetime plans; explicit cost/speed/outcome objective | Configured price estimates must match the supported model's billing inputs |
| Continue a bounded job | Admission reserves capacity under every parent plan; retries count; pause/stop, resume and explicit new total allowance are permission checked | A cap cannot cancel already dispatched provider work; outside calls and subscriptions remain outside the ledger |
| Understand effort | Settled configured-rate estimates, in-flight/unknown holds, captured attempts, reuse, provider time and observed completion elapsed | Estimates are not reconciled invoices; unavailable usage/outcomes remain unknown |
| Learn from local work | Actual sourced workflow observations update local evidence; recurrence is a possible repeat signal | Check results establish their declared condition; they do not certify overall quality or a causal model advantage |
| Manage a paid workspace | Approved tenant binding plus signed live subscription and paid-invoice evidence gates paid inference; verified tenant administrator can open the Stripe portal | Live account, price, subscription/customer mapping, webhook delivery and portal settings require operator qualification |

No human ratings, model-generated correctness scores or inferred task completion
are required. A terminal model response, cache hit, or agent process exit alone
does not complete a job. An explicit workflow completion condition supplies the
observation. [Agent integration](AI_WORK_AGENT_INTEGRATION.md) documents the SDK,
CLI and supported native configuration.

## Operator enrollment and paid activation

1. Install this source/build in an isolated deployment and enable `ai_work` in its
   server profile. Keep the work database and billing database in private storage,
   with backup/restore and deployment ownership verified. Configure allowed model
   routes, provider credentials and actual rate cards on the server.
2. Qualify the customer's workflow and supported client/protocol. Verify the
   organization and application identity through the existing workspace and
   native/OIDC enrollment flow. Explicitly authorize its native/API client and
   model policy. An email address or browser membership is not application access.
3. For a paid deployment set `ai_work.require_paid`, the approved
   `billing_price_id` and explicit operator `administrator_actor_ids`. The customer
   requests qualification through `/work`; an operator reviews the actual workflow
   and current application policy. Checkout creates a durable server-owned session
   reference. Its signed live completion binds the customer/subscription only to
   that recorded reference. Existing subscriptions may use independently reviewed
   `billing_bindings`; public identifiers and arbitrary checkout metadata cannot
   establish ownership. Provider fees remain separate from Hormuz.
4. Supply the billing API key and webhook signing secret through the configured
   server environment secret names. The defaults are
   `HORMUZ_WORK_BILLING_API_KEY` and `HORMUZ_WORK_BILLING_WEBHOOK_SECRET`.
   Configure Stripe's signed webhook to the gateway's
   `/v1/work/billing/webhook`, using the implementation's pinned Stripe API version.
   Qualify live delivery of the reviewed subscription state and the paid invoice
   for the current period before admitting paid work. Checkout redirects and
   test-mode events do not activate paid access.
5. Confirm `/work` and `/v1/work/connect` show the actual account access/billing
   state. Create a job; set workspace/repository/job limits; attach its stable ID
   to every supported agent request. Perform one qualified provider request and
   one real workflow check, then compare the captured ledger with provider usage.
6. Qualify cancellation, nonpayment, expired periods and tenant isolation. Verify
   the administrator-only portal can update payment/cancel the reviewed customer
   subscription, and signed state changes close subsequent admission correctly.

The reviewed server configuration uses the following non-secret shape; replace
all example identifiers with independently verified operator/price configuration. No key values
belong in this file:

```json
{
  "ai_work": {
    "enabled": true,
    "database": "private/hormuz-work.sqlite3",
    "cache_enabled": false,
    "minimum_samples": 5,
    "require_paid": true,
    "billing_price_id": "price_VERIFIED",
    "administrator_actor_ids": ["reviewed-operator"]
  }
}
```

## Existing hosted process boundaries

The `provider-pilot` process explicitly admits the AI Work dashboard/API, model
catalog and Chat Completions routes through both the public proxy and private
handler. The existing ingress credential, exact canonical Host, OIDC/API identity,
Origin/CSRF, provider policy and eight-provider-request capacity boundaries still
apply. Unknown work routes stay closed. Signed Stripe webhooks require the trusted
ingress hop plus their own reviewed signing secret; no browser session or checkout
redirect grants entitlement.

For this fixed hosted profile set `ai_work.database` to
`<state_directory>/hormuz-work.sqlite3`. Payment credentials use only the reviewed
default environment names. Both the live Stripe API key and webhook signing
secret must be supplied when billing is configured; custom names, test API keys,
work databases outside the owned state directory, and inactive billing credentials
are rejected. Payment secrets go only to the provider child, never to Caddy or
provider-free authentication/workspace children.

The existing `workspace` hosting mode remains provider-free account provisioning.
Its `/work` entry redirects to `/workspace` on a verified canonical/custom host;
it cannot serve the AI Work runtime or activate paid inference. A customer must be
explicitly qualified and enrolled on the configured provider deployment before
using that deployment's `/work` dashboard. A generated provisioning address does
not establish provider access, paid entitlement or operational qualification.

The fixed provider-pilot profile deliberately excludes portfolio connector
configuration. Its workflow checks use explicit CLI/SDK observations. Automatic
signed GitHub/Linear observations and integrated recovery are available in the
full single-node SQLite gateway profile with enrolled portfolio sources. These
deployment capabilities are distinct; the dashboard exposes only configured
channels. See [operations](AI_WORK_OPERATIONS.md) for activation, cancellation,
support, update and recovery commands.

## Recovery and support

If credentials, provider availability or payment evidence are unavailable,
preserve the job and display the actual denied/unknown state. Restore or redeliver
verified billing evidence through the operator path; never flip paid status from
a customer-submitted ID. Repeated webhook deliveries are idempotent, conflicting
events are rejected, and cross-tenant bindings do not grant access.

Unknown provider usage remains reserved. Investigate against actual provider
records before reconciling the incident; do not silently release the hold. Pause
a job to block future admissions while investigating, and use an explicit new
total allowance to continue within parent limits. Restoring a backup or changing
a tenant's reviewed payment binding requires requalification of its current state.

The dashboard and API summarize bounded recent records while totals retain the
full authorized scope. Owned job detail endpoints expose additional bounded
attempt records. A support receipt should identify the work ID, request IDs,
gateway version, configured rates and evidence sources, with credentials and
request content kept out of diagnostic exports.

Durable work and signed-event history has no cumulative row admission ceiling.
Monthly allowances apply to the accounting period rather than the number of
historical records. Operators must monitor available disk and retain consistent
backups; offline retention must preserve active allowance, unresolved holds and
payment replay/ordering evidence. A full or unavailable store requires recovery,
not deletion of a customer's history to reset spending. Memory caches, learning
windows and report responses remain bounded independently of durable history.

## Release evidence

After optional acquisition consent, a connection event requires a delivered,
uncached response with complete usage and successful terminal metadata from a
supported API. Reading setup instructions or finding configured credentials
does not supply this event. Historical setup-read events remain stored but are
excluded from the response count. This records API activity, not native-client
qualification or job completion. First and repeat work count distinct attributed
jobs with admitted activity; a denied-only job does not contribute.

Local HTTP fixtures exercise real sessions, CSRF, tenant/actor isolation, work
admission and settlement, passive observations and billing lifecycle boundaries.
The functional browser demo uses the actual gateway with a local identity/provider
fixture; it does not demonstrate paid provider usage or customer savings. Evidence
is stored in [the functional evidence directory](evidence/ai-work-functional/).

Before offering hosted activation, qualify the chosen deployment end to end:
package/install, domain/TLS, production identity and authorized client enrollment,
actual provider request/streaming/tool behavior, model price inputs and invoice
comparison, signed live payment lifecycle, portal/recovery, and operational
support. Those external checks remain distinct from local green tests.
