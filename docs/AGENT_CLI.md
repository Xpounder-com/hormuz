# Hormuz Agent CLI

The folder-first Agent CLI is a macOS preview for Codex and Claude sessions:
read-only planning, plan acceptance, concrete action approval, execution,
observed diff review, interruption, and explicit session resume. Open a terminal
in the folder you want to work on. A typed request starts planning; execution is
a separate command.

Interactive execution requires macOS, an interactive terminal, and Node.js 24 or
later. The Python `hormuz` launcher additionally requires Python 3.11 or later.
Windows and Linux interactive execution fail closed pending a qualified process
sandbox. Portable tests and offline commands do not qualify those launchers.

## Install from this checkout

From the Hormuz repository root:

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --editable .
npm ci --prefix clients/agent-runtime --ignore-scripts --maxsockets=4 --fetch-retries=2 --fetch-timeout=30000 --no-audit --no-fund
npm run build --prefix clients/agent-runtime
```

The npm build creates `clients/agent-runtime/dist`. It is required even when
running from source. The Python launcher discovers the Agent entry from its own
installed checkout; it does not run scripts found in the current project folder.

For Codex, separately provide **Codex CLI 0.160.0**, using its native executable
or the pinned official npm distribution. The default path is
`/opt/homebrew/bin/codex`. For another location, supply
`--codex-executable /absolute/path/to/codex` during configuration. Claude uses the
package's pinned **Claude Agent SDK 0.3.299** and its bundled native CLI; no
separate Claude Code installation is required for this entry.

Load the provider API credential into the environment of the shell that starts
Hormuz, using your usual credential-management workflow:

| Engine | Host environment variable |
| --- | --- |
| Codex | `OPENAI_API_KEY` |
| Claude | `ANTHROPIC_API_KEY` |

The CLI does not request or save API keys in profile configuration. The host
reads the appropriate variable and mediates inference through a private
loopback proxy. This preview uses provider API authentication; ChatGPT and
Claude subscription sign-in or entitlements are not used.

Select a project folder, then configure and open the terminal:

```sh
cd /absolute/path/to/your-project
hormuz agent init --engine codex
hormuz agent doctor
hormuz
```

`hormuz` with no arguments opens the Agent terminal for the current folder.
`hormuz agent` opens the same terminal explicitly. Existing gateway subcommands
keep their explicit command names; gateway `--config` does not configure Agent.
For Claude, configure a fresh profile with
`hormuz agent init --profile claude --engine claude`, then launch
`hormuz agent --profile claude`.

The setup prompts require an explicit model supported by your provider account
and conservative **USD per million input and output tokens** for that exact
model, service tier, and cache behavior. There are no vendor model or price
defaults. You can supply those settings with `--model`, `--input-rate`, and
`--output-rate`; changing engine or model requires explicit new input and output
rates. Plain decimal USD values accept up to six fractional digits; scientific
notation and extra precision are rejected.

## Install a standalone local npm artifact

`@hormuz/agent-runtime` is currently unpublished. Build and install a local
tarball from the checkout rather than an npm registry release:

```sh
cd /absolute/path/to/hormuz/clients/agent-runtime
npm ci --ignore-scripts --maxsockets=4 --fetch-retries=2 --fetch-timeout=30000 --no-audit --no-fund
npm run build
npm pack
npm install --global ./hormuz-agent-runtime-0.1.0.tgz --ignore-scripts --maxsockets=4 --fetch-retries=2 --fetch-timeout=30000 --no-audit --no-fund
```

Use your usual user-writable npm global prefix. The resulting `hormuz-agent`
entry requires Node.js 24 or later and can run without Python:

```sh
cd /absolute/path/to/your-project
hormuz-agent init --engine codex
hormuz-agent doctor
hormuz-agent
```

The same flags and terminal commands apply to `hormuz-agent` and `hormuz agent`.
With the Python Hormuz package also installed, a wheel installation finds the
standalone `hormuz-agent` entry on `PATH`; the source launcher prefers its own
checkout's built entry.

## Consent, plans, and approvals

Before the first plan or resume in each launch, the terminal displays the folder,
engine, model, and session allowance, and asks for cloud inference consent.
Prompts and tool context will go to OpenAI for Codex or Anthropic for Claude.
Consent defaults to **no** and is not saved in the profile. Type `yes` explicitly
when the prompt appears. Piped approval input is not accepted.

For example, type a request such as “Inspect the failing tests and propose a
small fix,” or launch with an initial prompt:

```sh
hormuz agent --prompt "Inspect the failing tests and propose a small fix"
```

Planning does not grant project mutations. Read the resulting plan, use
`/accept` for its current revision, then use `/execute`. Acceptance alone starts
no execution. Execution still asks for each concrete action that needs
permission, showing the operation and working directory. Type `yes` to approve
that action once; a blank or other answer declines it. Planning questions accept
an explicit option number or text answer.

| Terminal command | Behavior |
| --- | --- |
| Plain request or `/plan TEXT` | Investigate and propose a read-only plan |
| `/accept` | Accept the current plan revision for this launch |
| `/revise TEXT` | Edit the plan and clear its previous acceptance |
| `/reject` | Reject the plan and clear its execution authority |
| `/execute` | Consume acceptance and start the approved plan with separate action decisions |
| `/diff` | Show observed added, changed, and removed files, plus unavailable coverage |
| `/review accept`, `/review reject`, `/review changes` | Record a disposition for the last displayed diff revision |
| `/status` | Show phase, configured-rate accounting, holds, allowance, and runtime attempts |
| `/sessions` | List history for this folder and profile |
| `/resume ID` | Reopen matching history with fresh plan acceptance |
| `/new` | Create a new session for the current folder and fixed engine/model |
| `/interrupt` or Ctrl+C during active work | Cancel active preparation, inference, or pending decisions |
| `/help`, `/quit` | Show help or close the owned terminal and workers |

Ctrl+C while idle exits the terminal. EOF and termination also close owned work.
After interruption, an execution needs fresh plan acceptance or a new plan.
Review decisions record your assessment; they do not revert files, execute
follow-up work, commit, merge, publish, or deploy. Observed changes can include
concurrent user edits, so their authorship remains unknown.

## Budgets and private history

Setup defaults to a **$1 lifetime session allowance, $5 lifetime project
allowance, and $10 lifetime profile allowance**. Change them with `--budget`,
`--project-budget`, and `--profile-budget`; the session allowance must not exceed
the project allowance, which must not exceed the profile allowance. Token bounds
default to 100,000 input and 4,096 output tokens; configure them with
`--max-input-tokens` and `--max-output-tokens`.

These are **configured-rate estimates for admitted work, not provider
account-wide billing caps**. Each generation reserves a conservative estimate
before egress and settles observed usage at your configured rates. Uncertain
dispatched requests retain holds; `/status` reports uncertain usage within held
funds. Lifetime accounting persists across restarts and session deletion. It
does not reset monthly or include unrelated requests made outside Hormuz.
Raising a configured ceiling does not erase recorded usage.

The default state root is `~/.hormuz/agent-cli`; a profile's configuration,
sessions, runtime history, and usage journal are under
`profiles/<profile-name>/`. The default profile name is `default`. Use
`--profile NAME` to select another private profile. Folders share that profile's
state and lifetime profile budget, while each canonical folder gets a stable
named project ID. Symlink aliases of a folder resolve to the same project.

State must be outside the selected workspace. If working in your home directory,
or another folder containing the default state, choose a persistent private
directory outside it with `--state-root /absolute/path/to/private-state`. Repeat
the same profile and state-root options on later commands. Private directories
use mode 0700 and configuration files mode 0600; linked or unsafe state and
unsupported configuration schemas fail closed. Ambient project configuration
does not supply keys, grants, or cloud consent.

`hormuz agent init` saves profile configuration. Configuration flags on an
interactive launch apply to that launch; use `init` to persist changes.
Existing sessions keep their recorded engine, model, and session allowance.

Use `/diff` before restarting when you need the full ordinary text comparison.
Only the latest execution baseline's safe text remains in memory. Restart
retains provenance hashes and coverage, so prior **before-text can be
unavailable**. Sensitive files remain hash-only, and omitted files are reported
as unavailable rather than falsely added or deleted. Provider-owned conversation
history remains in its private session runtime subtree.

## Offline checks and explicit resume

```sh
hormuz agent --help
hormuz agent doctor
hormuz agent sessions
hormuz agent --resume last
# Or: hormuz agent --resume SESSION_ID
```

`--help`, `--version`, `init`, `doctor`, and `sessions` perform no provider
inference. `doctor` reports configuration, platform, runtime location, and
whether the expected credential variable is present, without displaying its
value. It does not launch a native engine or establish runtime compatibility;
its output explicitly reports `runtimeVersionVerified: false`.

Resume is explicit. `--resume last` chooses the latest history matching the
current folder, profile, engine, and model. Workspace, configuration/connection,
runtime version, allowance, and cloud permission are rechecked. Stored history
never restores action grants or plan execution authority: accept a retained
current plan again, or request a new plan if it has become stale. There is no
automatic paid replay or engine fallback.

See [the backend and its limits](../clients/agent-runtime/README.md) and
[the Agent roadmap](AGENT_EXECUTION_ROADMAP.md) for desktop integration and
release qualification work. CLI implementation is tracked in
[#521](https://github.com/Xpounder-com/hormuz/issues/521).
