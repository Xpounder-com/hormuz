# Codex surfaces for the shared Hormuz router

Research date: 2026-10-15. Decision question: [Determine which Codex surfaces can
activate the shared router from a skills-only plugin](https://github.com/Xpounder-com/hormuz/issues/540),
within [Finish the customer-local router within the approved architecture](https://github.com/Xpounder-com/hormuz/issues/536).

## Finding

Keep the shared CLI implementation and skills-only setup plugin. Current OpenAI
documentation provides a native custom-provider path for the Codex CLI and the
macOS/Windows desktop apps, including command-backed gateway authentication.
Desktop reads the user configuration and requires an app restart after provider
changes. No documented capability gap currently makes MCP necessary for this
setup path. This is a capability finding, **not Hormuz desktop qualification**.
See [provider setup](https://learn.chatgpt.com/docs/enterprise/connect-to-a-gateway#configure-the-provider)
and [custom providers](https://learn.chatgpt.com/docs/config-file/config-advanced#custom-model-providers).

The supported implementation remains an exact **Codex CLI 0.147.0** adapter.
The **0.160.0** plugin installation smoke is a different observation. It did not
activate a skill, change that host's provider or submit a model task. The desktop
engine and each execution surface require their own pins and native evidence.
These boundaries are explicit in Hormuz's [connection documentation](https://github.com/Xpounder-com/hormuz/blob/75f089a7065f1e4cdc9a0b6309f6733af81cdac3/docs/CONNECTED_CLIENT_LIFETIME.md)
and [installation record](https://github.com/Xpounder-com/hormuz/blob/75f089a7065f1e4cdc9a0b6309f6733af81cdac3/docs/AGENT_INSTALLATION.md).

This research changed no product code, user settings, installed plugins or
credentials. It performed no UI automation, tests, model calls, sign-in, asset
acquisition or publication. Live official documentation was fetched on the date
above; it describes current capabilities rather than every historical release.

## Evidence baseline

The research branch starts at main
`fb5084cb235fd9e9e82f501ce5af03c956432bf5`. Source inspection also includes the
unmerged credential/shadow stack at
`75f089a7065f1e4cdc9a0b6309f6733af81cdac3`. Those source states must not be
collapsed into a claim that the stack has landed.

- The [approved scope](https://github.com/Xpounder-com/hormuz/blob/fb5084cb235fd9e9e82f501ce5af03c956432bf5/docs/ROUTER_SCOPE.md)
  keeps the router, shared CLI and skills adapter. Coding clients retain their
  permissions, tools and agent loop; MCP requires a demonstrated need.
- The [adapter](https://github.com/Xpounder-com/hormuz/blob/75f089a7065f1e4cdc9a0b6309f6733af81cdac3/hormuz/connected_client.py)
  accepts only the [0.147.0 version pin](https://github.com/Xpounder-com/hormuz/blob/75f089a7065f1e4cdc9a0b6309f6733af81cdac3/hormuz/client_versions.py).
  It writes three user defaults (`model`, `model_provider`, `web_search`) and a
  generation-specific Responses provider. It does not select a desktop chat.
- [Managed local credentials](https://github.com/Xpounder-com/hormuz/blob/75f089a7065f1e4cdc9a0b6309f6733af81cdac3/docs/LOCAL_CREDENTIAL_SETUP.md)
  use an installed absolute helper command and generation binding. One pinned
  native CLI episode verified that path without manual token exports. It did
  not qualify later turns, resume/fork, desktop or subscription execution.
- [Native conversation binding](https://github.com/Xpounder-com/hormuz/blob/75f089a7065f1e4cdc9a0b6309f6733af81cdac3/docs/CONVERSATION_WORK_BINDING.md)
  has a bounded CLI tool-round-trip observation against a deterministic local
  provider. It explicitly leaves new turns, resume, fork and desktop unqualified.
- The [real-model record](https://github.com/Xpounder-com/hormuz/blob/75f089a7065f1e4cdc9a0b6309f6733af81cdac3/docs/qualifications/codex-0147-llama-b11617-qwen3-4b.md)
  establishes transport/tool observations and supplied-script execution, with
  retained quality failures. The [shadow experiment](https://github.com/Xpounder-com/hormuz/blob/75f089a7065f1e4cdc9a0b6309f6733af81cdac3/docs/SHADOW_ROUTING.md#pinned-experiment-2026-10-15)
  records zero of three independent repairs and three false-local predictions.
  Neither experiment authorizes active learned routing.

## Surface matrix

“Documented” means the vendor describes the capability. “Observed” means an
existing pinned Hormuz record covers only the stated case. “Unqualified” means
product acceptance remains open; it does not mean the vendor forbids the feature.

| Surface or action | Vendor capability and boundary | Hormuz state / remaining gate |
| --- | --- | --- |
| Codex CLI 0.147.0, explicit selected home and installed router | A custom Responses provider is configurable. The selected provider supplies gateway authentication independently of upstream authentication. [Provider guide](https://learn.chatgpt.com/docs/enterprise/connect-to-a-gateway) | Implemented exact-version adapter; bounded native transport/tool/credential observations exist. Broad autonomous coding capability remains unqualified. |
| Codex CLI 0.160.0 as plugin host | CLI marketplace installation is documented separately from provider setup. [Packaging commands](https://developers.openai.com/plugins/build/plugins#add-a-marketplace-from-the-cli) | Observed disposable marketplace registration, skill/reference copying and installed listing only. Existing routing adapter rejects this version as a target. |
| Other CLI releases | Custom-provider support is documented, but current docs do not establish identical historical contracts. [Config basics](https://learn.chatgpt.com/docs/config-file/config-basic) | Unqualified and rejected by the current pin. Add an explicit version/profile qualification before widening support. |
| Native Codex local desktop task, macOS | Same user config and custom-provider path are documented; restart the app. [macOS setup](https://learn.chatgpt.com/docs/enterprise/connect-to-a-gateway#configure-the-macos-app) | Unqualified. Record app build and **bundled** CLI/engine identity, effective configuration and actual task traffic. A terminal binary version does not identify the app engine. |
| Native Codex local desktop task, Windows | The app reads its Windows user config; documented helper paths must be local Windows executables. [Windows setup](https://learn.chatgpt.com/docs/enterprise/connect-to-a-gateway#configure-the-windows-app) | Unimplemented platform support. Hormuz lifetime currently uses POSIX locks and Unix sockets; configuration documentation alone does not make it a Windows adapter. |
| Skill/onboarding in an existing conversation | Onboarding invokes a packaged skill in a new conversation or the current conversation when installed there. [Onboarding](https://developers.openai.com/plugins/build/plugins#add-an-onboarding-skill) | Unqualified activation/setup flow. Installing or invoking the skill does not prove that this conversation reloads its provider. Verify a fresh task after the required restart. |
| Codex app-server / an embedding integration | Thread start/resume/fork, per-turn overrides and effective-config inspection are separate API operations. [App-server](https://learn.chatgpt.com/docs/app-server) | No new Hormuz agent runtime is needed or proposed. An embedding's overrides and thread lifecycle need explicit qualification if it becomes a supported target. |
| Work Cloud with local computer access, ordinary ChatGPT/cloud chats | Custom providers and host-local paths do not carry over through managed config to Work Cloud with local access. [Advanced configuration scope](https://learn.chatgpt.com/docs/config-file/config-advanced#applying-these-examples-to-synced-work) | Unsupported as a claim that editing local Codex defaults intercepts those chats. Locally executing tools do not establish local orchestration or model inference. |
| Public plugin directory | Skills-only packages are supported, subject to submission checks/review. Local execution has a Desktop only declaration. [Submission](https://developers.openai.com/plugins/deploy/submission), [desktop-only skills](https://developers.openai.com/plugins/deploy/desktop-only#plugins-without-an-mcp-server) | Unqualified distribution. A local marketplace smoke and valid manifest are not public acceptance or publication. |
| Local MCP control adapter | MCP exposes tools/context; a local MCP public submission requires approved-partner allowlisting. [MCP](https://learn.chatgpt.com/docs/extend/mcp), [local MCP submission](https://developers.openai.com/plugins/deploy/desktop-only#submit-a-plugin-with-a-local-mcp-server) | Optional, absent, and presently unnecessary. It would not replace provider activation or authorize inference. |

## Provider activation and precedence

The current order for ordinary configuration is CLI flags/`--config`, trusted
project layers (closest directory wins), selected profile file, user defaults,
cloud-managed defaults, system defaults, then built-ins. Enforced requirements
constrain those defaults separately. Profile files are distinct files in
`CODEX_HOME`; current documentation says the legacy inline profile tables stopped
being read at 0.134.0. [Precedence](https://learn.chatgpt.com/docs/config-file/config-basic#configuration-precedence),
[profiles and state location](https://learn.chatgpt.com/docs/config-file/config-advanced#profiles).

Consequently, Hormuz's `codex_user_defaults` receipt cannot certify the effective
provider of every project, launch command or loaded thread. Preserve override
visibility instead of removing user or organization choices. The adapter's
conservative known-managed-file rejection is not full attestation of every MDM,
remote requirement or future host policy. Its [documented lifetime scope](https://github.com/Xpounder-com/hormuz/blob/75f089a7065f1e4cdc9a0b6309f6733af81cdac3/docs/CONNECTED_CLIENT_LIFETIME.md)
already makes that distinction.

The provider's `base_url` determines model traffic; MCP/plugin connection
configuration is separate. Command-backed auth invokes an existing executable,
with no stdin, reads a trimmed stdout token, and has timeout/refresh controls.
It must not be combined with the alternate provider token mechanisms. Use
absolute installed paths, and keep stdout out of diagnostics. Desktop-launched
processes may lack terminal-exported variables; the managed helper avoids that
environment prerequisite. This changes neither subscription entitlement nor
upstream credentials. [Authentication contract](https://learn.chatgpt.com/docs/config-file/config-advanced#custom-model-providers),
[desktop/provider setup](https://learn.chatgpt.com/docs/enterprise/connect-to-a-gateway#use-an-organization-credential-helper).

Catalog metadata must match the deployed client and actual upstream capabilities;
desktop uses the bundled CLI version. A local catalog takes precedence over a
remote catalog URL. Generic alias handling is not proof of tools, reasoning or
context compatibility. A catalog is a qualification input, not a way to add model
capabilities. [Gateway metadata](https://learn.chatgpt.com/docs/enterprise/gateway-compatibility#recognized-model-names),
[catalog rollout](https://learn.chatgpt.com/docs/enterprise/roll-out-a-gateway#use-a-model-catalog-for-custom-names).

## Skills-only distribution and authorization

The existing root manifest and shared skill match the documented portable
packaging pattern, including `onboardingSkill`. However, this plugin calls local
programs. Current documentation says a skills-only package requiring local
execution must set `extensions.com.openai.requires_local_executor: true`. The
[inspected manifest](https://github.com/Xpounder-com/hormuz/blob/75f089a7065f1e4cdc9a0b6309f6733af81cdac3/plugin.json)
does not declare it. Treat that as a concrete packaging task before public
submission. The public metadata also needs the listing text and icon requirements
checked; its present minimal fields are not a completed submission package.
[Local-executor declaration](https://developers.openai.com/plugins/deploy/desktop-only#plugins-without-an-mcp-server),
[listing requirements](https://developers.openai.com/plugins/deploy/submission#listing-metadata).

Skills-only local execution is marked Desktop only and follows the standard
submission flow. The special approved-partner/organization-allowlist requirement
is expressly stated for **local MCP** submissions. Do not promise that skills
avoid ordinary submission review, that all organizations have portal access, or
that a desktop-only label disappears without MCP. If the portal rejects the
local-executor field, the documented next step is confirming submission access
with OpenAI. Public submission also excludes lifecycle hooks and registered-app
references; adding MCP to an already skills-only public plugin is currently not
supported. [Desktop-only guidance](https://developers.openai.com/plugins/deploy/desktop-only),
[submission boundaries](https://developers.openai.com/plugins/deploy/submission).

Package installation loads instructions; the host still decides which tools can
run and which filesystem changes need approval. In particular, `.codex` paths
are protected even inside default writable roots. A skills bundle cannot grant
itself permission to change configuration or restart the app. Show the exact
shared setup preview, follow the host's authorization for those changes, and
retain the existing ownership journal. Do not weaken sandbox or managed controls
to make onboarding appear automatic. [Protected paths and approvals](https://learn.chatgpt.com/docs/agent-approvals-security#protected-paths-in-writable-roots).

There is no documented install-time gateway bootstrap in the skills-only
manifest. Onboarding is a user-run workflow. The [current skill](https://github.com/Xpounder-com/hormuz/blob/75f089a7065f1e4cdc9a0b6309f6733af81cdac3/skills/hormuz/SKILL.md)
requires the installed router and existing verified assets, then calls shared
setup. Acquisition, extraction, installation/update and fresh-machine prerequisites
remain distinct product work. No duplicate plugin installer is proposed.

## Native acceptance checklist for a selected release

This is a proposed qualification gate, not evidence that the checks have run.
Use the same approved seam: **connect → actual host request → inspect
result/status → disconnect**. Freeze acceptance before native episodes and keep
failed/unknown outcomes. Vendor [gateway checks](https://learn.chatgpt.com/docs/enterprise/roll-out-a-gateway#test-the-client-and-gateway)
require the same CLI or desktop surface that users will actually run.

1. **Identify the target.** Pin OS/architecture, app build and bundled engine
   (desktop), or executable and hash (CLI), plus router wheel, helper, runtime,
   model and capability profile. Record plugin package and marketplace source
   independently. Select a disposable home/project and bounded cleanup owner.
2. **Install and activate instructions.** Check the installed skill/reference and
   explicit invocation. Test onboarding in a fresh conversation and installation
   during an existing conversation. If local executor or shell access is absent,
   return a scoped unsupported result; do not claim configuration was applied.
3. **Preview, authorize and connect.** Use shared setup with already verified
   assets. Confirm no persistent credential/process from preview, then matching
   apply and repeated-apply reuse. Test protected-path handling and managed
   restrictions. Require ready/installed/owned public receipts; never print the
   helper token. Exercise missing/changed credentials as explicit denial.
4. **Reload and prove the path.** Restart the client/app as documented, open a
   fresh native task and inspect the effective provider/model. Record correlated,
   content-free Hormuz work/attempt and intended destination facts for the actual
   model POST. Test a trusted project/profile/launch override as a separately
   visible boundary. A reply, readiness check or installed plugin is insufficient.
5. **Prove local-only and affinity.** Make the local destination unavailable and
   require denial before any cloud dispatch. Check ordinary completion, tool
   call/result/final answer, incremental SSE and cancellation. Keep later model
   calls on the admitted destination; shadow advice cannot alter it. Count model
   helpers separately. This does not audit every host/tool network connection.
6. **Qualify continuing conversations.** Test a follow-up turn, process/app
   restart and resume, and a fork on the selected surface. Record actual native
   thread/session envelope changes and work binding. Within a continuing owned
   router epoch, preserve successful-response affinity; unknown prior history
   or a changed destination generation must fail explicitly rather than be
   silently adopted. After reconnect, current Hormuz requires a fresh compatible
   conversation; do not silently replay inference or migrate opaque state.
7. **Inspect task outcomes independently.** Verify the requested file/result with
   predefined checks, preserving tool errors, non-edits and timeouts. Keep coding
   quality separate from transport success and classifier recommendations. The
   existing failed experiments remain historical failures.
8. **Disconnect and restore.** Use the exact owned generation; restore unchanged
   owned defaults/provider/auth values, preserve later user/project edits and
   report retained settings. Require the old generation's helper to refuse a token;
   retain its private credential file as documented. Stop/reap only owned
   router/engine resources, and close bounded observers. Check a fresh native
   task after the required reload uses restored effective settings. Disconnect
   before uninstalling instructions or the CLI. Keep restoration failure pending.

The checklist derives its application invariants from [connection ownership](https://github.com/Xpounder-com/hormuz/blob/75f089a7065f1e4cdc9a0b6309f6733af81cdac3/docs/CONNECTED_CLIENT_LIFETIME.md),
[conversation binding](https://github.com/Xpounder-com/hormuz/blob/75f089a7065f1e4cdc9a0b6309f6733af81cdac3/docs/CONVERSATION_WORK_BINDING.md)
and [gateway compatibility](https://learn.chatgpt.com/docs/enterprise/gateway-compatibility).
Raw identities, source content, prompts/replies, private homes and credentials
stay outside public artifacts. Publish exact versions, aggregate results,
content-free receipts and evidence digests.

## Handoff decisions and optional MCP gate

The owner still needs to choose the first native desktop/app build and minimum
CLI support envelope. A later implementation spec should (a) finish the existing
review stack, (b) make the shared adapter compatible with that chosen engine,
(c) correct local-executor/listing metadata, and (d) run the frozen native
acceptance above before claiming low-tweak desktop routing or public acceptance.
Do not replace the client agent loop or widen active routing to hide capability
failures. Public marketplace review, real coding capability and cloud eligibility
remain independent gates.

Only revisit MCP if a selected, supported local host demonstrably cannot invoke
the shared CLI through its ordinary execution tools **and** a supported local
MCP control tool would resolve that exact gap. A narrow adapter could expose
setup/status/disconnect over the same owner. It would still need host permissions,
provider configuration/reload, native qualification and any local-MCP submission
approval. MCP does not turn remote/cloud chats into locally intercepted model
requests. This conditional option is an inference from the documented
[MCP tools/context boundary](https://learn.chatgpt.com/docs/extend/mcp) and
[gateway data/tool boundary](https://learn.chatgpt.com/docs/enterprise/gateway-compatibility#data-and-tool-boundaries),
not a currently demonstrated requirement.
