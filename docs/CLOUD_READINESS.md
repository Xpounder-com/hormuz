# Cloud readiness before connection

The shared read-only preflight reports the current router configuration and
the blockers for three distinct authorization paths. It does not activate a
connection, read credential values or call a model provider.

```sh
hormuz --config hormuz.json client cloud-readiness codex
hormuz --config hormuz.json client cloud-readiness codex --actor developer
hormuz --config hormuz.json client cloud-readiness codex --surface desktop
```

Terminal and skill callers use the same command and
`ConnectedClientLifetime.cloud_readiness` implementation. Installing a plugin
does not activate routing. The pinned CLI version is read from the connection
implementation; this snapshot pins Codex CLI 0.147.0. The desktop option does
not run a terminal binary or treat its version as desktop qualification.

| Path | Current result | Remaining work |
| --- | --- | --- |
| `api_key` | `blocked` or `unverified`; `api_usage` billing | An explicit permitted cloud route, valid credentials and separate provider/client qualification |
| `native_codex_gateway` | `not_implemented`; `chatgpt_plan` billing | Native gateway adapter, a supported build, workspace-managed setup, actual consent and eligibility |
| `chatgpt_app` | `not_implemented`; `chatgpt_plan` billing | App-specific authorization, registration, stable host ID, plan request profile, actual consent and eligibility |

The API result describes declarations only. `unverified` means the CLI version
matches the connection pin, no managed configuration was detected, and the
effective identity, team, actor and organization policy permits a configured
OpenAI cloud route. Existing Codex user settings are not read or validated.
Credentials, provider health, actual request eligibility, budgets, host
overrides and subscription allowance remain unverified. It is never an
authorization grant. A stored API key is not ChatGPT plan authorization; the
subscription paths never prescribe adding an API key or a paid API route.

Hard local-only mode appears as a blocker on all paths, including when the
selected actor is unknown. Missing or invalid configuration, denied client or
model policy, a missing or unsupported CLI, desktop qualification and managed
setup have content-free recovery actions. Managed configuration is detected
by existence without reading its contents. Project/profile/launch overrides
and the effective provider used by a running client require separate diagnosis;
this receipt is explicitly scoped to `configuration_only`.

Preflight captures the router file using the connection owner's bounded,
nonblocking, no-follow reader. The normal strict parser validates that captured
snapshot and builds metadata without resolving environment or file credentials.
It does not open the client's auth cache, local credential files or usage
databases; does not create a home, lock or journal; and does not enter a
connection transaction. An existing connection and its generation are retained.
CLI inspection invokes only `--version` using the existing stripped environment,
three-second deadline, 4 KiB output bound and process cleanup. It starts no
router, model, download, sign-in or registration flow.

## Contract and rollback

`hormuz.cloud-readiness` version 1 is an additive CLI receipt listed by
`hormuz contract manifest`. Its strict validator accepts only the three known
paths, fixed billing/documentation facts, known blocker/action pairs,
configuration-only scope and zero-effect assertions. It rejects readiness
claims, missing consent blockers, private fields and inconsistent eligibility.
The [version 1 fixture](../tests/fixtures/cloud-readiness-v1.json) preserves
compatibility. No existing receipt, router configuration, credential, journal
or database version changes. Older installations do not recognize the new
command; no persisted state needs migration or rollback.

## Implementation acceptance

The owner selected this slice on October 15, 2026 while coding qualification
(#538), verified acquisition (#542) and plugin capability work (#545) proceed
independently. It advances the inspection stage of
[the approved workflow](https://github.com/Xpounder-com/hormuz/issues/523).
Tests stay at that public seam: invoke the real CLI against disposable client
and provider fixtures, connect and submit a request, inspect the receipt and
status, then disconnect and observe restoration. Negative cases must preserve
settings, credentials and provider traffic. Public receipt validation is part
of the inspection boundary. Native subscription, actual account and desktop
qualification remain separate work; this slice does not close #539, #541, #543
or the parent epic.

## Official sources

Checked October 15, 2026. The
[native gateway documentation](https://learn.chatgpt.com/docs/enterprise/sign-in-with-chatgpt-through-a-gateway)
requires separate gateway and ChatGPT credentials and describes workspace-managed
provider requirements. The
[app-specific authorization documentation](https://developers.openai.com/siwc/token-sharing-open-source/)
requires its own registration, stable host identity and plan consent. Existing
[API/provider gateway compatibility](https://learn.chatgpt.com/docs/enterprise/gateway-compatibility)
does not establish either subscription capability. These sources describe
available paths, not Hormuz implementation or this user's entitlement.
