# GitHub outcome connector

Hormuz exposes an opt-in GitHub.com receiver at
`POST /v1/connectors/github/events`. The receiver verifies a GitHub App
webhook signature over the exact request bytes, binds the signed installation
and repository IDs to an operator-owned portfolio connector, normalizes an
explicit metadata-only allowlist, and acknowledges only after an atomic
receipt commit. The historical
[offline preflight](GITHUB_CONNECTOR_PREFLIGHT.md) remains a frozen record of
the earlier proposal.

This source checkpoint does not install or modify a GitHub App and is not live
GitHub.com proof. A live installation still requires separate owner approval.

## Enrollment profile and permissions

The v1.3 profile is a dedicated tenant-owned GitHub App. Give each enrolled
signing channel a unique webhook secret, install the App only on selected
repositories, and register the resulting numeric installation and repository
IDs in `portfolio_control.connectors`. A shared cross-tenant App secret is not
supported by this profile. Hormuz rejects credential reuse across channels.

Use only these repository permissions:

| Permission | Level | Reason |
| --- | --- | --- |
| Metadata | Read | GitHub App repository identity and basic webhook metadata |
| Pull requests | Read | `pull_request` and `pull_request_review` deliveries |
| Checks | Read | completed `check_run` deliveries |

Do not grant Contents, Actions, Issues, Administration, repository-hook
management, or any write permission. Subscribe only to **Pull request**,
**Pull request review**, and **Check run**. The normalizer ignores every other
signed event. Installation, repository-control, and ping deliveries may be
recorded as durable unsupported coverage, but never become outcome events.

GitHub documents the minimum event permissions in its
[webhook event catalogue](https://docs.github.com/en/webhooks/webhook-events-and-payloads)
and recommends selecting the minimum permissions needed for subscribed events
in its [GitHub App permission guide](https://docs.github.com/en/apps/creating-github-apps/registering-a-github-app/choosing-permissions-for-a-github-app).

## Runtime configuration

`portfolio_control` remains the authority for organization, connector,
installation, and repository bindings. `outcome_connectors` contains only
versioned environment-variable references for activating those bindings:

```json
{
  "outcome_connectors": {
    "schema_id": "hormuz.outcome-connectors",
    "schema_version": 1,
    "github": [
      {
        "organization_id": "acme",
        "connector_id": "github-primary",
        "webhook_secrets": [
          {
            "version": "webhook-2026-09",
            "environment_variable": "HORMUZ_GITHUB_WEBHOOK_SECRET_2026_09"
          }
        ],
        "identity_keys": [
          {
            "version": "outcome-identity-v1",
            "environment_variable": "HORMUZ_GITHUB_OUTCOME_IDENTITY_V1"
          }
        ],
        "current_key_version": "outcome-identity-v1",
        "delivery_identity_key_version": "outcome-identity-v1"
      }
    ]
  }
}
```

Credential values must be unique printable ASCII strings from 32 through 128
bytes. Keep them in the deployment secret manager. They must never appear in
JSON, source, logs, issue comments, or evidence artifacts. At most eight
GitHub channels may be active in one process, each channel accepts at most two
webhook-secret versions, and it may retain at most eight outcome key versions.

`delivery_identity_key_version` is the stable identity for the enrolled
channel. Retain that version for the entire replay horizon. Rotate receipt and
provenance protection by adding a new identity key and changing
`current_key_version`; do not change the delivery identity version in place.
If the delivery identity key is compromised, disable the connector and enroll
a new connector ID rather than silently reinterpreting earlier deliveries.

## Authentication and delivery identity

The HTTP boundary accepts one JSON body from 1 byte through 1 MiB, refuses
chunked transfer and duplicate critical headers, caps header count and bytes,
uses an absolute ten-second body-read deadline, and admits at most eight
concurrent connector requests per process.

The adapter verifies `X-Hub-Signature-256` with HMAC-SHA256 over the exact raw
bytes using constant-time comparison before JSON decoding. It then checks the
signed numeric `installation.id` and, when present, `repository.id` against
the server-owned binding. Organization, connector, work scope, actor, names,
URLs, and unsigned headers never grant authority.

`X-GitHub-Event`, `X-GitHub-Delivery`, and hook headers are outside the body
HMAC. Hormuz does not use them for tenant selection, event selection, or replay
identity. It derives a tenant- and connector-separated delivery ID from the
signed bytes with the stable delivery identity key. Exact byte replay under
either allowed webhook-secret version returns the original receipt. Reusing a
body identity with conflicting bytes fails closed. Byte-identical provider
deliveries intentionally collapse because the unsigned delivery GUID is not
an authentication authority.

GitHub's [signature validation guide](https://docs.github.com/en/webhooks/using-webhooks/validating-webhook-deliveries)
defines the raw-body HMAC requirement.

## Normalization allowlist

The body shape, action, state, conclusion, signed installation, and signed
repository jointly select the mapping. The unsigned event-name header is
ignored.

| Signed body shape | Accepted values | Normalized result |
| --- | --- | --- |
| Pull request | `opened` | `created`, quality `unknown` |
| Pull request | `reopened` | `reopened`, quality `unknown` |
| Pull request | `synchronize` | `started`, quality `unknown` |
| Pull request | `closed`, `merged=true` | `completed`, quality `unknown` |
| Pull request | `closed`, `merged=false` | `canceled`, quality `unknown` |
| Submitted review | `approved` | `accepted`, source-qualified quality `accepted` |
| Submitted review | `changes_requested` | `defect_reported`, source-qualified quality `rejected` |
| Completed check with one PR | `success` | `completed`, source-qualified quality `accepted` |
| Completed check with one PR | `failure`, `timed_out`, `startup_failure`, `action_required` | `defect_reported`, source-qualified quality `rejected` |
| Completed check with one PR | `cancelled`, `stale` | `canceled`, quality `not_applicable` or `unknown` |
| Completed check with one PR | `neutral`, `skipped` | `completed`, quality `unknown` or `not_applicable` |

A check with zero or multiple PR associations is unsupported. Unknown actions,
unknown conclusions, review comments, dismissed reviews, repository control,
installation control, and ping bodies create no normalized outcome mutation.
A malformed allowlisted shape records a content-free failure rather than an
outcome. Source ordering uses the accepted event timestamp; commit SHAs are
not ordering counters and are not retained. This keeps force-push, rebase, and
squash behavior tied to the PR identity rather than inventing code lineage.

Only numeric repository and PR IDs, event timestamps, lifecycle states,
source-qualified review/check conclusions, credential/key versions, and
content-free coverage cross the storage boundary. Hormuz does not retain raw
payloads, names, URLs, branches, commit SHAs or messages, titles, bodies,
review text, check names, patches, paths, source, credentials, or sender data.
All connector results remain descriptive evidence and do not establish AI
causality or employee performance.

## Setup runbook

1. Create the dedicated GitHub App without installing it. Configure the HTTPS
   webhook URL ending in `/v1/connectors/github/events`, generate a high-entropy
   secret, set the three read-only permissions above, and subscribe to the
   three events above.
2. Review the App settings and selected-repository installation scope. Record
   only the numeric installation and repository IDs in the Hormuz connector
   binding.
3. Add the webhook secret and outcome key to the deployment secret manager,
   add their environment-variable names to `outcome_connectors`, and restart
   the gateway. Startup fails closed for missing, short, reused, or malformed
   credentials and for unknown or overlapping bindings.
4. Send a signed synthetic fixture locally. Confirm an HTTP 202 receipt, one
   content-free outcome or unsupported coverage row, exact replay returning
   the same receipt, and no raw or private marker in storage or logs.
5. After separate owner approval, install the App on the nominated test
   repository and perform one live delivery and GitHub redelivery. Preserve
   only content-free IDs, timestamps, versions, receipt disposition, exact
   source commit, and protected workflow links as evidence.

## Secret rotation runbook

1. Add the new GitHub webhook secret in GitHub and expose it through a second
   versioned `webhook_secrets` entry. Restart Hormuz; both distinct secrets are
   accepted and produce the same delivery identity for identical bytes.
2. Trigger a synthetic delivery signed by the new secret and replay an older
   delivery signed by the old secret. Confirm both receipts are durable and
   no unsigned overlap is accepted.
3. Remove the old secret from GitHub, remove its configuration entry, restart,
   and confirm old signatures fail authentication.
4. For receipt/provenance key rotation, retain every old `identity_keys`
   version, add the new version, and change only `current_key_version`.

## Failed delivery and storage outage runbook

An HTTP 202 means the accepted or unsupported receipt committed. HTTP 400,
401, 403, 409, 429, or 503 is not an acknowledgement. GitHub or the operator
may redeliver the exact bytes; Hormuz never retries provider work itself.

For a 400, verify that the delivery is one of the allowlisted shapes and that
the raw bytes were not changed by a proxy. For 401 or 403, inspect secret
version and numeric installation/repository enrollment without logging values
or payloads. For 409, preserve the database and investigate conflicting
identity evidence. For 429, retry after capacity is available. For 503, restore
the storage dependency and redeliver. A storage outage cannot produce a
success receipt.

## Reconciliation, disablement, and coverage

The v1.3 connector has no provider API token, polling worker, reconciliation
route, or backfill path. GitHub's authenticated delivery redelivery is the only
recovery input and remains distinct from webhook observation time. Any future
snapshot or backfill feature needs separate authentication, bounded pages,
bytes and time, and a distinct snapshot contract before activation.

To disable the connector, remove its `outcome_connectors.github` enrollment
and restart. New deliveries then fail before parsing or mutation while earlier
receipts and evidence remain immutable. For App suspension or uninstall,
disable the runtime enrollment, retain content-free receipts, and do not infer
deleted work. Re-enrollment requires a reviewed installation/repository
binding and fresh signing secret.

Coverage consists of durable accepted, unsupported, failed, late, ambiguous,
matched, unmatched, and excluded states already exposed by the portfolio
outcome repository. It is not a count of GitHub's total events. Compare
provider delivery history with content-free Hormuz receipt IDs during the live
proof; do not ingest payload contents into a report.

Run the offline verification with:

```bash
python -m unittest -v \
  tests.test_github_connector_runtime \
  tests.test_github_webhook_auth \
  tests.test_github_connector_fixtures \
  tests.test_sqlite_outcomes
```

PostgreSQL CI runs the same production adapter against the PostgreSQL outcome
repository. Live GitHub.com installation, delivery, and redelivery remain a
separate release gate until explicitly authorized and linked from issue #219.
