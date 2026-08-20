# Governed context in Codex and Claude Code

> **Deprecated experimental compatibility adapter.** Hormuz's supported Codex and Claude Code integration is the AI gateway path; the built-in context MCP surface is not an enterprise release gate. See [ADR 0008](decisions/0008-gateway-product-boundary.md).

Hormuz exposes one read-only local MCP tool, `hormuz_get_context`. The stdio
adapter calls the authenticated Hormuz Context Pack API; it does not open the
context database, read provider credentials, or accept caller-supplied
organization, team, actor, policy-version, or evaluation-time fields.

```text
Codex or Claude Code
        |
        | MCP stdio: hormuz_get_context(query, budget, optional narrower scope)
        v
hormuz mcp --profile CLIENT_PROFILE
        |
        | HTTPS + current short-lived Hormuz session credential
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

## Recommended human-session setup

The Hormuz HTTP service must be reachable and its browser session broker must
be configured. Create a separate client-bound secure-store profile for each
employee client:

```bash
hormuz login \
  --gateway https://hormuz.example.com \
  --profile codex \
  --client codex

hormuz login \
  --gateway https://hormuz.example.com \
  --profile claude \
  --client claude-code
```

The terminal redeems the browser enrollment into the operating system's secure
store. Access credentials are short-lived and refresh credentials never appear
in the generated MCP configuration, process arguments, stdout, or model-visible
tool errors. Company OpenAI and Anthropic keys remain only on the Hormuz service.

Use HTTPS for every non-loopback deployment. Session-profile commands require
HTTPS unless `--allow-insecure-http` is explicitly set for `127.0.0.1`, `::1`,
or `localhost` development. All modes reject URL credentials, queries, and
fragments.

## Codex

Generate a secret-free configuration:

```bash
hormuz mcp-config codex \
  --url https://hormuz.example.com \
  --profile codex
```

Add the result to the employee's `~/.codex/config.toml`:

```toml
[mcp_servers.hormuz]
command = "hormuz"
args = ["mcp", "--url", "https://hormuz.example.com", "--profile", "codex", "--timeout-seconds", "30"]
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
hormuz mcp-config claude \
  --url https://hormuz.example.com \
  --profile claude
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
        "--profile",
        "claude",
        "--timeout-seconds",
        "30"
      ]
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

## Workload-token compatibility mode

CI, service accounts, and existing managed installations may continue to use an
inherited Hormuz workload credential. If `--profile` is omitted, the adapter
defaults to `HORMUZ_TOKEN`; a different safe variable name may be selected with
`--credential-env`:

```bash
export HORMUZ_TOKEN="workload-specific-hormuz-token"
hormuz mcp-config codex --url https://hormuz.example.com
hormuz mcp-config claude --url https://hormuz.example.com
```

`--profile` and `--credential-env` are mutually exclusive. Environment mode
preserves the original generated configuration and request behavior.

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
hash, relevance, and token-estimate metadata. The response and deterministic
pack identity name the active policy, retrieval, and render versions. The additive lifecycle object
reports a trusted snapshot hash, `complete`, `partial`, or
`requires_resolution`, plus explicit authorized exclusions and contradiction
sources. Empty authorized retrieval is a successful pack with an empty `items`
array.

The tool returns one organization-bounded pack and has no pagination argument.
The server rejects cursor or page fields so repeated pages cannot bypass the
token and item caps. MCP calls retain explicit transport timeouts, bounded
responses, and cancellation suppression.

Gateway denials are returned as MCP tool errors with the stable Context Pack
API code, such as `context_policy_denied`, `unauthorized`, or
`context_rate_limited`. Transport failures use a sanitized
`context_gateway_unavailable` error. Redirects are not followed, response size
is bounded, and explicit client cancellation suppresses the result even when a
blocking HTTP request must finish in the worker before its timeout.

In profile mode, the adapter resolves the secure-store session for every tool
request rather than capturing one access credential when the stdio process
starts. The existing session client reuses an access credential while it has
more than 60 seconds remaining and otherwise rotates the access/refresh pair
under a cross-process profile lock. Missing login, revoked/expired session,
refresh failure, secure-store failure, and malformed credential all become the
fixed `context_auth_unavailable` tool error. The model does not receive the
profile, secure-store backend, refresh failure, or credential value.

An opt-in macOS integration test exercises the complete installed-client path
with exact Codex and Claude Code binaries. Each client obtains its inference
credential through the profile helper, receives a provider-shaped model tool
call, invokes this MCP process with the same client-bound profile, and returns
the selected pack to the next provider request. The child client environment
contains neither a static Hormuz identity credential nor either provider key;
the temporary session is stored in the real macOS Keychain and deleted after
the test.

## Current enforcement boundary

This is a real explicit tool connection, not automatic prompt injection. Codex and
Claude Code can discover and invoke `hormuz_get_context`, and the server
instructions tell them when policy requires governed context. A model may still
choose not to call a tool. Hormuz also has a separate disabled-by-default gateway
policy that automatically injects verified unscoped or exact administrator-granted
repository packs into supported generation requests before secret-egress inspection.
Repository selection travels through consumed official-client headers and remains
independent of this MCP tool. That bounded path does not
change this tool contract; see [CONTEXT_INJECTION.md](CONTEXT_INJECTION.md).

The MCP adapter supports both a saved human-session profile and the original
inherited workload-token mode. It does not enforce that a model calls the tool,
make the local secure store remotely manageable, or provide shared multi-node
session revocation. Real-IdP validation, SCIM, KMS-backed shared session storage,
and the accepted enterprise persistence topology remain separate release gates.
The macOS installed-client proof is local, opt-in evidence; it is not a blocking
Linux/Windows secure-store certification or a production identity-provider test.
