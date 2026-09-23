# Linear lifecycle connector runtime

Hormuz exposes an opt-in Linear receiver at
`POST /v1/connectors/linear/events`. It verifies `Linear-Signature` over the
exact raw request bytes before parsing, binds the signed workspace, webhook,
team, entity type, and entity ID to an operator-owned route, and returns HTTP
200 only after the receipt and all normalized metadata commit atomically. The
historical [preflight](LINEAR_CONNECTOR_PREFLIGHT.md) and
[transition checkpoint](LINEAR_CONNECTOR_TRANSITION.md) remain frozen records
of the earlier design and schema assignment.
Provider-free snapshot reconciliation is documented separately in
[LINEAR_RECONCILIATION.md](LINEAR_RECONCILIATION.md).

This source checkpoint does not create a Linear webhook, install a credential,
connect a workspace, run a provider backfill, or establish live delivery
evidence. Those actions require a separately selected and authorized test
workspace.

## Runtime enrollment

`portfolio_control.connectors` owns the organization, connector, Linear
workspace, and enrolled project IDs. `outcome_connectors` schema version 3
activates the exact webhook route, team IDs, typed entity IDs, signing-secret
versions, and keyed replay identities. Schema version 1 remains the unchanged
GitHub-only shape.

```json
{
  "outcome_connectors": {
    "schema_id": "hormuz.outcome-connectors",
    "schema_version": 3,
    "github": [],
    "linear": [
      {
        "organization_id": "acme",
        "connector_id": "linear-primary",
        "binding_version": 1,
        "source_webhook_id": "00000000-0000-4000-8000-000000000001",
        "source_team_ids": [
          "00000000-0000-4000-8000-000000000002"
        ],
        "typed_enrollment": {
          "initiative_ids": [],
          "project_ids": [
            "00000000-0000-4000-8000-000000000003"
          ],
          "cycle_ids": [],
          "issue_ids": [
            "00000000-0000-4000-8000-000000000004"
          ]
        },
        "active_webhook_secret": {
          "version": "linear-webhook-2026-09",
          "environment_variable": "HORMUZ_LINEAR_WEBHOOK_SECRET_2026_09"
        },
        "previous_webhook_secret": null,
        "active_snapshot_secret": {
          "version": "linear-snapshot-2026-09",
          "environment_variable": "HORMUZ_LINEAR_SNAPSHOT_SECRET_2026_09"
        },
        "previous_snapshot_secret": null,
        "identity_keys": [
          {
            "version": "1",
            "environment_variable": "HORMUZ_LINEAR_IDENTITY_KEY_1"
          }
        ],
        "current_key_version": "1",
        "body_fingerprint_key_version": "1",
        "source_fact_key_version": "1",
        "registered_by": "portfolio-admin"
      }
    ]
  }
}
```

All IDs are exact lowercase provider UUIDs. Project enrollment must equal the
Linear connector's project allowlist in `portfolio_control`; initiative,
cycle, and issue enrollment never inherits from that project list. One Linear
workspace may belong to only one Hormuz organization, and one webhook ID may
name only one organization/connector route. Configuration validation and the
database binding trigger both enforce those cardinalities. PostgreSQL records
the claims in a private append-only table outside tenant RLS; the runtime role
cannot read or write that table directly.

Credential values are unique printable ASCII strings from 32 through 128
bytes and belong only in the deployment secret manager. A process accepts one
active webhook secret and active snapshot secret, with at most one expiring
previous secret of each kind per route, plus
at most eight numeric identity-key versions. It accepts at most eight Linear
routes. Secret values, raw payloads, plain payload hashes, and credential-value
hashes never enter configuration files, logs, receipts, or audit evidence.

## Authentication, replay, and resource bounds

The HTTP boundary refuses chunked transfer, invalid or duplicate content
lengths, non-JSON content types, and duplicate critical headers. It caps the
request at 1 MiB, 64 headers, 8 KiB of header bytes, JSON depth 16, 4,096 JSON
members, and eight concurrent transport plus eight concurrent ingestion slots.
Its internal deadline begins before the first body read and expires after four
seconds.

The authenticator computes HMAC-SHA256 over the exact request bytes and compares
the result in constant time before decoding JSON. After authentication it
requires exact signed `organizationId`, `webhookId`, `webhookTimestamp`, event
type, action, entity ID, and any supplied team IDs. Hormuz-owned organization,
connector, work-scope, and tenant fields in the body are rejected. The unsigned
`Linear-Delivery` header is a bounded conflict hint, never authority.

Every accepted body receives tenant/connector/key-version separated HMAC
identities for the exact signed bytes. A separate keyed identity covers only
the stable allowlisted source fact. Exact committed body replay may return the
original receipt even after the one-minute freshness window, including when
the unsigned delivery header changes. A different body under an existing
delivery ID is a conflict. A provider retry that changes only delivery or
signed webhook timestamp collapses through the stable source-fact identity.
Unknown stale bodies fail authentication.

Linear documents the raw-body signature, signed timestamp, delivery header,
five-second response deadline, and retry schedule in its
[webhook guide](https://linear.app/developers/webhooks).

## Normalized metadata

The receiver accepts `create`, `update`, and `remove` for enrolled Issue,
Project, Initiative, and Cycle IDs. It stores opaque IDs, optional team IDs,
event/source-update timestamps, lifecycle state, a bounded normalized state,
allowlisted parent relationships, relationship coverage, ordering state,
scope state, key/credential versions, and keyed provenance.

Issue-to-project and issue-to-cycle, project-to-initiative, and initiative
parent relationships are retained only when both typed endpoints are enrolled.
An unenrolled, unsupported, or only partly represented relationship set makes
coverage partial; absent relationship fields remain unknown, while an explicit
empty set is complete. Issue outcomes are projected only for an exactly
enrolled project container. Updates emit started, completed, or canceled
outcomes only when `updatedFrom` identifies the corresponding state field as
changed. Every result stays descriptive and association eligibility stays
inconclusive.

Names, titles, descriptions, comments, attachment data, label text, URLs,
prompt/response content, and arbitrary fields are ignored and never persisted.
The SQLite 15 and PostgreSQL 20 schemas contain six append-only evidence families:
immutable source-binding versions, committed delivery receipts, context events,
separate retention markers, snapshot receipts, and snapshot context events.
PostgreSQL also has one private route-claim
table used only by the binding trigger. Linear source facts are linked into the
finite commit audit chain in the same transaction.

## Setup runbook

1. Register a Linear connector under the intended organization with the exact
   workspace ID and project IDs. Record the selected team and typed entity IDs
   without copying names or work content.
2. Create a dedicated Linear webhook for the selected workspace/team scope with
   URL ending in `/v1/connectors/linear/events`. Generate a unique high-entropy
   signing secret and store it in the deployment secret manager.
3. Add the versioned environment-variable references and exact route enrollment
   above, then restart Hormuz. Startup fails closed for missing, short, reused,
   malformed, overlapping, or unresolved credentials and routes.
4. Send a locally signed synthetic fixture. Confirm HTTP 200, one durable
   receipt/context transaction, exact replay returning the same receipt, and
   absence of private marker text from the database and logs.
5. Only after separate authorization, activate the nominated test webhook and
   observe one delivery plus each available provider retry/redelivery path.
   Retain only content-free IDs, timestamps, versions, dispositions, exact
   source commit, and protected workflow links.

## Signing-secret and identity-key rotation

1. Add a new Linear signing secret as `active_webhook_secret`. Move the old
   reference to `previous_webhook_secret` with a required expiry timestamp,
   and advance `binding_version` by exactly one. Restart; the two values must
   be distinct.
2. Send a synthetic delivery under the new secret and replay a previously
   committed body under the unexpired old secret. Both must resolve to the
   correct original receipt.
3. After the overlap ends, remove the previous reference and secret, restart,
   and confirm the old signature is rejected.
4. For body/source-fact identity rotation, retain old `identity_keys`, add the
   next numeric version, and advance the selected current/body/source-fact
   version deliberately. Advancing `body_fingerprint_key_version` also
   requires the next `binding_version`. Old keyed identities remain available
   for replay. Binding request identity stays pinned to the binding's stored
   body-fingerprint key version, so advancing only `current_key_version` does
   not invalidate an unchanged binding.

## Retry, outage, and deadline response

HTTP 200 means an exact prior receipt was verified or the new receipt, context,
outcome/coverage, binding evidence, and audit entries committed. HTTP 400, 401,
403, 409, 429, or 503 is not an acknowledgment. Hormuz never retries governed
provider work itself.

For 400, verify exact JSON shape and critical headers. For 401 or 403, check the
secret version and signed workspace/webhook/team/entity enrollment without
logging values or payloads. For 409, preserve the database and investigate the
conflicting unsigned delivery or immutable binding identity. For 429, retry
after bounded capacity returns. For 503, restore storage and let Linear follow
its provider retry schedule. A storage outage or deadline reached before commit
produces no Linear receipt/context commit. If a commit finishes at or just after
the internal deadline, the response can still be non-200; the provider retry
must resolve through the durable idempotent receipt. HTTP 200 always means the
commit or exact replay completed before the response.

## Reconciliation, disablement, retention, and coverage

The provider-free snapshot receiver is implemented at
`POST /v1/connectors/linear/snapshots`; its exact wire contract, signing rules,
page completeness query, and source-only runbook are in
[LINEAR_RECONCILIATION.md](LINEAR_RECONCILIATION.md). It does not include a
provider API token or polling worker. Never relabel a snapshot as a webhook
delivery, and do not claim live completeness without separately authorized
provider export evidence.

To disable ingress, remove the route from `outcome_connectors.linear` and
restart. Revoke both webhook secrets in Linear. Earlier binding versions,
receipts, context, coverage, outcome, and audit records remain immutable. A
new scope or route requires the next contiguous `binding_version`; do not edit
or reuse an earlier version.

Deleting or archiving a Linear object does not erase historical Hormuz
evidence. The `remove` action records a descriptive tombstone. Operator
retention markers remain separate from source lifecycle and do not delete
backups, exports, audit history, or provider data. Customer database and backup
operators retain the export, retention, restore, and deletion authority in
[DURABLE_DATA.md](DURABLE_DATA.md).

Coverage is the append-only set of accepted, late, incomparable, unknown,
matched, unmatched, excluded, complete, partial, and not-applicable states. It
does not prove that every Linear event was delivered. Release evidence must
keep provider delivery coverage, source/wheel/runtime tests, exact-main CI, and
live workspace proof as separate gates.

Run the provider-free checks with:

```bash
python -m unittest -v tests.test_linear_connector_runtime
python -m unittest -v tests.test_linear_snapshot_runtime
python tools/verify_linear_transition_plan.py
python tools/verify_linear_runtime_plan.py
python tools/verify_linear_reconciliation_plan.py
```

PostgreSQL qualification must use an owned disposable schema with the restricted
runtime role and reproduce the fixed schema-20 boundary of 219 canonical
non-owner ACL entries at
`a5aedcd593573c5d55f783904046d993bc6f2e0ddd03a48ed098c9498cd2adac`.
Live Linear proof remains open until an owner-selected workspace and webhook
are authorized.
