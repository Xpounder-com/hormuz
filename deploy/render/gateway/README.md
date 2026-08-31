# Hosted authentication staging on Render

This opt-in profile exercises team login, native sessions and the administrator
console on a persistent single-node gateway. **It cannot configure or forward
model requests.** It is not the released runtime, a production hosted offering,
an availability SLA, or the streaming/failover implementation. The separate
[HTTPS preflight](../preflight/README.md) remains unchanged.

## Runtime boundary

Render supplies public TLS. Caddy listens on `0.0.0.0:$PORT`; the Python gateway
listens only on `127.0.0.1:8787`. Caddy replaces all incoming ingress credential
values with its dedicated injected secret. The backend independently checks the
loopback peer, credential and exact configured Host. Existing Origin, CSRF,
membership and session checks remain in force. Both layers refuse inference,
portfolio, policy and custody routes. There are no upstreams or static identities
in the staging configuration.

The proxy receives only its port, temporary Caddy paths and ingress secret. The
backend receives only the three explicitly named credentials below. Neither
inherits unrelated deployment secrets or HTTP proxy settings. Caddy's admin
endpoint, configuration persistence, retries and upstream keepalive are disabled.
No secret is written into its configuration file. The processes share one UID;
this limits accidental environment inheritance, not a compromised process's OS
authority within the container.

The image runs as UID 65532, GID 1000, with no low-port file capability. Group 1000
permits reading Render's mounted configuration file as described in
[Render's Docker secret-file guidance](https://render.com/docs/docker-secrets).
State directories are owner-only `0700`, and database/manifest files are `0600`.
Local verification additionally enforces a read-only root filesystem, no Linux
capabilities, no new privileges, 512 MiB memory and a 128-task limit. These local
Docker flags are not a claim about Render's platform-level container settings.

Headers and bodies are limited to 16 KiB at the proxy; header/body reads have
five-second limits. The backend admits at most 32 connections and rejects
ambiguous framing. Each connection closes after one request, with five-second
socket waits and a 30-second maximum connection lifetime. IdP network operations
retain the broker's bounded response sizes and socket timeouts; slow IdP work can
still occupy a worker after its client disconnects. These are staging resource
limits, not a distributed abuse-control or total IdP-work deadline guarantee.
Shutdown stops Caddy first, then drains the backend, with forced termination if
necessary within the supervisor's budget. No paid inference runs in this profile.

Application output contains fixed lifecycle/error codes only; child stdout and
stderr are suppressed. This makes private diagnostics deliberately limited.
Render's own edge logs, operator shell history and monitoring have separate
retention/access controls. Never print environment values, use shell tracing,
dump live Caddy configuration, or place credentials in command arguments.

## Private configuration and bootstrap

Copy [profile.example.json](profile.example.json) into a private operator file.
Only its five documented fields are accepted. Use an exact HTTPS public origin,
the registered issuer and confidential client ID, and an absolute state directory
under the persistent disk, normally `/var/lib/hormuz/state`. No credential belongs
in this JSON. Do not commit real tenant URLs, client IDs, subjects or email addresses.

Set the profile as the Render secret file `hormuz-hosted.json`, available at
`/etc/secrets/hormuz-hosted.json`. Set the following as service-scoped secret
environment values through the protected Render configuration UI:

| Name | Required value |
| --- | --- |
| `HORMUZ_INGRESS_CREDENTIAL` | Independently generated random URL-safe secret, 43–128 characters |
| `HORMUZ_SESSION_MASTER_KEY` | Base64 encoding of 32 independently generated random bytes |
| `HORMUZ_OIDC_CLIENT_SECRET` | The confidential IdP application's client secret |

Retain the master key in an approved password/secret manager, separate from data
backups. The application cannot recover it. Neither it nor the IdP secret is sent
to Caddy. Do not inject provider keys. The Dockerfile declares no secret build
arguments and its build-context allowlist excludes operator profiles and state.
Render translates environment variables into build arguments; never add `ARG`
instructions for these credentials. See
[Docker environment guidance](https://render.com/docs/docker).

Register the exact `/v1/auth/callback` and `/v1/admin/auth/callback` URLs at the
IdP. Require authorization code + PKCE, confidential `client_secret_basic`,
`form_post`, and only `openid email` scopes. Keep owner-only assignment and the
existing MFA policy during qualification. The app consumes ID tokens for verified
identity, never stores IdP access/refresh tokens, and does not request
`offline_access`. A successful real code exchange is a separate deployment gate.

Deploy **maintenance first**, keeping `HORMUZ_HOSTED_MODE=maintenance` (the
default), manual deploys and no public enrollment invitations. Maintenance needs
no credential or initialized database: `/health` is 200 with status `maintenance`,
while `/ready`, console, callbacks and every model route are 503. Do not interpret
its health response as gateway readiness.

After mounting the disk and injecting private configuration, use the Render
service's Shell to initialize explicitly:

```sh
python -I -m hormuz.hosted initialize
python -I -m hormuz.hosted check
python -I -m hormuz.hosted team organization create \
  --organization evaluation --name Evaluation \
  --issuer https://identity.example.com/oauth2/default
python -I -m hormuz.hosted team create \
  --organization evaluation --team evaluation-eng --name Engineering
```

The initializer refuses any existing or partial state directory. It creates no
organization, user, invitation or administrator automatically. Operator team
commands require already initialized state. Issue invitations into new private
files using `python -I -m hormuz.hosted team invite --help`; privately deliver the
file manually. After an invited member completes login, grant the existing console
role with `python -I -m hormuz.hosted team administrators grant --help`. The scope,
recipient, reinvitation and removal rules remain those of
[team onboarding](../../../docs/TEAM_ONBOARDING.md) and the
[administrator console](../../../docs/ADMIN_CONSOLE_LOCAL.md).

Only then set `HORMUZ_HOSTED_MODE=active` and deploy the same reviewed image.
Startup refuses missing/corrupt state, changed key/origin/issuer/client binding,
unsafe permissions and a mismatched session schema. It never reseeds a missing
disk or silently upgrades an old session schema. `/health` and `/ready` then verify
local state; they do not prove IdP availability or successful end-user login.

## Persistent disk and Render settings

The proposed single-service setup uses a paid compute instance and a persistent
disk mounted at `/var/lib/hormuz`. Free services cannot attach a persistent disk.
Review the live monthly compute/disk quote before enabling paid resources; the
local code and tests do not purchase or deploy anything.

Set Dockerfile path `deploy/render/gateway/Dockerfile`, repository-root build
context, health-check path `/health`, one instance, and auto-deploys off. Keep the
public origin unchanged after initialization; do not add another custom domain
without planning the issuer/callback, Host and state-binding implications.
[Render health checks](https://render.com/docs/health-checks) use a verified custom
domain as Host if one exists, otherwise the service's `onrender.com` subdomain.

A Render disk is available only at runtime, so initialization and snapshots must
run in the service Shell, **not a build, pre-deploy command or one-off job**.
Only files under the mount survive restarts/deploys. The disk prevents horizontal
scaling and zero-downtime deploys. These are explicit limitations of this staging
profile, not evidence for a latency/availability product promise. See
[persistent disk limitations](https://render.com/docs/disks).

## Offline snapshot and conservative restore

Switch to maintenance and confirm callbacks/console return 503 before lifecycle
work. The backend and operator commands hold shared lifecycle locks; snapshots
and restores require an exclusive lock. Snapshots also reserve writes on both
SQLite databases before using SQLite backup readers. Copying live `.sqlite3`
files or using a platform disk snapshot alone is not this procedure.

```sh
python -I -m hormuz.hosted snapshot \
  --output-directory /var/lib/hormuz/backup-001
```

The output is a new owner-only directory containing the two consistent SQLite
files, initialization marker and a keyed manifest of their digests. It contains
sensitive identity metadata and is **not an encrypted export**. Encrypt and
transfer it through an approved private backup channel before relying on it for
disk-loss recovery. A copy on the same disk does not protect against disk loss.
Never upload snapshots, invitation files or keys as CI artifacts.

For recovery, keep maintenance active, preserve the damaged/original state for
investigation, and point a private profile at a **new, absent** state directory
on the persistent disk. Preserve the public origin, issuer, client ID and master
key. Do not rotate the master key to restore a managed directory: recipient hashes
also depend on it. Run:

```sh
python -I -m hormuz.hosted --config /etc/secrets/hormuz-restored.json restore \
  --snapshot-directory /var/lib/hormuz/backup-001
python -I -m hormuz.hosted --config /etc/secrets/hormuz-restored.json check
```

Restore verifies keyed bindings/digests and refuses unexpected sidecar files or an
existing destination. It disables **every restored membership**, revokes native
sessions, pending enrollments/invitations, console grants/sessions and pending
console flows, while preserving organizations, teams, recipient hashes and stable
subject bindings. The activatable marker is written last. Interrupted restoration
stays closed. Reinvite only currently approved members, require new login and
explicitly regrant administrator roles. No removed user's old credential regains
authority just because it appears in an old snapshot. Point `HORMUZ_CONFIG` at
the reviewed restored profile before switching the service back to active mode.

This deliberately sacrifices login continuity to avoid resurrecting stale access.
It does not restore changes newer than the backup, implement online key migration,
or replace operator access review. Remote encrypted backup transfer, actual Render
disk replacement and recovery timing must still be qualified before a customer pilot.

## Verification and remaining gates

```sh
python -m unittest tests.test_hosted_state tests.test_hosted_http -v
docker build --platform linux/amd64 -f deploy/render/gateway/Dockerfile \
  -t hormuz-hosted-staging:development .
python tools/verify_render_gateway.py --image hormuz-hosted-staging:development \
  --output /private/staging-check.json
```

The tests use synthetic identities and disposable volumes. They check uninitialized
startup, private ingress, framing/connection limits, closed provider routes,
credential inheritance, Render-style file permissions, restart/removal behavior,
stale restore and shutdown. The container verifier deletes only its own objects
and emits content-free results. CI builds this profile separately from release
images and runs no cloud deployment.

Real public HTTPS/Okta code exchange, Safari cookie behavior, Mac Keychain login,
an actual Render restart/disk recovery, provider streaming/failover qualification,
signed client distribution and independent onboarding remain open. These tests do
not increase external-onboarding counts or establish release/pilot readiness.
