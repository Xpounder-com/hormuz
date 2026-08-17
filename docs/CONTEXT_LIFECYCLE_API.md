# Context lifecycle connector API

Hormuz exposes a provider-neutral HTTP and CLI boundary for trusted CI jobs and internal connectors to submit lifecycle evidence, record repository state, and run one bounded revalidation batch. The connector does not need access to the local Hormuz configuration or context database.

This API automates authenticated delivery into the existing governed lifecycle. It is not a GitHub-, GitLab-, or CI-specific webhook, and authentication of the submitting workload does not independently prove that the claimed upstream event occurred.

## Authorization

Every operation requires a Hormuz identity with the `context_promoter` capability and an enabled `context_service.lifecycle` policy. The gateway authenticates the bearer credential and resolves its organization before reading the request body or accessing context storage. A request body may narrow repository and branch, but it cannot select another organization.

The bearer credential may be any identity mechanism already accepted by the gateway:

- a unique bootstrap token for local evaluation;
- a JWT from a configured workload OIDC issuer and subject mapping; or
- an opaque Hormuz session whose current identity has `context_promoter`.

For CI, prefer a short-lived workload OIDC token over a long-lived bootstrap token. Configure the issuer, audience, and exact subject mapping in Hormuz, obtain the token through the organization's CI identity flow, and place it in the connector process as `HORMUZ_TOKEN`. Hormuz does not trust team, organization, repository, or capability claims supplied by the caller; those attributes come from server configuration.

All non-loopback production traffic must use HTTPS. The client refuses redirects so an authorization header cannot be replayed to a different origin. `--allow-insecure-http` is limited to explicit loopback development.

## Connector CLI

Submit the existing example envelopes to a remote gateway:

```bash
export HORMUZ_TOKEN="short-lived-workload-credential"

hormuz lifecycle snapshot \
  --input examples/context-lifecycle-snapshot.json \
  --gateway https://hormuz.example.com

hormuz lifecycle evidence \
  --input examples/context-evidence-commit-merged.json \
  --gateway https://hormuz.example.com

hormuz lifecycle evidence \
  --input examples/context-evidence-ci-passed.json \
  --gateway https://hormuz.example.com

hormuz lifecycle revalidate \
  --repository Xpounder-com/hormuz \
  --branch main \
  --gateway https://hormuz.example.com
```

Use `--credential-env` when the runner exposes the credential under another environment variable. `snapshot --expected-version N` is required when replacing a different existing snapshot. `revalidate --batch-size N` may only lower the configured server-side batch limit.

The remote `lifecycle` commands intentionally run without `--config`: authorization and policy are server-owned. Input files are size-bounded, strict JSON objects; duplicate members, non-standard numeric constants, unknown schema fields, invalid timestamps, and unsupported signals fail closed.

## HTTP contracts

All requests use `Authorization: Bearer ...`, `Content-Type: application/json`, and versioned JSON schemas.

### Record evidence

`POST /v1/context/evidence` accepts `hormuz.context-evidence.v1`, the same envelope documented in [CONTEXT_LIFECYCLE.md](CONTEXT_LIFECYCLE.md). A new immutable event returns `201`; an exact retry returns `200` with `created: false`.

The `hormuz.context-evidence-result.v1` response includes the evidence ID, record/version binding, signal family, observation time, and applied policy version. It never returns the raw `evidence_ref` or its fingerprint, and states `raw_evidence_ref_retained: false`.

### Record trusted state

`PUT /v1/context/lifecycle-snapshots` accepts:

```json
{
  "schema_version": "hormuz.context-lifecycle-snapshot-write.v1",
  "organization_id": "xpounder",
  "repository_id": "Xpounder-com/hormuz",
  "branch": "main",
  "expected_version": null,
  "snapshot": {
    "schema_version": "hormuz.context-lifecycle-snapshot.v1",
    "repository_revision": "abc123",
    "artifacts": []
  }
}
```

The first write and an exact retry return `200`. A different snapshot requires the current positive `expected_version`; a stale or omitted version returns `409`. The `hormuz.context-lifecycle-snapshot-result.v1` response exposes only scope, repository revision, snapshot hash/version, artifact count, observation time, and policy version. Artifact URIs, revisions, and hashes are not echoed.

The CLI accepts the existing `hormuz.context-lifecycle-envelope.v1` file and converts it to this write contract.

### Run or resume revalidation

`POST /v1/context/revalidation-batches` accepts:

```json
{
  "schema_version": "hormuz.context-revalidation-batch-request.v1",
  "repository_id": "Xpounder-com/hormuz",
  "branch": "main",
  "batch_size": 100
}
```

Organization is deliberately absent and comes from the authenticated identity. The response is `hormuz.context-revalidation-batch-result.v1` and contains the deterministic job binding hashes, state, and counters. Repeat the operation while status is `pending`; `completed` means the frozen scope was evaluated, while `superseded` means its snapshot, records, or evidence changed and a later call must bind a new job.

## Stable failures and retry behavior

| HTTP | Code | Meaning |
| --- | --- | --- |
| `400` | `invalid_json` | The body is not strict JSON. |
| `400` | `incomplete_request_body` | The connection ended before the announced `Content-Length` was received. |
| `400` | `context_lifecycle_invalid_request` | The versioned schema or a field is invalid. |
| `401` | existing authentication code | The credential is absent, invalid, expired, or revoked. |
| `403` | `context_promotion_forbidden` | The identity lacks `context_promoter`. |
| `403` | `context_lifecycle_disabled` | Lifecycle automation is not enabled. |
| `403` | `context_lifecycle_scope_denied` | The envelope organization differs from authenticated scope. |
| `403` | `context_lifecycle_policy_denied` | A requested batch exceeds organization policy. |
| `408` | `request_body_timeout` | The complete announced body was not received within the configured absolute request-body deadline. |
| `409` | `context_lifecycle_conflict` | Record version, snapshot version, lease, or frozen state conflicts. |
| `413` | `request_too_large` | The request exceeds its endpoint limit. |
| `429` | `context_rate_limited` | The actor exceeded the server-owned context limit; honor `Retry-After`. |
| `503` | `context_store_unavailable` | The mutation could not be durably committed. |

Only exact evidence retries and exact snapshot retries are automatically idempotent. On an ambiguous network failure, retry the identical request. Do not change observation time or evidence reference merely to force a new ID. On `409`, reload the governed record or snapshot state and construct a new request only after resolving the stale binding.

## Data and trust boundary

Lifecycle requests never call OpenAI or Anthropic and never create usage-ledger token or cost records. Metadata-only audit retains scope, actor, record/version or job identifiers, policy, hashes, counts, and lifecycle actions. It excludes record content, artifact identities, and raw evidence references. Safe errors and structured server logs do not include storage exception text or submitted references.

The API proves who submitted an attestation under Hormuz policy; it does not yet verify a GitHub webhook signature, CI transparency record, merge object, or provider-issued event. A production source connector must validate its source protocol before submitting the normalized envelope. Signed attestations, hosted scheduling, cross-node leases, and automatic event collection remain open lifecycle work.
