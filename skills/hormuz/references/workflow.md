# Shared Hormuz workflow

All agent packages use these commands. The host agent retains its tools,
permissions and conversation. Hormuz owns only its connected-client lifetime.

## Prerequisites

Use an installed Hormuz 2.0 development CLI. If the binary is missing, install a user-selected,
reviewed Hormuz 2.0 source checkout in a dedicated Python 3.11+ virtual
environment. Select unused absolute paths and use the installed executable
for subsequent commands:

```sh
python3 -m venv /path/to/hormuz-venv
/path/to/hormuz-venv/bin/python -m pip install /path/to/reviewed-hormuz-checkout
/path/to/hormuz-venv/bin/hormuz --version
```

The skills bundle's version is independent of the router package version. Do not
substitute an older package with the same name from a package index or mutate
an unrelated virtual environment.

The persistent adapter supports exact Codex CLI **0.147.0 and 0.160.0** versions;
the plugin host can have a different version. Detect the selected target instead
of downgrading a supported 0.160.0 installation. Unknown versions remain blocked.
Ask for a target executable only if it cannot be determined safely. Keep
existing client settings behind the CLI's validation and restoration journal;
do not bypass its version checks or hand-edit those settings.

Before connecting, identify the target executable, client home and configuration
path. For a new local-only setup, select a
new configuration path plus the existing verified runtime archive, extracted
llama-server and GGUF. Shared setup generates the route and configuration.
For an existing user-selected configuration, preserve its actor and policy.
If the selected configuration declares a verified `managed_llama` profile,
the shared connect command verifies its existing runtime bundle/model files and
starts the owned engine. Use `auto` or `local_only` admission for that profile;
an occupied endpoint requires inspection, never process adoption or termination.
Without a managed profile, the local engine must already be running externally.
The example config intentionally has no qualified routes. Real qualification,
model downloads and cloud/authentication
eligibility are separate work: never invent qualification records, use native
login caches as entitlement, or introduce billable fallback.

Let shared setup manage its local caller credential. For an existing
environment-backed configuration, check variable presence without printing
values. Do not run `hormuz auth token` or `hormuz auth local-token` into the conversation,
read authentication caches, or include private config/prompt content in reports.

## Generate local setup from existing files

Use the shared operation when the user requests local-only setup and has
existing verified assets. Check `hormuz client setup --help` first: development
builds share a version number, so a version string alone cannot establish this
command. If it is missing, use the reviewed source-install path above and
check again before proceeding. The bundled profile pins the documented Codex
0.147.0 or 0.160.0 / llama.cpp b11617 / Qwen3-4B macOS arm64 experiment, selected
by the actual executable version. Preserve each row's limits and historical
quality failures. An explicitly selected reviewed qualification
file may be passed with `--qualification`; use existing evidence and trusted
digests rather than manufacturing qualification declarations.

```sh
hormuz --config /path/to/new-hormuz.json client setup codex \
  --executable /path/to/codex --client-home /path/to/codex-home \
  --runtime-archive /path/to/existing-runtime.tar.gz \
  --llama-executable /path/to/extracted/llama-server \
  --model-file /path/to/existing-model.gguf --dry-run
```

Default setup creates/reuses a private local credential and installs a
generation-bound command-backed auth helper. Review the version-2 setup
receipt's `configuration.authentication: managed_local` and nested
`connection` preview. Preview leaves no persistent new credential. A matching setup request authorizes the shown
creation and connection; run the identical command without `--dry-run`.

Require the nested receipt to report `connected`, `ready`, `installed` and
`local_runtime: {ownership: owned, state: ready}`. Then follow status and task
inspection below. Missing/corrupt files, unsupported rows, changed configuration
or an occupied endpoint block. Follow the public recovery action. Setup reuses
existing assets and identical configuration; no file discovery, extraction or
downloads occur. Startup failure keeps the generated configuration for retry
and restores owned client settings through the existing lifetime.
An explicitly selected `--credential-env NAME` retains the older environment
mode and version-1 setup receipt; only that mode requires the variable in both
Hormuz and Codex. Preserve existing configurations instead of converting them.
For missing, changed or unsafe managed credentials, follow
[credential recovery](../../../docs/LOCAL_CREDENTIAL_SETUP.md) and the public
error action. Preserve the credential file and journal; a helper failure is not
permission to remove authentication or read a native login cache.

When the user requests shadow routing, read [shadow routing](../../../docs/SHADOW_ROUTING.md)
and check setup help for `--shadow-router`. Add the option to both preview and
apply using a new configuration path. Require version-3 setup to report
`configuration.shadow_router: shadow`, then inspect the work attempt's versioned
advice. Preserve the actual route, hard local-only policy and continuing destination.
Keep classification advice separate from real coding outcomes; the option provides
no live learned routing or cloud/subscription permission.

## Inspect cloud readiness

When the user asks about cloud authorization or readiness, check
`hormuz client cloud-readiness --help` and use the reviewed source-install path
above if the command is unavailable. Select the existing router configuration,
client home and target executable, then run the shared read-only operation:

```sh
hormuz --config /path/to/hormuz.json client cloud-readiness codex \
  --executable /path/to/codex --client-home /path/to/codex-home
```

Preserve a selected `--actor ACTOR`. For the desktop surface add
`--surface desktop`; terminal version qualification does not qualify that app.
Report the `hormuz.cloud-readiness` receipt's configuration facts and each path's
status, billing scope and blocker/action pairs. `scope: configuration_only`
means the operation changes no settings, resolves no credentials and makes no
provider requests. The API path's `unverified` status describes declarations;
credentials, actual request eligibility and provider qualification need a
separately authorized flow. Both ChatGPT plan paths remain `not_implemented`
and require their own adapter, native consent and account eligibility.

An administrator-selected [native catalog configuration](../../../docs/NATIVE_CODEX_CATALOG.md)
provides discovery only. Keep that distinction in diagnosis: a catalog response
does not establish native inference, account eligibility or a supported host.
Do not read native login caches, generate native provider settings or reuse a
gateway/API credential as native auth to clear the remaining blockers.

Keep hard local-only policy and existing settings intact. Finish the preflight
by reporting unresolved blockers and the next action; preflight does not
authorize connection or billable fallback. For project/profile/launch conflicts,
follow [override inspection](capabilities.md#diagnose-and-explain). See
[the receipt contract and limits](../../../docs/CLOUD_READINESS.md) for detail.

## Connect an existing configuration

1. Use the user-selected mode. For local-only, verify `route_admission.mode` is
   `local_only`; for automatic routing, verify it is `auto` and inspect
   the explicitly allowed baseline. If the configuration needs changing,
   explain the precise edit and apply only the authorized scope. Keep any
   local-only ceiling; an unavailable local route must block rather than
   redirect to cloud.
2. Preview using the shared command. Replace placeholders with known absolute
   paths and quote them for the user's shell:

   ```sh
   hormuz --config /path/to/hormuz.json client connect codex \
     --executable /path/to/codex --client-home /path/to/codex-home --dry-run
   ```

   If an actor was selected, add the same `--actor ACTOR` on preview and connect.
   Proceed only after preview succeeds and the proposed changes match the
   requested connection. A request to set up routing authorizes those matching
   changes; ask only for a missing choice or a change outside that request.
3. Run the identical command without `--dry-run`, then inspect:

   ```sh
   hormuz client status codex --client-home /path/to/codex-home
   ```

   Connection success requires a public receipt for the owned generation with
   `phase: connected`, `router: ready` and `configuration: installed`. This is
   connection readiness, not proof of local model quality or completed work.
4. When the user has requested a coding task, submit it through the connected
   target and inspect its result/status. For 0.160.0, use the qualified launch
   profile below. Otherwise report connection readiness
   and leave task execution to the user's coding client. Project overrides,
   desktop engines and unrelated tools may have different routing scope.

### Codex 0.160.0 launch profile

The local llama.cpp qualification covers inline function tools over HTTP.
Launch the selected target with `-c agents.enabled=false --disable multi_agent_v2
-c 'model_reasoning_summary="none"' -c 'model_reasoning_effort="none"'`, keeping the user's requested command,
project and sandbox. For example:

```sh
codex exec -c agents.enabled=false --disable multi_agent_v2 \
  -c 'model_reasoning_summary="none"' -c 'model_reasoning_effort="none"' \
  --sandbox read-only \
  "Review this Python function using the supplied verification script."
```

Use these flags for the requested local coding task; preserve the host's tools
and approvals. The effort override preserves saved user preferences. The
qualified request reasoning is empty or explicitly `{"effort":"none"}`.
Namespace/freeform tools, reasoning execution controls and opaque
provider state outside the existing affinity rules remain blocked. The profile
does not establish autonomous coding quality. Read [the version qualification](../../../docs/CODEX_0160.md)
for native evidence, model-catalog compatibility and rollback.

## Status and troubleshooting

Run the status command for the same client home. Report `phase`, `router`,
`configuration`, `retained_settings` names and content-free error/action codes.
For coding readiness, check `client status --help` for `--routing`, then run
`hormuz client status codex --client-home /path/to/codex-home --routing`.
Report its `coding_readiness` and `automatic_task_types` separately from engine
health. The bundled experiment has unknown coding readiness and blocks automatic
coding. For reviewed outcome receipts, read [coding qualification](../../../docs/LOCAL_CODING_QUALIFICATION.md).
Keep router readiness separate from model readiness and task completion. A
local-only admission rejection is a blocked request, not permission to change
privacy mode. Follow the CLI's recovery action; never delete its journal,
retry an ambiguous model request or stop a process that Hormuz does not own.

## Disconnect and restore

```sh
hormuz client disconnect codex --client-home /path/to/codex-home
hormuz client status codex --client-home /path/to/codex-home
```

Use `--generation GENERATION` on disconnect when acting for a specific receipt.
Completed cleanup requires `phase: disconnected`, `router: stopped` and
`configuration: not_owned`. Report any retained setting names: newer user
edits are intentionally preserved. Pending recovery or shutdown is not
completed cleanup. A managed receipt uses schema version 2 and reports
`local_runtime.ownership: owned`. Require its state to be `stopped` before
reporting completed engine cleanup; `disconnecting` remains pending.
Externally started engines and unrelated clients remain user-owned.

Disconnect before removing the skill/plugin or router binary. Uninstalling
instructions alone does not restore a connected client's configuration.

## Device-local history and spending

Use `hormuz --config /path/to/hormuz.json history --help` for the installed
command surface. History is separate from connection ownership and starts
disabled. The user must explicitly request enabling capture; connecting a
client does not grant history consent. Enabling retains future redacted text
and usage on the device, encrypted with a local key. Select an actor explicitly
when multiple actors are configured. Capture does not require a reconnect.

Use `history status`, `history list` and `history insights` for metadata-only
inspection. Report configured-price estimates and unknown costs separately,
with coverage limited to retained opt-in attempts. Local infrastructure cost
and provider invoices are outside those estimates.

For content review, give the user `history show ENTRY_ID` to run directly in
their local terminal. Retained text stays out of agent tool results and chat;
those may be sent to a remote model by the host. For a requested export, use
`history export --id ENTRY_ID --output /explicit/local/file.json`
(repeat `--id`, or omit it for all retained entries). Export writes decrypted
content with owner-only permissions and refuses overwrite. Obtain a destination
from the user if none is specified; choose a private local path they control.
Keep exported content out of agent context. No upload, sync or fine-tuning is
part of history management.

Honor requested deletion using `history delete ENTRY_ID` or
`history delete --all`; `history disable` stops collection and retains existing
history. Pending captures are invalidated by disable or deletion. Report the
public result. Deletion does not remove exported copies, filesystem backups or
separate accounting evidence. Retention defaults to 30 days and cleanup runs
on use, including review/export; there is no exact idle-time deletion promise.
