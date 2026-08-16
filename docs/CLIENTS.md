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

Codex supports custom model providers using a base URL and credential environment variable. Provider selection belongs in user-level configuration; current Codex builds ignore provider settings found only in project-local configuration. See the official [Codex configuration reference](https://learn.chatgpt.com/docs/config-file/config-reference).

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

Anthropic documents `ANTHROPIC_BASE_URL` and `ANTHROPIC_AUTH_TOKEN` as the static-token path for an LLM gateway; the auth token is sent in the `Authorization` header. See Anthropic's [Claude Code authentication reference](https://code.claude.com/docs/en/authentication).

Do not set the company's `ANTHROPIC_API_KEY` on employee machines. Hormuz replaces the employee credential with the provider credential only for the upstream request.

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

## Image and file boundary

Codex and Claude Code can represent provider image and file inputs, but Hormuz does not yet have a trusted byte decoder/classifier. The secure default therefore denies recognized OpenAI image/file/screenshot blocks and Anthropic image or non-text document/file blocks before provider egress. The employee keeps the same client configuration; the request receives the provider-shaped DLP denial. Inline Anthropic text documents remain inspectable and usable.

The same unchanged client configuration automatically receives the DLP policy resolved from the authenticated employee's organization, team, and actor identity. A narrower overlay can only strengthen an enabled organization rule and optionally narrow its provider/model scope; the employee cannot select or bypass the overlay in a request.

An organization can set `egress_controls.dlp.rules.opaque_media.action` to `off`, but that is an explicit risk acceptance: the media then reaches the provider without Hormuz inspecting its bytes. See [SECRET_CONTROLS.md](SECRET_CONTROLS.md) for exact covered shapes and residual gaps.

## Deployment boundary

For a company rollout, endpoint management should install the client configuration and provision a unique identity for each human or service account. Shared employee tokens make per-person attribution and revocation unreliable. Browser login and opaque Hormuz session rotation are implemented; real-IdP validation, SCIM, admin revocation, and HA session persistence remain release gates. Provider keys remain server-side in every design.
