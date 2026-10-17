# Install Koto in an existing agent

The [README](../README.md#install-in-your-agent) lists default-branch install
commands. All packages expose the same [Koto skill](../skills/koto/SKILL.md)
and [shared workflow](../skills/koto/references/workflow.md). They contain no
MCP server, lifecycle hook, background installer or separate agent loop.

## Choose a source revision

The examples below select `main` explicitly. For a preview, replace `main`
with a reviewed branch or tag in the same repository. Use a fresh profile
when testing a preview so an active installation is preserved.

Codex (CLI `plugin add` checked on 0.160.0):

```sh
codex plugin marketplace add mehrdadxzaker/hormuz --ref main
codex plugin add koto@koto
```

Gemini CLI:

```sh
gemini extensions install https://github.com/mehrdadxzaker/hormuz --ref main
```

GitHub Copilot CLI:

```sh
copilot plugin marketplace add 'mehrdadxzaker/hormuz#main'
copilot plugin install koto@koto
```

Claude Code (or replace `claude plugin` with `/plugin` inside a session):

```sh
claude plugin marketplace add 'mehrdadxzaker/hormuz#main'
claude plugin install koto@koto
```

For Cursor, OpenCode or another agent supported by the open Agent Skills
installer, clone the selected revision into a new directory, then install the
skill from that local source. Choose an unused directory name if
`koto-preview` exists:

```sh
git clone --depth 1 --single-branch --branch main \
  https://github.com/mehrdadxzaker/hormuz.git koto-preview
npx skills add ./koto-preview --skill koto --agent cursor
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
**“Use Koto to check my Codex connection”**, **“Use Koto to set up local-only
routing for Codex”**, or **“Use Koto to disconnect Codex and restore settings.”**
Claude Code also exposes `/koto:koto`. The plugin's Codex onboarding points
at the same skill; no setup logic is hidden in its manifests.

| Installed surface | What this package supplies | Routing evidence |
| --- | --- | --- |
| Codex | Native marketplace entry and portable skills plugin | Exact CLI versions 0.147.0 and [0.160.0](CODEX_0160.md), plus [app engine 0.162.0-alpha.17.2](CODEX_APP_COMPATIBILITY.md); host/plugin installation is a separate version check |
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
| Codex | `codex plugin marketplace upgrade koto`, then use the plugin browser to review/update | `codex plugin remove koto@koto`, then `codex plugin marketplace remove koto` |
| Gemini CLI | `gemini extensions update koto` | `gemini extensions uninstall koto` |
| Copilot CLI | `copilot plugin marketplace update koto`, then `copilot plugin update koto` | `copilot plugin uninstall koto`, then `copilot plugin marketplace remove koto` |
| Claude Code | `claude plugin marketplace update koto`, then `claude plugin update koto@koto` | `claude plugin uninstall koto@koto`, then `claude plugin marketplace remove koto` |
| Agent Skills installer | `npx skills update koto` | `npx skills remove koto --agent AGENT` (add `--global` if used at install) |

**Disconnect first:** plugin removal removes instructions, not the router's
connection owner. Run the shared disconnect workflow and inspect its receipt
before uninstalling. Keep newer user edits and report retained setting names.

## Packaging and validation

`plugin.json` is the portable identity. `.agents/plugins/marketplace.json`
points Codex at the repository root. `.claude-plugin/` supplies Claude's manifest
and the catalog also understood by Copilot CLI. `gemini-extension.json` makes
the root installable by Gemini. All discover `skills/koto/`; no vendor copy
of the workflow is maintained. Skill bundle version `0.3.0` is independent of
the Python router's development version.

Run `python tools/verify_router_scope.py` for offline identity, path, content
and link checks. Vendor installation/activation is separate qualification; a
valid manifest is not evidence that the agent has loaded a skill. Do not test
by installing into an active user's configuration. Use disposable profiles
and bounded commands without real provider credentials.

Koto replaces the former plugin identity. For an existing installation,
disconnect through the old plugin first, remove that plugin from the host, and
install `koto@koto` from the marketplace again. Start a new session after the
switch. Existing router configuration and local data stay compatible; see the
[rebrand notes](KOTO_REBRAND.md).

Local check: Codex CLI 0.160.0 registered the local marketplace, installed
`koto@koto`, copied the skill and its reference, and listed it as installed
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
