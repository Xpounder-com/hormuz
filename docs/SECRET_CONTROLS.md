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
          "action": "deny",
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
```

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

Each DLP rule can be limited to `openai`, `anthropic`, and exact routed upstream model IDs. `detect` forwards the original value, `redact` replaces it with `[REDACTED:HORMUZ_DLP]`, and `deny` returns `hormuz_dlp_denied` without a provider call. `require_approval` currently returns `hormuz_dlp_approval_required` and fails closed. The durable, non-self approval grant and single-use consumption API is not shipped yet, so this action must not be configured when an operational exception path is required.

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

The broader boundary remains governed by [accepted ADR 0004](decisions/0004-structured-dlp-and-approval-boundary.md). Source classification, opaque-media denial, semantic detection, team/person DLP tightening, detector-version evidence, approval grants, content-cache invalidation, and organization-specific evaluation are still open. Issue #10 remains open until those paths and the complete compatibility, failure, migration, and privacy gates pass.

## Verify

The integration tests assert both sides of the boundary:

```bash
python3 -m unittest -v \
  tests.test_gateway.GatewayIntegrationTests.test_secret_is_redacted_before_provider_and_audited \
  tests.test_gateway.GatewayIntegrationTests.test_low_confidence_dlp_detection_forwards_unchanged_and_audits_metadata_only \
  tests.test_gateway.GatewayIntegrationTests.test_regulated_identifier_is_redacted_on_anthropic_path_before_provider \
  tests.test_gateway.GatewayIntegrationTests.test_company_dictionary_deny_blocks_before_egress_and_never_persists_value \
  tests.test_gateway.GatewayIntegrationTests.test_approval_requirement_binds_to_exact_routed_model_and_fails_closed
```

These tests prove credential and regulated-identifier transformation, detect-only forwarding, deny-before-egress, exact routed-model scoping, fail-closed approval-required behavior, and metadata-only evidence across the OpenAI and Anthropic compatibility paths.
