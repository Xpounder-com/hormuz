# Policy impact console (opt-in local source feature)

The console now supports **Usage → proposed team/model output limit → preview →
review → apply → request receipts → rollback**, with an Activity page for recent
proposals and immutable policy lifecycle events. This source feature is not a
hosted deployment or release claim. Native clients keep their existing controls.

## Operator setup and process boundary

Use an existing managed PostgreSQL policy deployment with the local administrator
console and managed onboarding configured. Provision the managed organization’s
initial policy and persistent administrator grants before evaluation. Keep its
static login identities out of the gateway configuration: onboarding rejects
those overlapping identities. Gateway and console take a credential-free policy
validation projection from the local managed directory at startup; restart both
after adding organizations. This projection grants no authentication or policy
write authority. Upgrade all policy readers/writers before staging a version-2 document.

1. Set `authentication.session_broker.policy_impact_enabled: true` alongside
   `enabled`, `onboarding_enabled`, and `console_enabled`. All defaults remain off.
2. Give the verified member an existing console grant (`report_viewer` or
   `member_admin`) and independently grant its exact OIDC issuer/subject
   [policy-administrator authority](POLICY_CONTROL.md#administrator-changes).
   Neither console role, email, group, nor employee credential grants policy writes.
3. Restart the gateway with its existing **runtime** database credential and
   provider/session credentials. It captures metadata but exposes no policy writes.
4. Start the separate listener with the same non-secret configuration, public
   origin, session master key, OIDC login secret, session database path, and
   routing definitions:

   ```sh
   python -m hormuz.policy_console --config /private/operator/hormuz.json --port 8788
   ```

   This listener binds loopback only. Its environment needs the configured runtime
   database DSN for usage reads and the separate policy-control DSN for policy
   operations. Its loader does not resolve static employee tokens, provider keys,
   migration credentials, or break-glass credentials. Keep those out of its
   process environment. The two processes share an owner-only local directory;
   this slice does not support independent hosts or network filesystems.
5. Route `/console`, `/console/*`, and `/v1/admin/*` at the existing public origin
   to this listener. Keep inference and `/v1/auth/*` on the gateway. Preserve exact
   Host, Origin, cookies and configured trusted-ingress checks. Register the
   existing `/v1/admin/auth/callback` URI at the IdP. Configuration here does not
   deploy a proxy, change credentials, or grant an administrator.

The listener permits eight concurrent handlers, a bounded accept queue and a
10-second socket timeout, in addition to the existing console request limiter.
Its handlers have no inference routes. A write rechecks the persistent PostgreSQL
policy grant inside the serialized transaction. Browser membership/session/grant
validity is checked at request admission and when preparing the command;
revocation is not a distributed cancellation of a command already admitted.

## Using and demonstrating it

Open `/console`, sign in, and choose **Preview a policy change**. Select a team
and model alias, enter a lower ceiling, and click **Preview impact**. The setup
lists the first 100 managed teams and 128 routes; the API reports the teams cursor
but this initial UI does not page larger directories. Review scope and possible
truncation before checking the acknowledgement and applying.

To demonstrate with meaningful evidence, first issue controlled requests under
the existing policy through a connected native client. Preview a lower limit,
apply it, issue another controlled request, then refresh the result page. Each
receipt names the enforced limit and exact policy version. Choose **Review
rollback**, acknowledge, and restore the previous version. Activity reopens your
last 20 retained proposals and shows the organization's last 20 lifecycle events.

The form uses an explicit preview button instead of the prototype's live slider.
It remains script-free under the existing restrictive CSP. The real console
never fabricates traffic or gives a browser session inference authority. Tests
use disposable IdP/provider simulators; their output is not customer evidence.

## Interpretation and bounds

Capture is a nonblocking queue of at most 256 attempt records. The sidecar stores
up to 4,000 observations per organization and 20,000 globally, with a seven-day
window. Expired rows are purged on startup, reads, and writes; the running
capture worker also sweeps every 60 seconds while idle. An offline or disabled
process cannot delete files: the next open sweeps them, and the operator owns
deletion while the feature is off. Proposals are capped at 100 per organization
and 1,000 globally, preventing a single organization from consuming the shared
capacity. Queue saturation, unavailable storage, interrupted requests and process
exit can lose capture; the denominator is always the **captured sample**, not all
usage. Pending or missing outcomes are unknown. No request/response content is
stored, and the immutable usage ledger is unchanged.

An observation contains exactly: request attempt ID, organization/team/actor IDs,
resolved model alias, policy version, original requested output limit, serialized
effective output limit, UTC start time, route fingerprint, outcome, known output
tokens and configured-rate-card cost estimate. The alias-route fingerprint
contains no credential and excludes samples made with different route definitions.
Each provider failover attempt is a separate observation.

Preview uses only the chosen organization/team/model, exact active policy version
and route fingerprint. Existing tighter request/actor/org caps remain effective.
A lower-limit count measures changed ceilings. Completions above the proposed
ceiling warn of possible truncation; they do not establish savings or quality.
Savings and quality fields remain null. A scope without observations can be
previewed but cannot be applied through this workflow.

## Reviewed proposal and concurrency contract

A proposal pins the current policy digest **and activation generation**, candidate
digest, route fingerprint, exact scope/ceiling, captured comparison and a ten-minute
approval expiry. The server stores it with a membership-bound HMAC derived from
the existing session master key using a dedicated domain. This protects temporary
browser proposal state; it is not a transferable approval credential or a new
custody approval key. Re-login by the same active membership can reopen it.

Apply accepts only the opaque proposal ID and an acknowledgement, never candidate
JSON or a caller-supplied actor. It reconstructs the candidate and atomically
compares both baseline digest and generation. Another activation invalidates the
proposal, including changing away and back to the same digest (ABA). Concurrent
applies have one winner. A lost-response retry is idempotent only at the immediately
resulting candidate/generation.

Rollback restores the **complete** preceding policy document and is available
only while the reviewed candidate remains the latest activation. Any intervening
activation prevents this rollback. Policy history remains immutable. Results and
rollback links last up to seven days, subject to current session and root authority;
apply approval still expires after ten minutes. Session master-key rotation
invalidates retained proposal signatures.

## Versioned data and compatibility

`hormuz.policy-document` **v1 remains unchanged**, including canonical bytes and
hashes. V2 adds the required closed `policies.team_model_output_limits` mapping:

```json
{"engineering": {"gpt-5.4": 4000}}
```

It contains at most 128 teams, each with 1–128 configured model aliases. Caps are
strict integers from 1 to 1,000,000. V2 permits an empty top-level mapping. Effective
caps take the minimum of this binding and existing organization/team/actor caps.
Policy fallback uses the resolved alias; operational failover also honors the
alternate alias while retaining the original restriction. Requests already
admitted keep their pinned document.

The nested structural `change_summary` has its own version: v2 retains v1 fields
and adds `model_output_limits: {team_count, binding_count}`. No scope names, model
names, cap values or content are added to this summary. The outer lifecycle event
and SQL tables are unchanged. New readers accept both nested versions; historical
v1 documents/events retain their original serialization. The installed manifest
adds a distinct document-v2 entry and preserves its historical entries.

**Upgrade/downgrade:** old binaries reject v2 documents/summaries. Do not mix old
and new readers once any v2 version is staged. Roll back the active policy to the
previous v1 version using upgraded tooling before reverting runtime binaries.
Older status/history readers still cannot read retained v2 rows, even after a
policy rollback; keep upgraded administration tools. Do not delete immutable rows
to enable downgrade. No usage/PostgreSQL/session schema migration is introduced.
The optional disposable sidecar has its own schema version 1; unsupported or
partial shapes fail closed. Disabling capture does not remove an active cap.

## Browser transport

The existing cookie, exact Host/Origin, CSRF, closed-input and no-bearer rules apply.
JSON integers reject booleans and floats; form token limits are decimal integers.
All fields below are required, with no extra or duplicate keys:

| Method / route | Input after session authentication | Output |
| --- | --- | --- |
| GET `/v1/admin/policy/scopes` | No query | Scoped teams/routes and current policy/generation |
| POST `/v1/admin/policy/preview` | `csrf_token`, `team_id`, `model_alias`, `proposed_limit` | Signed-state preview projection |
| POST `/v1/admin/policy/review` | `csrf_token`, `preview_id` | Revalidated preview |
| POST `/v1/admin/policy/apply` | `csrf_token`, `preview_id`, `acknowledged: true` | Results |
| GET `/v1/admin/policy/results` | Only `preview_id` query | Results |
| POST `/v1/admin/policy/rollback-review` | `csrf_token`, `preview_id` | Results with rollback available |
| POST `/v1/admin/policy/rollback` | `csrf_token`, `preview_id`, `acknowledged: true` | Results |

HTML pages are `/console/policy`, `/console/policy/results?preview_id=…`, and
`/console/policy/activity`. Form POSTs return HTML; JSON POSTs return JSON. Errors
reuse `hormuz.admin-error` v1 and fixed messages (400 invalid input, 401 absent or
revoked session, 403 authority/origin/CSRF, 404 unavailable scope, 409 stale/expired
proposal or no observations, 503 storage/capacity). Submitted values never enter
error messages.

The separate JSON shapes have `schema_id` and integer `schema_version: 1`:

- `hormuz.policy-impact-comparison`: nonnegative integer `captured_requests`,
  `lower_limit_requests`, `unchanged_limit_requests`, `known_completions`,
  `completions_above_limit`, `unknown_completions`; `coverage` is
  `bounded_captured_sample_only`; `availability` is `available` or `no_observations`;
  `savings` and `quality_effect` are null.
- `hormuz.policy-impact-preview`: opaque `preview_id` (at most 64 characters),
  organization/team/model IDs, nullable `current_limit`, positive `proposed_limit`,
  `baseline_version`, positive `baseline_generation`, `candidate_version`,
  `routing_fingerprint`, UTC `created_at`/`expires_at`, `comparison`, nullable UTC
  `observed_since`, boolean `can_apply`. Digests are `sha256:` plus 64 hex digits.
- `hormuz.policy-impact-results`: `preview`, active digest/generation, boolean
  `candidate_active`/`rollback_available`; nonnegative counts `captured_requests`,
  `known_errors`, `unknown_outcomes`, `cost_known_requests`; integer summed
  `estimated_cost_microusd`; at most 20 metadata `receipts`; the same bounded
  `coverage`; `cost_basis: configured_rate_card_estimate`; null savings/quality.
  A zero summed estimate with zero known-cost requests means unavailable cost.
- `hormuz.policy-impact-scopes`: organization ID, policy version/generation,
  at most 100 existing managed-team records, nullable opaque `teams_next_cursor`,
  at most 128 `{alias, protocol}` models. Protocol is `openai` or `anthropic`.

## Validation

Focused tests cover bounded/lossy capture, exact tenant scope, unknown outcomes,
v1 bytes, v2 caps and failover, real OIDC/session/CSRF transport, tampering, expiry,
revocation, idempotence, receipts and rollback. PostgreSQL tests additionally cover
persistent root grants, concurrent generation CAS, pinned requests, v1 restoration,
and an isolated listener that starts without inference credentials. Browser QA
covers the complete flow, responsive widths and script-free form behavior.

The unrelated finance account-binding v8 preflight pins a historical 161-file
runtime. Its CI invocation now explicitly verifies that historical plan rather
than treating the changed checkout as the old release. Frozen hashes and the
default strict runtime-mismatch rejection remain intact, and the output states
that the current finance runtime was not qualified.
