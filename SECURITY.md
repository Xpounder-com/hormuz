# Hormuz security policy

Hormuz is alpha software and has not received a third-party security review.
Do not expose the development server directly to the public internet or treat
this policy as an enterprise security certification.

## Supported versions

| Surface | Security-fix boundary |
| --- | --- |
| Current `main` | Fixes land here while the first alpha is being prepared |
| Most recent published `v0.x` tag | Best-effort fixes after the first public tag exists |
| Older tags and branches | No guaranteed backports |

The exact platform and client versions exercised by release gates are listed in
[SUPPORT.md](SUPPORT.md). A dependency or upstream-client release is not
supported merely because it is newer.

## Report a vulnerability privately

Use GitHub's [private vulnerability reporting
form](https://github.com/Xpounder-com/hormuz/security/advisories/new). Do not
open a public issue, pull request, Discussion, or commit containing the report.

If the private form is unavailable, open a
[content-free installation
issue](https://github.com/Xpounder-com/hormuz/issues/new?template=installation.yml)
stating only that the private security channel is unavailable. Do not include
the affected component, reproduction, exploitability, identities, or customer
details in that public issue.

A useful private report includes:

- the affected commit, tag, image digest, or package version;
- the security boundary believed to be violated;
- a minimal reproduction using synthetic values;
- impact and prerequisites;
- whether a credential or customer system may already be exposed;
- suggested remediation, if known.

Never submit a real API key, OIDC token, employee credential, prompt, response,
customer record, private hostname, private repository name, or raw production
log. If a real credential may be exposed, revoke or disable it at its
authoritative provider first; Hormuz cannot revoke provider-side credentials.

Maintainers aim to acknowledge a complete report within five business days,
but the alpha has no response or remediation SLA. Maintainers will coordinate
validation, a fix, release scope, and disclosure timing through the private
advisory. Do not assume silence authorizes public disclosure.

## Current guarantees

- Employee bootstrap or OIDC credentials are never forwarded to OpenAI or Anthropic.
- Provider credentials are read from server-side environment variables.
- Prompt and response bodies are relayed in memory and are not written to the usage database.
- Usage storage contains identity, team, client, protocol, model, policy outcome, token, cost, status, and provider-request metadata.
- Configurable secret controls redact or deny high-confidence credential formats, every configured Hormuz/provider credential, and exact environment-provided values before upstream serialization.
- Secret-control evidence stores only rule identifiers and detection counts, never matched values.
- OpenAI Responses requests are forced to `store: false`, and background mode is denied, unless an administrator explicitly allows those storage modes.
- Identity-token comparisons use constant-time comparison.
- OIDC JWT access tokens require a configured issuer and audience, asymmetric signature verification, expiry, a key ID, and an explicit issuer-subject mapping. Discovery and JWKS use bounded responses and HTTPS outside loopback tests; unknown key IDs cannot trigger unlimited refreshes.
- Request bodies have a configurable size limit and upstream calls have a configurable timeout.

## Current limitations

- The built-in server does not terminate TLS.
- Static environment-provided identity tokens remain available for bootstrap and break-glass use.
- The OIDC path verifies short-lived JWT access tokens but does not yet implement browser login, refresh-token custody, opaque-token introspection, active revocation, or SCIM provisioning.
- SQLite is a single-node development store.
- Configuration contains rate cards and policy, but there is not yet a signed configuration or change-approval workflow.
- Secret detection is best-effort and text-only. It does not inspect images, decode arbitrary encodings or archives, or infer semantically sensitive company information.
- Logs and provider behavior still require deployment-specific review.

Terminate TLS and enforce network access controls in front of Hormuz for any
shared test deployment. Use unique identities for every human or service
account, never shared team credentials. Do not send an OIDC ID token where an
API access token is required.

## Scope boundary

Reports may cover the Hormuz core package, official source repository,
official OCI image and release workflow, policy enforcement, provider egress,
identity mapping, tenant isolation, metadata-only evidence, or bundled
verification tooling. Provider platforms, third-party clients, GitHub, Docker,
PostgreSQL, OpenBao, Ceph, and other dependencies retain their own disclosure
programs. Report an integration defect privately when Hormuz introduces or
amplifies the risk; report an upstream-only defect to the upstream owner.
