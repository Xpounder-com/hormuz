# Native Codex model discovery

The router has an opt-in discovery endpoint for the documented native Codex
gateway path. It consumes a separate Hormuz gateway credential and forwards
the coding client's native ChatGPT credential only to the fixed ChatGPT
backend. [Native Responses](NATIVE_CODEX_RESPONSES.md) adds separately enabled
HTTP/SSE inference. Workspace-managed client setup, supported host/build
qualification, consent and actual account eligibility remain incomplete. A catalog response grants
none of those capabilities. [Cloud readiness](CLOUD_READINESS.md) continues
to report `native_codex_gateway: not_implemented`.

## Configuration

An administrator can add the following fields to an existing reviewed router
configuration. Keep its identities, policy ceilings and local-only choices.
The model identifier must be explicitly selected and allowed for that identity;
the identifier below is a fixture, not a claim about available account models.

```json
{
  "native_codex_catalog": true,
  "upstreams": {
    "native-codex": {
      "protocol": "openai",
      "locality": "cloud",
      "authentication": "codex_chatgpt",
      "base_url": "https://chatgpt.com/backend-api/codex"
    }
  },
  "model_routes": {
    "native-code": {
      "protocol": "openai",
      "destination": "native-codex",
      "upstream_model": "fixture-cloud-model"
    }
  }
}
```

The boolean defaults to false. The new authentication profile requires this
fixed HTTPS backend, `openai` protocol and `cloud` locality. It rejects API-key
environment/envelope fields and storage/background opt-ins. An API-key route
is unnecessary. Generic `/v1/models` discovery omits native profiles; preflight
counts only API-key routes for the API authorization path. Native profiles
cannot execute through generic API/local endpoints or automatic/fallback
selection. Native HTTP/SSE inference requires its separate Responses flag.
Existing CLI/plugin connect remains the existing API/local adapter; this slice
does not generate native host configuration or change `requires_openai_auth`.
Both generated configuration and persistent connection defaults skip native
profiles. If no generic inference model is allowed, they fail before installing
settings. AI-work selection also excludes native profiles before choosing or
persisting an exploration route.

## Request boundary

`GET /backend-api/codex/models` requires one `X-Gateway-Key` for an existing
Hormuz identity and one `Authorization: Bearer ...` for native ChatGPT auth.
An optional `ChatGPT-Account-ID` is forwarded unchanged. The client owns native
sign-in and refresh; Hormuz does not read a login cache or persist these native
headers. Duplicate/ambiguous headers and known Hormuz/API credentials presented
as native tokens fail. The backend remains authoritative for native grants.

The only supported query is an optional numeric `client_version`, such as
`0.147.0`; accepting that parameter does not qualify a build. The gateway uses
the fixed `https://chatgpt.com/backend-api/codex/models` URL, verified TLS, native
auth/account headers and `Accept: application/json`. Gateway keys, API keys,
cookies and other caller headers are never forwarded. No redirect, system
proxy, retry or API billing fallback is used.

Identity and effective organization/team/actor policy must permit Codex and
the configured native model. Hard config-level and request-level
`X-Hormuz-Routing-Mode: local_only` forbid discovery before egress. Generic
endpoints reject native intent (`X-Gateway-Key` or `ChatGPT-Account-ID`) before
authentication fallback can send a request to an API/local model.

An owned isolated Python worker performs each fetch with a three-second socket
timeout and a parent-enforced three-second deadline covering system DNS,
connection, TLS and response processing. Native credentials cross a private
stdin pipe; they are absent from its command line, environment and stderr.
The worker receives only TLS trust-store/system-root environment names, has
no child processes, closes its connection on exit and is always reaped. On a
deadline, cleanup allows 0.2 seconds after termination before killing the owned
worker, then at most one second for reaping. No resolver thread survives a
timeout. The server admits at most eight native requests/workers at once.
The outbound slot is released before writing the response. Native responses
close their client connection and use a separate three-second socket write
timeout; a stalled reader cannot retain a slot or delay shutdown indefinitely.
Partial writes close the connection without attempting another response.
It reads at most 1 MiB plus an overflow byte and accepts at most
256 unique nonempty model slugs. A strict JSON
`models` array is required; malformed/duplicate records fail. Allowed model
objects retain provider-owned metadata; root extras and unconfigured models
are discarded. No model-usage request is recorded for discovery.

Missing or ambiguous auth returns 401; denied policy/generic inference returns
403; unsupported queries return 400; disabled discovery returns 404. Native
401, 403 and 429 retain their status with a fixed, content-free gateway error;
a valid bounded Retry-After is retained on 429. Other native statuses, TLS,
transport, deadline or catalog errors return 502. Local capacity exhaustion
returns 429. Provider bodies, redirects and credential values are not reflected
in errors. Failures never choose another provider or billing path.

## Contract, migration and rollback

Success uses provider-owned JSON with a `models` array and the additive header
`X-Hormuz-Contract: hormuz.native-codex-catalog;v=1`. The public contract manifest
lists the header contract; the [version 1 fixture](../tests/fixtures/native-codex-catalog-v1.json)
captures its request/response boundary. Errors retain `hormuz.gateway-error`
version 3 and existing error codes. No database, credential, journal or existing
receipt changes version. Existing configurations retain their behavior.

Adding the opt-in flag/authentication profile is an explicit config migration;
older binaries reject them. Disconnect through the shared connection owner
before changing a running configuration. Restart creates a new destination
generation under [ADR 0015](decisions/0015-execution-destination-ownership.md).
To downgrade, disable/remove the flag and remove native upstreams, routes and
their policy/fallback references, then use the older reviewed binary. Preserve
local credentials, journals, accounting and unrelated client edits. There is
no new persisted native-auth state to migrate or delete.

## Evidence boundary

Workflow tests use disposable TLS services, external DNS/trust-store fixtures
(including in the real isolated worker) and synthetic credentials through
the real CLI, gateway, policy and transport. They establish credential custody,
filtering, denial, limits and cleanup. They do not establish subscription
entitlement, native inference, OS/client support or release readiness.

The [official gateway guide](https://learn.chatgpt.com/docs/enterprise/sign-in-with-chatgpt-through-a-gateway),
checked October 15, 2026, documents separate gateway/native credentials,
workspace-managed provider requirements and this backend. The catalog contract covers `/models`; the separate
[Responses contract](NATIVE_CODEX_RESPONSES.md) covers opt-in inference. See [ADR 0027](decisions/0027-native-codex-catalog.md).
