# Shared Hormuz workflow

All agent packages use these commands. The host agent retains its tools,
permissions and conversation. Hormuz owns only its connected-client lifetime.

## Prerequisites

Use an installed Hormuz 2.0 development CLI and an existing, user-selected
router configuration. If the binary is missing, install a user-selected,
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

The implemented persistent adapter pins Codex CLI **0.147.0**; the plugin host
can have a different version. A newer host does not satisfy the target pin.
Ask for a target executable only if it cannot be determined safely. Keep
existing client settings behind the CLI's validation and restoration journal;
do not bypass its version checks or hand-edit those settings.

Before connecting, identify the selected config, actor, target executable and
client home. An existing qualified local route and its running local engine
are required for local inference. The example config intentionally has no
qualified routes. Real qualification, model downloads and cloud/authentication
eligibility are separate work: never invent qualification records, use native
login caches as entitlement, or introduce billable fallback.

Keep credentials in their configured environment variables. Check presence
without printing values. Do not run `hormuz auth token` into the conversation,
read authentication caches, or include private config/prompt content in reports.

## Setup

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
   target and inspect its result/status. Otherwise report connection readiness
   and leave task execution to the user's coding client. Project overrides,
   desktop engines and unrelated tools may have different routing scope.

## Status and troubleshooting

Run the status command for the same client home. Report `phase`, `router`,
`configuration`, `retained_settings` names and content-free error/action codes.
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
completed cleanup. Model engines and unrelated clients remain user-owned.

Disconnect before removing the skill/plugin or router binary. Uninstalling
instructions alone does not restore a connected client's configuration.
