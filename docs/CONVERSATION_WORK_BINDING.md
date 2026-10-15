# Automatic conversation work binding

With `ai_work.enabled: true`, Hormuz can associate native Codex HTTP requests
with one owned work item without requiring the caller to retain
`X-Hormuz-Work-ID`. The existing connection commands and skills-only setup path
use this router behavior. There is no additional setup flag, CLI command or
public endpoint.

A binding associates intercepted requests; it does not declare a completed
coding task. Provider success, conservative reservations, unknown outcomes,
local-only permission, route qualification and tool affinity keep their existing
meanings. See [admission](LOCAL_ROUTE_ADMISSION.md) and
[connected-client lifetime](CONNECTED_CLIENT_LIFETIME.md).

## Recognized wire envelope

Automatic association is limited to authenticated, accounted OpenAI-protocol
`POST /v1/responses` traffic identified by the router as Codex. It requires one
`thread-id` header and one `session-id` header, each a canonical UUID string,
with matching flat copies in `client_metadata`:

```http
POST /v1/responses HTTP/1.1
Authorization: Bearer <local-router-caller-token>
Content-Type: application/json
thread-id: 11111111-1111-4111-8111-111111111111
session-id: 22222222-2222-4222-8222-222222222222
```

```json
{
  "model": "local-code",
  "input": "Summarize this fixture.",
  "client_metadata": {
    "thread_id": "11111111-1111-4111-8111-111111111111",
    "session_id": "22222222-2222-4222-8222-222222222222"
  }
}
```

The example shows association fields, not a complete qualified client request.
Model capabilities and the admitted request envelope must still match. Thread
and session IDs may differ; a changing session does not split an unchanged
thread into new work. Each request's session header must still match its flat
body copy. Duplicate headers, malformed UUIDs and mismatched copies are rejected
with HTTP 400 `hormuz_ai_work_invalid_conversation_metadata`.
If `x-codex-turn-metadata` is supplied, its bounded JSON thread/session copies
must also agree. Recognized native conversation requests use strict bounded JSON
decoding; duplicate object names fail with HTTP 400 `invalid_json` before binding.

The association key includes the authenticated organization and actor, client,
protocol, endpoint and thread. `turn_id`, `prompt_cache_key`, window and
installation IDs do not identify work. Embedded metadata is not an alternative
identity source. IDs do not override caller authentication or establish that a
request came from an attested native installation.

Requests with neither native identity header retain the existing accounting
behavior; a partial pair is rejected. Disabled AI Work does not acquire automatic associations. Other
endpoints and clients are outside this slice.

## Creation, continuation and explicit work IDs

The router resolves an existing association after policy and secret inspection,
before route admission. A fresh request creates its work and association only
after admission. The existing ledger performs that creation atomically, so
concurrent first requests for the same owner and thread cannot mint separate
jobs. The existing local-capacity limit still applies to provider execution.

A recognized thread with unknown prior history receives HTTP 409
`hormuz_route_affinity_required`; it cannot create a new job from continuation
state. This also applies in configured routing mode. Automatic continuation
requires a prior successful response, an eligible pinned route, matching work
context and cache generation, and the current destination generation. Failed or
pending attempts cannot establish successful affinity. Opaque provider state is
not translated or adopted as a new conversation.
Actual `reasoning`, `item_reference` and encrypted-content input are classified
as state, unlike the optional encrypted-output include marker. Missing affinity
for those inputs receives 409 rather than a fresh-feature qualification denial.

A known association keeps its work ID when that job is paused, canceled or
otherwise limited. Existing work-state and budget checks apply; the router does
not mint replacement work to bypass them.

An explicit `X-Hormuz-Work-ID` retains the existing manual path:

- Without a known automatic association, an owned explicit work ID is used but
  does not establish a new automatic association.
- With a known association, the same explicit work ID is accepted. A different
  owned ID receives HTTP 409 `hormuz_ai_work_conversation_work_conflict` before
  provider dispatch.
- An inaccessible work ID keeps HTTP 404 `hormuz_ai_work_work_not_found`; thread
  metadata cannot confer another actor's authority.

Explicit work IDs do not relax policy, local-only enforcement or destination
generation checks. Native Codex must not receive a persistent per-conversation
work-ID override merely to use automatic binding.

## Public receipts and limits

Successful relay responses retain the existing headers:

```http
X-Hormuz-Work-ID: work-<opaque-work-id>
X-Hormuz-Work-Outcome: unknown
X-Hormuz-Work-Route: session_affinity
```

The route reason above describes a successful continuation; a fresh request can
have a different existing reason. The header never certifies task completion.
The authenticated owner can inspect `GET /v1/work/jobs`,
`GET /v1/work/jobs/{work_id}` and
`GET /v1/work/jobs/{work_id}/attempts`. These retain their existing response
shapes. Native client output need not expose the relay's headers, so the public
owned-job report remains the status path. It lists a bounded recent set and
reports truncation; it is not a raw-thread-ID lookup API.

At most 1,024 automatic bindings exist across all owners in one router epoch.
An additional new association receives HTTP 503
`hormuz_ai_work_conversation_capacity_reached`. Existing associations remain
usable subject to their normal gates. No eviction silently discards affinity or
creates replacement jobs.

## Private contract version 1, migration and rollback

The private binding contract is version 1. Its key domain is
`codex-conversation-v1`, and its storage is the additive `ai_work_conversations`
table in the existing AI Work SQLite ledger, whose schema version remains 1:

The [version-1 request fixture](../tests/fixtures/codex-conversation-request-v1.json)
freezes the compatible identity envelope through the public workflow.

| Column | Meaning |
| --- | --- |
| `organization_id`, `actor_id`, `client` | Existing authenticated ownership scope |
| `conversation_key` | HMAC-SHA256 of the scoped thread identity using the process-local key |
| `work_id` | Foreign key to the existing owned job |

The owner/client/key tuple is unique. New installations and existing supported
version-1 ledgers acquire the table with `CREATE TABLE IF NOT EXISTS`; existing
jobs, attempts and reservations retain their meaning. Association creation uses
`BEGIN IMMEDIATE`, including the global capacity check. No second database,
credential-verification key or transcript ledger is introduced. Neither raw
thread/session IDs nor the HMAC key are stored in this table. The private
association key is not added to public work receipts.

Runtime initialization clears the association table because the HMAC key and
execution-destination generations are new. Historical jobs and attempts remain
readable. Previously pending attempts keep conservative holds and unknown
outcomes. An old history-only request cannot rebuild an association after
restart; begin a fresh native conversation. No inference is replayed to recover
affinity.

Before upgrading or rolling back, disconnect with the binary owning the
connection and resolve configuration restoration. Older binaries do not provide
automatic binding. Preserve their matching verified ledger snapshot or retain
the newer binary, following [migration](MIGRATION_2.md); do not share the mutable
ledger between old and new writers. Start fresh conversations after reconnect,
or deliberately use the supported manual work-ID path with compatible fresh
state. Do not remove historical work evidence or alter local-only policy to make
a downgrade appear successful.

The [PR #529 qualification record](qualifications/codex-0147-llama-b11617-qwen3-4b.md)
records the earlier manual-binding baseline with its original pins, hashes,
outcomes and limitations. This slice supersedes its manual-binding requirement
for recognized traffic. That historical experiment does not establish native
qualification of this new association path; any new qualification must record
its own exact source, wire envelope and public workflow evidence.
