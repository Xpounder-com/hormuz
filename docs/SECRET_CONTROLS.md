# Secret egress controls

Hormuz inspects JSON string values after identity and model policy evaluation and before the request is serialized to OpenAI or Anthropic. It never changes JSON keys. Prompt text, system instructions, tool outputs, and reusable context all pass through the same boundary when they are represented as JSON strings.

## Configuration

Secret controls are global because every provider-bound request must cross the same organization egress boundary:

```json
{
  "egress_controls": {
    "secrets": {
      "mode": "redact",
      "builtins": true,
      "custom_secret_envs": ["HORMUZ_INTERNAL_SECRET"]
    }
  }
}
```

Set custom values only in the Hormuz service environment:

```bash
export HORMUZ_INTERNAL_SECRET="an-exact-company-secret-value"
```

Hormuz refuses to start when a configured custom-secret variable is absent or shorter than eight characters. The configuration stores only the environment-variable name; the value stays in the service's secret store and process memory.

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

For redacted requests, Hormuz returns `X-Hormuz-Redactions` and appends `+redacted` to `X-Hormuz-Policy-Decision`. A separate security event stores the action, detection count, and rule identifiers for every inspected endpoint, including token-count calls that are excluded from inference usage. Accounted generation events also carry the redaction count for usage reporting. Neither ledger stores the matched value, prompt, transformed prompt, or response.

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

This layer is deterministic secret detection, not a complete data-loss-prevention system. It does not currently inspect image contents, decode arbitrary base64 or archives, infer proprietary meaning, or reliably detect transformed and obfuscated values. A custom exact value protects only that exact textual representation.

Use `deny` when forwarding a detected credential is unacceptable. Production deployments should combine Hormuz with least-privilege provider keys, short-lived employee identity, network controls, provider retention settings, code-host secret scanning, and a reviewed list of organization-specific values. Semantic classification and structured PII policies are later milestones and require measured false-positive/false-negative evaluation before enforcement.

## Verify

The integration tests assert both sides of the boundary:

```bash
python3 -m unittest -v \
  tests.test_gateway.GatewayIntegrationTests.test_secret_is_redacted_before_provider_and_audited \
  tests.test_gateway.GatewayIntegrationTests.test_secret_deny_mode_blocks_provider_and_records_metadata
```

The first test proves the original credential does not reach the upstream request. The second proves deny mode makes no upstream call.
