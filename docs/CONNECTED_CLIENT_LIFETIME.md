# Persistent Codex connection

This implements connected-client lifetime from [router spec #523](https://github.com/Xpounder-com/hormuz/issues/523), alongside [execution destinations](EXECUTION_DESTINATIONS.md) and [local route admission](LOCAL_ROUTE_ADMISSION.md). The CLI connects Codex's user defaults to one generation-owned router and can restore the settings it owns. A plugin can invoke the same commands rather than maintaining another installer.

The implemented workflow is tested with disposable Codex-compatible executables, temporary configuration and loopback providers. It uses POSIX file locks, private files and Unix-domain process control. The pinned version check is Codex CLI `0.147.0`; it does not qualify the Codex desktop app's separately bundled engine. These synthetic checks do not establish installed operating-system support, real model quality, subscription eligibility or plugin marketplace acceptance. Persistent Claude configuration, model installation, startup registration and native subscription adapters remain separate work.

## Preview and connect

Use an existing gateway configuration with local ingress bound to `127.0.0.1`, a configured actor with a gateway token environment variable and at least one policy-permitted OpenAI route. Configure the intended [admission mode](LOCAL_ROUTE_ADMISSION.md) and qualified local destination before connecting. Connection does not itself create model qualification or authorize cloud inference.

Review the proposed user-default changes before activation:

```sh
hormuz --config /path/to/hormuz.json client connect codex \
  --executable /path/to/codex --actor developer --dry-run
```

Activate the same connection:

```sh
hormuz --config /path/to/hormuz.json client connect codex \
  --executable /path/to/codex --actor developer
```

The default client home is `CODEX_HOME`, or `~/.codex` when that variable is absent. `--client-home /path/to/disposable-codex-home` selects an explicit home for every lifecycle command. Routine tests use that explicit disposable path and never modify the active user's configuration.

Setup checks the executable version, known local managed-configuration files, gateway settings and safe file ownership before changing user defaults. The preview lists the settings and provider-table change without starting the router or activating routing. Connect modifies `model`, `model_provider` and `web_search`, adds a unique Hormuz provider table and starts an owned local router. The provider table contains the gateway credential's environment-variable name rather than its value. The runtime and Codex must receive the appropriate gateway token through their ordinary environment; provider credentials remain governed by the gateway configuration. Existing Codex authentication files are not imported or repurposed.

Keep the existing coding app. Run the qualified Codex CLI normally after connect. An already-running client may require a restart to reload its user defaults. Trusted project configuration, profiles and command-line overrides can select other settings; this connection does not guarantee that every Codex request traverses Hormuz. Hard local-only applies to the model request paths that actually reach the configured router. Other tools, plugins and app connections retain their own network behavior.

## Status and disconnect

```sh
hormuz client status codex
hormuz client disconnect codex
```

Lifecycle commands emit JSON with `schema_version: 1`. Status separates the durable setup phase from the owned router's current response. A `connected` journal phase alone does not prove that the router is reachable, that a local model is ready or that a task completed. Inspect [router state](LOCAL_ROUTE_ADMISSION.md#status-compatibility-and-rollback) and request receipts for admission, destination and request outcomes.

The connection receipt includes its opaque `generation`. Automation retaining a receipt can guard cleanup:

```sh
hormuz client disconnect codex --generation RECEIPT_GENERATION
```

A mismatched generation cannot remove a newer connection. Repeated connect with the same active setup reuses the owner; changed setup requires an explicit disconnect/reconnect. Repeated disconnect is safe. Removing plugin instruction files does not disconnect the router; invoke the disconnect command before assuming the connection is removed.

Disconnect restores each owned setting only when its current value still equals the value Hormuz installed. Later user edits remain intact and appear in `retained_settings`. An edited provider table is retained rather than overwritten or deleted. Unrelated preferences and provider tables remain in place. This is value restoration, not a whole-document backup replay or a promise to reproduce every original byte.

Disconnect also requests shutdown over the generation-owned private control socket and waits for the kernel-held runtime lease to release. Cleanup drains the router, cancels owned client sockets and closes owned resources. A bounded shutdown watchdog may exit only that owned host after five seconds; it does not search for or terminate unrelated processes. Existing conservative accounting and recovery for ambiguous provider work remain authoritative.

## Recovery and restrictions

One private journal and bounded file-lock transaction coordinate CLI and future plugin callers. The journal records prepared, connected, disconnecting, restore_required and disconnected phases so rerun can distinguish an incomplete setup from an active owner. Incomplete restoration is explicit rather than reported as disconnected. Recovery compares current settings before writing; an edit observed before replacement produces an explicit failure rather than an unconditional overwrite.

Unsupported versions, unsafe files, ambiguous recovery, unavailable runtime control and managed settings produce explicit errors. Disconnect remains the restoration path; do not manually delete the private journal while it owns a connection. Known local requirements and managed configuration are detected conservatively. Real MDM, remote requirements and host-app configuration precedence still need separate product qualification; no claim that user defaults override managed restrictions is made.

Files are bounded, private and owned by the invoking user. The connection journal lives beneath the selected client home in `.hormuz-router` and stores ownership metadata, original owned values, installed owned values, source identity and the private control path. It does not store gateway/provider credential values, authentication-file contents, prompts or transcripts. Status and receipts expose bounded connection facts, not the private journal.

## Compatibility, migration and rollback

The new lifecycle commands are additive. `client config`, existing transient Personal launch adapters and their relay contracts retain their separate behavior. Persistent setup authority belongs to the connected-client lifetime module; existing launch adapters do not gain general permission to edit client configuration.

The connection journal and public lifecycle output begin at schema version 1. Unknown journal versions fail explicitly instead of being interpreted as current state. No AI Work ledger schema, credential format or historical relay version is changed. A router restart creates a new destination generation; existing continuation/evidence rules still require fresh compatible state.

Before downgrading to a binary without these lifecycle commands, disconnect with the current binary and verify restoration and router shutdown. Preserve reported user edits. If restoration is incomplete, resolve the specific conflict and rerun disconnect before removing ownership records or replacing the binary. Disconnect does not remove models, unrelated processes, gateway data or the CLI installation. Restart or reconnect is required to apply changed gateway configuration; reconnect is not an automatic substitute for consent to remove a local-only ceiling.


## Version 1 fields and limits

A successful status/connection/disconnection receipt has `schema_version: 1`,
`scope: "codex_user_defaults"`, `phase`, `router`, `configuration` and
`retained_settings`. Once a journal exists it also has `generation`,
`client_version` (last qualified pin) and `owned_settings` (top-level setting
names). The opaque 32-hex generation identifies this setup, separately from a
provider's destination generation.

| Field | Meaning |
| --- | --- |
| `phase` | `prepared` records an unfinished setup; `connected` records an installed owner; `disconnecting` means cleanup is pending; `restore_required` means its router stopped but settings need repair; `disconnected` means owned cleanup completed. |
| `router` | `ready` means its control endpoint and gateway readiness check succeeded; `unavailable` means no successful response; `stopping` means shutdown was acknowledged; `stopped` requires completed owned cleanup. This is not model readiness. |
| `configuration` | `installed` means owned values match; `changed` means newer values differ; `unavailable` means the document cannot be safely inspected; `not_owned` applies after disconnection. |
| `retained_settings` | Names of edited top-level values or an edited provider table left for the user; no private original values are returned. |

Preview output instead has `phase: "preview"`, `settings` (proposed values) and
`changes` (add/replace operations). An active identical owner previews empty
changes. Preview rejects pending recovery without performing it. An error exits
2 and emits `{"schema_version":1,"error":{"code":"connected_client_...","action":"..."}}`
on stderr. `action` describes the next safe operation; no credential or private
source path is returned. The workflow's [status v1 fixture](../tests/fixtures/connected-client-status-v1.json)
freezes the completed-cleanup shape.

The private journal has exactly `schema_version`, `generation`, `phase`,
`client_version`, `installed`, `original`, `provider_block`, `gateway_config`,
`gateway_digest`, `actor_id`, `control`, and optional `retained_settings`.
`original` records only the previous three owned string values or absence;
`provider_block` records only the generated environment-reference provider.
`gateway_digest` pins the startup source bytes; changing gateway configuration
requires disconnect/reconnect. Environment credential rotation also requires
restarting the owned router. The record's phase is recovery intent, never proof
of a successful coding task. Invalid fields, duplicate JSON members and unknown
versions are rejected; records are not silently upgraded or discarded.

The selected client home and lifetime directory must be owned private directories
(mode 0700); journals/locks use 0600. Symlinked client homes, linked or unsafe
files, unsupported setting syntax and documents over 128 KiB are rejected.
Existing unrelated TOML strings, comments and provider tables are preserved;
owned top-level settings must have simple string assignments. A missing original
configuration returns to client defaults (an empty document may remain).
Known local checks cover requirements/managed files in the selected home,
`/etc/codex` and macOS managed-preference file locations. They do not query
account policy or read native authentication caches.

One bounded file-lock transaction owns setup per selected client home. Concurrent
callers converge on that owner. An occupied listener fails setup and restores the
owned values; it is not adopted or stopped. The owned host accepts at most eight
HTTP handlers and has finite client/control timeouts. Route admission's local
capacity limit remains authoritative for admitted local requests. Disconnect
allows a bounded grace interval, closes owned client sockets and waits up to
seven seconds for lease release; its own five-second watchdog bounds a stuck
host. A still-held lease remains `disconnecting`, never reported stopped.
External providers, model runtimes and coding clients are not terminated.

Codex's [documented configuration precedence](https://developers.openai.com/codex/config-basic#configuration-precedence)
and [managed configuration](https://developers.openai.com/codex/enterprise/managed-configuration)
explain why this user-default scope cannot promise control over every app or
project request. See the [qualification evidence](evidence/connected-client-lifetime/README.md)
for the exact synthetic checks and remaining support work.

The version probe reads only its seven named process-context variables; gateway
and provider credentials are excluded. The schema 1 secret inventory adds only
non-secret client-home/probe-context reads, with no new secret purpose or custody
mode. Installed-package and source inventory validation must both pass.
