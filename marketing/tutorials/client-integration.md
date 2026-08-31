# Connect one coding client through Hormuz

This tutorial is a navigation layer over the maintained [client guide](../../docs/CLIENTS.md),
not a second configuration contract. Baselines at preparation: Codex 0.147.0
and Claude Code 2.1.233; newer versions require verification.

1. Complete the [provider-free quickstart](https://usehormuz.github.io/docs/).
   It does not require a provider account, employee tokens, or a shared server.
2. Choose one non-production client path and a named gateway operator. Follow
   the root [README](../../README.md#configure-providers-and-clients) for
   server configuration. Company provider keys stay on the server.
3. Outside local development, configure TLS and an organization-controlled
   hostname. `hormuz.example.com` below is illustrative, not a public service.
4. Generate configuration for the chosen client:

```bash
hormuz --config /etc/hormuz/hormuz.json client config codex \
  --url https://hormuz.example.com

# Or, for Claude Code:
hormuz --config /etc/hormuz/hormuz.json client config claude \
  --url https://hormuz.example.com
```

5. For Codex, use the employee's **user-level** configuration. For Claude Code,
   use the generated environment settings and an explicit supported model.
   Leave optional gateway model discovery disabled. See the maintained guide
   for exact settings and the Codex private-catalog warning.
6. Provision a unique bootstrap identity or an OIDC JWT through approved
   organization tooling. Never share an employee token among people. The OIDC
   helper reads a supplied JWT; it does not mint or refresh it.
7. Make one approved synthetic request, then one request your policy denies.
   Check policy/model outcomes, identity attribution, and the absence of an
   upstream call for the denial. Inspect metadata through the [usage guide](../../docs/USAGE.md).

## Done means

Record the client version, Hormuz source/image selection, relevant policy
version, observed outcomes, and open gaps. Confirm prompts/responses and keys
are absent from exported evidence. Do not put private logs or configuration in
public issues. A running client is not proof of every protocol feature, complete
provider-account coverage, production fitness, or governance of client-side
shell/MCP/Git/browser actions.

Use [Support](../../SUPPORT.md) and [live-client conformance](../../docs/LIVE_CLIENT_CONFORMANCE.md)
for evidence and troubleshooting boundaries before widening the deployment.
