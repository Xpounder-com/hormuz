# Connect existing AI clients

Hormuz sits between an employee's existing AI client and the provider API. The employee uses either a unique bootstrap identity token or a short-lived OIDC JWT access token; only the Hormuz service receives the organization's OpenAI or Anthropic credential.

Use TLS and an organization-controlled hostname outside local development. The examples below use `https://hormuz.example.com` as that deployment URL.

The v1 compatibility baseline is Codex CLI `0.147.0` and Claude Code
`2.1.233`. Blocking CI installs those exact releases; a separate weekly canary
checks the latest releases without provider credentials. A newer client is not
part of the supported baseline until its protocol path is deliberately
verified. See [live client conformance](LIVE_CLIENT_CONFORMANCE.md) for the
real-provider release gate and its metadata-only evidence boundary.

Compatibility and pilot qualification are separate scopes. Hormuz continues to
support both protocol adapters and both pinned clients. The explicitly selected
`codex_openai` signed-Mac pilot qualifies only Codex `0.147.0` through the
`external_pilot_openai` gateway profile, with exactly the OpenAI Responses
protocol and the `openai-primary` and `openai-secondary` aliases. The legacy
`full_dual_provider` contract remains the default and requires both pinned
clients and both protocols. Missing Claude evidence never selects the narrow
contract. See [signed Mac pilot qualification](MACOS_PILOT_QUALIFICATION.md).

## Codex

Generate the configuration from the running Hormuz configuration:

```bash
hormuz --config /etc/hormuz/hormuz.json client config codex \
  --url https://hormuz.example.com
```

Put the generated block in the employee's user-level `~/.codex/config.toml`:

```toml
model = "gpt-5.4-mini"
model_provider = "hormuz"

[model_providers.hormuz]
name = "Hormuz"
base_url = "https://hormuz.example.com/v1"
env_key = "HORMUZ_TOKEN"
wire_api = "responses"
```

Set the employee-specific token through the organization's endpoint-management or secrets system, then use Codex normally:

```bash
export HORMUZ_TOKEN="employee-specific-hormuz-token"
codex
```

Codex supports custom model providers using a base URL and credential environment variable. Provider selection belongs in user-level configuration; current Codex builds ignore provider settings found only in project-local configuration. See the official [Codex configuration reference](https://developers.openai.com/codex/config-reference).

Hormuz defaults to native OpenAI model IDs in its example configuration. This lets Codex retain its bundled model metadata while Hormuz decides whether that model is allowed, capped, denied, or replaced with the configured fallback. Optional company aliases work at the HTTP layer, but arbitrary aliases may cause Codex to use fallback client metadata.

Some Codex versions probe the custom provider's `/v1/models` endpoint. Hormuz does not currently implement the private Codex catalog schema, because that schema includes version-specific agent instruction metadata rather than the public OpenAI Models API. A refresh warning is therefore expected; generation continues with Codex's bundled metadata when a native model ID is used.

## Claude Code

Generate the shell configuration:

```bash
hormuz --config /etc/hormuz/hormuz.json client config claude \
  --url https://hormuz.example.com
```

The result points the existing Claude Code client to Hormuz and sends the employee identity as a bearer token:

```bash
export HORMUZ_TOKEN="employee-specific-hormuz-token"
export ANTHROPIC_BASE_URL="https://hormuz.example.com"
export ANTHROPIC_AUTH_TOKEN="${HORMUZ_TOKEN}"
claude --model claude-sonnet-5
```

Anthropic documents `ANTHROPIC_BASE_URL` and `ANTHROPIC_AUTH_TOKEN` as the static-token path for an LLM gateway; the auth token is sent in the `Authorization` header. See Anthropic's [Claude Code authentication reference](https://code.claude.com/docs/en/authentication).

Do not set the company's `ANTHROPIC_API_KEY` on employee machines. Hormuz replaces the employee credential with the provider credential only for the upstream request.

Claude Code `2.1.129` and later can optionally query a gateway `/v1/models`
endpoint when `CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY=1`. Hormuz does not
yet implement that optional discovery surface. Leave it disabled and select an
explicit supported model ID; generation continues through `/v1/messages`.
See Anthropic's [LLM gateway reference](https://code.claude.com/docs/en/llm-gateway).

## Optional client-side context optimization

The normal Hormuz Mac connector can run either supported client through a
short-lived authenticated relay on `127.0.0.1`. Its **Context optimization**
toggle is Off by default and is saved per connection profile. When On, the
relay may replace verified repetitive `rg`/`Glob` tool results with a bounded,
lossless `structural-v1` representation. Unsupported histories, tools, result
shapes, client versions, missing tokenizer resources, and older gateways pass
through unchanged.

The relay sends exactly one representation of each request to the gateway. It
does not upload the source and compact forms together, and it does not persist
request content or token estimates. The compact form remains reversible, so the
gateway can read it and reconstruct selected blocks in memory to preserve its
existing secret checks. The provider receives the governed final request.

Direct configurations generated by the commands above do not use the local
optimizer. Sign in with the Hormuz Mac app, review and save its dedicated
launcher, and run that launcher to use the toggle. Existing launchers must be
reviewed and saved once after upgrading. See [client-side context
optimization](CONTEXT_OPTIMIZATION.md) for formats, limits, setup, and the exact
privacy boundary.

## Generic OIDC credentials

After configuring the issuer and explicit subject mapping described in [OIDC.md](OIDC.md), add `--actor` and `--auth-mode oidc` to either `client config` command. The Codex output uses its command-backed bearer-token configuration. The Claude output uses `apiKeyHelper`. Both invoke:

```bash
hormuz auth token --env HORMUZ_OIDC_ACCESS_TOKEN
```

The helper does not load server configuration and does not mint tokens. It re-reads the credential supplied by the organization's OIDC tooling. The company must currently ensure that source contains a valid JWT access token for the Hormuz audience.

## Deployment boundary

For a company rollout, endpoint management should install the client configuration
and provision a unique identity for each human or service account. Shared employee
tokens make per-person attribution and revocation unreliable. OIDC JWT verification
is available. The opt-in [browser-login broker](HOSTED_LOGIN_LOCAL.md) adds opaque,
rotating client sessions, and [team onboarding](TEAM_ONBOARDING.md) adds invitations
and member removal. Hormuz v1.2.0 includes a signed Apple Silicon distribution and
bounded Okta, Render, hosted-recovery, and OpenAI/Codex qualification for the exact
release candidate. Other identity providers, model providers, deployments, clients,
operating systems and architectures require their own qualification. SCIM,
independent customer acceptance, and any availability or latency SLA remain future
work. Provider keys remain server-side.

The native app exposes the same distinction as an explicit setup choice. Its
hosted Codex/OpenAI preset enforces HTTPS, Codex, and the two approved aliases;
the custom setup retains the generic client controls. Selecting the preset does
not change the server profile or prove any qualification gate by itself.

Hormuz governs the model-provider request path. It does not govern Codex or
Claude Code shell commands, MCP servers, Git traffic, browser requests, or
other client-side tools. The current v1 protocol boundary is OpenAI
Responses and Anthropic Messages/count-tokens over HTTP/SSE; unsupported
features and fail-closed behavior are listed in
[live client conformance](LIVE_CLIENT_CONFORMANCE.md#unsupported-and-failure-behavior).
Requests are readable to the customer-operated gateway process while it applies
policy, redaction, budget controls, routing, and usage accounting. Hormuz adds no
request or response content store; operators must still control gateway host,
process, administrator, and observability access.
