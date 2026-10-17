# Codex app local transport

For the normal Codex app, use the [explicit Hormuz chat workflow](HORMUZ_CHAT.md).
It keeps the native model, account and startup path intact. Normal-app bridge
activation is disabled. The pinned transport qualification below is historical
and is exercised only in separate control homes; it does not authorize replacing
the normal app provider or downgrading an updated app.

The macOS app engine `0.162.0-alpha.17.2` has a separate exact compatibility
row from terminal Codex `0.160.0`. Select the executable actually bundled by
the app for shared setup. Neither executable needs replacement or downgrade.
This profile uses the existing llama.cpp b11617 archive and Qwen3-4B-Q4_K_M
weights, verified by the hashes in its bundled qualification.

## Full client features within a bounded local profile

A native request with the normal configuration, plugins, MCP and max reasoning
enabled was 913,482 bytes; tool definitions occupied 886,883 bytes. Advertising
native client tool discovery (`supports_search_tool`) reduced the initial
request to about 93 KiB while retaining access to deferred plugin/MCP tools.
Increasing a byte limit alone would not fit the complete tool catalog in the
model's 32,768-token context.

`hormuz.local-setup-profile` version 3 permits a 2 MiB serialized request,
8,192 output tokens and a 360-second request deadline. Managed llama profile
version 2 uses the existing 32,768-token context and a positive 1,024-token
reasoning budget, leaving room for tool calls and answers. Codex effort and
summary controls remain enabled and reach the model. Native local-model effort
semantics differ from GPT models. Explicit `effort: none` is still respected.
Byte admission does not measure tokenizer context; an over-context request
blocks or fails locally and never enables cloud-model fallback.
The adapter also sends that positive thinking bound on each provider request,
capped below a smaller requested output limit. Explicit `effort: none` alone
uses a zero thinking budget. Callers cannot inject an unrestricted runtime
budget. This retains reasoning while bounding individual generations; a
timed-out native experiment remains an unknown attempt and is never silently
retried.

The opt-in `codex_llama_v1` capability translates Responses features to this
runtime's inline function interface and reverses tool identities in output:

- Namespace functions retain their namespace and name in Codex; bounded aliases
  avoid collisions and overly long runtime tool names.
- Client tool search remains client-executed. Discovered definitions and their
  schemas become callable on subsequent requests, including deferred and
  repeated discovery results. Search results contain only discovered tools.
- Grammar custom tools, including apply_patch, retain their raw custom input.
  The client remains responsible for grammar checking, approvals and execution.
- Plain reasoning and completed tool history replay on the pinned destination.
  Opaque encrypted reasoning, undeclared calls and unsupported request forms
  fail closed. Malformed provider frames settle ambiguous work as unknown.
- Native automatic approval review retains its JSON-schema response format,
  low-effort reasoning and bounded parent/subagent metadata. The app profile
  declares structured output for this path. Legacy profiles still reject these
  additional tags. Review and execution remain owned by Codex.
- A live web-search declaration discovers the client's existing search MCP.
  The model must subsequently call that client tool to obtain results. There
  is no provider-hosted search implementation; cached-only and restricted-domain
  hosted search requests remain unsupported. Client tools retain their own
  permission and network behavior.

The `interactive` qualification label permits ordinary prompts only when the
caller explicitly selects that local model. It does not qualify arbitrary
cloud requests for automatic local redirection. Existing policy, redaction,
credential binding, output limits, single active local request, cancellation,
continuation affinity and unknown-outcome rules still apply.

The catalog enables plugin, skill and app instructions, reasoning and custom
tools. Normal launches need no feature-disabling flags. An app may save another
default model without breaking the owned provider's command-backed credential;
that helper still requires the exact owned provider/auth table and generation.
Provider edits block authentication. Disconnect preserves newer user edits.

## Desktop model selection and provider binding

Normal-app activation of the [app-server adapter experiment](CODEX_APP_BRIDGE.md)
is disabled after a reported organization-policy startup failure. Use the
[explicit Hormuz chat workflow](HORMUZ_CHAT.md), which leaves native startup,
authentication and model selection intact. The following describes the stock
app's provider-binding limits, not a verified combined desktop picker.

The installed app engine binds one provider when a thread starts. Its
`model/list` response carries model names and visibility, without a per-model
provider; `turn/start` can change the model but has no provider override.
Selecting Qwen in an existing OpenAI-backed thread therefore sends Qwen to the
ChatGPT provider and returns `The 'Qwen3-4B-Q4_K_M' model is not supported when
using Codex with a ChatGPT account.` The model label alone cannot select Hormuz.

For local routing, require an active shared connection, reload the app's
configuration and create a new chat using the connected provider. Keep existing
ChatGPT chats on their original provider. The local catalog advertises Qwen as
`visibility: list` at priority zero, and the native smoke test checks that it
appears in `model/list` with `includeHidden: false`. That establishes normal
catalog visibility, not its exact placement in the desktop menu.

The observed desktop picker changes the model and effort, leaving the thread's
provider unchanged. Mixing usable ChatGPT-plan models and Hormuz local models
in one picker requires the app to retain a provider identity for each model and
select that provider when starting a chat. A plugin or a catalog-only change
cannot supply that missing app behavior. Report this UI requirement separately
from backend routing. Adding OpenAI model names to a local-only catalog would
not provide ChatGPT-plan inference and is outside this profile.

## Durable compatibility and migration

Setup profile version 2 retains main's nullable coding evidence and original
limits. Version 3 retains that evidence field while adding the expanded app
transport envelope; it is the first published version of that expansion.
Existing coding qualification and request-profile checks remain in force.
The earlier machine-local version-2 app declaration was unpublished and must be
requalified as version 3 before a future setup. Existing generated gateway
configurations and the installed runtime are not rewritten by this PR.

Version-1 setup and managed profiles retain their original 64 KiB/512-token
bounds and behavior. Version-2 profile fixtures live in `tests/fixtures`.
Existing setup receipts, connection journals and work receipt shapes remain
compatible. Unknown executable versions remain blocked.

Response translation can increase the number of bytes delivered to Codex.
Provider-attempt metrics version 2 records actual provider and downstream byte
counts independently; version 1 retains its relay-only inequality. SQLite
migration 20 adds an append-only v2 metrics table with the same fields and
cross-version attempt uniqueness, preserving all v1 records and triggers.
Actor-scoped reliability totals include both versions. No counts are clamped
or invented. The frozen v2 fixture documents the expanded-byte case.

To migrate an existing connection, disconnect its recorded generation with its
current owner, preserving returned retained-setting names. Use a new router
configuration path in a separate control home and the pinned executable with shared setup preview, then run
the matching apply. All existing plugin/MCP/preferences remain in the client
home. Keep old configuration, assets and evidence for rollback.

To roll back, disconnect the new generation with this binary first. Reconnect
using the prior profile and prior configuration/database. An older binary
cannot open a migration-20 database or understand the v2 profile; do not edit
migration ledgers or reinterpret v2 evidence as v1.

## Verification

The approved automated boundary is public CLI setup/connect, requests through
the installed router, controlled provider traffic, public work receipts, then
disconnect and conditional restoration. Regression tests cover large requests,
rich catalogs, namespace/custom/discovered tools, reasoning replay, model
selection, malformed declarations/provider output and expanded-byte settlement.
They use isolated client homes and fake endpoints.

`tools/verify_codex_app.py` is an opt-in bounded native smoke test. Supply the
installed app executable, existing runtime/archive/model and reviewed profile.
It starts the app-server protocol, enables plugins/MCP/max reasoning, executes
a supplied script or an MCP probe, inspects public local work attempts, and
cleans up its owned processes and settings. It grants only the exact requested
read-only probe through Codex's normal approval mechanism. `--normal-mcp` uses
the existing node_repl MCP; `--client-home` targets an explicitly selected home.
Before inference it checks the effective default provider and the visible
native model list. Thread creation inherits that provider without a
`modelProvider` override, so a test-only provider selection cannot conceal a
broken default connection.
Wire capture is restricted to synthetic isolated homes. Use `--leave-connected`
only for the requested persistent installation.
`--capture-shapes` records only request structure and statuses; it can diagnose
normal-home failures without retaining request prompts or credentials.
`--task approval` exercises the existing automatic reviewer with a harmless
print command. `--discover-first` separates MCP discovery and invocation into
two native turns. MCP acceptance checks the selected server and returned
result, excluding arguments and the model's final claims.
`--task calculation` exercises the reported unsupported-model failure with
the exact `APP_LOCAL_OK: 7` answer, one successful provider attempt, reasoning
and no tool execution. It uses the connected default provider and visible
model list checks above.

Native app-server evidence proves the bundled engine's transport and tool
round trips. Visible app selection and interaction require separate evidence.
Neither a ready connection nor these bounded probes certify general coding
quality. The small Qwen model can choose incorrect tools; retain client
approvals and review its work.

On October 15, 2026, isolated native episodes using the installed app engine
completed the supplied Python script in 39.4 seconds (two successful local
attempts) and an actual MCP discovery/call/result in 70.7 seconds (three
successful local attempts). Both retained max effort and observed completed
reasoning, with zero retries, failovers or unknown attempts, and restored
their client settings after disconnect. Earlier diagnostic episodes exposed
the deferred-tool and expanded-byte defects fixed here; others exposed wrong
tool choices or claimed results without tool execution. Verification requires
actual command/MCP completion items, not the model's final text.

The automatic-review follow-up also completed an isolated native episode with
the saved automatic reviewer enabled: the parent request, JSON-schema review
subagent and parent continuation all succeeded locally, and the reviewed
read-only print command exited zero. Bounded parent tags were retained on the
provider request. No review/approval policy was relaxed.
