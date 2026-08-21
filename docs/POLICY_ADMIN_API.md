# Versioned policy administration

Hormuz supports authenticated, tenant-scoped policy staging, activation, and
rollback when the gateway uses PostgreSQL persistence. The caller must have the
`policy_admin` capability. Organization scope always comes from the
authenticated identity; it is never accepted in an activation request.

The current administrable document is `hormuz.policy-projection.v3`. It
contains model routes and rate cards, organization/team/person model, budget,
and provider-cache policy, secret-control mode, DLP rule metadata, and DLP
overlays. It contains no provider or employee credential, resolved
custom-secret value, dictionary value, approval fingerprint key, prompt,
response, filename, cache key, or source content. Existing immutable
`hormuz.policy-projection.v2` versions remain readable and canonical; version
3 is emitted for new exports and stages.

Deployment-owned state cannot be introduced through this API. A staged
projection may use only provider protocols, identities, teams, custom-secret
environment names, DLP dictionaries, and an approval key already provisioned
in the running deployment. Unknown fields, non-canonical JSON, cross-tenant
scopes, and unprovisioned references fail before a version is stored.

## CLI workflow

Export the canonical projection from reviewed configuration:

```bash
hormuz --config /etc/hormuz/hormuz.json policy export \
  --organization xpounder > policy.json
```

Stage it without changing live traffic:

```bash
hormuz policy stage \
  --input policy.json \
  --gateway https://hormuz.example.com \
  --profile ai-policy-admin
```

The response contains the content-addressed `hpv_v1_<sha256>` version and a
structural, content-free change summary. Repeating the same stage is
idempotent.

Inspect and activate with compare-and-swap semantics:

```bash
hormuz policy active \
  --gateway https://hormuz.example.com \
  --profile ai-policy-admin

hormuz policy activate hpv_v1_<sha256> \
  --expected-current hpv_v1_<current-sha256> \
  --gateway https://hormuz.example.com \
  --profile ai-policy-admin
```

Omit `--expected-current` only for a tenant's first activation. If another
administrator or process changed the pointer, activation returns
`policy_activation_conflict`; it never overwrites the newer choice.

Rollback names a previously active immutable version and requires the exact
current compare value:

```bash
hormuz policy rollback hpv_v1_<prior-sha256> \
  --expected-current hpv_v1_<current-sha256> \
  --gateway https://hormuz.example.com \
  --profile ai-policy-admin
```

Version documents and policy events are append-only. Activation changes one
tenant pointer transactionally and records the decision actor, prior version,
new version, sequence, timestamp, action, and structural change summary.

## HTTP contract

- `POST /v1/admin/policy-versions` with `{"projection": {...}}` stages a
  version. It returns `201` when created and `200` for an idempotent repeat.
- `GET /v1/admin/policy-active` returns the exact active version and canonical
  projection.
- `POST /v1/admin/policy-activations` accepts `version_id` and nullable
  `expected_active_version_id`.
- `POST /v1/admin/policy-rollbacks` accepts `version_id` and a required
  non-null `expected_active_version_id`.

Requests are strict JSON and limited to 1 MiB. Stable outcomes are `400` for an
invalid document or identifier, `403` without `policy_admin`, `404` for a
missing active or staged version, `409` for compare/rollback conflicts, and
`503` when PostgreSQL policy state is unavailable.

## Enforcement and failure behavior

Every supported provider request reads the tenant's active pointer before
model policy evaluation. Immutable materialized documents may be cached by
version, but the pointer is not cached, so separate gateway instances observe
an activation through shared PostgreSQL without a process restart. If no
version has ever been activated, Hormuz uses the configuration-seeded policy
and computes its deterministic `hpv_v1_<sha256>` bootstrap version.

If the active pointer, version, digest, or projection cannot be read and
validated, Hormuz returns `503 hormuz_policy_unavailable` before DLP approval,
budget reservation, or provider egress. Accounted requests store the exact
`governance_policy_version` in usage audit schema version 3. That field is
separate from the DLP effective policy version and deprecated context lineage.

The current v0.2 authorization decision is one authenticated `policy_admin`
for stage, activation, and rollback. Two-person approval is intentionally not
claimed; it can be added later as a separate workflow without weakening the
immutable history or compare-and-swap contract.

## Operator deploy, verify, and rollback runbook

1. Back up PostgreSQL according to the organization's database procedure, then
   install the candidate package and apply schema version 8 with the owner DSN:

   ```bash
   hormuz --config /etc/hormuz/hormuz.json storage migrate
   hormuz --config /etc/hormuz/hormuz.json storage verify
   ```

2. Export the reviewed deployment configuration, stage it, inspect the result,
   and activate it with the exact current version as shown above. Keep the prior
   `hpv_v1_...` identifier in the deployment record.

3. On every gateway replica, run `hormuz policy active` against that replica's
   administrative endpoint and require the same version identifier. Send one
   bounded test request through each supported client and verify that its
   content-free usage audit has the expected requested model, effective model,
   and `governance_policy_version` before expanding traffic.

4. If verification fails, stop rollout and use `hormuz policy rollback` with
   the saved prior identifier and the failing version as `--expected-current`.
   Re-run the active-version and bounded-request checks on every replica. A
   conflict means another administrator changed the pointer; inspect the active
   version and event ledger rather than forcing an overwrite.

Do not claim a successful deploy from a `200` activation alone. The completion
evidence is the converged active identifier plus exact-version request usage
from each replica; provider failure, `hormuz_policy_unavailable`, or a version
mismatch keeps the rollout incomplete.
