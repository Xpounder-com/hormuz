# Governed context in Codex and Claude Code

Hormuz exposes one read-only local MCP tool, `hormuz_get_context`. The stdio
adapter calls the authenticated Hormuz Context Pack API; it does not open the
context database, read provider credentials, or accept caller-supplied
organization, team, actor, policy-version, or evaluation-time fields.

```text
Codex or Claude Code
        |
        | MCP stdio: hormuz_get_context(query, budget, optional narrower scope)
        v
hormuz mcp
        |
        | HTTPS + employee Hormuz credential
        v
POST /v1/context/packs
        |
        +--> authenticate employee
        +--> derive organization, team, actor, and policy
        +--> authorize and budget before content decode
        +--> apply server-owned lifecycle state and contradiction/quarantine rules
        +--> commit metadata-only read audit
        v
governed context pack
```

The adapter supports the legacy MCP initialization handshake used by current
clients and the MCP `2026-07-28` per-request discovery protocol. It emits only
newline-delimited JSON-RPC on stdout. Context and credentials are never logged
by the adapter.

## Before configuring a client

The Hormuz HTTP service must be reachable and the employee must have a unique
Hormuz credential. For local development:

```bash
export HORMUZ_TOKEN="employee-specific-hormuz-token"
hormuz --config hormuz.json serve
```

Use HTTPS for every non-loopback deployment. `hormuz mcp` rejects plaintext
HTTP to a non-loopback host, URLs containing credentials, and URLs containing a
query or fragment. Company OpenAI and Anthropic keys remain only on the Hormuz
service and are not needed by the MCP process.

## Codex

Generate a secret-free configuration:

```bash
hormuz mcp-config codex --url https://hormuz.example.com
```

Add the result to the employee's `~/.codex/config.toml`:

```toml
[mcp_servers.hormuz]
command = "hormuz"
args = ["mcp", "--url", "https://hormuz.example.com", "--credential-env", "HORMUZ_TOKEN", "--timeout-seconds", "30"]
env_vars = ["HORMUZ_TOKEN"]
startup_timeout_sec = 10
tool_timeout_sec = 35
required = true
```

Then verify the installed client sees the server:

```bash
codex mcp list
codex mcp get hormuz --json
```

Codex desktop, CLI, and IDE surfaces share the MCP configuration. The official
[Codex MCP documentation](https://learn.chatgpt.com/docs/extend/mcp?surface=cli)
also documents `codex mcp add` and the equivalent TOML fields.

## Claude Code

Generate a secret-free project configuration:

```bash
hormuz mcp-config claude --url https://hormuz.example.com
```

Merge the resulting `mcpServers.hormuz` object into a project `.mcp.json`, a
user-scoped configuration, or an organization-managed MCP configuration:

```json
{
  "mcpServers": {
    "hormuz": {
      "type": "stdio",
      "command": "hormuz",
      "args": [
        "mcp",
        "--url",
        "https://hormuz.example.com",
        "--credential-env",
        "HORMUZ_TOKEN",
        "--timeout-seconds",
        "30"
      ],
      "env": {
        "HORMUZ_TOKEN": "${HORMUZ_TOKEN}"
      }
    }
  }
}
```

Verify with:

```bash
claude mcp list
claude mcp get hormuz
```

The official [Claude Code MCP documentation](https://code.claude.com/docs/en/mcp)
documents local stdio servers, environment expansion, project/user scopes, and
managed organization configuration.

## Tool contract

Required arguments:

- `query`: a concrete task or question;
- `token_budget`: a positive integer. The server may enforce a lower cap.

Optional arguments may only narrow retrieval: `max_items`, `repository_id`,
`branch`, `clearance`, and `include_provisional`. A branch requires a
repository. The employee cannot expand identity or policy scope through MCP.

The success result includes the complete `hormuz.context-pack.v1` object as
both structured content and JSON text for older clients. Provider-neutral
items retain classification, verification, freshness, provenance, content
hash, relevance, and token-estimate metadata. The additive lifecycle object
reports a trusted snapshot hash, `complete`, `partial`, or
`requires_resolution`, plus explicit authorized exclusions and contradiction
sources. Empty authorized retrieval is a successful pack with an empty `items`
array.

Gateway denials are returned as MCP tool errors with the stable Context Pack
API code, such as `context_policy_denied`, `unauthorized`, or
`context_rate_limited`. Transport failures use a sanitized
`context_gateway_unavailable` error. Redirects are not followed, response size
is bounded, and explicit client cancellation suppresses the result even when a
blocking HTTP request must finish in the worker before its timeout.

## Current enforcement boundary

This is a real tool connection, not automatic prompt injection. Codex and
Claude Code can discover and invoke `hormuz_get_context`, and the server
instructions tell them when policy requires governed context. A model may still
choose not to call a tool. Hard enforcement that injects an authorized pack
into every applicable model request is tracked separately because it must place
retrieved content before the existing secret-egress inspection and preserve
client protocol compatibility.

The current credential is inherited from an environment variable. Browser
OIDC login, short-lived session refresh, and OS secure-store custody now exist
for provider-gateway helpers. This MCP adapter still reads its credential from
an inherited environment variable; it does not yet invoke a saved session profile.
That additive adapter change remains separate from the provider-gateway session path.
