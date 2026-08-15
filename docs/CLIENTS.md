# Connect existing AI clients

Hormuz sits between an employee's existing AI client and the provider API. The employee receives a unique Hormuz identity token; only the Hormuz service receives the organization's OpenAI or Anthropic credential.

Use TLS and an organization-controlled hostname outside local development. The examples below use `https://hormuz.example.com` as that deployment URL.

## Codex

Generate the configuration from the running Hormuz configuration:

```bash
hormuz --config /etc/hormuz/hormuz.json client-config codex \
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

Codex supports custom model providers using a base URL and credential environment variable. Provider selection belongs in user-level configuration; current Codex builds ignore provider settings found only in project-local configuration. See the official [Codex configuration reference](https://developers.openai.com/codex/config-reference/).

Hormuz defaults to native OpenAI model IDs in its example configuration. This lets Codex retain its bundled model metadata while Hormuz decides whether that model is allowed, capped, denied, or replaced with the configured fallback. Optional company aliases work at the HTTP layer, but arbitrary aliases may cause Codex to use fallback client metadata.

Some Codex versions probe the custom provider's `/v1/models` endpoint. Hormuz does not currently implement the private Codex catalog schema, because that schema includes version-specific agent instruction metadata rather than the public OpenAI Models API. A refresh warning is therefore expected; generation continues with Codex's bundled metadata when a native model ID is used.

## Claude Code

Generate the shell configuration:

```bash
hormuz --config /etc/hormuz/hormuz.json client-config claude \
  --url https://hormuz.example.com
```

The result points the existing Claude Code client to Hormuz and sends the employee identity as a bearer token:

```bash
export HORMUZ_TOKEN="employee-specific-hormuz-token"
export ANTHROPIC_BASE_URL="https://hormuz.example.com"
export ANTHROPIC_AUTH_TOKEN="${HORMUZ_TOKEN}"
claude --model claude-sonnet-5
```

Anthropic documents `ANTHROPIC_BASE_URL` and `ANTHROPIC_AUTH_TOKEN` as the static-token path for an LLM gateway; the auth token is sent in the `Authorization` header. See Anthropic's [Claude Code LLM gateway configuration](https://docs.anthropic.com/en/docs/claude-code/llm-gateway).

Do not set the company's `ANTHROPIC_API_KEY` on employee machines. Hormuz replaces the employee credential with the provider credential only for the upstream request.

## Deployment boundary

For a company rollout, endpoint management should install the client configuration and issue a unique, revocable Hormuz identity to each human or service account. Shared employee tokens make per-person attribution and revocation unreliable. A later enterprise milestone will replace static employee tokens with SSO-backed short-lived credentials; the provider keys remain server-side in either design.

