# ADR 0004: Structured DLP and approval boundary

- Status: **Accepted**
- Decision date: **2026-08-15**
- Product owner: **Mehrdad**
- Implementation issue: [#10](https://github.com/Xpounder-com/hormuz/issues/10)

## Decision

Hormuz will implement organization-governed data-loss prevention at the final provider-egress boundary. Deterministic high-confidence secrets and regulated identifiers are redacted by default. An organization administrator may tighten a rule to deny or require approval. Lower-confidence PII begins in detect-only mode until organization-specific evaluation supports enforcement.

Company dictionaries, source classifications, and custom rules are configurable organization policy. Team and person overlays may only tighten the organization policy; they cannot weaken it. Existing exact-secret detection remains the first implemented subset of this accepted architecture.

## Request pipeline

Every provider-bound value, including future injected context and tool output, follows one ordered path:

1. authenticate the caller and derive organization, team, actor, client, and permissions;
2. resolve model and provider policy;
3. authorize and inject governed context, if enabled;
4. decode and normalize supported structured request content within bounded resource limits;
5. classify and detect protected data;
6. apply the effective detect, redact, approval, or deny action;
7. apply provider storage and cache policy to the transformed payload;
8. forward only the approved or transformed payload;
9. persist metadata-only policy and usage evidence.

Unredacted content cannot enter a Hormuz context cache or provider cache before DLP evaluation. A DLP policy-version change invalidates affected reusable context artifacts.

## Rule confidence and default action

| Rule class | Initial default | Examples |
| --- | --- | --- |
| High-confidence credential or regulated identifier | Redact | private keys, provider tokens, organization-defined exact secrets, validated account or government identifiers |
| Organization-classified source or dictionary match | Administrator-configured detect, redact, approval, or deny | unreleased product names, customer lists, classified repository paths |
| Lower-confidence PII or semantic classifier | Detect only | ambiguous names, locations, prose that may describe proprietary strategy |
| Unsupported opaque media under an enforced rule | Deny | encrypted archive, uninspectable binary, unsupported image or document payload |

Promotion from detect-only to redact, approval, or deny requires a labeled organization-representative evaluation with recorded false-positive and false-negative results. A model-based detector is advisory until it meets that gate; it does not silently make irreversible transformations.

## Approval contract

An approval is a narrow exception for one exact outbound payload after all deterministic transformations:

- the request receives a keyed payload fingerprint that cannot reveal the raw content;
- the approval record binds organization, actor, rule IDs, provider, the exact routed upstream model known before egress, policy version, and fingerprint;
- the approval identifier is opaque, single-use, and expires after 15 minutes;
- replay, payload mutation, model/provider change, policy change, expiry, or actor change invalidates it;
- the provider-returned actual model is audited after egress; a mismatch from the approved routed model raises a security event rather than retroactively broadening the approval;
- the approval record and notification contain no prompt, matched value, response, file content, or provider credential;
- only a principal with the `dlp_approver` capability may approve;
- the request actor cannot approve their own exception;
- a team or person policy cannot grant approval authority or bypass an organization denial.

The future UI or CLI may display a separately authorized minimal review excerpt, but content-review permission is distinct from metadata approval permission and requires its own retention and access audit.

## Failure behavior

- Deterministic enforced rules fail closed when their detector, decoder, policy store, or approval store is unavailable.
- Detect-only telemetry may fail open only when the request remains subject to all enforced rules and the degraded state is surfaced to operators.
- Bounded decoding rejects decompression bombs, excessive nesting, oversized encoded fields, and unsupported recursive containers before expensive classification.
- Unsupported opaque media is denied whenever organization policy requires inspection.
- Redaction must preserve valid provider request structure. If a safe transformation cannot be produced, the request is denied.

## Audit and privacy boundary

Routine security events contain only event-time identity/scope, client, provider, requested and actual model, policy and detector versions, rule identifiers, action, counts, approval metadata, timing, and a non-reversible keyed fingerprint where required. They never contain prompt or response text, matched values, filenames, source content, embeddings, provider credentials, authorization codes, session credentials, or unkeyed content hashes.

Detector access, exception decisions, policy changes, and exports require separate capabilities and auditable access. Token and spend reporting must not become an employee performance score.

## Alternatives considered

### Deny every possible PII match immediately

This creates an unmeasured false-positive boundary across code, prose, logs, and international identifiers. Rejected as the default; organizations can tighten evaluated rules.

### Redact all suspected semantic content

Irreversible semantic redaction can corrupt technical meaning without proving protection. Rejected until a labeled evaluation supports the rule and action.

### Let employees bypass a finding locally

This defeats organization-wide policy and weakens attribution. Rejected. Exceptions use the bounded approval contract.

### Store matched content for reviewer convenience

This would create a concentrated sensitive-data store in the control plane. Rejected for routine evidence. Any future content-review store requires a separate architecture decision.

## Consequences

- Hormuz remains a trusted plaintext egress processor and must be deployed inside the customer's controlled boundary.
- The DLP engine needs provider-format adapters, bounded decoders, versioned rule packs, capability-based approval APIs, a durable replay-safe approval store, and organization-specific evaluation tooling.
- Exact-secret redaction is useful current behavior but does not satisfy this ADR by itself.
- Issue #10 remains open until the implementation, evaluation, client compatibility, failure, privacy, and migration evidence passes.

## Verification required

- unit corpora for every detector/action with labeled false positives and false negatives;
- end-to-end OpenAI Responses and Anthropic Messages tests for detect, redact, approve, deny, stream, tool, and context-injection paths;
- cross-tenant, cross-actor, replay, self-approval, expiry, mutation, and policy-version rejection tests;
- decoder resource-exhaustion, opaque-media, dependency-outage, and fail-closed tests;
- proof that provider calls and cache writes receive only transformed or approved content;
- metadata, log, error, audit, package, and credential scans proving sensitive values are absent;
- migration and rollback evidence for policy and approval storage;
- organization-representative evaluation before any lower-confidence rule moves beyond detect-only.

## Owner approval record

Accepted in the product-development conversation on 2026-08-15. The owner explicitly approved the recommended architecture: high-confidence secret and regulated-identifier redaction by default, configurable organization policy, detect-only lower-confidence PII, short-lived non-self approvals, fail-closed enforced rules, and denial of uninspectable media under enforced DLP. The owner subsequently approved binding an approval to the exact routed upstream model available before egress, with the provider-returned model audited afterward. The owner also accepted transparent automatic consumption when the same employee retries the exact approved operation and payload, preserving unchanged Codex and Claude Code clients instead of requiring a custom client header.

GitHub acceptance record: [issue #10 comment](https://github.com/Xpounder-com/hormuz/issues/10#issuecomment-5305144582).

Acceptance authorizes implementation under [#10](https://github.com/Xpounder-com/hormuz/issues/10). The local deterministic subset, monotonic identity-derived team/person overlays, provider-format-aware denial of recognized opaque media, and single-node approval workflow have implementation evidence, but acceptance does not claim the remaining structured-DLP or enterprise release gates are complete.
