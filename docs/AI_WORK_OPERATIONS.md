# AI Work activation and recovery

AI Work is opt-in. The approved gateway profile supplies model aliases, provider
credentials, the approved Stripe subscription price and operator actor IDs.
Browser requests supply none of those secrets or payment ownership identifiers.
Customer administrators manage their own organization; qualification review,
reset and recovery verification additionally require an explicit
`ai_work.administrator_actor_ids` grant.

## Guided activation

After the existing verified console/workspace sign-in, `/work` shows only actions
that the authenticated account can perform. The steps are durable:

1. Request qualification for one permitted model/client pair. Supported API
   protocols are OpenAI Responses, OpenAI Chat Completions and Anthropic Messages.
   The existing `codex` capability label authorizes OpenAI API adapters; the
   existing `claude-code` label authorizes Anthropic adapters. These labels do not
   infer a native application process or enroll a new client type.
2. A configured operator reviews the applicant's current model/client policy,
   configured server credential and actual application compatibility. Approval
   records the operator and bounded qualification reference. Credential presence
   alone is configuration evidence; it is not a successful live provider test.
   An application grant advances the membership authorization version and
   requires signing in/enrolling again.
3. Checkout uses a server-created opaque reference and the approved price. The
   server records that reference before creating a Stripe Checkout Session with
   the same idempotency key. Retries reuse the recorded open session. Checkout
   redirects grant no authority.
   A new session has one immutable one-hour expiry. Creation retries use the
   same reference and parameters only while at least 30 minutes plus transport
   headroom remain. An unresolved creation with a shorter or expired window
   stays recorded and requires operator review/reset before another checkout;
   a timeout never renews its parameters or silently starts a second session.
   A successful response must confirm the recorded expiry.
   Outstanding creation or an unexpired open session also prevents a new
   qualification generation or price from starting another checkout for the
   organization. Changing a model, client or approved price cannot clear that
   barrier. Operator reset explicitly closes the old references before renewed
   qualification; other organizations retain their own independent checkout.
   The validated original return URL is stored with the session parameters.
   Creating retries from a different gateway origin, or legacy uncertain records
   without that URL, require the same operator review/reset instead of changing
   Stripe's parameters under an existing idempotency key. Known legacy open
   sessions remain reusable until their recorded expiry.
4. A signed live `checkout.session.completed` event binds the recorded session
   to its customer/subscription. Activation still requires an active subscription
   at the approved price and a paid invoice covering the current instant.
   Subscription/invoice events arriving before checkout remain bounded normalized
   facts and are applied only after the trusted association exists.
5. The signed payment gate opens covered inference. Signed cancellation or
   payment failure closes it. Customer administrators open the Stripe billing
   portal through `/work` to manage the bound subscription.

Set `ai_work.billing_portal_configuration_id` to the reviewed Stripe `bpc_…`
configuration for this offer. The gateway sends that exact non-secret ID when
creating the portal session and requires Stripe's response to confirm it. Review
payment updates, cancellation mode and any permitted product/price changes in
the same Stripe account. Omitting the setting keeps Stripe's account default;
qualify that default before using it for Hormuz. Portal configuration never grants
paid entitlement or changes the trusted customer/subscription binding.

Changing an approved price closes inference and refuses a second checkout for
an existing trusted subscription. The same organization's portal management
remains available so the customer can cancel that subscription. A stale static
configuration cannot manage a customer/subscription pair that has been rebound.

The Stripe adapter pins `2026-09-30.endive`, rejects redirects from the API, bounds
response size, and sends checkout/portal requests only to `api.stripe.com`.
Checkout URLs must belong to `checkout.stripe.com`; portal URLs must belong to
`billing.stripe.com`. The return URL comes from authenticated gateway origin,
never a browser-supplied URL. Live deployment, payment and provider qualification
remain separate release operations; local fixture tests do not prove them.

The JSON counterparts are `GET /v1/work/activation`, and CSRF-protected browser
POSTs to `/v1/work/activation/request` (`model`, `client`), `/review` (`action`,
`reference`), `/reset` (`reference`), `/reverify` (no payment fields), and
`/v1/work/billing/checkout` or `/portal` (no payment fields). A bearer request
uses the existing authenticated identity and JSON. Tenant/actor/customer/
subscription IDs, prices and entitlement assertions are rejected from bodies.

## Support and connection changes

Authenticated owners download `GET /v1/work/support/receipt` for a bounded
diagnostic receipt. It includes version, billing/activation state and their own
recent work accounting/outcome evidence. It excludes prompts, responses,
repository names, credentials, checkout URLs and payment ownership identifiers.
An owned job's complete JSON receipt is `GET /v1/work/jobs/{work_id}`.

Configured connector choices are private to the authenticated organization.
`configured_signed_channel` means that channel configuration exists; it does
not assert a live delivery. An owned job can explicitly bind a configured
GitHub or Linear object to its versioned completion condition. Signed checks,
reviews, merges and issue completion remain distinct evidence.

`hormuz client config --help` prints supported application configuration; it
does not change the application's files. `hormuz logout --gateway GATEWAY_ORIGIN
--profile PROFILE` revokes the managed session and removes its local credential.
`hormuz personal remove --profile PROFILE` removes an installed Personal
Optimizer profile through its existing transaction. Stop/resume actions on a job affect subsequent
covered requests. An operator activation reset closes admission and stale
checkout references while retaining billing and accounting history. Reset does
not cancel a Stripe subscription; use the billing portal to cancel it.

## Consistent offline backup: SQLite single-node profile

The supported integrated recovery profile co-locates `usage.sqlite3`,
`sessions.sqlite3`, `hormuz-work.sqlite3` and optional
`hormuz-work.billing.sqlite3` under one private state directory. It requires
SQLite usage storage, enabled AI Work and the existing enabled session broker.
The full gateway JSON and independent backup key file must be owner-only.
The key is one canonical base64-encoded random AES-256 key, separate from the
session master key. Existing key-generation/custody procedures apply.

Stop the gateway owner before capture. Its exclusive work owner lock and the
existing hosted state lock prevent an active owner from racing a snapshot.
All SQLite writer locks are held together before copying the databases.
Integrity/schema checks precede capture. The following are actual offline CLI
commands; none starts a listener or calls a provider/payment API:

```sh
python -m hormuz.hosted --config /owner/gateway.json --gateway-profile \
  snapshot --output-directory /owner/snapshot-new
python -m hormuz.hosted --config /owner/gateway.json --gateway-profile \
  backup-export --key-file /owner/backup-key --output-file /owner/work-state.hzb
python -m hormuz.hosted --gateway-profile \
  backup-verify --key-file /owner/backup-key --archive-file /owner/work-state.hzb
```

The additive version-2 encrypted archive includes work/billing owners; legacy
version-1 archives retain their original file contract. Verification checks the
authenticated file list and digests. To restore, prepare a private full profile
with the same identity/key bindings and co-located filenames pointing to a new,
absent state directory; no existing destination is overwritten:

```sh
python -m hormuz.hosted --config /owner/recovery-gateway.json --gateway-profile \
  backup-restore --key-file /owner/backup-key --archive-file /owner/work-state.hzb
python -m hormuz.hosted --config /owner/recovery-gateway.json --gateway-profile \
  recovery-check
```

Restore validates every file first, prepares private staging, closes restored
authority and publishes the whole directory atomically. Failure publishes no
partial destination. Native/browser sessions, invitations and previous workspace
authority are revoked. Pending requests become unknown while keeping their
reserved spend. Live jobs pause, answer-cache generations advance, pending
connector bridge events close and existing source bindings receive a recovery
time fence. The restore does not replay inference or release unknown holds.

Restored billing admission closes with a recovery cutoff. Old signed events,
including previously unseen events, cannot revive entitlement. Restore requires
normal organization/member authority recovery, operator requalification and
`POST /v1/work/activation/reverify`, which reads the currently bound subscription
and invoice through authenticated Stripe API calls. A new checkout cannot create
a second subscription for an existing trusted binding. Customer work resumes
only through its normal explicit action and applicable budget checks.

AI Work with PostgreSQL usage deliberately refuses this integrated SQLite
capture with `hosted_work_consistent_recovery_requires_sqlite`. In particular,
`--provider-profile` does not pretend that its dormant SQLite usage database is
the live PostgreSQL store. PostgreSQL logical capture/restore is not qualified by
this archive implementation. Operators must not advertise the SQLite archive as
a full PostgreSQL recovery solution.

## Updates and retention

Before updating the single-node package, stop the owner, export and verify the
integrated archive, retain the previous package artifact, install the reviewed
replacement and run the offline `--gateway-profile check`. A package rollback
does not imply a compatible schema rollback: retain the archive and its matching
package. Restores always start closed and require current authorization and
payment revalidation.

Do not delete ledger, entitlement or replay rows to reset usage or payment
state. Unknown holds and billing history remain evidence. Removing an agent
connection uses logout and the existing profile removal command; removing a gateway deployment is
an explicit operator operation after independent backup verification and
subscription cancellation. No browser action silently deletes customer history
or provisions paid hosting.
