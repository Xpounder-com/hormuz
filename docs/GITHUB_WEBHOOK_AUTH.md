# GitHub outcome webhook: dormant authentication checkpoint

This #219 checkpoint authenticates a synthetic GitHub App delivery in memory.
It creates no HTTP receiver, App installation, credential loader, normalized
outcome, schema migration, background replay, or release claim. The prior
[offline preflight](GITHUB_CONNECTOR_PREFLIGHT.md) and its frozen fixtures remain
proposals. `GitHubWebhookAuthenticator` is not wired into `OutcomeIngestor` in
production; a later normalizer needs its own reviewed event semantics.

## Permission and event decision

The first possible outcome profile is a dedicated, tenant-owned GitHub App
limited to explicitly enrolled repositories. Its requested repository
permissions would be **Pull requests: read** and **Metadata: read**; no
organization permission, Contents, Issues, Actions,
Checks, or write permission is needed for this first profile. Subscribe only
to `pull_request`; a future normalizer may consider `opened` and `closed` after
checking signed body shape and the existing source-neutral outcome contract.
Every other action is excluded. No event is subscribed or accepted by this
checkpoint. GitHub may deliver App installation/control events automatically;
they are never outcome observations. The first live setup still needs the
owner's explicit authorization and a review of the actual App permissions.
The event name header is unsigned, so no outcome normalizer may rely on it.
The future event/body discriminator needs a separate review; if body shape
cannot establish it unambiguously, normalization needs an authenticated
GitHub delivery lookup before any outcome write.

| Candidate event/action | Minimum GitHub App permission | This checkpoint |
| --- | --- | --- |
| `pull_request` / `opened`, `closed` | Pull requests: read; Metadata: read | Auth body only; mapping pending |
| `pull_request_review` / `submitted`, `dismissed` | Pull requests: read; Metadata: read | Excluded; no subscription or mapping |
| `check_run` / `completed` | Checks: read; Metadata: read | Excluded; no subscription or mapping |
| `issues`, `push`, `workflow_run`, content APIs | Different/additional permissions | Excluded |

GitHub's [event catalogue](https://docs.github.com/en/webhooks/webhook-events-and-payloads)
states the minimum permissions for `pull_request`, `pull_request_review`, and
`check_run`. Its [permission guide](https://docs.github.com/en/apps/creating-github-apps/registering-a-github-app/choosing-permissions-for-a-github-app)
recommends choosing permissions per subscribed webhook. This matrix limits
permission planning; it does not assert that the current App has these settings
or that the pending fixtures have accepted mappings.

## Signing channel and delivery identity

A channel is an operator-registered `(organization, connector, installation,
repository allowlist)` plus an App webhook secret held outside the config and
source tree. The intended enrollment is one App secret per tenant-owned App;
do not reuse the optimizer App's secret or a shared cross-tenant App for this
profile. The adapter accepts at most two distinct secret versions during an
explicit rotation overlap. It verifies `X-Hub-Signature-256` with HMAC-SHA256
over the exact raw bytes using constant-time comparison **before** JSON
decoding. It then requires the signed body's numeric `installation.id` and
`repository.id` to match the server binding. The body cannot select an
organization, connector, new repository, or work scope. Duplicate/malformed
signature headers fail closed; missing or invalid signing material is denied.

The constructor also rejects a second configured connector, including in
another tenant, that overlaps the same installation/repository pair.

GitHub's [signature guide](https://docs.github.com/en/webhooks/using-webhooks/validating-webhook-deliveries)
defines the raw-body HMAC. `X-GitHub-Delivery`, `X-GitHub-Event`, and hook
headers are **outside** that HMAC. GitHub [documents the delivery GUID](https://docs.github.com/en/webhooks/webhook-events-and-payloads),
but changing it leaves the same signed body and tag. Therefore this checkpoint
does not use any unsigned header for tenant, event, or replay authority.
The internal `source_delivery_id` is a tenant/connector-separated HMAC of the
signed raw bytes under a separately retained outcome identity key/version.
Exact body replay, even with altered headers or an overlapping webhook secret,
has one identity. Byte-identical distinct provider deliveries conservatively
collapse; this is a known limit until GitHub delivery GUID can be independently
authenticated. The identity key/version must remain available across the
replay horizon; loss fails closed. A future receiver must select the secret
channel without trusting body/header claims, reject ambiguous duplicate HTTP
headers at transport, and commit a receipt before acknowledging a delivery.

The module returns only the existing content-free `AuthenticatedDelivery`
metadata. It neither returns nor logs title, text, branch, path, URL, patch,
commit message, actor name, or raw payload. The synthetic test-only adapter
shows that the existing atomic receipt path deduplicates the derived identity;
it is not a GitHub normalizer or a live transport test.

Run the offline witness with:

```bash
python -m unittest -v tests.test_github_webhook_auth
```
