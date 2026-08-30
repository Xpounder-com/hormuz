# Governed-run attribution (v1.1.0 development)

Attribute an accounted AI request to one exact tenant-local use-case version
without collecting its prompt, response, source code or work-item content.
This is #216's source implementation, not a v1.1.0 release or external pilot.
It adds SQLite migration 6 / PostgreSQL migration 10 and preserves v1 evidence.
Follow the stopped-writer [transition procedure](ATTRIBUTION_TRANSITION.md).

## Operator-controlled admission

Create an active `use_case` in the [registry](REGISTRY.md). Add this top-level
section to the existing gateway JSON configuration, substituting existing
tenant/actor IDs and the returned scope ID/version:

```json
{
  "attribution_control": {
    "schema_id": "hormuz.attribution-control",
    "schema_version": 1,
    "bindings": [{
      "organization_id": "acme",
      "actor_id": "alice",
      "client": "codex",
      "allowed_work_scopes": [{"work_scope_id": "RETURNED_OPAQUE_ID", "version": 1}],
      "default_work_scopes": [],
      "require_scope": false
    }]
  }
}
```

Restart through the normal operator-controlled configuration process. Add a
separate `claude-code` binding when needed. Existing client permissions,
inference policy and budgets still apply; attribution and portfolio roles
grant none of those powers. There is no discovery by repository name, ticket
title, path, prompt or other content.

Configuration permits at most 1,000 unique tenant/actor/client bindings and
128 unique exact-version references per allowlist/default list. Defaults must
be allowed references. IDs follow the approved opaque-ID grammar. Versions
are integers from 1 through 2147483647, not strings or booleans. Unknown fields
and identities fail loading. No configuration means attribution is off.

An authenticated request may send one ASCII header, at most 192 bytes:

```text
X-Hormuz-Work-Scope: v1;work_scope_id=RETURNED_OPAQUE_ID;version=1
```

The reference must be in that identity/client's allowlist and still be the
latest active use-case version. A material scope change requires an explicit
operator configuration update; there is no silent retargeting.

| Selection | Confidence | Behavior |
| --- | --- | --- |
| Authorized explicit header | `explicit_authorized` | Overrides a configured default |
| Exactly one configured default | `server_side_default` | Uses its exact version |
| No header/default in a binding | `unattributed` | No primary; reason `missing_evidence` |
| No header, multiple defaults | `ambiguous` | No primary; no guessed choice |

`require_scope: true` rejects the last two cases before policy, reservation or
provider traffic. Without a binding or header, requests retain legacy behavior
and create no attribution event. Explicit attribution without configuration
is rejected. Token counting has no governed attempt; an explicit header there
is unsupported.

Duplicate/malformed/unsupported headers return 400, unauthorized references or
required missing/ambiguous scope return 403, changed versions return 409, and
unavailable storage returns 503. Errors retain the native OpenAI or Anthropic
body shape. Opted-in results use only fixed values:

```text
X-Hormuz-Work-Scope-Result: v1;status=attributed;reason=bound
X-Hormuz-Work-Scope-Result: v1;status=rejected;reason=unauthorized_scope
```

No submitted header/ID is reflected. Neither attribution header goes upstream;
native response bodies, streams and compact responses are not decorated.
Successful admission is reported only after its event commits. Earlier native
body/policy failures do not claim successful admission.

## Administrator corrections and reads

Use an existing identity with explicit `portfolio_admin` authority in
`portfolio_control`; admission permission alone cannot read or correct records.
The additive HTTP routes are `GET` and `POST` at
`/v1/admin/portfolio/attributions`. They share the registry's strict envelopes,
`Idempotency-Key`, `Cache-Control: no-store`, and
`X-Hormuz-Contract: <schema>;v=1`. Tenant and actor come from authentication.

After an attempt is no longer pending, save a strict request such as:

```json
{
  "schema_id": "hormuz.governed-run-attribution-request",
  "schema_version": 1,
  "request_attempt_id": "EXISTING_ATTEMPT_ID",
  "work_scope": {"work_scope_id": "RETURNED_OPAQUE_ID", "version": 1},
  "expected_attribution_event_id": "LATEST_ATTRIBUTION_EVENT_ID",
  "state": "active",
  "reason_code": "corrected"
}
```

```bash
hormuz --config hormuz.json portfolio attribute correction.json \
  --idempotency-key correction-001
hormuz --config hormuz.json portfolio attributions --limit 50
hormuz --config hormuz.json portfolio attributions --work-scope-id RETURNED_OPAQUE_ID
```

Credentials come from `HORMUZ_PORTFOLIO_TOKEN`, or the environment-variable
name supplied with `--token-env`; never put a token in a JSON file or command
argument. A denied command does not open the request file or create the DB.

For a first post-run binding, use expected event `null` and reason `bound`.
To void the latest event, supply its ID, set `work_scope` to `null`, `state`
to `voided`, and reason to `voided`. Rebinding uses the void's ID and reason
`corrected`. Exact idempotency replay returns the original immutable result
without a write. Different request data under the same key, or competing
expected prior events, conflict.

Reads return **immutable history**, not one row per request. `state` describes
an event at creation; superseded rows are not rewritten. Follow
`supersedes_event_id` to find the latest event for an attempt. Only that event
can supply the current primary; a void or null scope supplies none. Do not
count historical `active` rows as simultaneous primaries.

Pages default to 50, maximum 100, at a frozen sequence boundary. Cursors are
tenant-, actor- and role-bound, expire after one hour, and permit only a page
limit alongside them. Initial filters permit `work_scope_id` and paired UTC
`start_at`/`end_at`. Reads commit safe audit before delivery.

## Evidence and failure limits

Internal fact readers join immutable v1 attempt/event/usage IDs. Event-time
identity/team, client, policy, requested alias, resolved alias, routed model,
provider-reported model and cost basis remain distinct. Missing model is not
an alias; missing cost is not zero; a rate-card estimate is not final spend.

Rejected admissions produce only a fixed-class receipt when storage works;
they do not fabricate attempts or enter governed-attempt denominators.
Legacy requests, missing attribution after a crash, unknown outcomes and
missing actual models remain coverage gaps. Internal fact/count readers are
seams for later scorecards, not extra public endpoints or a dashboard.

Reservation and attribution are separate transactions. Provider traffic waits
for attribution commit. A proven pre-egress scope conflict may terminate the
reservation through the v1 failure lifecycle; uncertain storage failure
retains the hold for explicit reconciliation. No automatic provider replay or
cross-repository atomicity is claimed. Portfolio budgets, connector outcomes,
recommendations and causal productivity analysis remain outside this slice.
