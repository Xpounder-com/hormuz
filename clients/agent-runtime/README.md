# Hormuz project Agent backend

macOS CLI and source preview of the host-owned backend for [Agent epic #505](https://github.com/Xpounder-com/hormuz/issues/505).
This package is independent of the simulated desktop UI. It is not a packaged
desktop release or a claim that either vendor's full application is embedded.

## CLI quickstart

Requires macOS, Node.js 24+, and an interactive terminal. Codex requires a
separately installed trusted Codex CLI **0.160.0**; its default executable is
`/opt/homebrew/bin/codex`. Configure another absolute path with
`--codex-executable`. Claude uses this package's pinned SDK **0.3.299** and
bundled CLI. Set `OPENAI_API_KEY` for Codex or `ANTHROPIC_API_KEY` for Claude in
the launching shell through your credential-management workflow. Profile
configuration stores no API keys or cloud consent. This preview uses API auth;
subscription login is not used.

The package is unpublished. From this package directory, build and install a
local artifact:

```sh
npm ci --ignore-scripts --maxsockets=4 --fetch-retries=2 --fetch-timeout=30000 --no-audit --no-fund
npm run build
npm pack
npm install --global ./hormuz-agent-runtime-0.1.0.tgz --ignore-scripts --maxsockets=4 --fetch-retries=2 --fetch-timeout=30000 --no-audit --no-fund
cd /absolute/path/to/your-project
hormuz-agent init --engine codex
hormuz-agent doctor
hormuz-agent
```

Setup asks for an explicit provider model and conservative current **USD per
million input/output tokens**; vendor prices are never assumed. For Claude, use
`hormuz-agent init --profile claude --engine claude`, then
`hormuz-agent --profile claude`. Setup defaults to lifetime session/project/profile
allowances of $1/$5/$10. These are configured-rate estimates for admitted work,
not account-wide provider billing caps; usage and uncertain holds survive
restart and session deletion.

Type a request to plan, then `/accept` and `/execute`. Concrete actions still
require one-time approval. Use `/diff`, `/review accept|reject|changes`, `/status`,
and `/sessions`; `/interrupt` or Ctrl+C stops active work. Resume explicitly with
`hormuz-agent --resume last` or `/resume ID`, and accept any retained plan again.
Ctrl+C while idle or `/quit` closes the terminal.

Private profile state defaults to `~/.hormuz/agent-cli/profiles/default`, shared
by folders for that profile's lifetime budget. Use `--profile NAME` and
`--state-root PATH` consistently; state must remain outside the selected folder.
After restart, diff provenance remains but prior baseline text can be
unavailable. Help, init, doctor, and session listing perform no inference;
doctor locates dependencies without verifying a native runtime handshake.
Windows/Linux interactive execution remains unavailable.

The Python Hormuz source launcher additionally requires Python 3.11+ and
`python -m pip install --editable .` from the repository root. Run the bounded
`npm ci` command above with `--prefix clients/agent-runtime`, then
`npm run build --prefix clients/agent-runtime`. In a project folder, bare
`hormuz` or explicit `hormuz agent` opens the same terminal. See the
[complete CLI guide](https://github.com/Xpounder-com/hormuz/blob/main/docs/AGENT_CLI.md)
for source installation, configuration, consent, command details, and resume.

## Implemented behavior

- Named-project sessions, explicit workspace authorization, isolated runtime
  directories and atomic private session records. Temporary Agent sessions are
  rejected; exported context cannot restore live runtime authority.
- A shared application scheduler and host bridge: read-only planning, explicit
  current-revision plan acceptance, separate concrete action decisions, execution,
  observed changes and test/tool events, interrupt and explicit resume. Resume
  rechecks workspace, connection, runtime, consent, tool policy and allowance.
  Editing a plan clears acceptance; rejecting a plan prevents its execution.
  Revision-specific diff dispositions include accept, reject and request changes.
- Codex 0.160.0 app-server over bounded JSON-RPC stdio, native planning/questions,
  isolated configuration and concrete write/escalation approval requests. Both
  planning and execution start with the native read-only sandbox; accepted
  operations remain under the outer workspace ceiling. Planning/questions use
  that version's experimental protocol.
- Claude Agent SDK 0.3.299 in a supervised worker. Explicit tools/settings and
  API authentication, per-call hooks, planning mutation denial, scoped decisions,
  SDK session history and bounded owned-process cleanup.
  The initial bounded API path disables Claude extended thinking; enabling it
  requires a separately qualified input-history and spending contract.
- An authenticated loopback inference lane: provider key stays in the host;
  each generation request reserves a conservative upper estimate before egress.
  JSON/SSE usage settles configured-rate estimates; uncertain dispatched calls
  retain holds. Lifetime session, project and profile ceilings share a private
  content-free journal that survives session deletion.
- Bounded dirty-workspace snapshots and actual changes, with hash revisions and
  omitted coverage. Sensitive files have no review text. Changes are **observed**,
  not automatically attributed to the agent. No destructive revert is exposed.

## Developer verification

Requires Node.js 24 or later. From this directory:

```sh
npm ci --ignore-scripts --maxsockets=4 --fetch-retries=2 --fetch-timeout=30000 --no-audit --no-fund
npm run check
npm test
```

Routine tests use fake protocols/SDK behavior and disposable local provider
simulators. They make no paid provider calls. Test files run serially and all
local servers and child processes have cleanup. Actual macOS sandbox tests run
on macOS; other targets do not imply a qualified runtime launcher.
CI runs portable fixtures in the Agent backend workflow and macOS confinement
plus the installed Claude SDK fixture in the existing native contracts job.
The installed Codex 0.160.0 fixture is an explicit local opt-in.

Optional installed-engine fixtures use synthetic provider responses and explicit
flags documented in their test files. They verify the pinned harnesses and host
integration, not model quality, customer value or provider invoices.

```sh
HORMUZ_TEST_INSTALLED_CODEX=1 node --test --test-concurrency=1 test/codex-installed.test.ts
HORMUZ_TEST_INSTALLED_CLAUDE=1 node --test --test-concurrency=1 test/claude-installed.test.ts
```

## Default limits and compatibility

Local verification on 2026-10-14: macOS 26.2 (25C56), arm64, Node 25.2.1.
TypeScript check/build passed. The serial suite with both installed-engine flags
passed **163/163**, with no skips; it includes a compiled package installed under
`node_modules`, hoisted Claude dependencies and a sandboxed synthetic native
plan. Codex 0.160.0 and SDK 0.3.299 / bundled CLI 2.1.299 completed the synthetic
host journeys. The Python CLI, legacy ownership, governance, CI-scope,
secret-inventory and public-community-path suite passed **211/211**. Installed
CLI and Python-entry TTY checks covered folder session creation, cloud denial,
status/listing and clean interruption. External provider requests and paid spend
were zero. CI evidence is separate from these local checks.

| Boundary | Source-preview limit |
| --- | --- |
| Application | One active inference owner; one read-only workspace observer |
| Host request / prompt / plan | 128 KiB / 64 KiB / 64 KiB |
| Pending approvals | Eight, expiring after 60 seconds by default |
| Session run | Five-minute host deadline; no automatic restart/replay |
| Provider lane | One active request; at most eight sockets; 2 MiB request, 8 MiB response; explicit timeout and no proxy retries |
| Runtime stdio | 1 MiB frame, 64 MiB total output; 16 pending RPCs |
| Workspace observation | 5,000 files, 1 MiB per file, 8 MiB total captured text; 15-second worker deadline, explicit omitted coverage |
| Durable records | Schema 1, private atomic records; 16 MiB session record, 4 KiB content-free usage receipt |
| Runtime history | 64 MiB logical bytes, 4,096 entries, depth 16; 128 persisted native startup attempts per session |

Each connection supplies explicit generation input/output caps, request lifetime
limit and conservative prices. The developer host allows at most 25 admitted
requests per session lifetime. Its history belongs to a named private profile;
cross-profile/project lookup, unknown schemas and runtime-version changes fail
closed. There is no migration from existing desktop/gateway storage. To roll
back, stop the owned host and revert this optional package; preserve the separate
usage journal and uncertain holds. No existing gateway API/schema changes.

Runtime history is checked before startup/resume, after startup and reaped
cleanup, and before every inference dispatch. A single local metadata scan every
500 ms interrupts a running turn on overflow, unsafe entries or uncertain
observation. This is a detection and stop threshold; in-flight writes can
overshoot it before interruption. No transcript bytes are read by this guard,
and it is not an OS disk quota. Attempts are saved before native startup even
when no inference is admitted. Over-limit history is preserved for explicit
removal; it is never truncated or automatically replayed. Earlier source-preview
records without the attempt counter fail closed and require a new session.
The exact three Codex 0.160.0 launcher aliases under `tmp/arg0` are counted as
link metadata only when they point to the adapter's trusted canonical executable.
Their targets are never opened by the history guard; all other links and special
entries are rejected. This policy comes from the host adapter and is never
accepted from the renderer or persisted session data.

## Host integration

Import `@hormuz/agent-runtime` from a trusted Node/Electron host (source entry:
`src/index.ts`). Construct `AgentSessions`
with a private `SessionStore`, shared `ApplicationScheduler`, exact runtime
factory, host-owned connection resolver, workspace/allowance/cloud authorization
and finite parent allowances. Connecting a provider is separate from project cloud
consent. Never let a renderer supply provider credentials, prices, upstream URLs,
runtime commands or its own sender policy. Register only trusted project/window
senders and their canonical workspace roots with the narrow versioned bridge;
revoke registration on navigation, project switch or window disposal.
`registerAgentIPC` also requires an `authorizeEvent` callback that checks the
current trusted main-frame document; a registered webContents ID alone does not
authorize child frames or a document loaded after navigation.

Listen for `event`, `approval` and `review`. An approval has a request identity,
fingerprint and deadline. Return it through `decide` with approve, deny or cancel;
expired, replayed, changed-scope or out-of-workspace decisions fail closed.
Acceptance of a plan or diff is not permission to increase spending, commit,
merge, publish or deploy. The bridge has no arbitrary RPC/process/file operation.

`src/cli.ts` is a developer JSONL host. Its explicit
`HORMUZ_AGENT_HOST_CONFIG` contains profile/project IDs, canonical workspace,
private state directory, engine/model, allowance in micro-USD, conservative
input/output rates in micro-USD per million tokens, and input/output token caps.
`allowCloudInference: true` must also be supplied by the trusted developer host
before a session's cloud-consent flag can permit inference; it defaults to off.
It resolves `OPENAI_API_KEY` or `ANTHROPIC_API_KEY` in the host environment only.
It never creates credentials or starts inference automatically. Requests include
schemaVersion 1, method, projectId and the method's bounded fields; responses,
progress and pending decisions are JSONL. A renderer integration should use the
typed bridge instead of allowing arbitrary configuration text.
Run it with `npm run host` after supplying the explicit host configuration.

## Exact boundaries

Production launch currently supports macOS only, with an OS process sandbox
limiting private-file reads, writes, executable scope, local proxy port and owned
signals. Necessary system runtime, read-only Codex managed-preference access and
Claude's Unix sockets under its own private temporary directory are explicit
exceptions. System timezone data is readable for native startup. Workspace
observation also runs in a separate read-only worker with networking disabled,
so ancestor changes cannot redirect privileged host reads. Shared hardlinks are
omitted from review coverage. Windows/Linux launch fails closed until their process
and filesystem owners are qualified; portable fixture tests do not qualify them.
The developer Codex path defaults to `/opt/homebrew/bin/codex`; packaged runtime
provenance, signing, updates and end-user toolchain-free installation remain
release work.

API auth is the initial path. No ChatGPT/Claude subscription entitlement is
assumed. Prices must be conservative and qualified for the exact model, service
tier and cache behavior. These are configured-rate estimates for admitted work,
not an account-wide provider bill cap. Native proxy compaction and opaque
reasoning histories remain blocked before egress because their cost bound is
not established. Optional encrypted-reasoning output inclusion is removed from
the request and this capability is disclosed; unsupported histories never fall
back to a different engine or bypass admission.
The SDK's exact `?beta=true` Messages marker is normalized to the stable endpoint.
Known automatic optional beta headers are omitted and this capability is exposed;
unknown flags, context editing, effort settings and thinking-enabled beta payloads
remain unavailable. [Claude's environment reference](https://code.claude.com/docs/en/env-vars)
documents the output-cap and isolated temporary-directory settings used here.

Source copies are not put in usage receipts. Only the latest execution's bounded
safe baseline text is retained in memory for review; restart retains hashes and
coverage, and clearly reports missing before-text. Engine-owned conversation
history stays in its named private runtime subtree. Removing a session deletes
that history but preserves incurred/uncertain content-free budget receipts.
Parent ceilings are lifetime ceilings for this preview, not monthly billing.

See [the implementation roadmap](https://github.com/Xpounder-com/hormuz/blob/main/docs/AGENT_EXECUTION_ROADMAP.md). UI human
acceptance, capped live-provider checks, model-quality assessment and packaged
per-platform installation qualification remain separate gates in #513/#517 and
#340/#346/#347.
