# Hormuz

Hormuz is a customer-local model router for existing coding clients. Its entry
points are a terminal CLI and a skills-only plugin that calls the same
setup, status and disconnect implementation. Coding clients retain their editor,
tools, permissions and agent loop.

The approved workflow is **connect → submit a coding-client request through the
router → inspect result/status → disconnect**. See [scope and implementation
order](docs/ROUTER_SCOPE.md) and [specification #523](https://github.com/Xpounder-com/hormuz/issues/523).

The current source implements destination ownership, automatic route admission,
hard local-only enforcement and recoverable Codex CLI connection lifetime.
Routine tests use disposable clients and model endpoints. Real llama.cpp/model
quality, subscription eligibility, installed-platform support and plugin
distribution remain separate qualification work. The plugin bundles setup,
status, troubleshooting and disconnect instructions over the CLI. MCP is
optional if a concrete integration needs it.

A [pinned Codex → llama.cpp profile](docs/qualifications/codex-0147-llama-b11617-qwen3-4b.md)
now records real local tool execution, incremental streaming, cancellation and
restoration. Its supplied-script case passed; autonomous repair and strict
answer formatting failed. That historical run used manual work binding; the
router now supports [automatic conversation binding](docs/CONVERSATION_WORK_BINDING.md)
for the recognized native HTTP envelope.

An optional [managed llama.cpp profile](docs/MANAGED_LLAMA_LIFETIME.md) now
starts and stops one engine from existing verified runtime/model files through
the same connection commands. It verifies bundled libraries as well as the
launcher, preserves external engines, and keeps cleanup pending until its owned
lease is released. Downloads and model selection remain separate work.

## Install in your agent

The plugin manifests and shared CLI implementation are included in this source.
Installation remains subject to each coding client's plugin support and review.

### Codex

```sh
codex plugin marketplace add Xpounder-com/hormuz
codex plugin add hormuz@hormuz
```

Use a CLI with `codex plugin add` (checked on 0.160.0). Alternatively, after adding
the marketplace, select Hormuz in your client's plugin browser and install it.

### Gemini CLI

```sh
gemini extensions install https://github.com/Xpounder-com/hormuz
```

Restart the Gemini CLI session after installation.

### GitHub Copilot CLI

```sh
copilot plugin marketplace add Xpounder-com/hormuz
copilot plugin install hormuz@hormuz
```

### Claude Code

```sh
claude plugin marketplace add Xpounder-com/hormuz
claude plugin install hormuz@hormuz
```

Inside Claude Code, the equivalent commands start with `/plugin` instead of
`claude plugin`. Start a new session or reload plugins after installation.

### Cursor, OpenCode and other skill-capable agents

Use the [open Agent Skills installer](https://github.com/vercel-labs/skills)
(requires Node.js/npm). Choose your agent when prompted:

```sh
npx skills add Xpounder-com/hormuz --skill hormuz
```

Or select it explicitly with `--agent cursor` or `--agent opencode`.

After installation, ask: **“Use Hormuz to set up local-only routing for Codex.”**
The skill checks the installed router, client version and route qualification
before using shared setup. Plugin installation installs instructions; the
router runtime and a qualified local model are separate prerequisites. The
implemented persistent connection targets Codex CLI 0.147.0. Other agents can
use the skill to manage that connection; their own inference-routing adapters
are not implemented. See [installation, updates, removal and compatibility](docs/AGENT_INSTALLATION.md).

## Source evaluation

Use Python 3.11 or later in a virtual environment:

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install .
python -m hormuz --help
python -m unittest tests.test_execution_destination_workflow tests.test_connected_client_lifetime_workflow
```

Installation may download Python dependencies. Tests require loopback access;
they use temporary files and processes with synthetic credentials. They do not
contact external model providers or change your active coding-client settings.

For deliberate setup, copy `config.example.json` to `hormuz.json`, set the
environment token named by its local identity, and review:

```sh
hormuz --config hormuz.json client connect codex --dry-run
hormuz --config hormuz.json client connect codex
hormuz client status codex
hormuz client disconnect codex
```

The example is deliberately unqualified and blocks inference until a real local
route has been qualified. It does not install a model or claim a known-good
runtime. See [admission](docs/LOCAL_ROUTE_ADMISSION.md),
[connection lifetime](docs/CONNECTED_CLIENT_LIFETIME.md) and [support](SUPPORT.md).

## Product limits

Local-only blocks every intercepted model path from cloud execution, including
helpers. It does not control unrelated coding-client tools, plugins or network
connections. Automatic mode considers only eligible qualified local routes;
unknown evidence uses an explicitly permitted baseline or blocks. Continuing
tool and provider state retains destination affinity.

Codex subscription access is not established by login or these tests. There is
no automatic fallback to billable API usage. Unknown costs remain unknown.

[Optional local history](docs/LOCAL_HISTORY.md) adds encrypted, redacted text
review and spending estimates on your device. Collection starts disabled;
`hormuz --config hormuz.json history enable` opts in for future intercepted
requests. Use `history list`, `history show`, `history insights`, `history export`,
`history delete` and `history disable` to control retained data. No training or
cloud upload is part of this feature; exports are explicitly decrypted files.

The 2.0 development line retires standalone apps, hosted/enterprise front doors,
billing and PostgreSQL deployment support. [Migration and rollback](docs/MIGRATION_2.md)
describe the breaking changes. Existing releases, local user data and cloud
resources are not deleted by this repository cleanup.
[Operational resource cleanup remains open](docs/OPERATIONS_CLEANUP.md).

Use [CONTRIBUTING.md](CONTRIBUTING.md), [SUPPORT.md](SUPPORT.md),
[SECURITY.md](SECURITY.md) and [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).
