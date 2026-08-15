# Hormuz security posture

Hormuz is alpha software and has not received a third-party security review. Do not expose the current development server directly to the public internet.

## Current guarantees

- Employee Hormuz tokens are never forwarded to OpenAI or Anthropic.
- Provider credentials are read from server-side environment variables.
- Prompt and response bodies are relayed in memory and are not written to the usage database.
- Usage storage contains identity, team, client, protocol, model, policy outcome, token, cost, status, and provider-request metadata.
- Configurable secret controls redact or deny high-confidence credential formats, every configured Hormuz/provider credential, and exact environment-provided values before upstream serialization.
- Secret-control evidence stores only rule identifiers and detection counts, never matched values.
- OpenAI Responses requests are forced to `store: false`, and background mode is denied, unless an administrator explicitly allows those storage modes.
- Identity-token comparisons use constant-time comparison.
- Request bodies have a configurable size limit and upstream calls have a configurable timeout.

## Current limitations

- The built-in server does not terminate TLS.
- Identity tokens are static environment-provided secrets, not short-lived SSO credentials.
- SQLite is a single-node development store.
- Configuration contains rate cards and policy, but there is not yet a signed configuration or change-approval workflow.
- Secret detection is best-effort and text-only. It does not inspect images, decode arbitrary encodings or archives, or infer semantically sensitive company information.
- Logs and provider behavior still require deployment-specific review.

Terminate TLS and enforce network access controls in front of Hormuz for any shared test deployment. Use unique identity tokens for every human or service account, never shared team credentials.

Report security issues privately to the repository maintainers. Do not include real API keys, identity tokens, prompts, responses, or customer data in a report.
