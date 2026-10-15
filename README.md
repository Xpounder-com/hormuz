# Hormuz

Hormuz is a customer-local model router for existing coding clients. Its entry
points are a terminal CLI and a skills-only plugin that will call the same
setup, status and disconnect implementation. Coding clients retain their editor,
tools, permissions and agent loop.

The approved workflow is **connect → submit a coding-client request through the
router → inspect result/status → disconnect**. See [scope and implementation
order](docs/ROUTER_SCOPE.md) and [specification #523](https://github.com/Xpounder-com/hormuz/issues/523).

The current source implements destination ownership, automatic route admission,
hard local-only enforcement and recoverable Codex CLI connection lifetime.
Routine tests use disposable clients and model endpoints. Real llama.cpp/model
quality, subscription eligibility, installed-platform support and plugin
distribution remain separate qualification work. The skills-only plugin is not
shipped by this cleanup. MCP is optional if a concrete integration needs it.

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

The 2.0 development line retires standalone apps, hosted/enterprise front doors,
billing and PostgreSQL deployment support. [Migration and rollback](docs/MIGRATION_2.md)
describe the breaking changes. Existing releases, local user data and cloud
resources are not deleted by this repository cleanup.
[Operational resource cleanup remains open](docs/OPERATIONS_CLEANUP.md).

Use [CONTRIBUTING.md](CONTRIBUTING.md), [SUPPORT.md](SUPPORT.md),
[SECURITY.md](SECURITY.md) and [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).
