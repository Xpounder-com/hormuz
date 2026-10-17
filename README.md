# Koto

<img src="assets/koto-logo.png" alt="Koto wooden K logo" width="160" height="160" />

Koto is a customer-local model router for existing coding clients. Its entry
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
lease is released. [Verified acquisition](docs/LOCAL_ASSET_ACQUISITION.md) now
previews and explicitly downloads the existing pinned experimental runtime/model
into a private generation. Model selection and coding qualification remain separate.

[Shared local setup](docs/SHARED_LOCAL_SETUP.md) now generates the local-only
configuration from existing verified engine/model files and connects through
that same owner. The CLI and plugin use `client setup codex`; repeated setup
reuses the configuration and active generation, and preserves edited files.
The installed router remains a prerequisite. Setup now creates a private local
caller credential and configures Codex's command-backed authentication without
manual token exports. See [local credential setup](docs/LOCAL_CREDENTIAL_SETUP.md).

Opt-in [shadow routing](docs/SHADOW_ROUTING.md) records a local model's routing
recommendation alongside the actual attempt. Shared setup accepts `--shadow-router`;
recommendations preserve existing routing, local-only policy and conversation affinity.

[Local coding qualification](docs/LOCAL_CODING_QUALIFICATION.md) now gates automatic
coding admission on a pinned, predeclared campaign of actual edits, independent
checks and normal completion. The bundled experiment remains unqualified for
automatic coding. Engine health and shadow advice do not grant that qualification.

The [first candidate setup](docs/CANDIDATE_SETUP.md) pins an experimental
Codex 0.160.0/macOS arm64 combination and a sixteen-case preparation campaign.
It has no new native coding outcomes or admission receipt.

[Personal model scorecards](docs/MODEL_SCORECARDS.md) benchmark your own examples
through the local router. Create a suite with reference checks and targets, compare
quality, latency, streaming first-token timing, throughput, usage and configured
cost estimates, then explicitly enable deterministic routing among already
qualified local models. Scorecards stay local and preserve failed or unknown
measurements. They do not grant coding qualification.

## Install in your agent

The plugin manifests and shared CLI implementation are included in this source.
Installation remains subject to each coding client's plugin support and review.

### Codex

```sh
codex plugin marketplace add mehrdadxzaker/hormuz
codex plugin add koto@koto
```

Use a CLI with `codex plugin add` (checked on 0.160.0). Alternatively, after adding
the marketplace, select Koto in your client's plugin browser and install it.

### Gemini CLI

```sh
gemini extensions install https://github.com/mehrdadxzaker/hormuz
```

Restart the Gemini CLI session after installation.

### GitHub Copilot CLI

```sh
copilot plugin marketplace add mehrdadxzaker/hormuz
copilot plugin install koto@koto
```

### Claude Code

```sh
claude plugin marketplace add mehrdadxzaker/hormuz
claude plugin install koto@koto
```

Inside Claude Code, the equivalent commands start with `/plugin` instead of
`claude plugin`. Start a new session or reload plugins after installation.

### Cursor, OpenCode and other skill-capable agents

Use the [open Agent Skills installer](https://github.com/vercel-labs/skills)
(requires Node.js/npm). Choose your agent when prompted:

```sh
npx skills add mehrdadxzaker/hormuz --skill koto
```

Or select it explicitly with `--agent cursor` or `--agent opencode`.

After installation, ask: **“Use Koto in this chat. Let me choose the configured
router or a qualified local model.”** Follow the [explicit chat workflow](docs/KOTO_CHAT.md)
to preserve the native model, account, plugins, MCP and reasoning. Normal-app
startup bridge activation is disabled. For an explicitly requested persistent
CLI connection, the skill checks the router, client version and qualification
before shared setup. Plugin installation installs instructions; the
router runtime and a qualified local model are separate prerequisites. The
implemented persistent connection supports Codex CLI 0.147.0 and the
[0.160.0 single-agent HTTP profile](docs/CODEX_0160.md), plus the
[isolated Codex app engine 0.162.0-alpha.17.2 qualification](docs/CODEX_APP_COMPATIBILITY.md) with
plugins, MCP discovery and reasoning enabled. Other agents can
use the skill to manage that connection; their own inference-routing adapters
are not implemented. See [installation, updates, removal and compatibility](docs/AGENT_INSTALLATION.md).

## Source evaluation

Use Python 3.11 or later in a virtual environment:

```sh
python3 -m venv .venv-koto
. .venv-koto/bin/activate
python -m pip install .
python -m koto --help
python -m unittest tests.test_execution_destination_workflow tests.test_connected_client_lifetime_workflow
```

For an existing installation, follow the [Koto migration notes](docs/KOTO_REBRAND.md)
before replacing the former Python distribution or plugin.

Installation may download Python dependencies. Tests require loopback access;
they use temporary files and processes with synthetic credentials. They do not
contact external model providers or change your active coding-client settings.

For deliberate setup, copy `config.example.json` to `koto.json`, set the
environment token named by its local identity, and review:

```sh
koto --config koto.json client connect codex --dry-run
koto --config koto.json client connect codex
koto client status codex
koto client disconnect codex
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

Inspect [cloud authorization readiness](docs/CLOUD_READINESS.md) before changing
settings with `koto --config koto.json client cloud-readiness codex`.
The opt-in [native Codex catalog](docs/NATIVE_CODEX_CATALOG.md) adds separate
gateway/native authentication for discovery. Separately enabled
[native Responses](docs/NATIVE_CODEX_RESPONSES.md) adds bounded HTTP/SSE inference,
continuation isolation and process-scoped usage with unknown plan cost.
The read-only receipt distinguishes declared API access from the two separately
authorized ChatGPT plan paths and reports their remaining blockers.

[Optional local history](docs/LOCAL_HISTORY.md) adds encrypted, redacted text
review and spending estimates on your device. Collection starts disabled;
`koto --config koto.json history enable` opts in for future intercepted
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

## Plugin capabilities

The skills-only plugin now offers focused diagnosis, observed request explanations,
bounded local model probes, advisory recommendations, privacy/budget staging,
usage insights and safe restoration. See the [capability contract](docs/PLUGIN_CAPABILITIES.md)
and [command workflow](skills/koto/references/capabilities.md) for coverage and limits.
