# Customer-local router migration

Version `2.0.0a1` is a source-development prerelease. This cleanup does not publish
a release or establish installed-client support.

Supported commands: `serve`, `client config/connect/status/disconnect`, `auth
token` and `contract manifest`. Invoking `hormuz` prints help. It no longer
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

The declarative main ruleset keeps `CI / required` and removes the retired
`Native Mac client and loopback contract` and `Website checks` requirements.
Live GitHub protections were **not changed** while #524–526 are active. An
administrator must synchronize just these obsolete required contexts when this
retirement lands, keeping strict checks and PR protection. The cleanup PR stays
draft until that cutover is ready. Do not add fake passing legacy jobs.

The retirement draft starts at the published lifetime tip
`2e5011f8e1064976c33c5694e74fd0bb17539c14`. Parent CI work may advance separately;
rebase and resolve retained routing/lifecycle contracts before landing retirement.
Do not publish another checkout's unpublished fixes to work around this ordering.
