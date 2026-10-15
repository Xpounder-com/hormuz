# Install Hormuz in an existing agent

The [README](../README.md#install-in-your-agent) lists default-branch install
commands. All packages expose the same [Hormuz skill](../skills/hormuz/SKILL.md)
and [shared workflow](../skills/hormuz/references/workflow.md). They contain no
MCP server, lifecycle hook, background installer or separate agent loop.

## Preview this branch

Until [PR #527](https://github.com/Xpounder-com/hormuz/pull/527) lands, choose one
of these installs. They select the branch containing the manifests, rather than
the default branch where this package is not yet available.

Codex (CLI `plugin add` checked on 0.160.0):

```sh
codex plugin marketplace add Xpounder-com/hormuz --ref mehrdad/router-scope-cleanup-20261014
codex plugin add hormuz@hormuz
```

Gemini CLI:

```sh
gemini extensions install https://github.com/Xpounder-com/hormuz --ref mehrdad/router-scope-cleanup-20261014
```

GitHub Copilot CLI:

```sh
copilot plugin marketplace add 'Xpounder-com/hormuz#mehrdad/router-scope-cleanup-20261014'
copilot plugin install hormuz@hormuz
```

Claude Code (or replace `claude plugin` with `/plugin` inside a session):

```sh
claude plugin marketplace add 'Xpounder-com/hormuz#mehrdad/router-scope-cleanup-20261014'
claude plugin install hormuz@hormuz
```

For Cursor, OpenCode or another agent supported by the open Agent Skills
installer, clone the preview into a new directory, then install the skill from
that local source. Choose an unused directory name if `hormuz-preview` exists:

```sh
git clone --depth 1 --single-branch --branch mehrdad/router-scope-cleanup-20261014 \
  https://github.com/Xpounder-com/hormuz.git hormuz-preview
npx skills add ./hormuz-preview --skill hormuz --agent cursor
```

Replace `cursor` with `opencode`, or omit `--agent` to choose interactively.
The skills installer defaults to the current project; add `--global` for a
user-wide installation. Installations may download the repository or installer;
review the host's normal trust prompt.

## Use and compatibility

For a new local-only connection, the skill now calls
[`client setup codex`](SHARED_LOCAL_SETUP.md) to generate configuration from
existing verified runtime/model files. An installed router remains a
prerequisite; shared setup manages its local caller credential without manual
exports. User invocation, actual skill activation and supported
target versions remain separate from package installation.

Start a fresh agent session if the host has not loaded the new skill. Ask
**“Use Hormuz to check my Codex connection”**, **“Use Hormuz to set up local-only
routing for Codex”**, or **“Use Hormuz to disconnect Codex and restore settings.”**
Claude Code also exposes `/hormuz:hormuz`. The plugin's Codex onboarding points
at the same skill; no setup logic is hidden in its manifests.

| Installed surface | What this package supplies | Routing evidence |
| --- | --- | --- |
| Codex | Native marketplace entry and portable skills plugin | Persistent adapter pins Codex CLI 0.147.0; host/plugin installation is a separate version check |
| Gemini CLI | Skills-only extension | Gemini inference-routing adapter is not implemented |
| GitHub Copilot CLI | Marketplace plugin; reads the Claude-compatible catalog | Copilot inference-routing adapter is not implemented |
| Claude Code | Native marketplace and plugin manifest | Persistent Claude adapter is not implemented; non-Claude main-model substitution is not promised |
| Cursor, OpenCode and other supported agents | Portable skill through the open Agent Skills installer | Each target requires its own adapter and real qualification |

The plugin can manage a supported Codex target from any of these hosts. It does
not intercept the host's model calls merely by being installed. Router/model
installation and real qualification remain prerequisites. Installing a skill
does not establish subscription entitlement or authorize API spending. See
[support](../SUPPORT.md) and [connection lifetime](CONNECTED_CLIENT_LIFETIME.md).

## Update and remove

| Host | Refresh/update | Remove instructions |
| --- | --- | --- |
| Codex | `codex plugin marketplace upgrade hormuz`, then use the plugin browser to review/update | `codex plugin remove hormuz@hormuz`, then `codex plugin marketplace remove hormuz` |
| Gemini CLI | `gemini extensions update hormuz` | `gemini extensions uninstall hormuz` |
| Copilot CLI | `copilot plugin marketplace update hormuz`, then `copilot plugin update hormuz` | `copilot plugin uninstall hormuz`, then `copilot plugin marketplace remove hormuz` |
| Claude Code | `claude plugin marketplace update hormuz`, then `claude plugin update hormuz@hormuz` | `claude plugin uninstall hormuz@hormuz`, then `claude plugin marketplace remove hormuz` |
| Agent Skills installer | `npx skills update hormuz` | `npx skills remove hormuz --agent AGENT` (add `--global` if used at install) |

**Disconnect first:** plugin removal removes instructions, not the router's
connection owner. Run the shared disconnect workflow and inspect its receipt
before uninstalling. Keep newer user edits and report retained setting names.

## Packaging and validation

`plugin.json` is the portable identity. `.agents/plugins/marketplace.json`
points Codex at the repository root. `.claude-plugin/` supplies Claude's manifest
and the catalog also understood by Copilot CLI. `gemini-extension.json` makes
the root installable by Gemini. All discover `skills/hormuz/`; no vendor copy
of the workflow is maintained. Skill bundle version `0.1.0` is independent of
the Python router's development version.

Run `python tools/verify_router_scope.py` for offline identity, path, content
and link checks. Vendor installation/activation is separate qualification; a
valid manifest is not evidence that the agent has loaded a skill. Do not test
by installing into an active user's configuration. Use disposable profiles
and bounded commands without real provider credentials.

Local check: Codex CLI 0.160.0 registered the local marketplace, installed
`hormuz@hormuz`, copied the skill and its reference, and listed it as installed
in a disposable profile. The profile was removed afterward. No model task or
active client configuration was changed. Gemini, Copilot and Claude CLIs were
not installed locally; their formats/commands were checked against these
references, not exercised as native installation/activation tests.

Command and manifest references:

- [Codex commands](https://learn.chatgpt.com/docs/developer-commands) and [plugin packaging](https://developers.openai.com/plugins/build/plugins).
- [Gemini extension reference](https://geminicli.com/docs/extensions/reference/).
- [Copilot CLI plugin reference](https://docs.github.com/en/copilot/reference/copilot-cli-reference/cli-plugin-reference).
- [Claude Code installation](https://code.claude.com/docs/en/discover-plugins) and [manifest reference](https://code.claude.com/docs/en/plugins-reference).
- [Open Agent Skills installer](https://github.com/vercel-labs/skills).
