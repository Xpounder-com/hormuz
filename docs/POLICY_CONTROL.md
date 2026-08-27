# Governed policy control

This document describes the versioned policy-administration boundary in the core gateway. It is deliberately separate from identity facts and from inference entitlement:

- the identity provider verifies who a caller is and, for runtime requests, Hormuz maps that caller to an organization, team, and person;
- PostgreSQL holds the tenant's root `policy_admin` authorities and immutable policy versions;
- an active policy determines which identities may use which models and budgets.

A policy administrator can create policies that grant model access or budget. Treat it as root authorization authority even when the active policy gives that administrator no inference entitlement.

## Managed configuration

Use `policy_control.mode: "postgresql"` only with PostgreSQL usage storage. The configuration contains credential *names*, policy-routing metadata, and one-time bootstrap identities. It does not contain a mutable `policies` section in this mode.

```json
{
  "usage_storage": {
    "backend": "postgresql",
    "postgres_dsn_env": "HORMUZ_POSTGRES_DSN",
    "postgres_migration_dsn_env": "HORMUZ_POSTGRES_MIGRATION_DSN",
    "postgres_schema": "hormuz",
    "postgres_runtime_role": "hormuz_runtime"
  },
  "policy_control": {
    "mode": "postgresql",
    "postgres_control_dsn_env": "HORMUZ_POLICY_CONTROL_DSN",
    "postgres_control_role": "hormuz_policy_control",
    "bootstrap_administrators": [
      {"organization_id": "xpounder", "actor_id": "alice"}
    ],
    "break_glass": {
      "enabled": true,
      "token_env": "HORMUZ_POLICY_BREAK_GLASS_TOKEN"
    }
  }
}
```

`HORMUZ_POSTGRES_DSN`, `HORMUZ_POSTGRES_MIGRATION_DSN`, and `HORMUZ_POLICY_CONTROL_DSN` must name three different credentials. `postgres_control_role` must differ from `postgres_runtime_role`. The runtime role can read an active policy version; it cannot read policy administrators or change versions. The policy-control role may stage versions and manage administrators, but cannot alter immutable versions, control events, or tenant initialization rows. The migration credential owns schema changes. Keep all three connection strings and the optional break-glass secret in a secret manager, never in JSON, shell history, or employee configuration.

The bootstrap list accepts only tenant-qualified identities:

```json
{"organization_id": "xpounder", "actor_id": "alice"}
```

or an OIDC identity:

```json
{
  "organization_id": "xpounder",
  "issuer": "https://identity.example.com",
  "subject": "stable-provider-subject"
}
```

An email address, username, team name, group display name, clearance, capability, or `allowedClients` field is not accepted. OIDC administration keys are exactly `(organization_id, issuer, subject)`. No IdP or SCIM group automatically becomes a policy administrator.

## One-time bootstrap

After a migration, run bootstrap from a tightly controlled control host. The credential is authenticated; the CLI does not accept an actor flag or a caller-supplied identity.

```bash
hormuz --config /etc/hormuz/hormuz.json storage migrate
hormuz --config /etc/hormuz/hormuz.json policy bootstrap \
  --organization xpounder \
  --credential-env HORMUZ_POLICY_ADMIN_TOKEN
```

For the first invocation only, Hormuz confirms that the authenticated caller matches one configured bootstrap identity. In one PostgreSQL transaction it inserts the tenant initialization marker, all bootstrap administrators, and a `bootstrap_initialized` audit event.

After that marker exists, the bootstrap command stops before consulting the configuration list and returns `policy_bootstrap_already_initialized`. Editing `bootstrap_administrators` later does not add, remove, or authorize anyone. Normal policy-admin authorization reads PostgreSQL only. Configuration remains relevant only to verify an OIDC credential's trusted issuer and to validate that a newly staged policy is compatible with currently configured gateway routes; neither use can grant or remove root authority.

Static identities are permitted only as bootstrap records. A current policy administrator can retire one through the governed service, but cannot grant a new static administrator:

```bash
hormuz --config /etc/hormuz/hormuz.json policy administrator retire static \
  --organization xpounder \
  --credential-env HORMUZ_POLICY_ADMIN_TOKEN \
  --actor-id alice
```

## Immutable policy documents

Stage a strict JSON policy document, then activate its returned SHA-256 version. The accepted document has schema `hormuz.policy-document` v1 and only these roots:

```text
schema_id, schema_version, organization_id, policies, egress_controls
```

`policies` contains organization, team, and actor overlays for allowed clients/models, fallbacks, output caps, and token/budget limits. `egress_controls` contains only the supported OpenAI storage/background flags and secret-detection mode. Prompts, responses, filenames, sources, notes, arbitrary JSON, detector values, and provider credentials are rejected.

Start from a built-in policy instead of authoring the v1 JSON structure by
hand:

```bash
hormuz policy templates

hormuz --config /etc/hormuz/hormuz.json policy create \
  --template standard \
  --output engineering-standard.json
```

`standard` uses the configured identity client allowlists and model aliases,
redacts detected secrets, disables OpenAI response storage/background mode,
and caps each response at 16,000 output tokens. `strict` uses the same
configured allowlists with secret denial and a 4,000-token cap. `lockdown`
uses empty client and model allowlists to deny every request. None of the
templates invents provider credentials, team or actor scopes, fallback routes,
or monetary budgets.

Add optional organization-level budget limits at creation time when they are
part of the intended policy:

```bash
hormuz --config /etc/hormuz/hormuz.json policy create \
  --template strict \
  --monthly-budget-usd 250 \
  --per-actor-monthly-budget-usd 25 \
  --output engineering-strict.json
```

Creation loads only credential-free configuration facts and validates the
result through the same policy-document parser used by validation and staging.
With one configured organization, Hormuz selects it automatically. With more
than one, pass `--organization`. The explicit output is owner-only, existing
files are preserved unless `--force` is passed, and symbolic-link targets are
always refused.

Validate a candidate before staging it:

```bash
hormuz --config /etc/hormuz/hormuz.json policy validate engineering-standard.json
```

This command reads only the local configuration and candidate file. It does
not resolve configured credentials, open policy-control PostgreSQL,
authenticate a policy administrator, stage a version, or change the active
policy. A valid document reports its canonical SHA-256 version plus team and
actor scope counts. An invalid document reports a schema-owned field path and
a safe reason, with a correction hint when one is available. Diagnostics never
repeat submitted field names, scope IDs, model aliases, budget values, or other
policy values.

The normal administrator path applies a reviewed candidate in one command:

```bash
hormuz --config /etc/hormuz/hormuz.json policy apply engineering-standard.json \
  --organization xpounder \
  --credential-env HORMUZ_POLICY_ADMIN_TOKEN \
  --if-active sha256:...
```

Hormuz reads, validates, canonicalizes, and hashes the file before acquiring the
tenant database lock. Inside one locked transaction it checks the optional
`--if-active` guard, stages the immutable document when needed, and advances the
active pointer. A guard mismatch returns the stable
`policy_active_version_mismatch` error without staging the candidate.

Apply has exact retry behavior:

- If the candidate is already active, the command succeeds without an event or
  generation change.
- If the candidate is already staged but inactive, only an activation event is
  added.
- If the candidate is new, staging and activation events are committed in that
  deterministic order.

No policy-control command asks for interactive confirmation. Use `policy
compare` and `policy preview` before apply when an administrator needs a
deliberate review path.

The separate commands remain available for advanced workflows that deliberately
stage now and activate later:

```bash
hormuz --config /etc/hormuz/hormuz.json policy stage \
  --organization xpounder \
  --credential-env HORMUZ_POLICY_ADMIN_TOKEN \
  --file engineering-standard.json

hormuz --config /etc/hormuz/hormuz.json policy activate \
  --organization xpounder \
  --credential-env HORMUZ_POLICY_ADMIN_TOKEN \
  --version sha256:... \
  --if-active sha256:...
```

Staging validates the policy against configured provider routes, canonicalizes
it, hashes it, stores the immutable document with its author/time, and records
only a structural change summary. The summary has field names and scope counts;
it never includes model aliases, budget values, source content, or credentials.
Duplicate JSON keys and non-finite numeric values are rejected before a version
is created.

By default, rollback is a one-step undo based exclusively on activation
generation. Hormuz resolves the version used by generation `current - 1` and
activates it in the same locked transaction:

```bash
hormuz --config /etc/hormuz/hormuz.json policy rollback \
  --organization xpounder \
  --credential-env HORMUZ_POLICY_ADMIN_TOKEN \
  --if-active sha256:...
```

Rollback never edits history. It creates a new audited activation generation,
so repeated rollback can toggle to the version just left. For example, undoing
`A -> B` activates `A`; immediately undoing again uses the preceding generation
and activates `B`. If no predecessor generation exists, Hormuz returns
`policy_rollback_predecessor_unavailable`. An advanced administrator can still
reactivate a selected previously active immutable version with `--version
sha256:...`.

The gateway reads the active PostgreSQL pointer when it begins each request and pins that exact version for routing, egress controls, budget reservation, and durable usage evidence. There is intentionally no managed-policy process cache, so instances converge on the next request after a committed activation. A request already in flight keeps its original snapshot.

## Inspect and export policy versions

Current policy administrators can print the active immutable document, or
select an exact staged version, without loading every historical document:

```bash
hormuz --config /etc/hormuz/hormuz.json policy show \
  --organization xpounder \
  --credential-env HORMUZ_POLICY_ADMIN_TOKEN

hormuz --config /etc/hormuz/hormuz.json policy show \
  --organization xpounder \
  --credential-env HORMUZ_POLICY_ADMIN_TOKEN \
  --version sha256:...
```

`policy show` writes only the selected `hormuz.policy-document` v1 JSON to
standard output, so it can be inspected or piped without parsing prose. If no
version is supplied, the active pointer is resolved in the same authenticated
tenant-scoped query. Hormuz returns a stable error when no policy is active or
when an explicit version does not exist.

The lifecycle timeline is a separate bounded view rather than an alias for
`policy status`:

```bash
hormuz --config /etc/hormuz/hormuz.json policy history \
  --organization xpounder \
  --credential-env HORMUZ_POLICY_ADMIN_TOKEN \
  --limit 20

hormuz --config /etc/hormuz/hormuz.json policy history \
  --organization xpounder \
  --credential-env HORMUZ_POLICY_ADMIN_TOKEN \
  --limit 20 \
  --json
```

History returns only `policy_staged`, `policy_activated`, and
`policy_rolled_back` events, newest first. Each event includes the immutable
version ID and digest, timestamp, opaque actor identity reference, activation
generation when applicable, and the version's metadata-only structural change
summary. The default limit is 20, the maximum is 100, and the versioned
`hormuz.policy-history` v1 JSON contract sets `has_more` when older events were
not returned. Policy values, model aliases, budget amounts, subjects, prompts,
responses, and credentials are not timeline fields.

Export uses the same selection semantics and writes an owner-only copy:

```bash
hormuz --config /etc/hormuz/hormuz.json policy export \
  --organization xpounder \
  --credential-env HORMUZ_POLICY_ADMIN_TOKEN \
  --output active-policy.json
```

The write is atomic with mode `0600`. Existing files are preserved unless
`--force` is supplied; symbolic links, directories, and special files are
refused. `--version sha256:...` exports an exact non-active version. These
inspection commands remain policy-administrator-only in v1; a narrower policy
auditor role is intentionally deferred.

## Zero-network administrator demo

Run the complete local policy-UX path without a provider account, network
access, policy-administrator credential, or PostgreSQL:

```bash
hormuz policy demo
```

The command creates standard and strict-derived local policy files, validates
them, performs a semantic comparison, creates two explicit request scenarios,
and evaluates them against a disposable SQLite database. That database begins
with exactly zero current usage. The documented changes concern model access
and output caps; the demo does not manufacture budget consumption. It makes no
provider call and never stages, activates, or rolls back a policy.

Temporary state is deleted by default. Retain a new owner-only workspace for
inspection with:

```bash
hormuz policy demo --output ./policy-demo
```

The output path must not exist. Hormuz creates it with mode `0700`, writes all
artifacts with mode `0600`, and refuses to overwrite an existing file,
directory, link, or special path. The final `policy apply`, `policy history`,
and `policy rollback` lines are real managed commands shown for the operator;
the demo does not execute them. This proves only the policy-administration UX,
not completion of the enterprise v1 release gate.

The independent v1 gate and its fixed offline/PostgreSQL task cards are defined
in [POLICY_ADMIN_USABILITY.md](POLICY_ADMIN_USABILITY.md). The gate remains at
0/5 offline and 0/3 PostgreSQL participants until qualifying external evidence
exists; the demo and synthetic contract fixture cannot increase either count.

## Compare and preview a candidate

Compare a local candidate with the active version by default before staging or
activation:

```bash
hormuz --config /etc/hormuz/hormuz.json policy compare engineering-strict.json \
  --organization xpounder \
  --credential-env HORMUZ_POLICY_ADMIN_TOKEN \
  --json
```

`policy compare` normalizes the policy document and reports stable semantic
paths such as `policies.organization.max_output_tokens`, with `before`,
`after`, and `added`, `removed`, or `changed`. Object key order and the order
of allowlist values do not create changes. The baseline and candidate each
include an immutable version ID and content digest; a local candidate receives
its digest before it is staged. Use `--version sha256:...` to select a saved
candidate and `--against-version sha256:...` to select a non-active baseline.

Use `--baseline FILE` to select a local baseline. Policy analysis is
credential-free only when the baseline and candidate are both local files and
the configured usage backend is SQLite:

```bash
hormuz --config ./policy-demo/hormuz.json policy compare \
  ./policy-demo/candidate.json \
  --baseline ./policy-demo/baseline.json \
  --organization demo-organization \
  --json
```

An active or saved baseline, a saved candidate, or PostgreSQL usage storage
switches the operation to managed mode. Hormuz then verifies current persisted
policy-administrator authority before any policy lookup or usage-store access.
Missing credentials, unsupported policy-control configuration, and mixed
inputs fail closed; Hormuz never silently reinterprets them as offline mode.

The versioned JSON contract is `hormuz.policy-comparison` v1. Exit status is
`0` for semantically identical documents, `1` for differences, and `2` for an
error, so scripts can distinguish an expected change from a failed comparison.

Preview one explicit request against the active baseline by default, or select
a local/saved baseline and a local/saved candidate:

```bash
hormuz --config /etc/hormuz/hormuz.json policy preview engineering-strict.json \
  --organization xpounder \
  --credential-env HORMUZ_POLICY_ADMIN_TOKEN \
  --actor alice \
  --client codex \
  --protocol openai \
  --model gpt-5.4-mini \
  --max-output-tokens 1000 \
  --json
```

Preview pins the selected baseline before loading the candidate. Both
decisions then use that pinned baseline/candidate pair and one read-only
set of the current actor, team, and organization monthly totals. It does
not call a provider, reserve budget, write usage evidence, or activate a
policy. The `hormuz.policy-preview` v1 contract includes `evaluated_at`, the
UTC billing/usage period, `usage_basis: current`, the request dimensions, and
the baseline and candidate decisions with their version IDs and digests. Exit
status is `0` when the candidate allows the request, `3` when it denies, and
`2` on error; the baseline decision is context and does not select the exit
status. Preview is a point-in-time administrative evaluation; concurrent usage
or reservations can still affect whether a later live request is admitted.
`--baseline FILE` applies the same local-versus-managed authentication rules.

## Saved scenario suites

Create a portable suite from one explicit request without loading runtime
configuration, provider credentials, policy-administrator credentials, or
PostgreSQL:

```bash
hormuz policy scenarios create \
  --organization xpounder \
  --id codex-default \
  --actor alice \
  --client codex \
  --protocol openai \
  --model gpt-5.4-mini \
  --max-output-tokens 1000 \
  --output engineering-scenarios.json
```

Add another explicit request atomically, or validate and identify the complete
suite later:

```bash
hormuz policy scenarios add engineering-scenarios.json \
  --id claude-large \
  --actor alice \
  --client claude-code \
  --protocol anthropic \
  --model claude-sonnet-5 \
  --max-output-tokens 16000

hormuz policy scenarios validate engineering-scenarios.json
```

`hormuz.policy-scenario-suite` v1 contains one to 100 scenarios with unique
IDs. Each scenario stores only actor, client, protocol, model alias, and an
optional output-token request. Prompt text, system instructions, responses,
credentials, and arbitrary notes are not accepted. Object order and scenario
order do not affect the canonical suite digest. Creation, addition, and forced
replacement are atomic owner-only writes with mode `0600`; symbolic links,
directories, and special files are refused. Suite files are bounded to 1 MiB
on both read and write. Concurrent add commands are serialized; if another
editor replaces the loaded suite, the add fails with
`policy_scenario_concurrent_update` instead of discarding either change. On a
platform without advisory file locking, add fails closed with
`policy_scenario_lock_unavailable`; use an explicitly reviewed replacement
rather than accepting a possible lost update.

Evaluate the active baseline and one local or saved candidate across the whole
suite:

```bash
hormuz --config /etc/hormuz/hormuz.json policy evaluate engineering-strict.json \
  --organization xpounder \
  --credential-env HORMUZ_POLICY_ADMIN_TOKEN \
  --scenarios engineering-scenarios.json \
  --output engineering-evaluation.json \
  --json
```

The selected baseline and candidate are each pinned once before evaluation. Use
`--against-version sha256:...` for a selected baseline and `--version
sha256:...` for a saved candidate, or `--baseline FILE` for a local baseline.
Credential-free evaluation requires both local files and SQLite; saved policy
state or PostgreSQL usage remains administrator-authenticated. Every configured
actor referenced by the suite receives one read-only snapshot of current actor,
team, and organization monthly totals, reused for both policies and every
scenario for that actor.
No provider call, budget reservation, usage record, policy-control event,
stage, or activation occurs.

`hormuz.policy-evaluation` v1 records the suite identity, both policy version
IDs and digests, `evaluated_at`, UTC usage period, `usage_basis: current`,
per-scenario baseline/candidate decisions, semantic behavior-change flags, and
bounded summary counts. Policy-version identity alone is not a behavior
change; allow/deny, action, reason, resolved route, and output cap are. Exit
status is `0` when every evaluated behavior is unchanged, `1` when one or more
scenarios change, and `2` on error. An expected denial is result data, not an
execution error. `--output` uses an atomic `0600` write and requires `--force`
to replace an existing regular file.

Evaluation remains a point-in-time current-usage result rather than durable
historical evidence. An organization-wide scan is intentionally outside v1:
explicit suites avoid identity enumeration, unbounded pagination, and implicit
assumptions about which requests matter.

## Administrator changes

An existing policy administrator can add or remove a verified OIDC identity by its stable issuer/subject pair:

```bash
hormuz --config /etc/hormuz/hormuz.json policy administrator grant \
  --organization xpounder \
  --credential-env HORMUZ_POLICY_ADMIN_TOKEN \
  --issuer https://identity.example.com \
  --subject stable-provider-subject
```

The issuer must already be configured for generic OIDC verification. Granting an OIDC policy administrator does not create an inference identity, map a team, or grant a model/budget. Conversely, a runtime identity or IdP group does not gain policy authority unless an existing policy administrator explicitly grants this record.

Hormuz refuses to revoke the final active administrator. Use `policy status --json` for the versioned metadata-only administration view; it is restricted to a current policy administrator.

## Break-glass recovery

Break-glass is disabled unless explicitly configured. It is not a normal authentication path and can run only when the tenant has zero active policy administrators. Recovery requires the separately managed secret named by `break_glass.token_env`, a configured issuer/subject pair for the recovered administrator, and one of the fixed reason codes:

```bash
hormuz --config /etc/hormuz/hormuz.json policy recover \
  --organization xpounder \
  --issuer https://identity.example.com \
  --subject recovery-administrator \
  --reason-code all_administrators_lost
```

The command prompts for the recovery secret without echoing it; do not put it in an argument, environment-variable reference, command history, or checked-in file. Hormuz compares that transient value to the separately managed secret named by `break_glass.token_env` and records a `break_glass_recovered` event without logging either value. Organizations should store that configured secret under a distinct emergency-access process and test recovery in an isolated environment. This mechanism is a recovery control, not a substitute for KMS, audited custody, or operational incident procedures.

Primary CLI command names use separate words. Earlier hyphenated command
spellings remain hidden v1 compatibility aliases so existing scripts continue
to receive their prior output and exit behavior; new documentation and help
use only the spaced command tree.

## Interface and current boundary

The product interface is CLI-first. Internally the CLI authenticates a credential and calls `PolicyControlService`; it never writes PostgreSQL tables directly and does not accept a self-asserted actor. The present alpha implementation runs that service boundary in the CLI process with the dedicated control credential. Run it only from a locked-down administrator environment: possession of that control credential is already root policy authority. A remote API or restricted local control socket can use the same service contract later without changing bootstrap, authorization, staging, activation, or rollback semantics.

The control plane is a bounded shared-state implementation, not a completed production deployment. External-IdP conformance, secret rotation/KMS, tamper-evident retention, TLS/HA, pooling, backup/PITR, and multi-instance operational drills remain separate release gates. See [STORAGE.md](STORAGE.md), [OIDC.md](OIDC.md), and [ROADMAP.md](ROADMAP.md).

## Verification

```bash
python3 -m unittest -v tests.test_policy_document tests.test_contracts
HORMUZ_TEST_POSTGRES_DSN='postgresql://operator@host:5432/hormuz_test' \
  python3 -m unittest -v tests.test_postgres_policy_control
```

The PostgreSQL suite proves bootstrap is transactional and one-time,
non-administrators are denied, configuration drift cannot create a new root
authority, apply retries have exact event semantics, guard failures cannot
stage a candidate, staging and activation roll back together on failure,
generation rollback resolves and activates under one tenant lock, immutable
version activation/rollback is audited, durable control events validate against
their explicit schema, separate database roles enforce their boundaries,
break-glass needs administrator loss, and two runtime instances observe the
committed active version.
