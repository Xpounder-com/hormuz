# Secret and structured DLP egress controls

Hormuz inspects JSON string values after identity and model policy evaluation and before the request is serialized to OpenAI or Anthropic. It never changes JSON keys. Prompt text, system instructions, tool outputs, and reusable context all pass through the same boundary when they are represented as JSON strings.

## Configuration

Egress controls are global because every provider-bound request must cross the same organization boundary:

```json
{
  "egress_controls": {
    "secrets": {
      "mode": "redact",
      "builtins": true,
      "custom_secret_envs": ["HORMUZ_INTERNAL_SECRET"]
    },
    "dlp": {
      "policy_version": "organization-dlp-v1",
      "approval": {
        "enabled": true,
        "fingerprint_key_env": "HORMUZ_DLP_FINGERPRINT_KEY"
      },
      "rules": {
        "us_ssn": {"action": "redact", "providers": ["openai", "anthropic"]},
        "payment_card": {"action": "redact", "providers": ["openai", "anthropic"]},
        "email_address": {"action": "detect", "providers": ["openai", "anthropic"]}
      },
      "dictionaries": [
        {
          "rule_id": "company.codename",
          "category": "company_dictionary",
          "confidence": "high",
          "action": "require_approval",
          "providers": ["openai"],
          "models": ["gpt-5.4"],
          "values_env": "HORMUZ_COMPANY_TERMS"
        }
      ]
    }
  }
}
```

Set custom values only in the Hormuz service environment:

```bash
export HORMUZ_INTERNAL_SECRET="an-exact-company-secret-value"
export HORMUZ_COMPANY_TERMS='["PROJECT-ORBITAL","CUSTOMER-ALPHA"]'
export HORMUZ_DLP_FINGERPRINT_KEY="$(python3 -c 'import base64,secrets; print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode().rstrip("="))')"
```

Put the fingerprint key in the service's secret manager rather than a shell-history file. Hormuz requires a base64url value that decodes to exactly 32 random bytes. It treats the encoded key itself as an exact secret at the egress boundary, hides both forms from configuration representations, and never stores either form in SQLite.

Hormuz refuses to start when a configured custom-secret variable is absent or shorter than eight characters. A DLP dictionary variable must contain a JSON array of 1 to 1000 unique or repeated strings; each value must be trimmed, printable, and 4 to 512 UTF-8 bytes, and one dictionary is capped at 256 KiB. The configuration stores only environment-variable names; values stay in the service's secret store and process memory and are hidden from configuration representations.

Modes:

- `redact` replaces every detected value with `[REDACTED:HORMUZ_SECRET]` and forwards the transformed request. This is the default.
- `deny` sends no provider request and returns `hormuz_secret_detected` using the provider's error shape.
- `off` disables inspection and should be an explicit exception.

Built-in high-confidence detectors currently cover:

- PEM private keys.
- OpenAI and Anthropic API keys.
- GitHub access tokens.
- AWS access-key IDs.
- Google API keys.
- Slack tokens.

Hormuz also treats every configured employee identity token and every available upstream provider credential as an exact protected value, regardless of its format.

Structured DLP currently adds three deterministic built-ins:

- `us_ssn` recognizes valid hyphenated US Social Security number structure and redacts by default.
- `payment_card` recognizes 13-to-19-digit card candidates only after a Luhn check and redacts by default.
- `email_address` recognizes conventional email-address syntax in detect-only mode. It is deliberately low-confidence telemetry, not a complete PII classifier.

Each DLP rule can be limited to `openai`, `anthropic`, and exact routed upstream model IDs. `detect` forwards the original value, `redact` replaces it with `[REDACTED:HORMUZ_DLP]`, and `deny` returns `hormuz_dlp_denied` without a provider call. With approval disabled, `require_approval` continues to fail closed without creating a grant. With approval enabled, it creates a durable metadata-only request and returns `hormuz_dlp_approval_required` plus the opaque request ID in the message and `X-Hormuz-DLP-Approval-Request` header.

## Exact single-use approval

Approval authority is an explicit identity capability. Add `"capabilities": ["dlp_approver"]` to the static identity or OIDC subject mapping for security personnel. Enabling approvals for a `require_approval` rule fails configuration validation when an affected organization has no configured approver. Possessing the capability does not permit self-approval.

The employee does not change Codex or Claude configuration. They give the opaque `apr_...` ID to an approver, who uses their own Hormuz credential:

```bash
hormuz dlp approval show apr_0123456789abcdef0123456789abcdef \
  --gateway https://hormuz.example.com \
  --profile security-approver

hormuz dlp approval approve apr_0123456789abcdef0123456789abcdef \
  --gateway https://hormuz.example.com \
  --profile security-approver
```

For bootstrap identities, replace `--profile security-approver` with `--credential-env HORMUZ_APPROVER_TOKEN`. The CLI refuses non-loopback plaintext HTTP unless `--allow-insecure-http` is explicitly supplied.

The approver sees only organization, employee/team, client, provider, requested and exact routed model, policy version, rule IDs, detection count, timestamps, and status. They do not see the prompt, matched value, response, source file, or keyed fingerprint. `approve` is idempotent for the same approver while the request remains approved.

The employee then retries the unchanged request in the same client. Hormuz canonicalizes the final transformed JSON together with the provider operation, computes an HMAC-SHA-256 fingerprint, and atomically consumes a matching grant before egress. The grant binds organization, employee, client, provider, requested model, exact routed upstream model, policy version, rule IDs, operation, and payload. It expires 15 minutes after approval and is single-use: concurrent or later replay, payload mutation, a different endpoint, provider/model/policy change, actor change, expiry, or key rotation produces a new blocked request instead of reusing the grant. Consumption happens before the provider call, so an upstream failure does not restore the grant. The complete endpoint and error contract is in [DLP_APPROVAL_API.md](DLP_APPROVAL_API.md).

The provider-returned actual model is recorded in ordinary usage evidence. If it differs from the approved routed model, Hormuz also emits a `security.dlp.approval` `model_mismatch` event after egress; it never broadens the consumed grant retroactively.

For transformed requests, Hormuz returns `X-Hormuz-Redactions` and appends `+redacted` to `X-Hormuz-Policy-Decision`. DLP matches also return `X-Hormuz-DLP-Detections`; a detect-only match does not claim a transformation. A separate security event stores event-time scope, requested and exact routed model, policy version, action, counts, and finding metadata for every inspected endpoint, including token-count calls that are excluded from inference usage. Accounted generation events carry only actual transformation counts. Neither ledger stores the matched value, prompt, transformed prompt, or response.

## OpenAI provider storage policy

Hormuz also enforces provider-side storage policy for `POST /v1/responses`:

```json
{
  "upstreams": {
    "openai": {
      "allow_response_storage": false,
      "allow_background": false
    }
  }
}
```

With the defaults above, Hormuz overwrites any client value with `store: false` and rejects `background: true` with `hormuz_provider_policy_denied`. An administrator must set either flag to `true` explicitly to allow that behavior. OpenAI documents that Responses API application state is retained for at least 30 days by default or when `store` is true, and that background mode requires temporary storage; see [OpenAI platform data controls](https://platform.openai.com/docs/models/default-usage-policies-by-endpoint).

This request-level policy does not itself enroll an organization in OpenAI Zero Data Retention, configure Anthropic retention, or replace provider-side enterprise agreements. Those remain provider-account controls that Hormuz should later reconcile and display as deployment posture.

## What this does not guarantee

This is a bounded deterministic DLP subset, not a complete data-loss-prevention system. It inspects JSON values, not caller-controlled provider headers or JSON keys. It does not inspect image contents, decode arbitrary base64 or archives, classify source paths, infer proprietary meaning, or reliably detect transformed and obfuscated values. A custom exact value protects only that exact case-sensitive textual representation. The SSN detector intentionally supports only the high-confidence hyphenated form; the email detector has not passed an organization-specific false-positive/false-negative evaluation and therefore remains detect-only.

Use `deny` when forwarding a detected credential is unacceptable. Production deployments should combine Hormuz with least-privilege provider keys, short-lived employee identity, network controls, provider retention settings, code-host secret scanning, and a reviewed list of organization-specific values.

The broader boundary remains governed by [accepted ADR 0004](decisions/0004-structured-dlp-and-approval-boundary.md). Source classification, opaque-media denial, semantic detection, team/person DLP tightening, detector-version evidence, multi-node approval persistence/notification, content-cache invalidation, and organization-specific evaluation are still open. Issue #10 remains open until those paths and the complete compatibility, failure, migration, and privacy gates pass.

## Verify

The integration tests assert both sides of the boundary:

```bash
python3 -m unittest -v \
  tests.test_gateway.GatewayIntegrationTests.test_secret_is_redacted_before_provider_and_audited \
  tests.test_gateway.GatewayIntegrationTests.test_low_confidence_dlp_detection_forwards_unchanged_and_audits_metadata_only \
  tests.test_gateway.GatewayIntegrationTests.test_regulated_identifier_is_redacted_on_anthropic_path_before_provider \
  tests.test_gateway.GatewayIntegrationTests.test_company_dictionary_deny_blocks_before_egress_and_never_persists_value \
  tests.test_gateway.GatewayIntegrationTests.test_approval_requirement_binds_to_exact_routed_model_and_fails_closed \
  tests.test_gateway.GatewayIntegrationTests.test_non_self_approval_allows_one_exact_retry_for_openai_and_anthropic \
  tests.test_gateway.GatewayIntegrationTests.test_dlp_approval_store_failure_blocks_before_egress_without_content_leak \
  tests.test_store.UsageStoreMigrationTests.test_dlp_approval_expiry_and_concurrent_retry_fail_closed
```

These tests prove credential and regulated-identifier transformation, detect-only forwarding, deny-before-egress, exact routed-model scoping, non-self authorization, CLI/API approval, exact single-use consumption, expiry, concurrent replay rejection, model-mismatch evidence, store-outage denial, and metadata-only evidence across the OpenAI and Anthropic compatibility paths.
