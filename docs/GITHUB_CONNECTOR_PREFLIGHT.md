# GitHub outcome connector: offline v1.3 preflight proposal

This is a proposed #219 feature checkpoint under #214, based on public main
`7c8e5296329255bca35ef5e7d2cda9735885e7df`. It is not an accepted
compatibility decision, a GitHub App, a live webhook, a release candidate, or
authorization to activate a connector. The machine-readable companion is
[github-connector-preflight-v1.json](github-connector-preflight-v1.json).
The accepted source-neutral outcome foundation remains the only runtime path.

## Compatibility and authority decision

The v1.0.0 and published v1.2.0 gateway, authentication, policy, errors,
pagination, CLI, and evidence contracts stay unchanged. The closed outcome
event/page/receipt v1 shapes remain unchanged. The approved design names
`POST /v1/connectors/github/events`, but current server routing does not
activate it. No connector-owned production DDL, migration number, secret
store, or live App permission is introduced by this preflight. Current main
uses SQLite 12 and PostgreSQL 17; those are observed versions, not a claim on
successor migration numbers. If implementation requires storage or public API
changes, a reviewed successor #214 checkpoint must precede them.

GitHub documents `X-Hub-Signature-256` as HMAC-SHA256 over the exact request
body. That authenticates body bytes under a known webhook secret; it does not
by itself make event/delivery headers trustworthy or choose a tenant when one
App secret can cover more than one installation. The provider adapter must
establish the signing channel, delivery identity, server-known installation
and repository binding, and signature rotation before returning
`AuthenticatedDelivery`. It must verify raw bytes before JSON parsing or
repository access. Body-supplied organization, installation, repository, actor
or work-scope claims cannot grant authority. A dedicated tenant-owned App and
a shared App are alternative enrollment profiles; neither is selected here.
The exact permission set and signing-channel design require technical review.
Every accepted delivery during key rotation must still have a valid signature.

The [synthetic GitHub fixture pack](../tests/fixtures/connectors/github/cases.json)
contains proposed PR, review and check cases. Every mapping is pending; even
the two candidate PR observations are marked `proposal_only`. In particular,
a check or review without a verified PR relationship must remain unsupported
or unmatched. A code SHA is not an ordering counter for PR lifecycle events.
No external event is evidence of AI causality or employee performance. Exact
event/action/conclusion and least-privilege permission matrices must be
reviewed together before a normalizer can accept an outcome. Only approved
numeric identities, revisions, timestamps, lifecycle and quality states, and
coverage metadata may cross storage. Raw payloads, repository names and URLs,
branches, commit messages, review text, patches, paths and credentials do not.

## Deterministic transition and recovery contract

The connector-specific transition is disabled to enabled configuration, with
no schema migration proposed here. Stop writers/pools before any eventual
feature migration; never weaken the complete v1.0.0/v1.2.0 to candidate
transition required by #214. The following offline sequence is deliberately
synthetic and tests only the source-neutral receipt/storage foundation:

1. Snapshot a populated baseline with the connector disabled. Authentication
   failure or missing authority must occur before parsing or storage and leave
   the snapshot unchanged.
2. With a test-only verifier, force a normalization failure for one delivery.
   It may append fixed, content-free failure/coverage evidence, but cannot
   acknowledge success or append a normalized outcome.
3. Repair the test-only normalizer and retry the exact same authenticated
   delivery. One atomic receipt and outcome may commit; exact redelivery must
   return the original receipt without further writes. Conflicting bytes fail.
4. Disable the connector. New deliveries must fail before mutation; existing
   receipts and audit evidence remain. After zero post-checkpoint writes and
   stopped processes, a verified old application/database pair may be restored
   only into a separate destination. Keep the candidate snapshot.
5. If any post-checkpoint write exists, or its count is unknown, retain the
   candidate and recover forward. Never decrement schema ledgers, silently
   discard outcome evidence, release uncertain reservations, or replay AI
   provider work. A storage outage cannot produce a success acknowledgement.

The focused SQLite witnesses separately exercise a zero-post-checkpoint-write
snapshot restore and a post-write retained forward recovery, alongside failure,
retry, and disablement. The restore witness uses the current binary and a
separate database destination; it does **not** prove a v1.2.0 application and
database pair, PostgreSQL parity, source/wheel or
signed-OCI/Compose transition, or real GitHub delivery. Those are explicit
implementation and final-candidate gates in the plan. If the connector needs
new tables or permissions, the next preflight must add a red-first dual-backend
migration/ACL/rollback witness before runtime implementation.

## Review and operation gates

Before implementation: review App visibility, exact repository permissions,
installation/repository enrollment, verified delivery-header identity,
event/action/conclusion mapping, credential rotation and failure/reconciliation
behavior. Before merge: source and wheel tests, both adapters, header/raw-byte
mutation, unknown installation, cross-tenant, replay/race, resource bounds,
outage and rollback/recovery tests must pass at the exact reviewed head.
Before closing #219: protected-main CI plus an explicitly authorized live
GitHub.com test installation/delivery/redelivery with content-free evidence.
#214 additionally requires both published predecessor paths and exact
source/wheel/signed-OCI/Compose candidate proof; #225 requires an independent
external pilot. These are separate gates.

Run this offline proposal's checks from a source checkout or unpacked source
kit:

```bash
python tools/verify_github_connector_preflight.py
python -m unittest -v tests.test_github_connector_preflight tests.test_github_connector_fixtures
```

Official references: [validating webhook deliveries](https://docs.github.com/en/webhooks/using-webhooks/validating-webhook-deliveries),
[webhook event/payload catalogue](https://docs.github.com/en/webhooks/webhook-events-and-payloads),
and [GitHub App webhooks](https://docs.github.com/en/apps/creating-github-apps/registering-a-github-app/using-webhooks-with-github-apps).
