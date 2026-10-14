# Hormuz

**One workspace for local and cloud AI.**

Hormuz is being redesigned as an open-source desktop workspace for your projects,
chats, files, instructions and model preferences. A small glossy prompt bar gives
quick access to the active project. Choose a model yourself, or use Auto to
suggest an eligible local or paid model for the task.

**Status: first desktop UI simulation in progress.** Explore the
[simulated workspace and Auto prompt bar](docs/DESKTOP_UI_IMPLEMENTATION.md).
The Electron host, managed llama.cpp setup and real project-aware local/cloud Auto are planned. This
repository already contains the Python gateway, CLI, Personal Optimizer and
legacy clients; their release and qualification boundaries remain separate.

[Desktop direction](#the-desktop-were-planning) ·
[Roadmap](docs/ELECTRON_DESKTOP_ROADMAP.md) ·
[Backend boundaries](docs/DESKTOP_BACKEND_BOUNDARIES.md) ·
[UI simulation](docs/DESKTOP_UI_IMPLEMENTATION.md) ·
[Try the existing gateway](#quickstart) ·
[Contribute](#contributing-and-community)

[![CI](https://github.com/Xpounder-com/hormuz/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/Xpounder-com/hormuz/actions/workflows/ci.yml)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)

## The desktop we're planning

| Experience | Planned behavior |
| --- | --- |
| Full project workspace | Each project owns chats, selected files, instructions and model/Auto preferences, with local history and search. |
| Minimal prompt bar | Prompt, Send, compact model picker, small Auto control and project/Temporary destination, using the same glossy curved visual language as the full window. |
| Local model setup | Discover, download, verify, load and remove curated text models inside Hormuz through a managed llama.cpp engine. |
| Auto with an override | While typing, preview the proposed model and a small Local/Cloud indicator. Selecting a model pins it; selecting Auto resumes routing. |
| Optional paid models | Connect your own provider API account, enable cloud use explicitly and set an allowance. Provider usage is billed separately. |
| Research Review | Optional full-window review with distinct blind reviewers, visible findings/disagreement and human sign-off for a named revision. |

The planned host is **Electron**, with a shared **Svelte/TypeScript** interface
and **llama.cpp** for local inference. Windows, Linux and macOS are target
platforms; exact supported OS, architecture and hardware rows require installed
qualification. No new cross-platform release is available from this plan.

First scope is text work. Arbitrary marketplace loaders, image/audio inference,
agents/computer control, file editing, remote sync and collaborative cloud
projects remain later decisions. Local projects and direct provider access need
no hosted Hormuz login, Render service or Hormuz subscription.

## Projects, privacy and model choice

Named projects are planned to save conversations and drafts locally. Temporary
chat stays in memory across navigation and hidden windows, then clears on
explicit discard or app quit. First file scope uses imported UTF-8 text/Markdown
copies; project membership does not automatically include a file in a request.
The composer shows selected context and effective instructions.

Local-only is the initial default. Connecting a provider does not enable cloud
processing: project cloud use needs explicit opt-in, eligible context and a
finite allowance. Credential custody and content-free usage records remain
separate from saved project content.

Auto preview has no provider calls, model loads or downloads. Send revalidates
the proposed plan and can authorize a disclosed load/swap of a qualified,
already downloaded local model. A blocked manual pin stays pinned; local-only
work cannot silently become a cloud request. Estimates, actual costs and unknown
outcomes are shown separately.

Research Review freezes an explicit draft/source packet and uses two distinct
blind reviewers. Local roles swap sequentially under the first one-loaded-model
plan. Edited or reconciled versions need fresh review; model agreement does not
establish correctness or approve an entire project. A human decides the scope
and revision to accept.

These are [recorded planning boundaries](docs/DESKTOP_BACKEND_BOUNDARIES.md).
The final glossy finish and integrated UI still need review.

## Delivery order

1. Define the shared visual system and project-window/prompt-bar handoff.
2. Complete simulated projects, chats, context, local setup, connections,
   preferences, Auto and Research Review; review the integrated UI in
   [#492](https://github.com/Xpounder-com/hormuz/issues/492).
3. Accept the runtime contract, then implement Electron hosting, local storage,
   llama.cpp and one paid-provider baseline; integrate real Auto afterward.
4. Qualify lifecycle, packages, updates, accessibility and each declared platform.
   Real Research Review follows a useful single-response baseline.

Follow [desktop epic #479](https://github.com/Xpounder-com/hormuz/issues/479),
the [UI plan](docs/DESKTOP_UI_PLAN.md) and
[delivery roadmap](docs/ELECTRON_DESKTOP_ROADMAP.md). UI work uses mock services
first. Dates, implementation owners, exact models/providers and final runtime
limits remain explicit later decisions.

<a id="try-the-real-gateway-without-a-provider-account"></a>

## Quickstart

**Try the existing gateway from source.** This provider-free tour exercises the
implemented gateway and work ledger with simulated replies. It is separate from
the planned desktop and requires no API key, provider account or payment.

You need Git, Python 3.11+ and permission to bind local loopback ports. From a
fresh directory:

~~~bash
git clone --branch main --depth 1 https://github.com/Xpounder-com/hormuz.git
cd hormuz
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --editable .
python tools/ai_work_provider_examples.py --tour
~~~

A successful tour shows:

~~~text
PASS openai-smoke: an API request reached the gateway
PASS budget-stop: a zero job budget blocked the request
PASS exact-answer-reuse: an eligible repeat reused its answer
PASS correction-bypasses-reuse: a correction forced a fresh provider response
External provider calls: 0
~~~

Installation downloads Python dependencies; tour requests stay on loopback and
temporary state is removed on exit. This shows gateway mechanics, not model
quality, customer savings or desktop setup.

[First-run walkthrough](docs/TRY_HORMUZ.md) ·
[Full provider tour](examples/providers/README.md) ·
[SDK recipes](examples/sdk/README.md) ·
[Workflow results](docs/AI_WORK_DELIVERY_EXAMPLES.md)

### Stable gateway demo

The [v1.8.0 source release](https://github.com/Xpounder-com/hormuz/releases/tag/v1.8.0)
retains the provider-free `hormuz demo` and `hormuz policy demo` workflows.
See [the release scope](docs/releases/v1.8.0-notarized-mac.md) and
[policy workflow guide](docs/POLICY_ADMIN_USABILITY.md) for their exact conditions.

## Existing gateway and clients

<a id="why-hormuz"></a>
<a id="what-works"></a>
<a id="how-it-works"></a>

The existing gateway governs supported Codex, Claude Code and SDK requests with
identity, model policy, budget admission, secret-egress controls, streaming and
metadata-only evidence. OpenAI Responses, Chat Completions and Anthropic
Messages have their own implemented protocol paths. The AI Work runtime adds
job attribution and opt-in exact answer reuse under its existing contract.

<a id="personal-optimizer"></a>

The terminal-first [Personal Optimizer](docs/PERSONAL_OPTIMIZER.md) remains a
separate runnable path. The published v1.8.0 notarized Apple Silicon Mac archive
and unsigned unsupported Windows preview retain their
[documented release boundaries](docs/releases/v1.8.0-notarized-mac.md).
Hosted pilot services are suspended; retained data and remaining account
retirement are tracked in the [account ledger](docs/DESKTOP_ACCOUNT_CLEANUP.md).

> Existing release qualification: Hormuz 1.0 established stable CLI and
> policy/evidence contracts. Hormuz
> 1.8 preserves those contracts and the terminal-first Personal Optimizer.
> The original qualification included five isolated internal repetitions of one
> exact offline policy workflow and exact-byte candidate custody. This bounded
> result does not prove external human usability, general production fitness or
> independent security review. See the
> [release boundaries](docs/releases/v1.8.0-notarized-mac.md).

Recorded same-revision Codex/OpenAI and Claude Code/Anthropic evidence is in
[issue #115](https://github.com/Xpounder-com/hormuz/issues/115).
It does not prove provider-invoice reconciliation, every client feature,
enterprise production readiness, or coverage of traffic bypassing Hormuz.
[Support](SUPPORT.md) and [verification](docs/VERIFICATION.md) define the exact
available and qualified boundaries.

### Configure providers and clients

Use [client setup](docs/CLIENTS.md), [runtime configuration](docs/AI_WORK_RUNTIME.md)
and [policy control](docs/POLICY_CONTROL.md) for existing gateway connections.
Keep credentials outside source control. Live provider examples require your
own API account and an explicit bounded spend scope; paid chat subscriptions
are separate from provider API access.

<a id="usage-visibility-without-content-capture"></a>
<a id="policy-workflow"></a>
<a id="deployment-options"></a>
<a id="project-status-and-scope"></a>
<a id="software-cloud-and-appliances"></a>

Detailed usage reporting, deployment references and historical hosted/commercial
scope are linked below. Hosted Pro is deferred; it is not a prerequisite for the
planned local desktop. Existing gateway usage/evidence stores remain content-free;
future saved project history has its own local content contract.

## Documentation

| Topic | Guide |
| --- | --- |
| Desktop direction and delivery | [Roadmap](docs/ELECTRON_DESKTOP_ROADMAP.md), [UI plan](docs/DESKTOP_UI_PLAN.md), [backend boundaries](docs/DESKTOP_BACKEND_BOUNDARIES.md) |
| Research Review | [Role, revision and evidence contract](docs/RESEARCH_REVIEW_MODE.md) |
| Cleanup and transition | [Backlog reconciliation](docs/DESKTOP_BACKLOG_RESET_2026-10-13.md), [account ledger](docs/DESKTOP_ACCOUNT_CLEANUP.md), [completed local cleanup](docs/DESKTOP_LOCAL_CLEANUP.md) |
| Existing gateway architecture and runtime | [Architecture](docs/ARCHITECTURE.md), [AI Work guide](docs/AI_WORK_PRODUCT.md), [runtime](docs/AI_WORK_RUNTIME.md) |
| Clients and context optimization | [Client setup](docs/CLIENTS.md), [Personal Optimizer](docs/PERSONAL_OPTIMIZER.md), [context optimization](docs/CONTEXT_OPTIMIZATION.md) |
| Existing policy, usage and evidence | [Policy](docs/POLICY_CONTROL.md), [usage](docs/USAGE.md), [secret controls](docs/SECRET_CONTROLS.md), [audit](docs/AUDIT.md) |
| Existing deployment and storage | [Deployment](docs/DEPLOYMENT.md), [storage](docs/STORAGE.md), [operations](docs/OPERATIONS.md), [OCI](docs/OCI.md) |
| Program and qualification history | [Program map](docs/ROADMAP.md), [verification](docs/VERIFICATION.md), [support](SUPPORT.md) |

## Development and testing

Existing source-development setup and checks are in
[CONTRIBUTING.md](CONTRIBUTING.md). Routine verification uses local fake providers;
live provider/deployment checks retain their own explicit prerequisites.
This planning change starts no application build, real model execution or release.

## Contributing and community

Start with the [desktop roadmap](docs/ELECTRON_DESKTOP_ROADMAP.md) or an existing
bounded gateway issue, and read [CONTRIBUTING.md](CONTRIBUTING.md).

- Discuss the planned experience in [Discussions](https://github.com/Xpounder-com/hormuz/discussions/211).
- Use the [issue tracker](https://github.com/Xpounder-com/hormuz/issues) for
  reproducible bugs, documentation problems and feature proposals.
- Check [SUPPORT.md](SUPPORT.md) for installation and compatibility boundaries.
- Follow [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).
- Report suspected vulnerabilities through [SECURITY.md](SECURITY.md).

Use synthetic examples in public reports; omit keys, private prompts and
responses. Community support is best effort and carries no response, remediation,
uptime, compatibility or enterprise-support SLA.

## License

Hormuz is Apache-2.0 software. See [LICENSE](LICENSE). Provider usage and any
optional services are separate costs.
