# Secret and structured DLP egress controls

Hormuz inspects JSON string values and recognized provider content blocks after identity and model policy evaluation and before the request is serialized to OpenAI or Anthropic. It never changes JSON keys. Prompt text, system instructions, tool outputs, and reusable context all pass through the same boundary when they are represented as JSON strings.

## Configuration

Every provider-bound request crosses the organization rule boundary. Optional team and actor overlays may only tighten that organization policy:

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
        "email_address": {"action": "detect", "providers": ["openai", "anthropic"]},
        "opaque_media": {"action": "deny", "providers": ["openai", "anthropic"]}
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
      ],
      "overlays": {
        "teams": {
          "engineering": {
            "policy_version": "engineering-dlp-v1",
            "rules": {
              "email_address": {"action": "redact"}
            }
          }
        },
        "actors": {
          "alice": {
            "policy_version": "alice-dlp-v1",
            "rules": {
              "company.codename": {
                "action": "deny",
                "providers": ["openai"],
                "models": ["gpt-5.4"]
              }
            }
          }
        }
      }
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

Structured DLP currently adds four deterministic built-ins:

- `us_ssn` recognizes valid hyphenated US Social Security number structure and redacts by default.
- `payment_card` recognizes 13-to-19-digit card candidates only after a Luhn check and redacts by default.
- `email_address` recognizes conventional email-address syntax in detect-only mode. It is deliberately low-confidence telemetry, not a complete PII classifier.
- `opaque_media` recognizes provider-defined image, file, document, and screenshot content positions whose bytes Hormuz cannot inspect. It denies by default and cannot be configured to redact or require approval because neither action produces inspected content.

Each DLP rule can be limited to `openai`, `anthropic`, and exact routed upstream model IDs. `detect` forwards the original value, `redact` replaces it with `[REDACTED:HORMUZ_DLP]`, and `deny` returns `hormuz_dlp_denied` without a provider call. With approval disabled, `require_approval` continues to fail closed without creating a grant. With approval enabled, it creates a durable metadata-only request and returns `hormuz_dlp_approval_required` plus the opaque request ID in the message and `X-Hormuz-DLP-Approval-Request` header.

## Team and person tightening

The organization policy owns every detector, dictionary, category, confidence, base action, and maximum provider/model scope. A team or actor overlay references an enabled organization `rule_id` and supplies a required `policy_version`, a strictly stronger action, and optionally a narrower provider/model scope. It cannot add a detector, load separate dictionary values, enable a rule the organization turned off, broaden its scope, or change its metadata.

Action strength is `detect` < `redact` < `require_approval` < `deny`. Hormuz applies the authenticated identity's team overlay and then actor overlay, but resolves one strongest effective rule for each request. An actor declaration therefore cannot weaken a stronger team rule. Unknown team/actor IDs, unknown rules, actions equal to or weaker than the organization action, broader scopes, and unsupported routed models fail configuration validation instead of becoming silent policy gaps. Until the enterprise tenancy ADR is implemented, an overlaid team ID must also map to identities in exactly one configured organization; ambiguous cross-organization team names fail closed.

When an identity has an overlay, Hormuz derives a bounded `dlp-effective-v1:...` policy version from the safe organization/team/actor rule metadata and declared layer versions. That deterministic value binds approval retries and is stored in metadata-only DLP evidence; it contains no dictionary values. Administrators must increment the owning `policy_version` whenever an environment-backed dictionary's values change because those protected values are deliberately excluded from the digest. `hormuz policy-check` shows the exact effective rule actions for an actor, provider, and requested model before rollout.

Approval remains organization-governed. If any active overlay selects `require_approval` while approvals are enabled, configuration validation still requires a same-organization `dlp_approver`; an overlay cannot grant that capability or bypass non-self approval.

## Opaque image and file boundary

The OpenAI Responses contract accepts `input_image` values by URL, data URL, or file ID and `input_file` values by encoded data, URL, or file ID. Hormuz also recognizes those blocks when they are nested in provider-defined tool output, plus computer screenshot outputs. The Anthropic Messages contract accepts image blocks and document sources by base64, URL, or provider file reference. Hormuz recognizes those blocks in messages and nested tool/search-result content. See the official [OpenAI Responses request schema](https://developers.openai.com/api/reference/resources/responses/methods/create), [Anthropic vision guide](https://platform.claude.com/docs/en/build-with-claude/vision), and [Anthropic PDF guide](https://platform.claude.com/docs/en/build-with-claude/pdf-support).

Hormuz does not fetch or decode those bytes yet. With the secure default, `opaque_media` produces one high-confidence `unsupported_media` finding per recognized block, commits metadata-only `security.dlp` evidence, and returns the ordinary provider-shaped `hormuz_dlp_denied` outcome before a provider call or usage charge. Filenames, URLs, file IDs, media data, prompts, and surrounding content are excluded from the event, log message, and error.

Anthropic document sources whose type is `text` or `content` remain inspectable. Their nested JSON strings continue through secret and structured-DLP transformation, so a regulated identifier in an inline text document is redacted rather than causing a blanket media denial.

The `opaque_media` action accepts only `deny` or `off`. `off` is an explicit organization risk acceptance that permits supported provider media to pass without byte inspection. That exception is object-local: Hormuz skips generic string scanning only for the recognized opaque object while continuing credential and DLP inspection of inspectable sibling text and values in the same request. Hormuz refuses `detect`, `redact`, or `require_approval` for this rule rather than implying that an opaque file was reviewed or safely transformed.

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

This is a bounded deterministic DLP subset, not a complete data-loss-prevention system. It inspects JSON values and known provider media shapes, not caller-controlled provider headers or JSON keys. It denies recognized opaque media but does not inspect image/file contents, decode arbitrary base64 embedded in ordinary text, unpack archives, classify source paths, infer proprietary meaning, or reliably detect transformed and obfuscated values. A custom exact value protects only that exact case-sensitive textual representation. The SSN detector intentionally supports only the high-confidence hyphenated form; the email detector has not passed an organization-specific false-positive/false-negative evaluation and therefore remains detect-only.

Use `deny` when forwarding a detected credential is unacceptable. Production deployments should combine Hormuz with least-privilege provider keys, short-lived employee identity, network controls, provider retention settings, code-host secret scanning, and a reviewed list of organization-specific values.

The broader boundary remains governed by [accepted ADR 0004](decisions/0004-structured-dlp-and-approval-boundary.md). Source classification, semantic detection, provider-header and JSON-key inspection, arbitrary encoded-text/archive decoding, detector-version evidence, multi-node approval persistence/notification, content-cache invalidation, and organization-specific evaluation are still open. Issue #10 remains open until those paths and the complete compatibility, failure, migration, and privacy gates pass.

## Verify

The integration tests assert both sides of the boundary:

```bash
python3 -m unittest -v \
  tests.test_gateway.GatewayIntegrationTests.test_secret_is_redacted_before_provider_and_audited \
  tests.test_gateway.GatewayIntegrationTests.test_low_confidence_dlp_detection_forwards_unchanged_and_audits_metadata_only \
  tests.test_gateway.GatewayIntegrationTests.test_team_and_actor_dlp_overlays_apply_to_both_provider_paths \
  tests.test_gateway.GatewayIntegrationTests.test_regulated_identifier_is_redacted_on_anthropic_path_before_provider \
  tests.test_gateway.GatewayIntegrationTests.test_opaque_media_is_denied_for_openai_and_anthropic_before_provider \
  tests.test_gateway.GatewayIntegrationTests.test_opaque_media_denial_on_token_count_has_no_usage_charge \
  tests.test_gateway.GatewayIntegrationTests.test_organization_can_disable_opaque_media_without_disabling_sibling_dlp \
  tests.test_gateway.GatewayIntegrationTests.test_inline_anthropic_text_document_remains_inspectable \
  tests.test_gateway.GatewayIntegrationTests.test_company_dictionary_deny_blocks_before_egress_and_never_persists_value \
  tests.test_gateway.GatewayIntegrationTests.test_approval_requirement_binds_to_exact_routed_model_and_fails_closed \
  tests.test_gateway.GatewayIntegrationTests.test_non_self_approval_allows_one_exact_retry_for_openai_and_anthropic \
  tests.test_gateway.GatewayIntegrationTests.test_dlp_approval_store_failure_blocks_before_egress_without_content_leak \
  tests.test_store.UsageStoreMigrationTests.test_dlp_approval_expiry_and_concurrent_retry_fail_closed
```

These tests prove credential and regulated-identifier transformation, detect-only forwarding, monotonic team/person tightening, provider-format-aware opaque denial, object-local opaque-media risk acceptance, inspectable text-document transformation, deny-before-egress, exact routed-model scoping, non-self authorization, CLI/API approval, exact single-use consumption, expiry, concurrent replay rejection, model-mismatch evidence, store-outage denial, and metadata-only evidence across the OpenAI and Anthropic compatibility paths.
