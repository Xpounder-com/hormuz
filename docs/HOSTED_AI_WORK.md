# Explicit integrated hosted AI Work

`HORMUZ_HOSTED_MODE=ai-work` serves the existing full gateway composition: workspace
login, administrator console, native application grants, policy/routing, AI Work
receipts, optional paid access and operator-enrolled signed workflow sources. It
does not replace the provider-free `active`/`workspace` modes or the fixed
PostgreSQL `provider-pilot` profile. `maintenance` remains the default.

Use the [full owner JSON example](../deploy/render/gateway/ai-work-profile.example.json)
as a template, not a qualified account configuration. Replace the origin, issuer,
login client, models, rates and optional live billing price with reviewed values.
The example rates are placeholders, not provider-confirmed costs. Existing
provider account/mode/region/context pricing qualification still applies. Keep
credentials out of JSON; it holds only environment-reference names.

The profile requires external TLS proxy ingress from `127.0.0.1/32`, backend
`127.0.0.1:8787`, HTTPS login/origin, enabled workspace/console/onboarding/AI Work,
and local SQLite control. All owned stores live together under the persistent
state directory: `usage.sqlite3` (including portfolio/attribution metadata),
`sessions.sqlite3`, `hormuz-work.sqlite3`, and, when billing is configured,
`hormuz-work.billing.sqlite3`. On Render the paths are
`/var/lib/hormuz/private/state` under trusted parent `/var/lib/hormuz`; one worker
and the declared generated HTTPS origin must match Render metadata. External
custody, provider envelopes, external audit anchors and PostgreSQL adapters are
refused in this mode. Existing PostgreSQL recovery is a separate contract; never
convert the current pilot or treat its dormant SQLite usage file as a live backup.

The profile file must be a regular single-link file owned by the container UID or
root, at most 64 KiB and not writable by group/others. A root-owned Render secret
file readable by GID 1000 is supported. State and lock files retain their existing
owner-only permission, parent-chain and identity-binding checks.

The full profile is structurally validated before credential selection. The
backend receives only configured provider keys, static tokens if used, dedicated
ingress/master/login secrets, optional billing/domain/detector secrets, all
configured signed-source rotation references and bounded deployment metadata.
Process-control, proxy, AWS and migration credentials cannot be credential refs.
Caddy receives only its port/temporary paths and dedicated ingress secret. Missing
credentials fail before a listener. If a billing price is configured, both reviewed
live-shape API and webhook credentials are required; test keys do not qualify paid
delivery. Account, price, webhook and actual payment remain separate live gates.

Prepare a private full JSON at `HORMUZ_CONFIG`, keep the service in maintenance,
and explicitly initialize all stores once:

```sh
python -I -m hormuz.hosted --config /etc/secrets/hormuz-ai-work.json --ai-work-profile initialize
python -I -m hormuz.hosted --config /etc/secrets/hormuz-ai-work.json --ai-work-profile check
```

The `--ai-work-profile` selector is only for offline owner lifecycle operations.
It cannot start inference. Do not rerun initialization over an existing or partial
directory. Source enrollment, users, allowances and payment are explicit existing
operator/customer actions. Initialization builds all stores in a private sibling
directory and publishes the checked, closed set together; normal failures clean
only that attempt's known private entries and allow a fresh retry. An abrupt
process kill can leave an empty reserved destination or private staging directory.
Keep maintenance closed and have the owner inspect that residue before explicit
removal/retry; serving never repairs or removes it. Initialization grants none of
the enrollment, user or payment authorities.
Before activation,
register the exact native `/v1/auth/callback`, console `/v1/admin/auth/callback`
and workspace `/v1/workspaces/auth/callback` origins at the reviewed IdP. Preserve
its PKCE/confidential-client/form-post/email/MFA requirements. Optional custom
domains use existing verified registry/ownership/TLS checks; the generated address
comes first.

After the operator's identity, disk and paid-hosting gates are met, explicitly set
`HORMUZ_HOSTED_MODE=ai-work`, set `HORMUZ_CONFIG` to that private JSON, and start
the image's existing `serve` entrypoint. Never resume or provision paid hosting
before verified customer payment. Startup checks the marker and all store files;
restart cannot reinitialize missing state. The supervisor starts the backend,
checks readiness with at most four one-second attempts and 0.5/1/2-second backoff
plus at most 100 milliseconds of jitter per retry,
then starts Caddy. Failure cleans up every owned child.

The proxy forwards only supported paths, strips forged caller identity and
forwarding headers, preserves signed connector headers, and preserves the exact
`Idempotency-Key` required by portfolio mutations. The backend rechecks framing,
private-hop credential, canonical/registered Host and exact route shapes before
dispatch. Existing tenant/owner, session, Origin/CSRF, signature, budget and paid
checks remain authoritative. There are eight provider slots and nine connection
slots, finite deadlines and zero proxy retries. Shutdown stops admission first
and uses the existing finite child drain/termination budget.

The `hormuz.hosted-ai-work` health response describes liveness and this SQLite
single-node contract. Readiness additionally verifies marker, stores and drain
state. Reported source/Render metadata is not independent image attestation;
health does not prove login, enrolled events, provider quality/cost, billing,
customer adoption, HA or an SLA.

Stop serving and keep maintenance closed before recovery. Use the same profile
with `--ai-work-profile snapshot`, `backup-export`, `backup-verify`, `backup-restore`
and `recovery-check`, with the existing owner-only backup-key/output arguments
documented in [AI Work operations](AI_WORK_OPERATIONS.md). An active owner refuses
backup. The archive coordinates all configured stores; restored sessions,
memberships, work, billing and workflow authority remain closed and unknown holds
remain reserved. Keep the archive, backup key and session master key separately.
Signed native distribution, clean-machine installation and live provider/customer
checks remain independent of this serving-mode implementation.
