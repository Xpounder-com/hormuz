# ADR 0005: GitHub lifecycle event trust and collection

- Status: **Proposed — owner approval required**
- Date proposed: 2026-08-16
- Decision owner: Product owner
- Tracking issue: [#12](https://github.com/Xpounder-com/hormuz/issues/12)
- Unblocks after acceptance: the first source-specific lifecycle collector under [#12](https://github.com/Xpounder-com/hormuz/issues/12)

## Decision requested

Choose how Hormuz should establish trust in GitHub merge and CI evidence before that evidence can promote or invalidate organizational context:

1. **GitHub App webhook source plane plus Actions OIDC attestation plane — recommended.** Verify GitHub App webhooks as the authoritative delivery channel for a narrow set of GitHub source events. Keep the existing GitHub Actions OIDC route as a separate, explicitly delegated channel for workflow identity, snapshots, and evidence that GitHub does not issue as a supported webhook fact.
2. **GitHub App webhooks only.** Accept only source events delivered to the app and defer all workflow-submitted snapshots and custom attestations.
3. **GitHub Actions OIDC only.** Let configured workflows submit all lifecycle claims, including merge and CI outcomes.
4. **Manual trusted submission only.** Keep the present generic lifecycle API and do not collect source events automatically.

This ADR proposes option 1. It is not accepted and does not authorize a GitHub App endpoint, app registration, webhook secret, private key, or stable connector schema until the product owner approves it.

## Context

Hormuz already accepts capability- and organization-scoped lifecycle evidence, repository snapshots, and revalidation work through a generic authenticated API. That boundary proves who submitted an envelope. It does not prove that the referenced merge, review, CI result, or repository state occurred.

GitHub provides two different trust mechanisms, and they prove different things:

| Mechanism | What Hormuz can prove | What Hormuz cannot infer |
| --- | --- | --- |
| GitHub App webhook with a valid `X-Hub-Signature-256` | The exact raw body matches an HMAC produced by a holder of the configured shared webhook secret; under uncompromised secret custody, Hormuz treats it as a GitHub delivery | That the event is the latest state, that deliveries arrived in order, that one review satisfies repository policy, or that an arbitrary business claim is true |
| GitHub Actions OIDC JWT | A short-lived token was issued for a job with verified repository, workflow identity and workflow SHA, ref, run, actor, and related claims | That the job succeeded, that its submitted body is truthful, or that the workflow is trusted merely because it exists in the repository |
| Existing Hormuz evidence reference fingerprint | A stable external reference was bound to one record subject without retaining the raw reference | That the external source produced the referenced event |

GitHub recommends validating the exact webhook body with HMAC-SHA256 before processing it, using HTTPS, accepting only required event/action pairs, returning within ten seconds, and deduplicating deliveries with `X-GitHub-Delivery`. A redelivery keeps the same delivery identifier. The HMAC uses a shared secret, so compromise of that secret also compromises this source-authentication boundary. See [Validating webhook deliveries](https://docs.github.com/en/webhooks/using-webhooks/validating-webhook-deliveries), [Best practices for using webhooks](https://docs.github.com/en/webhooks/using-webhooks/best-practices-for-using-webhooks), and [Webhook events and payloads](https://docs.github.com/en/webhooks/webhook-events-and-payloads).

GitHub Actions OIDC tokens expose repository, repository ID, owner ID, workflow reference and SHA, run ID and attempt, event, ref, actor, and other claims. Those claims support a narrow workload trust rule, not a provider-signed test result. See GitHub's [OpenID Connect reference](https://docs.github.com/en/actions/reference/security/oidc).

## Proposed decision

### Separate source events from delegated attestations

Hormuz will maintain two explicit evidence origins:

- **`github_webhook`:** a normalized fact derived from an allowlisted event/action after the exact raw payload passes GitHub App signature verification;
- **`github_actions_oidc`:** a claim submitted by an allowlisted workflow whose OIDC identity satisfies organization policy.

The origins are not interchangeable. A generic or Actions-OIDC caller cannot label its own envelope `github_webhook`. A source event is not trusted merely because its JSON resembles a GitHub payload.

The GitHub App webhook is the source plane for GitHub facts that GitHub itself delivers, beginning with merged pull requests and completed GitHub Actions workflow runs. The existing Actions OIDC route remains useful for customer-owned snapshots, artifact manifests, custom CI systems, and evidence that an organization deliberately delegates to an exact workflow. The UI, CLI, API, audit record, and lifecycle policy must preserve the origin and trust policy used.

### Installation and tenant binding

A GitHub App installation does not choose its Hormuz tenant. A Hormuz administrator with connector-administration capability must bind the numeric GitHub installation, owner, and selected numeric repository IDs to one existing Hormuz organization.

Every webhook request derives organization and repository scope from that server-side binding. Values in the payload, query string, URL path, or normalized evidence body cannot override it. Unknown, suspended, deleted, or repository-mismatched installations fail closed before lifecycle records are queried.

The first app requests only the repository permissions required by the initial event set:

- read-only **Pull requests** permission for `pull_request` events;
- read-only **Actions** permission for `workflow_run` events; and
- GitHub's mandatory repository metadata access.

It does not request write permission, OAuth user authorization, repository contents, checks, issues, administration, or organization permissions for this slice. Customers can install it on selected repositories rather than all repositories. Additional events or API reads require a documented permission change and compatibility/security evidence.

### Initial event-to-evidence allowlist

Only the following mappings are eligible in the first implementation:

| GitHub event | Required state | Hormuz result | Additional policy |
| --- | --- | --- | --- |
| `pull_request` / `closed` | `pull_request.merged` is true and GitHub supplies the relevant head and merge commit SHAs | `commit_merged` for records deterministically bound to one of those exact revisions | Repository and base branch must be allowlisted |
| `workflow_run` / `completed` | conclusion is `success` and the run supplies an exact head SHA | `ci_passed` for records bound to that revision | Numeric workflow ID or immutable configured workflow identity and triggering branch/event must be allowlisted |
| `workflow_run` / `completed` | conclusion is `failure` or `timed_out` | `ci_failed` for records bound to that revision | Same workflow allowlist as the positive signal |

Other workflow conclusions, including `cancelled`, `neutral`, `skipped`, `stale`, and `action_required`, do not become positive or negative evidence. Absence of a pass is not automatically a failure.

The first connector deliberately does not infer:

- `commit_reverted` from a commit message, branch name, or later push;
- `review_accepted` from one submitted approval;
- `review_rejected` from one review state without the configured aggregate review policy;
- `adr_approved`, `incident_resolved`, or a human decision from a merged file or comment; or
- a repository/dependency snapshot from a webhook that does not contain the complete configured snapshot.

Those signals require a later explicit source contract, aggregate-state verification, or delegated attestor policy. Hormuz must not convert a convenient heuristic into verified organizational memory.

### Deterministic record binding

The webhook payload never names a Hormuz context record. After normalization, a lifecycle worker joins the trusted numeric repository identity and exact Git revision to records whose source or dependency provenance already references that revision. A connector cannot fan evidence out to a caller-supplied record ID.

The normalized merge fact may retain both GitHub-supplied pull-request head SHA and merge commit SHA so squash, rebase, and merge-commit strategies can be handled without guessing. The evidence stored against a record states which exact revision matched. No fuzzy repository name, branch-name-only, commit-message, actor, or time-window association is sufficient for promotion.

### Webhook verification and replay behavior

The receiver must:

1. require HTTPS at the public deployment boundary;
2. read the exact, bounded request bytes without JSON reserialization or proxy mutation;
3. validate `X-Hub-Signature-256` with HMAC-SHA256 and constant-time comparison before parsing or acting on the body;
4. require the expected content type, GitHub event header, globally unique delivery ID, installation target, installation, repository, action, and source identifiers;
5. derive Hormuz organization/repository scope from the server-side installation binding;
6. reject every unrecognized event, action, conclusion, repository, workflow, branch, or schema shape without lifecycle mutation;
7. commit a minimal normalized inbox fact and its metadata-only audit event before returning success; and
8. perform record matching and lifecycle revalidation outside the delivery response.

The body has an explicit operator-configured limit no higher than GitHub's documented 25 MB webhook cap. Oversize or malformed requests fail before JSON expansion and create a content-free coverage alert. The implementation must return a 2XX response within GitHub's ten-second window only after the normalized fact is durable. A storage failure returns non-2XX so the delivery is visibly failed rather than silently dropped.

`X-GitHub-Delivery` is the idempotency key inside one installation. An exact redelivery returns the prior result. Reuse of one delivery ID with a different keyed body fingerprint is a security conflict. Webhook secret rotation may temporarily accept current and immediately previous secret versions during an operator-controlled overlap, and the accepted key version is recorded without logging either secret or signature.

GitHub does not automatically redeliver failed webhooks. The initial slice documents operator redelivery and exposes coverage gaps. Automated delivery reconciliation requires GitHub API authentication and is deferred until app-private-key custody is implemented under the KMS/operations gates.

### Ordering and current-state limits

Signature verification authenticates a delivery; it does not make network delivery ordered or current. Each normalized fact stores GitHub's source object ID, source event time, Hormuz receipt time, delivery ID, run attempt where applicable, repository ID, and exact revision. Lifecycle ordering uses the source object's event/completion time and deterministic source identity, not arrival time alone.

Late events are retained and evaluated under the existing opposing-signal conflict rules. A newer accepted negative signal can invalidate older positive evidence. Equal-time opposing facts remain an explicit conflict. A source API re-fetch may later confirm current aggregate state, but the passive first slice does not hold a GitHub App private key and must not claim that it does.

### Actions OIDC delegation policy

An organization may separately authorize an Actions workload as a delegated attestor only when policy matches stable numeric owner and repository identity plus an exact workflow or reusable-workflow reference. The verifier also constrains issuer, audience, expiration, workflow SHA/ref, run ID/attempt, event, branch/ref, and repository visibility as applicable.

Repository membership alone is insufficient. A workflow on an unprotected feature branch cannot become a trusted attestor merely by requesting `id-token: write`. The organization must identify the exact protected workflow/ref or reusable workflow it owns and the evidence/snapshot types that workflow may submit.

OIDC-submitted evidence continues to be labeled delegated attestation. It may become trusted organizational policy, but Hormuz never describes it as a GitHub source event or treats the OIDC token itself as proof that tests passed.

### Data minimization and retention

Webhook payloads can contain pull-request text, user data, commit messages, repository metadata, and URLs. Hormuz therefore verifies and normalizes in memory and does not retain the raw body in the usage ledger, context store, ordinary logs, errors, or routine audit export.

The durable normalized inbox contains only:

- Hormuz organization and connector IDs;
- numeric installation, owner, repository, workflow/run, pull-request, and source object IDs when applicable;
- event, action, accepted conclusion, branches/event trigger needed by policy, exact Git SHAs, source and receipt times, and processing state;
- delivery ID or a keyed representation where export minimization requires it;
- a separate keyed raw-body fingerprint for idempotency conflict detection;
- webhook secret key version and normalization policy version; and
- resulting metadata-only evidence IDs, counts, and safe error codes.

It excludes pull-request titles/bodies, review text, commit messages, usernames/emails, source code, file paths, workflow logs, artifacts, raw webhook payloads, webhook signatures/secrets, OIDC tokens, GitHub App private keys, and unkeyed content hashes. Normalization errors must not echo submitted values.

The normalized source-event retention period follows organization audit policy. Raw payload persistence is not authorized by this ADR. If a future asynchronous queue cannot meet delivery durability without temporary raw storage, encrypted bounded raw-payload retention requires a separate owner-reviewed privacy and key-custody decision.

### Deployment and secret boundary

The first passive webhook collector needs one high-entropy GitHub App webhook secret but no GitHub App private key. The secret is server-side, never stored in repository/config examples, and is loaded through the deployment secret boundary. Local development may receive webhooks only through an operator-chosen HTTPS tunnel; tunnel use is not production evidence.

API re-fetch, automated redelivery, or installation-token use adds a GitHub App private key, which GitHub describes as the app's most valuable secret. Those features remain blocked on KMS-backed custody, rotation, least-privilege app authentication, audit, and failure-path evidence under [#17](https://github.com/Xpounder-com/hormuz/issues/17) and [#11](https://github.com/Xpounder-com/hormuz/issues/11).

The first compatibility claim is limited to GitHub.com. GitHub Enterprise Server requires separate base-URL, webhook-version, OIDC-issuer, permission, and compatibility evidence.

## Failure and observability contract

The connector exposes metadata-only counters and health for:

- valid, invalid-signature, malformed, oversize, unsupported, unknown-installation, and repository-mismatch deliveries;
- exact redeliveries and conflicting delivery-ID reuse;
- normalized facts awaiting, completing, conflicting, or failing lifecycle processing;
- last successful delivery and processing time per installation/repository;
- delivery and processing latency; and
- explicit coverage gaps caused by endpoint downtime, rejected payloads, disabled/suspended installations, or failed processing.

A lack of observed events is never reported as proof that no GitHub activity occurred. The product labels webhook coverage separately from organizational AI usage coverage and from the correctness of context promotion.

## Alternatives considered

### GitHub Actions OIDC only

This reuses the current API and avoids a public webhook endpoint. It proves workflow identity, not the truth of arbitrary merge or CI fields submitted by that workflow. A compromised or misconfigured trusted workflow could manufacture source facts. Rejected as the sole source plane in the proposal.

### Webhooks only

This provides source-authenticated merge and workflow-run events without a private API key. It cannot produce complete repository/dependency snapshots or organization-specific attestations that GitHub does not natively issue. Preserved as option 2, but less capable than the proposed split.

### API polling with a GitHub App private key

Polling can re-fetch current aggregate state and recover missed deliveries. It introduces the app's most sensitive credential, rate-limit and scheduling behavior, and a wider operational boundary before KMS custody exists. Deferred as later hardening rather than the first connector.

### Infer all lifecycle signals from repository conventions

File paths, commit messages, branch names, review comments, and ticket labels are convenient but organization-specific and easy to spoof or misunderstand. Rejected for verified memory unless an organization later approves a precise delegated policy.

## Consequences if accepted

- Hormuz gains a narrow source-authenticated path for automatic merge and GitHub Actions result evidence without requiring a GitHub App private key.
- Customers must install a least-privilege GitHub App on selected repositories and bind each installation to a Hormuz organization.
- The lifecycle schema must preserve evidence origin, source object identity, normalization policy, ordering, and coverage state.
- The public webhook endpoint becomes a security-sensitive, resource-bounded ingress but never a provider-egress path and never creates token or cost usage.
- Review, revert, ADR, incident, human, third-party CI, full snapshot, and API-reconciliation semantics remain open; this ADR deliberately does not overclaim them.
- Issue #12 remains open until source connectors, remaining lifecycle policy, hosted operations, and release evidence pass.

## Verification required after acceptance

- official GitHub webhook signature vectors plus raw-byte, Unicode, proxy-mutation, missing-header, wrong-secret, rotation, and constant-time comparison tests;
- event/action/conclusion allowlist tests using representative GitHub payload fixtures, with every unsupported shape proving zero lifecycle mutation;
- cross-tenant, unknown/suspended installation, repository mismatch, workflow/branch mismatch, and body-supplied scope override tests;
- exact redelivery, conflicting delivery ID, out-of-order, equal-time conflict, late failure, and retry-after-storage-failure tests;
- deterministic revision-to-record fanout tests for merge, squash, rebase, rerun, and unrelated-record cases;
- OIDC tests proving that workflow identity and submitted outcome are separate, including untrusted branch/workflow and repository-name-reuse cases;
- body-size, JSON expansion, timeout, concurrency, dependency outage, and fail-closed resource tests;
- log, audit, database, error, package, and credential scans proving excluded webhook/OIDC content is absent;
- installed GitHub App permission review and live GitHub.com delivery/redelivery evidence on a test repository;
- source and wheel install checks plus existing Codex/Claude compatibility, privacy, lifecycle, and benchmark gates; and
- an operational runbook for secret rotation, delivery failure, suspension/uninstall, coverage gaps, rollback, and connector disablement.

## Owner approval record

Pending. Approval must identify option 1, 2, 3, or 4 and record any changes to evidence mapping, GitHub App permissions, raw-payload retention, private-key boundary, or GitHub.com-only first compatibility claim.
