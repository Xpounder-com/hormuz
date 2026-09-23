# Linear provider-free reconciliation

Hormuz exposes `POST /v1/connectors/linear/snapshots` for operator-generated,
metadata-only reconciliation pages. The endpoint does not call Linear and does
not accept a provider API token. An authorized operator exports only enrolled
opaque IDs, timestamps, state types, team IDs, and allowlisted relationships,
then signs the exact JSON bytes with a dedicated snapshot secret.

This source checkpoint proves the bounded receiver with synthetic fixtures. It
does not authorize a live workspace, prove a complete provider export, or claim
that any live gap has been reconciled.

## Enrollment and credentials

The route uses the same organization, connector, workspace, team, and typed
entity enrollment as the lifecycle webhook. `outcome_connectors` schema version
3 adds one active snapshot secret and, during rotation, at most one expiring
previous snapshot secret:

```json
{
  "active_snapshot_secret": {
    "version": "linear-snapshot-2026-09",
    "environment_variable": "HORMUZ_LINEAR_SNAPSHOT_SECRET_2026_09"
  },
  "previous_snapshot_secret": null
}
```

Snapshot and webhook secret values must be distinct. Values remain in the
deployment secret manager and never enter configuration, receipts, evidence,
logs, or the database. Keep older identity keys while their keyed receipt and
source-fact identities may still be replayed.

## Wire contract and bounds

The request uses `Content-Type: application/json` and exactly these security
headers:

- `X-Hormuz-Linear-Snapshot-Timestamp`: unsigned decimal Unix milliseconds.
- `X-Hormuz-Linear-Snapshot-Signature`: lowercase hexadecimal HMAC-SHA256 of
  `<timestamp>.<exact raw request bytes>`.

Authentication happens before JSON decoding. The timestamp must be within five
minutes of the server clock, and `captured_at` cannot be after the signed
timestamp or more than five minutes before it. An exact committed page replay
may return its original receipt after the freshness window; an unknown stale
page is rejected.

Each request is at most 1 MiB, contains 1 through 100 items, and declares 1
through 100 pages. At most four snapshot ingestions run concurrently. The body
has this exact shape:

```json
{
  "schema_id": "hormuz.linear-authorized-snapshot",
  "schema_version": 1,
  "workspace_id": "00000000-0000-4000-8000-000000000001",
  "reconciliation_id": "00000000-0000-4000-8000-000000000002",
  "snapshot_id": "00000000-0000-4000-8000-000000000003",
  "page_id": "00000000-0000-4000-8000-000000000004",
  "page_number": 1,
  "page_count": 1,
  "captured_at": "2026-09-23T12:00:00.000000Z",
  "items": [
    {
      "type": "Issue",
      "data": {
        "id": "00000000-0000-4000-8000-000000000005",
        "teamId": "00000000-0000-4000-8000-000000000006",
        "projectId": "00000000-0000-4000-8000-000000000007",
        "cycleId": null,
        "updatedAt": "2026-09-23T12:00:00.000000Z"
      }
    }
  ]
}
```

Allowed types are `Initiative`, `Project`, `Cycle`, and `Issue`. Every ID must
already be present in the route's exact typed enrollment, and supplied team IDs
must be enrolled. Names, titles, descriptions, labels, comments, attachments,
URLs, arbitrary fields, prompts, and responses are rejected rather than stored.

All pages for one `snapshot_id` must use one server-side binding version,
`reconciliation_id`, `page_count`, and `captured_at`. Pages may arrive out of
order. A page number may commit only once, and a different body under an
existing page ID or page number is an idempotency conflict. SQLite serializes
the write and enforces the page-set shape with an insert trigger. PostgreSQL
takes tenant and snapshot transaction-scoped advisory locks before its storage
trigger checks the page set.

## Commit and evidence behavior

SQLite migration 15 and PostgreSQL migration 20 add
`gateway_linear_snapshot_receipts`,
`portfolio_linear_snapshot_context_events`, and
`portfolio_linear_snapshot_context_retention_events`. Snapshot provenance and
retention foreign keys are separate from webhook receipts and webhook context
rows. Both context sources share one organization commit sequence, stable
semantic deduplication by object revision and normalized metadata, and
supersession ordering. Each snapshot context binds its evidence to the signed
page ID through `source_delivery_id`; capture-specific timestamps do not create
a second context for the same semantic revision. Its `normalizer` reference uses
the digest-bound `linear-authorized-snapshot-normalizer`, so the provenance
identifies the capture-time bounds and snapshot lifecycle rules that produced
the durable event.

A page commits its receipt, metadata-only context events, and finite audit-chain
entries in one transaction. It emits no outcome event. A context already
represented by a webhook or earlier snapshot is recorded as a duplicate page
receipt with zero accepted context events. Raw request bytes, plaintext hashes,
credentials, and content fields are never stored.

HTTP 200 means the page transaction committed or an exact prior page receipt
was verified. HTTP 400, 401, 403, 409, 429, or 503 is not an acknowledgment.
Hormuz never retries provider work. Preserve the original bytes and page IDs
for safe operator retry.

## Provider-free operator runbook

1. Confirm the route's workspace, teams, and typed enrollment. Generate a new
   reconciliation ID, snapshot ID, and unique page ID per page.
2. Produce canonical UTF-8 JSON containing only the fields above. Keep one
   captured timestamp across a snapshot and keep every page's declared count
   identical.
3. Sign the exact saved bytes. This example prints only the signature:

   ```bash
   SNAPSHOT_TIMESTAMP_MS="$(python3 -c 'import time; print(int(time.time()*1000))')"
   export SNAPSHOT_TIMESTAMP_MS
   python3 - <<'PY'
   import hashlib, hmac, os, pathlib
   raw = pathlib.Path("linear-snapshot-page.json").read_bytes()
   signed = os.environ["SNAPSHOT_TIMESTAMP_MS"].encode("ascii") + b"." + raw
   print(hmac.new(os.environ["HORMUZ_LINEAR_SNAPSHOT_SECRET"].encode("ascii"), signed, hashlib.sha256).hexdigest())
   PY
   ```

4. POST the saved bytes with the timestamp and signature headers. Retain only
   page IDs, receipt IDs, dispositions, timestamps, and the exact source commit.
5. After all pages return HTTP 200, establish completeness from durable receipts:

   ```sql
   SELECT reconciliation_id, snapshot_id, page_count,
          MIN(binding_version) AS binding_version,
          MIN(captured_at) AS captured_at,
          COUNT(*) AS received_pages,
          MIN(page_number) AS first_page,
          MAX(page_number) AS last_page
   FROM gateway_linear_snapshot_receipts
   WHERE organization_id = :organization_id
     AND connector_id = :connector_id
     AND snapshot_id = :snapshot_id
   GROUP BY reconciliation_id, snapshot_id, page_count
   HAVING COUNT(*) = page_count
      AND COUNT(DISTINCT page_number) = page_count
      AND COUNT(DISTINCT binding_version) = 1
      AND COUNT(DISTINCT captured_at) = 1
      AND MIN(page_number) = 1
      AND MAX(page_number) = page_count;
   ```

   No row means the snapshot is incomplete. Do not infer completeness from an
   individual HTTP 200 or from the declared `page_count` alone.
6. Compare only metadata coverage and fixed disposition counts. Live source
   completeness requires separate workspace authorization and retained provider
   export evidence.

Rotate snapshot secrets independently from webhook secrets. Move the old
snapshot reference to `previous_snapshot_secret` with an expiry, restart, prove
one synthetic request under each usable version, then remove the old reference
after the overlap.

Run the provider-free checks with:

```bash
python -m unittest -v tests.test_linear_snapshot_runtime
HORMUZ_TEST_POSTGRES_DSN=... python -m unittest -v tests.test_postgres_linear_snapshot_runtime
python tools/verify_linear_reconciliation_plan.py
```

PostgreSQL qualification must reproduce the fixed schema-20 boundary of 222
canonical non-owner ACL entries at
`cd86c395bea316873e11e175563cbf31563212c45ca6cb6b6fb066f2f8f1e64d`.
