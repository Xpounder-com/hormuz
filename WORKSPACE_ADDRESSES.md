# Customer workspace addresses

This implements the address and signup slice of [Hosted Pro workspace provisioning #438](https://github.com/Xpounder-com/hormuz/issues/438). It does not enable provider enrollment, inference credentials, subscriptions, metering, or paid checkout. Those remain #439–#443. No cloud resources, identity applications, DNS records, or public deployments are created by enabling this code in a checkout.

## Customer flow

1. Open `https://usehormuz.github.io/workspace/` and choose **Open your workspace**.
2. Sign in through the configured browser identity provider. The first verified sign-in atomically creates one workspace, an owner membership, and an administrator grant. Retries recover that workspace.
3. Open the included address, `https://<dashboard-origin>/w/workspace-<slug>`. No customer DNS is needed.
4. Optionally use **Custom domains → Connect domain**. Add the displayed TXT ownership record and CNAME routing record, then check the connection. Activation requires ownership, routing, provider verification, and a valid HTTPS response from this Hormuz deployment.

[GitHub Pages serves static files](https://docs.github.com/en/pages/getting-started-with-github-pages/what-is-github-pages). It is the public entry point. The authenticated dashboard is served by the hosted Python gateway. The public site exposes no customer credentials and uses no cross-domain session-cookie sharing or browser credential storage. Changing the public website domain or selecting a customer dashboard domain does not require moving repositories.

The default address stays available after adding or removing a custom domain. A custom hostname serves the same dashboard on that host. It uses canonical OIDC sign-in and a browser-bound, single-use, 60-second form POST handoff to establish a host-specific cookie. The inference host remains separate; workspace browser credentials cannot authenticate inference requests.

## Enable the hosted workspace profile

Use the existing single-node hosted image and persistent disk. Copy `deploy/render/gateway/workspace-profile.example.json` to the private hosted config file, set the real canonical backend HTTPS origin and supported OIDC browser client, and use `HORMUZ_HOSTED_MODE=workspace`. The new mode has no provider routes or keys. Existing `active` and `provider-pilot` profiles retain their separate route and secret inventories.

Configure the OIDC application for:

- A confidential browser client with PKCE S256, authorization code flow, and `form_post` callbacks.
- Exact callback URL `https://<dashboard-origin>/v1/workspaces/auth/callback`.
- `openid` and `email` scopes, with an authoritative `email_verified: true` claim. Omitted email claims may be completed from subject-matched UserInfo.
- The account registration/sign-in policy intended for customers. Hormuz provisions workspaces; it does not create identity-provider accounts.

Use dedicated `HORMUZ_INGRESS_CREDENTIAL`, `HORMUZ_SESSION_MASTER_KEY`, and `HORMUZ_OIDC_CLIENT_SECRET` environment credentials, as with existing hosted staging. New deployments use the existing `initialize` operator command with `--workspace-profile` before admission. The master key remains stable for durable account mapping. The single-node profile uses SQLite for sessions/tenancy and requires neither PostgreSQL nor a Raspberry Pi.

For a regular gateway configuration, explicitly enable `authentication.session_broker.workspace_enabled`, select `workspace_signup_issuer`, and enable managed onboarding. The public workspace origin must use HTTPS, including local workspace development behind a TLS terminator. HTTP loopback workspace signup is rejected because the cross-site `form_post` callback requires a `SameSite=None; Secure` binding cookie. Defaults leave signup off. Owners receive an empty coding-client allowance; signup cannot inherit operator pilot access.

Workspace requests have their own process-wide rate-limit bucket, independent of administrator-console traffic. At most 1,000 login flows and 1,000 live handoffs can be pending. Completed and failed login flows are deleted; expired flows and unneeded expired/consumed handoffs are reclaimed before admitting another start. These transient records are not an audit-retention mechanism.

## Enable optional Render custom domains

Keep these settings absent until the service, credentials, domain allowance, and operating cost have been reviewed. A [Render custom domain](https://render.com/docs/custom-domains) may consume the account's domain allowance. This implementation does not create a new Render service or buy domains.

Add `domain_target` and `domain_service_id` to the workspace profile. `domain_target` must equal the canonical service hostname, for example `your-dashboard.onrender.com`. Supply the dedicated `HORMUZ_DOMAIN_API_KEY` for that existing service. For a regular gateway config, use the corresponding `workspace_domain_target`, `workspace_domain_service_id`, and optional `workspace_domain_api_key_env` session settings.

Initially support direct subdomain CNAMEs, such as `ai.customer.com`. Apexes, wildcards, static `github.io` addresses, and `www` auto-pairing are excluded. Keep both DNS records in place; proxied/flattened CNAME setups are not qualified by this adapter. DNS checks use Google's fixed public DNS-over-HTTPS endpoint. TLS checks pin public IPs, verify the hostname, refuse redirects/private addresses, and verify a fresh deployment-bound challenge response. Operator API credentials are sent only to the fixed Render API origin.

The initial deployment admits at most 20 live domain records, with at most two per workspace; pending and removing records consume capacity too. The built-in worker checks due domains every 30 seconds in batches of ten, with each record due after five minutes. This capacity leaves renewal headroom for two batches even when each check takes 50 seconds. Increasing capacity requires qualifying reconciliation throughput. Successful verification grants a 30-minute serving lease. Failures remove active routing and invalidate domain-bound sessions and pending handoffs. An expired lease fails closed even if the worker is stopped. Domain removal disables routing first, then deletes the provider record; failed deletion remains `removing` and reserves the name until reconciliation succeeds. Reassignment always issues a new TXT challenge and authority version. There is no host cache to retain a stale tenant binding.

[Render can use a verified custom hostname for health checks](https://render.com/docs/health-checks). Health/readiness accepts a hostname only after durable provider-registration intent follows successful DNS ownership and routing checks. It stays independent of that domain's serving lease and provider deletion propagation; these content-free endpoints grant no workspace access. A bare, unverified claim cannot widen the health Host allowlist.

## Publish the Pages entry point

Build the website with `NEXT_PUBLIC_HORMUZ_DASHBOARD_ORIGIN=https://<dashboard-origin>`. This is a public origin, not a secret. Without it the page says sign-in is being prepared and offers the local installation; it does not advertise working hosted access.

Set the same variable in the separate `usehormuz/usehormuz.github.io` publication workflow, update its `site-source.json` pin to the reviewed product commit, publish, and run its live-site verification. The product repository's `website/deployment/verify-live-site.mjs` now checks the workspace page too. A product merge alone does not publish Pages. Set the destination only after the real backend and identity callback have been tested.

## Upgrade and recovery

Session schema v5 adds only closed, metadata-only workspace tables. Direct broker startup migrates existing v2/v3/v4 stores transactionally. Hosted startup continues to require an already-qualified current schema; it does not silently migrate a serving deployment.

For an existing hosted v4 deployment, switch to maintenance, stop the backend, and run the new offline command using the original origin, issuer, client, and master key:

```sh
HORMUZ_HOSTED_MODE=maintenance python -m hormuz.hosted --config /etc/secrets/hormuz-hosted.json sessions-migrate --snapshot-directory /var/lib/hormuz/pre-workspace-snapshot
```

Use `--workspace-profile` before the subcommand if that config has already been converted to the workspace schema. The command locks state, validates the old closed schema and signed manifest, snapshots both stores, then migrates v4 to v5. An older binary refuses v5. Rollback requires the original binary and the v4 snapshot while admission remains closed; changing the version number is not a rollback.

Snapshots and authenticated backups include workspace metadata. Restore closes workspaces, disables memberships/grants, revokes browser sessions, fails pending logins, consumes handoffs, and disables domains. It does not silently re-enable customer access or delete external provider records. Review the restored account/domain records and external DNS/provider state before a separate recovery activation procedure. Automated reopening after restore is outside this slice.

## Acceptance and limits

Local synthetic tests cover distinct customers, duplicate/concurrent provisioning, transaction rollback, reopened persistence, removed membership, guessed paths, verified-email/UserInfo checks, callback replay, CSRF and Host/Origin checks, custom-host serving and session isolation, one-use handoffs, domain removal/reassignment/expiry, private-address probe refusal, provider-record repair, v4 migration rollback/concurrent opens, and recovery closure. Browser checks exercise real rendered forms using disposable local identity/domain simulators.

Live acceptance still requires the actual backend origin, identity-provider client/registration policy, configured Render service/API credential, and a controlled customer domain. No local fake-provider result establishes deployed TLS, real DNS propagation, or production readiness. Billing and working inference are explicitly unavailable in this delivery.
