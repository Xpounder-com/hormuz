# Connect existing AI clients

Hormuz sits between an employee's existing AI client and the provider API. The employee uses a unique bootstrap credential, a workload OIDC JWT access token, or a short-lived Hormuz human session; only the Hormuz service receives the organization's OpenAI or Anthropic credential.

This page configures provider traffic through Hormuz. To add governed organizational context as a native tool in the same clients, also follow [MCP.md](MCP.md).

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

Codex supports custom model providers using a base URL, credential environment variable, and static custom `http_headers`. Provider selection belongs in user-level configuration; current Codex builds ignore provider settings found only in project-local configuration. See the official [Codex configuration reference](https://developers.openai.com/codex/config-reference).

Hormuz defaults to native OpenAI model IDs in its example configuration. This lets Codex retain its bundled model metadata while Hormuz decides whether that model is allowed, capped, denied, or replaced with the configured fallback. Optional company aliases work at the HTTP layer, but arbitrary aliases may cause Codex to use fallback client metadata.

Some Codex versions probe the custom provider's `/v1/models` endpoint. Hormuz does not implement the private Codex catalog schema, because that schema includes version-specific agent instruction metadata rather than the public OpenAI Models API. Hormuz's model-discovery response implements only Anthropic's published Claude Code contract. Codex continues with its bundled metadata when a native model ID is used.

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
export CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY=1
claude --model claude-sonnet-5
```

Anthropic documents `ANTHROPIC_BASE_URL` and `ANTHROPIC_AUTH_TOKEN` as the static-token path for an LLM gateway; the auth token is sent in the `Authorization` header. See Anthropic's [Claude Code authentication reference](https://code.claude.com/docs/en/authentication).

Do not set the company's `ANTHROPIC_API_KEY` on employee machines. Hormuz replaces the employee credential with the provider credential only for the upstream request.

Claude Code v2.1.129 or later can discover gateway models at startup. The generated Hormuz configuration enables that opt-in behavior. Claude sends `GET /v1/models?limit=1000` using the same employee bearer token or `x-api-key` helper credential used for inference. Hormuz authenticates the employee, resolves the organization/team/person policy for `claude-code`, and returns up to 1,000 allowed Anthropic-route aliases whose IDs contain `claude` or `anthropic`, which is the client compatibility filter. Use one of those strings in company-facing Claude aliases when picker discovery is required.

Discovery returns policy aliases, never upstream model names or provider credentials. It does not call a provider, reserve budget, or create a usage event. Inference still performs the authoritative live policy and budget check. Hormuz accepts the exact published discovery query and fails other `/v1/models` query shapes closed.

See Anthropic's [gateway protocol reference](https://code.claude.com/docs/en/llm-gateway-protocol#model-discovery) for the client request and response contract.

## Exact repository scope for automatic context

Repository context is never inferred from a working directory and a request header is never authorization. First grant the exact repository in the applicable administrator policy:

```json
{
  "context_injection": {
    "mode": "optional",
    "allowed_repositories": ["Xpounder-com/hormuz"],
    "max_classification": "internal"
  }
}
```

Then generate the project profile for either existing client:

```bash
hormuz --config /etc/hormuz/hormuz.json client-config codex \
  --url https://hormuz.example.com \
  --repository Xpounder-com/hormuz \
  --branch main \
  --revision abc123

hormuz --config /etc/hormuz/hormuz.json client-config claude \
  --url https://hormuz.example.com \
  --repository Xpounder-com/hormuz \
  --branch main \
  --revision abc123
```

Codex receives an `http_headers` map in its custom-provider configuration. Claude Code receives newline-separated `ANTHROPIC_CUSTOM_HEADERS` in its managed settings or shell environment, as documented in Anthropic's [environment-variable reference](https://code.claude.com/docs/en/env-vars) and [LLM gateway guide](https://code.claude.com/docs/en/llm-gateway). Endpoint management should install the profile appropriate to that repository. Branch and revision are optional narrowing; revision requires branch. Regenerate or update a pinned revision when trusted repository state changes.

Hormuz accepts only one bounded safe value for each of `X-Hormuz-Repository`, `X-Hormuz-Branch`, and `X-Hormuz-Revision`. It verifies the repository against the effective policy and the optional revision against its trusted repository/branch snapshot, then consumes all three. They never reach the provider. See [CONTEXT_INJECTION.md](CONTEXT_INJECTION.md) for failure and audit behavior.

## Generic OIDC credentials

After configuring the issuer and explicit subject mapping described in [OIDC.md](OIDC.md), add `--actor` and `--auth-mode oidc` to either `client-config` command. The Codex output uses its command-backed bearer-token configuration. The Claude output uses `apiKeyHelper`. Both invoke:

```bash
hormuz auth token --env HORMUZ_OIDC_ACCESS_TOKEN
```

The helper does not load server configuration and does not mint tokens. It re-reads the credential supplied by the organization's OIDC tooling. The company must currently ensure that source contains a valid JWT access token for the Hormuz audience.

## Browser SSO and Hormuz sessions

For human employees, prefer the accepted session-broker path in [OIDC.md](OIDC.md). Log in separately for each client-bound profile, then generate the native client configuration:

```bash
hormuz login --gateway https://hormuz.example.com --profile codex --client codex
hormuz login --gateway https://hormuz.example.com --profile claude --client claude-code

hormuz --config /etc/hormuz/hormuz.json client-config codex \
  --url https://hormuz.example.com --actor alice --auth-mode session --profile codex
hormuz --config /etc/hormuz/hormuz.json client-config claude \
  --url https://hormuz.example.com --actor alice --auth-mode session --profile claude
```

The generated Codex auth command invokes `hormuz auth token --gateway ... --profile ...`. Claude Code's string-valued `apiKeyHelper` uses the shell-safe `--gateway-env HORMUZ_SESSION_GATEWAY` form, with the non-secret URL supplied in its managed `env` block. Both read the OS secure store and rotate the session when needed. Neither prints the refresh credential.

The repository's opt-in macOS installed-client test exercises this exact helper
and the profile-based MCP configuration together. A fake model requests
`hormuz_get_context`; the stock client returns the authorized pack and then
finishes generation. The test removes static Hormuz tokens and provider keys
from the child environment, and removes its transient Keychain entries during
cleanup. This is local compatibility evidence, not real-IdP or production-host
certification.

The pinned Codex and Claude Code publication gates separately prove the native
authentication-hook retry contract: each client first receives a deliberately
invalid, ephemeral credential; the local gateway returns `401`; the client
re-invokes its helper; and only the fresh credential reaches the provider path.
The test stores only helper-invocation markers, asserts that neither test
credential appears in output or the usage database, and does not claim that a
revoked Hormuz session can be refreshed. Secure-store rotation and
replay-family revocation are tested at the session-broker boundary.

## Image and file boundary

Codex and Claude Code can represent provider image and file inputs, but Hormuz does not yet have a trusted byte decoder/classifier. The secure default therefore denies recognized OpenAI image/file/screenshot blocks and Anthropic image or non-text document/file blocks before provider egress. The employee keeps the same client configuration; the request receives the provider-shaped DLP denial. Inline Anthropic text documents remain inspectable and usable.

The same unchanged client configuration automatically receives the DLP policy resolved from the authenticated employee's organization, team, and actor identity. A narrower overlay can only strengthen an enabled organization rule and optionally narrow its provider/model scope; the employee cannot select or bypass the overlay in a request.

An organization can set `egress_controls.dlp.rules.opaque_media.action` to `off`, but that is an explicit risk acceptance: the media then reaches the provider without Hormuz inspecting its bytes. See [SECRET_CONTROLS.md](SECRET_CONTROLS.md) for exact covered shapes and residual gaps.

## Deployment boundary

For a company rollout, endpoint management should install the client configuration and provision a unique identity for each human or service account. Shared employee tokens make per-person attribution and revocation unreliable. Browser login and opaque Hormuz session rotation are implemented; real-IdP validation, SCIM, admin revocation, and HA session persistence remain release gates. Provider keys remain server-side in every design.
