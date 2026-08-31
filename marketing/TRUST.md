# Hormuz trust and data-flow brief

Scope: current v1 source contracts and public reference evidence, reviewed
August 30, 2026. This is an engineering summary, not an independent assessment,
certification, legal opinion, or data-processing agreement.

## Request path

Employee client → Hormuz identity/policy/secret/budget checks → allowed provider
request. Metadata is recorded in the configured gateway store. A denial must
not make the governed upstream call. Client shell commands, MCP, browser/Git
traffic, and requests outside this route are not governed by Hormuz.

## Data handling

| Data | Handling and responsibility |
| --- | --- |
| Prompts/responses | Relayed transiently; excluded from routine usage/security ledgers. Allowed content reaches the provider under the customer's agreement. |
| Employee credentials | Authenticate to Hormuz; not forwarded as provider credentials. Unique identities are necessary for meaningful attribution. |
| Provider credentials | Remain server-side in the configured environment or custody system. Do not distribute company keys to employees. |
| Identity/usage metadata | Organization, team/person, model, policy outcome, tokens, estimated cost, status, and bounded secret outcomes; treat this as sensitive metadata. |
| Secret-control evidence | Bounded rule/action/count/outcome fields; no matched secret value or raw request material. |
| Infrastructure logs/backups | Operator-owned. Avoid body logging, restrict metadata access, and define retention/deletion and backup protection. |

## Implemented versus not claimed

- OIDC JWT resource-server verification is implemented. Issuance and refresh
  stay with the identity tooling. Native Hormuz browser login, refresh custody,
  and its own session-revocation endpoint are not currently provided.
- Deterministic secret modes are redact, deny, and off. No complete semantic
  DLP guarantee is made. Custody-lifecycle approvals are not per-inference
  human approval.
- Costs are configured-rate-card estimates for captured requests, not
  reconciled provider invoices or complete provider-account spending.
- v1.0.0 stabilizes CLI/policy/evidence contracts. The separately signed OCI
  reference is v0.1.3 linux/amd64. Neither label certifies a customer deployment.
- Public reference tests and the synthetic demo are not independent security
  review, human onboarding validation, customer endorsements, or an SLA.

## Buyer-review checklist

Before using customer secrets or production traffic, identify owners and
evidence for TLS/ingress and bypass controls; identity/token issuance and
revocation behavior; provider/key custody; roles and administrative access;
metadata retention, access and deletion; logs/backups; migrations/rollback;
availability, capacity, recovery and RPO/RTO; representative secret-control
evaluation; provider terms; and independent security review. Resolve gaps in
your environment instead of treating a reference profile as blanket approval.

## Inspect the proof

The [demo page](https://usehormuz.github.io/demo/) includes real CLI
recordings and a JSONL sample from a separate synthetic run: four usage events
and one secret-control event, validated before export. No customer data is used.
See [audit schemas](../docs/AUDIT.md), [architecture](../docs/ARCHITECTURE.md),
[OIDC](../docs/OIDC.md), [secret controls](../docs/SECRET_CONTROLS.md),
[usage](../docs/USAGE.md), and [operations](../docs/OPERATIONS.md).

Report vulnerabilities through [SECURITY.md](../SECURITY.md), not a public issue
or marketing form. General evaluation contact: Mehrdad Zaker,
zaker.mehrdad@gmail.com.
