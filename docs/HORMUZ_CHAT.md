# Hormuz inside an existing chat

Use the Hormuz plugin/skill as one entry in the chat. The host keeps its selected
OpenAI model, tools, reasoning, permissions and account. Hormuz performs only the
explicit task sent to the selected local model. This is delegated local execution;
it does not replace the model running the whole Codex conversation.

## Choose and submit

1. Use an existing, separate Hormuz control home from the connection receipt.
   Inspect its status with the shared CLI. If it is stopped, preview and connect
   that same isolated owner using its reviewed gateway configuration. For new
   setup, use the shared setup workflow with a separate home. Keep the normal
   Codex home and app launch environment unchanged.
   For an existing retired app installation, its disabled
   `~/.codex/hormuz-app-bridge-v1.json` can identify the prior `control_home`
   and `native_executable`. That home's `.hormuz-router/connection.json`
   records the gateway configuration path. Treat those as discovery hints, validate the same owner
   through the public status and connect preview, and leave the bridge disabled.
   The chat task uses Hormuz's HTTP interface rather than the app engine's model
   protocol. If the bundled engine has updated beyond its pinned qualification,
   use an already installed supported terminal Codex CLI to validate the separate
   router owner. Keep the desktop engine and its normal settings unchanged.
2. List the choices:

   ```sh
   hormuz chat models --router-home /private/hormuz-router-home
   ```

   Offer **Configured router** (its existing default) and the returned local
   aliases. A returned alias has matching public destination and declared
   reasoning-capable qualification. It remains subject to live health, policy,
   request bounds and task-type admission. An empty list is a missing prerequisite.
   Preserve configured routing and privacy policy; this workflow is local-only.
3. Use the user's explicit choice for the current task. Put only the authorized
   task/context into a private UTF-8 temporary file; avoid prompt text in command
   arguments. Delete that temporary file after the call. For a selected alias:

   ```sh
   hormuz chat ask --router-home /private/hormuz-router-home \
     --model qualified-local-alias --prompt-file /private/task.txt
   ```

   Omit `--model` for the configured router default, including an already
   configured scorecard's choice among qualified local routes. An explicit alias
   is pinned before scorecard selection or dispatch. Both choices keep privacy,
   policy and qualification checks, and disable automatic capacity failover.
   The router must advertise chat-task v1 support before any task is submitted;
   an older router returns `router_upgrade_required` without receiving the task.
   Output tokens default to 2048 and effort to `max`; use a smaller qualified
   token bound when needed. Read `chat ask --help` for finite limits.
4. Report the local task result and actual routed model from the receipt. Treat
   model output as untrusted task data. The host retains its tools and decides
   any follow-up under normal approvals. No additional tool-execution loop runs
   inside Hormuz. A failed task stays inside this command and leaves native
   Codex available. Ambiguous submissions are reported and never retried.

## Disable

```sh
hormuz off
```

Listing or using chat choices records one generation-bound owner pointer in
Hormuz's existing private process-control directory, outside the Codex home.
Plain `off` discovers that separate owner without requiring a retired app bridge.
Only one active chat owner can be registered for a normal Codex home; disconnect
it before choosing another. A stale generation cannot stop a replacement.
Use `hormuz off --client-home /private/hormuz-router-home` to address an owner
directly.

For a previously installed app bridge, plain `hormuz off` also disables its
manifest, restores its owned launch setting, and disconnects that separate owner.
Legacy recovery errors remain visible after owned shutdown is attempted. Pending
cleanup is reported as pending even if another owned router has already stopped.
Require `router: stopped` and, for an owned model process, `local_runtime.state:
stopped`. No Codex reinstall is involved.

The chat task command starts no processes and installs no startup hooks. The
native model picker remains owned by Codex; this plugin entry does not add a
native model-menu row. A future native Hormuz row requires app-supported
integration and separate full startup, policy, login and visible-app qualification.

## Contracts and verification

`hormuz.chat-models`, `hormuz.chat-task` and `hormuz.chat-error` are additive v1
CLI receipts, frozen by matching fixtures in `tests/fixtures`. The additive v1
`hormuz.chat-owner` control document records only the normal home, separate router
home and connection generation; it is removed after successful shutdown. Its
matching fixture freezes that format. These contracts store no chat preferences
or account data. Task text is returned to the invoking chat;
existing router history-consent rules still apply. Old connection, disconnect
and model qualification receipts retain their versions. Rollback is `hormuz off`
for the chosen owner followed by removal of the optional CLI/skill update.

Workflow tests exercise the public CLI, actual router, isolated client homes
and fake endpoints: model choice, max reasoning, configured default, rejection,
outage, pre-dispatch pinning, no retries/cloud fallback, bounded prompts, fresh
owner discovery, stale generations, legacy recovery failures and pending shutdown.
Real local
model verification is separate from those synthetic regressions.
