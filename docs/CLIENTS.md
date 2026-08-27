# Connect existing AI clients

Hormuz sits between an employee's existing AI client and the provider API. The employee uses either a unique bootstrap identity token or a short-lived OIDC JWT access token; only the Hormuz service receives the organization's OpenAI or Anthropic credential.

Use TLS and an organization-controlled hostname outside local development. The examples below use `https://hormuz.example.com` as that deployment URL.

The public-alpha compatibility baseline is Codex CLI `0.147.0` and Claude Code
`2.1.233`. Blocking CI installs those exact releases; a separate weekly canary
checks the latest releases without provider credentials. A newer client is not
part of the supported baseline until its protocol path is deliberately
verified. See [live client conformance](LIVE_CLIENT_CONFORMANCE.md) for the
real-provider release gate and its metadata-only evidence boundary.

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

## Generic OIDC credentials

After configuring the issuer and explicit subject mapping described in [OIDC.md](OIDC.md), add `--actor` and `--auth-mode oidc` to either `client config` command. The Codex output uses its command-backed bearer-token configuration. The Claude output uses `apiKeyHelper`. Both invoke:

```bash
hormuz auth token --env HORMUZ_OIDC_ACCESS_TOKEN
```

The helper does not load server configuration and does not mint tokens. It re-reads the credential supplied by the organization's OIDC tooling. The company must currently ensure that source contains a valid JWT access token for the Hormuz audience.

## Deployment boundary

For a company rollout, endpoint management should install the client configuration and provision a unique identity for each human or service account. Shared employee tokens make per-person attribution and revocation unreliable. OIDC JWT verification is available now; native browser login, refresh-token custody, opaque-token handling, and SCIM remain later milestones. Provider keys remain server-side in every design.

Hormuz governs the model-provider request path. It does not govern Codex or
Claude Code shell commands, MCP servers, Git traffic, browser requests, or
other client-side tools. The current public-alpha protocol boundary is OpenAI
Responses and Anthropic Messages/count-tokens over HTTP/SSE; unsupported
features and fail-closed behavior are listed in
[live client conformance](LIVE_CLIENT_CONFORMANCE.md#unsupported-and-failure-behavior).
