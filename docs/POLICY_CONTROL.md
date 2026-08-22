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
hormuz --config /etc/hormuz/hormuz.json policy administrator revoke-static \
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

```bash
hormuz --config /etc/hormuz/hormuz.json policy stage \
  --organization xpounder \
  --credential-env HORMUZ_POLICY_ADMIN_TOKEN \
  --file engineering-standard.json

hormuz --config /etc/hormuz/hormuz.json policy activate \
  --organization xpounder \
  --credential-env HORMUZ_POLICY_ADMIN_TOKEN \
  --version sha256:...
```

Staging validates the policy against configured provider routes, canonicalizes it, hashes it, stores the immutable document with its author/time, and records only a structural change summary. The summary has field names and scope counts; it never includes model aliases, budget values, source content, or credentials. Duplicate JSON keys and non-finite numeric values are rejected before a version is created.

Activation atomically advances one tenant's active-version pointer and records a new event. Repeating activation of the already active version is idempotent. Rollback means a new, audited activation of a previously active immutable version; it never edits history:

```bash
hormuz --config /etc/hormuz/hormuz.json policy rollback \
  --organization xpounder \
  --credential-env HORMUZ_POLICY_ADMIN_TOKEN \
  --version sha256:...
```

The gateway reads the active PostgreSQL pointer when it begins each request and pins that exact version for routing, egress controls, budget reservation, and durable usage evidence. There is intentionally no managed-policy process cache, so instances converge on the next request after a committed activation. A request already in flight keeps its original snapshot.

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
hormuz --config /etc/hormuz/hormuz.json policy break-glass recover \
  --organization xpounder \
  --issuer https://identity.example.com \
  --subject recovery-administrator \
  --reason-code all_administrators_lost
```

The command prompts for the recovery secret without echoing it; do not put it in an argument, environment-variable reference, command history, or checked-in file. Hormuz compares that transient value to the separately managed secret named by `break_glass.token_env` and records a `break_glass_recovered` event without logging either value. Organizations should store that configured secret under a distinct emergency-access process and test recovery in an isolated environment. This mechanism is a recovery control, not a substitute for KMS, audited custody, or operational incident procedures.

## Interface and current boundary

The product interface is CLI-first. Internally the CLI authenticates a credential and calls `PolicyControlService`; it never writes PostgreSQL tables directly and does not accept a self-asserted actor. The present alpha implementation runs that service boundary in the CLI process with the dedicated control credential. Run it only from a locked-down administrator environment: possession of that control credential is already root policy authority. A remote API or restricted local control socket can use the same service contract later without changing bootstrap, authorization, staging, activation, or rollback semantics.

The control plane is a bounded shared-state implementation, not a completed production deployment. External-IdP conformance, secret rotation/KMS, tamper-evident retention, TLS/HA, pooling, backup/PITR, and multi-instance operational drills remain separate release gates. See [STORAGE.md](STORAGE.md), [OIDC.md](OIDC.md), and [ROADMAP.md](ROADMAP.md).

## Verification

```bash
python3 -m unittest -v tests.test_policy_document tests.test_contracts
HORMUZ_TEST_POSTGRES_DSN='postgresql://operator@host:5432/hormuz_test' \
  python3 -m unittest -v tests.test_postgres
```

The PostgreSQL suite proves bootstrap is transactional and one-time, non-administrators are denied, configuration drift cannot create a new root authority, immutable version activation/rollback is audited, durable control events validate against their explicit schema, separate database roles enforce their boundaries, break-glass needs administrator loss, and two runtime instances observe the committed active version.
