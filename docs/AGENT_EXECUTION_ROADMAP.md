# Hormuz project Agent execution roadmap

Planning extension: 2026-10-13. Desktop parent: [#479](https://github.com/Xpounder-com/hormuz/issues/479).
Agent epic: [#505 — [Epic] Add project agent execution: Codex first, then Claude](https://github.com/Xpounder-com/hormuz/issues/505).
Backend source preview: 2026-10-13, following the user's instruction to implement
the backend issues. The roadmap and desktop release gates remain separate from
the developer host described below.

## Outcome and delivery order

Add optional **Agent mode** to the full Electron project window. Codex app-server
is the first execution engine. Claude Agent SDK follows the qualified Codex
journey through the same host-owned session contract and Hormuz interface.
The intended flow is:

```text
select named project + engine + authorized working directory
    -> investigate in enforced read-only Plan mode and answer questions
    -> accept the current plan revision and review finite allowance
    -> execute with concrete tool permissions and streamed progress
    -> review actual session changes and test evidence
    -> interrupt or finish; explicitly resume with current authority
```

Ordinary local/direct-provider chat, model Auto and Research Review keep their
own contracts. Agent runtime selection is separate from model selection. An
engine maintains its session affinity; Auto cannot silently replace Codex with
Claude or a local model during execution. A cross-engine handoff creates a new
session with reviewed context and fresh authority.

The first useful text-chat desktop remains independently deliverable. Agent UI
acceptance is an additive extension checkpoint, not a new prerequisite for
#492, #480 B, #482, #493 or #485. Its simulation can use the shared visual and
context mocks while baseline desktop work advances. Real Agent integration
requires its accepted UI/session contract and the applicable qualified host,
content and custody inputs. Do not wait for final installed qualification to
build the candidate that must supply that evidence.

## Implementation backlog

| Stage | Issue | Dependencies and completion evidence |
| --- | --- | --- |
| Shared contract | [#506 — Agents: define session, workspace, permission and cost contracts](https://github.com/Xpounder-com/hormuz/issues/506) | Draft alongside UI; accept after #480 B and Agent UI review with applicable #482/#493 inputs. Versioned sessions/events, workspace authority, separate approvals, custody/content/cost coverage and bounded ownership. |
| Agent UI simulation | [#507 — UI: simulate Agent planning, approvals, execution, diff and resume](https://github.com/Xpounder-com/hormuz/issues/507) | Shared #481/#487–#491 patterns and draft session contract. Synthetic plan, questions, approve/deny, execution, diff, crash and resume journeys; record extension-specific human acceptance. |
| Codex runtime and execution | [#508 — Codex: integrate supervised app-server sessions and execution](https://github.com/Xpounder-com/hormuz/issues/508) | Accepted shared contract/UI and applicable host/storage. Pinned app-server, local stdio, typed events, explicit API auth, tool execution, admission and owned cleanup. |
| Codex planning | [#509 — Codex: implement read-only planning and revision-bound plan acceptance](https://github.com/Xpounder-com/hormuz/issues/509) | Runtime/contract/UI. Actual read-only enforcement, questions and revision-bound plan acceptance; execution transition also needs action approvals. |
| Codex action approvals | [#510 — Codex: bridge command, file and permission approvals to the host](https://github.com/Xpounder-com/hormuz/issues/510) | Runtime/contract/UI; can advance alongside planning. Host-enforced command/file/network decisions, narrow scopes and stale/raced denial. |
| Codex diff review | [#511 — Codex: review session diffs, test results and dirty workspace provenance](https://github.com/Xpounder-com/hormuz/issues/511) | Runtime and action approvals. Dirty-workspace baseline, actual applied changes, tests, revision-specific review and safe disposition. |
| Codex session resume | [#512 — Codex: persist, interrupt and explicitly resume named-project sessions](https://github.com/Xpounder-com/hormuz/issues/512) | Runtime, planning/approvals and applicable #493/#339 inputs. Isolated named-project history, interrupt/quit/reopen and explicit revalidated resume; diff reconciliation integrates before qualification. |
| Codex qualification | [#513 — Codex: qualify the installed plan-to-resume Agent journey](https://github.com/Xpounder-com/hormuz/issues/513) | All Codex slices. Provider-free journey, separately authorized bounded live fixture and exact installed runtime/auth/platform evidence. Required before enabling Codex and real Claude integration. |
| Claude runtime and execution | [#514 — Claude: integrate the supervised Agent SDK execution engine](https://github.com/Xpounder-com/hormuz/issues/514) | Codex qualification and shared contracts. Pinned TypeScript SDK/bundled runtime, API auth, explicit prompt/settings loading, bounded streaming tools and owned cleanup. |
| Claude planning and approvals | [#515 — Claude: enforce read-only Plan mode and scoped action approvals](https://github.com/Xpounder-com/hormuz/issues/515) | Claude runtime and shared contracts. Enforced Plan, questions, explicit execution permission mode, every-tool hooks and scoped callback decisions. |
| Claude diff and resume | [#516 — Claude: integrate diff review and explicit session resume](https://github.com/Xpounder-com/hormuz/issues/516) | Claude runtime/planning and accepted Codex review/resume outcomes. Actual changes/tests, isolated SDK session history, checkpoint limits and safe explicit continuation. |
| Claude qualification | [#517 — Claude: qualify the installed plan-to-resume Agent journey](https://github.com/Xpounder-com/hormuz/issues/517) | All Claude slices and Codex qualification. Complete second-engine journey, permission/admission coverage and declared installed platform rows. |
| Folder CLI | [#521 — CLI: add folder-based Agent planning, approvals, execution, review and resume](https://github.com/Xpounder-com/hormuz/issues/521) | Reuse the shared backend for `hormuz`, `hormuz agent` and `hormuz-agent`. macOS source preview, compiled local package, explicit cloud consent and model/rates, bounded terminal ownership, observed diff review and folder-scoped resume. [Setup and commands](AGENT_CLI.md); installers and other platform qualification remain separate. |

The implementation work order authorizes a supervised backend candidate for
both engines, beginning with Codex. Claude's SDK integration is available to
developers for local qualification after the Codex provider-free journey;
enabling it in the desktop still requires #513 and its own #517 release gates.
#485 direct-provider and #341
legacy-relay dependencies apply only where their implementations are reused.
Neither llama.cpp nor a hosted gateway is a universal prerequisite for Agent
sessions. Both engine qualification issues contribute applicable lifecycle,
package/update/footprint and installed evidence to #339/#340/#346/#347 when
their engine is exposed. Start with an exact Apple Silicon macOS candidate;
Windows/Linux stay unavailable or explicitly preview until separately proven.
Release versions, dates and implementation owners remain unassigned.

## Backend source preview

The [folder CLI](AGENT_CLI.md) provides the same host-owned flow directly in a
terminal, without requiring the planned Electron interface. Each canonical
folder has a stable named-project identity under private profile state outside
the workspace. The local package ships compiled JavaScript and reuses the same
authority, admission, persistence and sandbox rules. Its terminal and installed
package fixtures do not complete desktop acceptance or paid-provider gates.

[`clients/agent-runtime`](../clients/agent-runtime/README.md) implements the
host-owned contracts for #506 and the backend slices of #508–#512 and #514–#516.
It supplies a trusted Node/Electron library, a bounded JSONL developer host,
private named-project sessions, revision-bound read-only planning and acceptance,
concrete expiring action decisions, supervised execution, observed diff review,
interrupt and explicit session resume. Codex app-server is pinned to 0.160.0 and
Claude Agent SDK to 0.3.299. Runtime selection is fixed for each session.

Provider credentials stay in the host. An authenticated loopback proxy reserves
configured-rate input/output estimates before each generation request. A private
content-free journal enforces lifetime session/project/profile ceilings, retains
unknown dispatched holds and survives session deletion. It is not a monthly or
account-wide provider invoice cap. Source review text is bounded and sensitive
files are omitted; persisted baselines retain hashes and coverage. Runtime
conversation history stays in the session's private content subtree. The host
checks 64 MiB logical bytes, 4,096 entries and depth 16 before loading or
dispatching, after reaped cleanup, and through one serialized 500 ms metadata
monitor. It persists at most 128 native startup attempts per session, including
failed or non-admitted attempts. Overflow or unsafe history blocks continuation;
in-flight writes may overshoot this detection threshold before interruption.
This is not an OS disk quota, and history is never automatically truncated.

The production process launcher currently supports macOS only. Portable mock
tests do not qualify another platform. The installed-engine fixtures use local
synthetic provider responses, actual pinned runtimes and disposable workspaces;
they are separate from paid-provider evidence. Opaque reasoning history and
proxy compaction remain blocked until their admission bounds are established.

This source preview does not complete #507 human UI acceptance, the desktop
host/content/custody acceptance checkpoints, capped live-provider evidence,
packaged toolchain-free installation, signing/update evidence, or Windows/Linux
qualification. These are tracked explicitly in #513/#517 and #339/#340/#346/#347.
The backend can be integrated into the accepted UI without claiming that those
release gates have passed.

## Session ownership and authority

Add an `AgentSessionAdapter` alongside the existing transport-oriented
`AgentAdapter`; launching an API relay does not expose task plans, approval
requests or a resumable execution session. Its versioned contract covers
start/resume/interrupt, engine/model/session/turn identity, plans, questions,
tool activity, pending decisions, diffs, usage and terminal/unknown outcomes.

A supervised process outside the Electron renderer owns each runtime,
filesystem authority and credentials. The renderer uses narrow validated IPC
to present events and return decisions. Pin runtime/SDK/generated schema
versions and expose only qualified capabilities; experimental or unavailable
operations are blocked rather than inferred from vendor desktop behavior.

First Agent scope is **named projects only**. App-server and SDK transcript and
config directories must be profile-isolated and covered by local content
retention, deletion, export and recovery. Temporary Agent mode stays unavailable
until a separate non-persistence contract is qualified. Hiding or navigating
does not erase active work; quitting stops owned execution without automatic
paid continuation on restart. Credentials, continuation authority and grants
are excluded from portable project exports. Agent transcripts, commands and
patches belong to the local content owner, never metadata-only usage records.

A working-directory grant is distinct from selecting imported `.txt`/`.md`
copies. Define root/path/symlink enforcement and explicit instructions/settings/
skills loading. Project instructions, imported content and model output are
untrusted input and cannot authorize filesystem, network or process access.

## Planning, approvals, execution and review

Keep three separate decisions:

1. **Plan acceptance:** the intended workflow at a named revision, with its
   workspace/context/engine/model and authority ceiling. Changed scope, plan,
   policy or relevant workspace state invalidates affected acceptance.
2. **Tool permission:** a concrete operation bound to the current session,
   turn, request, normalized working directory, arguments, paths, scope and
   expiry. The host validates it; stale, duplicate and raced answers fail closed.
3. **Spend allowance:** a finite authorized inference boundary under existing
   applicable parent restrictions. Plan or tool acceptance cannot increase it.

Existing AI Work `plan` and `approve` concern budget policy and a new total
allowance; they are not coding plans or tool permissions. Research Review
sign-off accepts a revision-specific artifact and grants no execution authority.

Read-only planning is enforced by actual tools/permissions/sandbox policy,
including shell writes and other side effects, not prompt text alone. Codex
mode and plan-event support is qualified against its pinned protocol. Claude
uses explicit permission modes; Plan-mode write requests must be denied by the
host. `canUseTool` handles pending approvals/questions, but auto-approved calls
can skip it: use `PreToolUse` for policy that must apply to every tool call.

Execution has bounded progress, actual operation results and explicit failed,
interrupted or unknown states. An approval response is not proof an operation
succeeded. Capture a pre-run baseline and distinguish session changes from
unrelated dirty or concurrent user edits. Review shows actual applied changes,
exact base/current revision, omitted coverage and real test results. Diff
acceptance grants no commit, merge, deployment or publication authority. Revert
or checkpoint rewind needs concrete intent and current-state proof that it will
preserve unrelated work; it cannot undo arbitrary external side effects.

Resume is explicit and revalidates runtime/schema, auth/model availability,
actual workspace state, plan, current tool policy, cloud consent and allowance.
Pending or expired grants do not survive as fresh authority. Unknown completed
actions are reconciled before any repeat; interrupted work is never replayed
automatically. Forked conversation history does not copy filesystem state.

## Privacy, cost and resources

Cloud processing needs explicit project permission and an eligible context.
Local-only and other parent restrictions cannot be bypassed by engine selection,
tools, approvals, resume or handoff. Reuse qualified custody/accounting where
compatible; local Agent sessions require no hosted Hormuz login, Render, Okta,
Stripe or PostgreSQL.

Qualify the actual auth/egress path before promising an enforced allowance.
Every inference attempt/retry must pass the declared admission boundary. Show
usage-only or unknown coverage when a runtime path bypasses it; never label such
a path capped. Initial integration uses provider API authentication. OpenAI's
Sign in with ChatGPT plan-usage route is an optional separately qualified path
with preview constraints; commercial/hosted access has separate requirements.
Claude subscription-backed product login is not assumed; use supported API auth
unless Anthropic has approved the integration.

Preserve the initial one-active-inference-attempt application scheduler across
chat, Agent and Research Review. No hidden parallel subagents in first Agent
scope. Set finite worker/output/history limits and explicit timeouts, at most
three retries with backoff/jitter, outbound concurrency at most eight and owned
cleanup on success, failure and quit. Never auto-retry uncertain side effects or
partial provider attempts. Shell sandboxing and non-shell/MCP tool authority need
separate proof. No load/stress tests or continuous polling are scheduled here.

## Qualification and references

Start with provider-free protocol, filesystem and rendered fixtures. Small live
fixtures require their own explicit execution scope and allowance. Qualify exact
engine/runtime/model/auth/egress/platform combinations, actual permission and
cost coverage, dirty-workspace handling, denial, interrupt, quit/reopen, deletion
and resume. Human acceptance is separate from automated checks and packaged
installation proof. No all-vendor feature parity, subscription portability,
all-platform support, customer savings or security-audit claim follows from
creating this roadmap.

First engine integrations expose a bounded tool set. Arbitrary marketplace
loaders, autonomous background jobs, general MCP ecosystems, computer control,
voice, remote sync and collaboration remain separate later decisions.

Official references checked for this plan:

- [Codex app-server](https://learn.chatgpt.com/docs/app-server): threads/turns,
  streamed plans/diffs, approvals and runtime lifecycle.
- [Sign in with ChatGPT plan usage](https://developers.openai.com/siwc/token-sharing-open-source)
  and [preview limitations](https://developers.openai.com/siwc/token-sharing-open-source/preview-limitations).
- [Claude Agent SDK](https://code.claude.com/docs/en/agent-sdk/overview): supported
  embedding, authentication and Claude Agent branding.
- [Claude permissions](https://code.claude.com/docs/en/agent-sdk/permissions),
  [user input](https://code.claude.com/docs/en/agent-sdk/user-input),
  [hooks](https://code.claude.com/docs/en/agent-sdk/hooks) and
  [sessions](https://code.claude.com/docs/en/agent-sdk/sessions).
