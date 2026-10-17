# Customer-local router migration

Version `2.0.0a1` is a source-development prerelease. This cleanup does not publish
a release or establish installed-client support.

Supported commands: `serve`, `client config/connect/status/disconnect`, `auth
token` and `contract manifest`. Invoking `koto` prints help. It no longer
launches a coding app. Agent, personal/desktop, account, finance, policy-admin,
hosted and storage-migration commands are rejected by the parser.

Configuration accepts local SQLite storage and environment-supplied caller and
provider credentials. Enterprise authentication, managed custody, portfolio and
outcome connectors, account bindings, hosted ingress, PostgreSQL and billing
settings fail before setup activation. Do not discard these settings silently
from an existing production profile; create a distinct local-router profile.

Retired browser, admin, account and billing routes return 404. Authenticated JSON
work receipt paths remain for routing evidence. `/v1/work/state` emits
`hormuz.ai-work-state` version **3**: an owned request report, local connection
description and eligible client choices. Versions 1/2's browser principal,
checkout/activation, funnel and billing fields are removed. Consumers must check
the version. Missing data does not imply zero cost or a paid entitlement. The
[v3 fixture](../tests/fixtures/router-work-state-v3.json) freezes required and
retired fields. Existing relay metadata v1/v2/v3 and connected-client
status/journal v1 retain their contracts. Individual receipts retain owned agent
choices and report unavailable connector bindings.

Existing SQLite evidence, including unknown/pending reservations, is retained.
Shared schema verification and historical contract readers remain even where
their names describe a retired product. This migration does not drop old tables.
PostgreSQL deployment/migration source is retired; operate old resources with a
pinned historical release and the [operational track](OPERATIONS_CLEANUP.md).

## Upgrade and rollback

1. Use the binary owning an existing connection to disconnect. Resolve any
   `restore_required` conflict and preserve edits and receipts before upgrading.
2. Keep matching old binaries, configurations and backups for legacy resources.
   Use a separate local-router configuration and explicit local credentials.
3. Preview the exact supported client change with `client connect codex --dry-run`.
   Qualify the real runtime/model/client before claiming usable routing.
4. Before downgrading, disconnect using the newer owner. Do not share a mutable
   SQLite store with an older writer: use its matching verified snapshot or
   retain the newer binary. Never delete user data as a rollback shortcut.

## CI and repository protection cutover

CI runs the workflow on Python 3.11–3.14, source checks and an installed-wheel
round trip. `CI / required` requires them all. PostgreSQL/HA, hosted deployment,
native-app and website publishing workflows are retired with their source.

The declarative main ruleset requires strict `CI / required`. That aggregate
requires all four Python workflow jobs and the installed-wheel job to pass.
At landing, synchronize the live status-check requirement with that aggregate
only after exact-head review and all six new checks pass. The October 15 live
ruleset still required 16 old contexts: package installation, Codex/Claude
compatibility, four Python versions, OCI runtime/reproducibility/supply chain,
PostgreSQL compatibility/recovery/HA, Compose, Kubernetes, disaster recovery
and the native Mac client. The new workflow replaces the retained package and
Python checks and retires the other products' jobs. The checked-in old rules
also named `Website checks`, which was absent from the live ruleset.

Change only the required-status-check list. Preserve strict/up-to-date checks,
PR and review-thread protection, all other rule parameters and bypass settings.
Save the live ruleset before the cutover and verify the resulting rules after
the update. Keep the cleanup PR draft until that transition is ready; merge
through normal protection and verify the exact merged commit's own CI. Do not
add fake passing legacy jobs or bypass a missing gate.

The retirement branch is rebased onto final destination/admission/lifetime main
checkpoint `8489d9d3a467442dca4b91d196928e325dd01a18`. Historical source and the
final synthetic qualification records remain available at that Git revision.
No other checkout's unpublished work is part of the rebase.
