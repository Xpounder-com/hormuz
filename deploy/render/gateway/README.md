# Hosted authentication and provider pilot on Render

The default and `active` profiles exercise team login, native sessions and the
administrator console on a persistent single-node gateway. **`active` cannot
configure or forward model requests.** A separate, explicit `provider-pilot`
mode can compose that hosted identity state with the streaming/failover gateway
under a strict small-compute envelope. Its design and activation gates are in
the [Render provider pilot](../../../docs/RENDER_PROVIDER_PILOT.md). Neither mode
is a production hosted offering or availability SLA. The separate [HTTPS
preflight](../preflight/README.md) remains unchanged.

## Runtime boundary

Render supplies public TLS. Caddy listens on `0.0.0.0:$PORT`; the Python gateway
listens only on `127.0.0.1:8787`. Caddy replaces all incoming ingress credential
values with its dedicated injected secret. The backend independently checks the
loopback peer, credential and exact configured Host. Existing Origin, CSRF,
membership and session checks remain in force. In authentication staging both
layers refuse inference, portfolio, policy and custody routes. There are no
upstreams or static identities in that staging configuration. The separately
validated `provider-pilot` profile enables only its fixed inference routes.

The proxy receives only its port, temporary Caddy paths and ingress secret. The
authentication-staging backend receives only the three explicitly named
credentials below. The provider backend receives those three plus two provider
keys, the restricted PostgreSQL runtime DSN, and the rehearsal credential
documented in the provider-pilot guide. It also receives an allowlisted set of
non-secret Render deployment metadata. The PostgreSQL migration-owner DSN is
never passed to either backend. Neither inherits unrelated deployment secrets
or HTTP proxy settings. Caddy's admin
endpoint, configuration persistence, retries and upstream keepalive are disabled.
No secret is written into a configuration file. The processes share one UID;
this limits accidental environment inheritance, not a compromised process's OS
authority within the container.

The image runs as the named non-root `hormuz` account at UID 65532, GID 1000,
with no low-port file capability. Group 1000 permits reading Render's mounted
configuration file as described in
[Render's Docker secret-file guidance](https://render.com/docs/docker-secrets).
State directories are owner-only `0700`, and database/manifest files are `0600`.
The account has `/bin/sh` and a private `0700` home and `~/.ssh` under
`/home/hormuz` solely for Render's injected SSH/SFTP transport. The persistent
disk at `/var/lib/hormuz` does not cover that home, and the image installs and
runs no SSH server. Keep account-level operator SSH keys short-lived and remove
them immediately after a transfer drill.
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
necessary within the supervisor's budget. No paid inference runs in `active`.
The separate provider mode uses eight generation slots, one reserved liveness
connection, a 1-to-4 PostgreSQL pool, an at-most-2-MiB body cap and
streaming-safe timeouts; see its guide for compute limits and bottlenecks.

Application output contains fixed lifecycle/error codes only; child stdout and
stderr are suppressed. This makes private diagnostics deliberately limited.
Render's own edge logs, operator shell history and monitoring have separate
retention/access controls. Never print environment values, use shell tracing,
dump live Caddy configuration, or place credentials in command arguments.

## Private configuration and bootstrap

Copy [profile.example.json](profile.example.json) into a private operator file.
Only its five documented fields are accepted. Use an exact HTTPS public origin,
the registered issuer and confidential client ID, and an absolute state directory
under the persistent disk, normally `/var/lib/hormuz/private/state`. No credential belongs
in this JSON. Do not commit real tenant URLs, client IDs, subjects or email addresses.

Set the profile as the Render secret file `hormuz-hosted.json`, available at
`/etc/secrets/hormuz-hosted.json`. Set `HORMUZ_CONFIG` to
`/var/lib/hormuz/operator/hormuz-runtime.json`; prepare that private regular file
below before activation. Set the following as service-scoped secret
environment values through the protected Render configuration UI:

| Name | Required value |
| --- | --- |
| `HORMUZ_INGRESS_CREDENTIAL` | Independently generated random URL-safe secret, 43–128 characters |
| `HORMUZ_SESSION_MASTER_KEY` | Base64 encoding of 32 independently generated random bytes |
| `HORMUZ_OIDC_CLIENT_SECRET` | The confidential IdP application's client secret |

Retain the master key in an approved password/secret manager, separate from data
backups. The application cannot recover it. Neither it nor the IdP secret is sent
to Caddy. Do not inject provider keys while using authentication staging. The Dockerfile declares no secret build
arguments and its build-context allowlist excludes operator profiles and state.
Render translates environment variables into build arguments; never add `ARG`
instructions for these credentials. See
[Docker environment guidance](https://render.com/docs/docker).

Register the exact `/v1/auth/callback` and `/v1/admin/auth/callback` URLs at the
IdP. Require authorization code + PKCE, confidential `client_secret_basic`,
`form_post`, and only `openid email` scopes. Keep owner-only assignment and the
existing MFA policy during qualification. The app validates ID tokens for the
authentication event. For an invited first login only, it may use the ephemeral
access token once at the discovery-advertised UserInfo endpoint when the ID token
omits an email scope claim. UserInfo must return the signed token's subject and
`email_verified: true`; the provider token is never stored. The app does not
request `offline_access`. A successful real code exchange is a separate
deployment gate.

Deploy **maintenance first**, keeping `HORMUZ_HOSTED_MODE=maintenance` (the
default), manual deploys and no public enrollment invitations. Maintenance needs
no credential or initialized database: `/health` is 200 with status `maintenance`,
while `/ready`, console, callbacks and every model route are 503. Do not interpret
its health response as gateway readiness.

Actual Render qualification found a root-owned, group-1000 disk mount with mode
`0775`, and a secret-file symlink whose regular target is root-owned `0640`.
The strict loader refuses symlinks, and the state lock refuses a foreign-owned
or group-writable immediate parent. Keep those checks. In the service Shell,
prepare new owner-only directories and a regular copy of the approved profile:

```sh
python -I - <<'PY'
import os
import stat
from pathlib import Path

os.umask(0o077)
with open("/etc/secrets/hormuz-hosted.json", "rb") as source:
    info = os.fstat(source.fileno())
    if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
        raise RuntimeError("profile_source_unsafe")
    data = source.read(16385)
    if len(data) > 16384:
        raise RuntimeError("profile_source_too_large")
for name in ("operator", "private"):
    Path("/var/lib/hormuz", name).mkdir(mode=0o700)
target = Path("/var/lib/hormuz/operator/hormuz-runtime.json")
fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
with os.fdopen(fd, "wb") as output:
    output.write(data)
    output.flush()
    os.fsync(output.fileno())
parent_fd = os.open(target.parent, os.O_RDONLY | os.O_DIRECTORY)
os.fsync(parent_fd)
os.close(parent_fd)
print("Private profile prepared; state is not initialized.")
PY
```

This one-time preparation refuses existing directories/files. Inspect any partial
attempt instead of overwriting it. It copies only the five-field configuration,
not environment credentials. Profile changes are explicit operator work: update
both the saved Render profile and the private runtime copy, review state bindings,
and check before deploying. A Render secret-file update alone does not replace
the private copy. No application code or file-safety check is bypassed.

After preparation, initialize explicitly. The path is supplied here even if the
new `HORMUZ_CONFIG` environment setting has not yet reached the running shell:

```sh
python -I -m hormuz.hosted --config /var/lib/hormuz/operator/hormuz-runtime.json initialize
python -I -m hormuz.hosted --config /var/lib/hormuz/operator/hormuz-runtime.json check
python -I -m hormuz.hosted --config /var/lib/hormuz/operator/hormuz-runtime.json team organization create \
  --organization evaluation --name Evaluation \
  --issuer https://identity.example.com/oauth2/default
python -I -m hormuz.hosted --config /var/lib/hormuz/operator/hormuz-runtime.json team create \
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

## Explicit usage-schema upgrade

The hosted process never changes a durable schema during startup. A reviewed
image that requires a newer usage-evidence schema therefore refuses active mode
until an operator performs an offline migration. Keep the last live deployment
available and record its exact source revision before starting.

Set `HORMUZ_HOSTED_MODE=maintenance`, deploy the target image, and require
`/health` to report maintenance while `/ready`, console, callbacks and model
routes remain unavailable. Then run the target image's explicit migration from
the service Shell with a new absolute snapshot directory on the persistent disk:

```sh
python -I -m hormuz.hosted --config /var/lib/hormuz/operator/hormuz-runtime.json migrate \
  --snapshot-directory /var/lib/hormuz/private/pre-usage-migration-001
python -I -m hormuz.hosted --config /var/lib/hormuz/operator/hormuz-runtime.json check
```

`migrate` refuses active mode, an unavailable lifecycle lock, an existing
snapshot destination, malformed or newer state, and a store already at the
target schema. Under one exclusive hosted-state lock it validates the bound
profile, exact current session schema and complete older usage ledger; takes one
SQLite-consistent snapshot of both databases; migrates the usage ledger in a
transaction; and rechecks the complete current state. Its output contains only
the source and target schema numbers plus `snapshot_created: true`. It performs
no provider call and does not enable inference.

Keep maintenance active if any step fails. A migration error leaves the
pre-migration snapshot for investigation and SQLite rolls back the uncommitted
schema transaction. The snapshot is plaintext and on the same disk, so it is a
rollback aid rather than a disaster-recovery copy. For a durable off-disk copy,
use the encrypted export procedure below with the prior compatible image before
the migration. Restoring an older snapshot requires that prior compatible image
and the conservative recovery path, which revokes restored authority rather than
promising login continuity. Only after `check` succeeds should the operator set
`HORMUZ_HOSTED_MODE=active` and deploy the same reviewed target revision.

## Offline snapshot, encrypted export and conservative restore

Switch to maintenance and confirm callbacks/console return 503 before lifecycle
work. The backend and operator commands hold shared lifecycle locks; snapshots
and restores require an exclusive lock. Snapshots also reserve writes on both
SQLite databases before using SQLite backup readers. Copying live `.sqlite3`
files or using a platform disk snapshot alone is not this procedure.

The low-level `snapshot --output-directory ...` command remains available for an
isolated same-disk check. Its output is a plaintext owner-only directory and does
not protect against disk loss. For an off-disk copy, create a separate 32-byte
backup key on an operator workstation and retain it outside Render and apart from
the session master key and archive. The command below creates the key without
printing it:

```sh
umask 077
python3 - <<'PY'
import base64
import os
import secrets

fd = os.open("hormuz-backup.key", os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
with os.fdopen(fd, "wb") as output:
    output.write(base64.b64encode(secrets.token_bytes(32)) + b"\n")
    output.flush()
    os.fsync(output.fileno())
PY
```

Transfer that owner-only key temporarily to the running service through the
approved SSH/SFTP path, then run in the service Shell while maintenance remains
closed:

```sh
python -I -m hormuz.hosted --config /var/lib/hormuz/operator/hormuz-runtime.json backup-export \
  --key-file /tmp/hormuz-backup.key \
  --output-file /var/lib/hormuz/private/hormuz-offsite-001.hzb
```

`backup-export` refuses a key equal to the session master key, an existing or
symlinked output, unsafe permissions, an active gateway, and paths inside the
live state directory. It takes one SQLite-consistent snapshot and streams an
uncompressed AES-256-GCM archive in 1 MiB chunks. The command has bounded memory,
does no background work, and reports only the ciphertext byte count and SHA-256
digest. The temporary plaintext snapshot is private and removed on normal exit;
an interrupted process can leave a hidden owner-only staging directory that the
operator must inspect and remove while maintenance remains closed.

Download the `.hzb` file using Render's documented
[SCP/SFTP transfer](https://render.com/docs/disks#transferring-files). Verify the
download on the operator workstation without the service profile or live session
credentials:

```sh
python -I -m hormuz.hosted backup-verify \
  --key-file ./hormuz-backup.key \
  --archive-file ./hormuz-offsite-001.hzb
```

Require the export and local-verification SHA-256 values to match. Verification
performs a full authentication pass before parsing, then checks the fixed file
set, declared sizes/digests, inner snapshot linkage, JSON framing and SQLite file
headers without writing decrypted state. Keep at least one verified archive and
its distinct key in separate approved off-disk locations. Delete the temporary
service-side key and archive only after that retention check. Never upload a raw
snapshot, encrypted backup, invitation file or key as a CI artifact.

For recovery, keep maintenance active, preserve damaged/original state for
investigation, and point a private profile at a **new, absent** state directory.
Preserve the public origin, issuer, client ID and session master key. Do not
rotate the master key to restore a managed directory: recipient hashes also
depend on it. Run against the transferred encrypted archive:

```sh
python -I -m hormuz.hosted --config /var/lib/hormuz/operator/hormuz-restored.json backup-restore \
  --key-file /tmp/hormuz-backup.key \
  --archive-file /tmp/hormuz-offsite-001.hzb
python -I -m hormuz.hosted --config /var/lib/hormuz/operator/hormuz-restored.json recovery-check
```

Archive restore authenticates before writing plaintext, verifies the original
keyed configuration binding and database schemas, and refuses an existing
destination. It disables **every restored membership**, revokes native sessions,
pending enrollments/invitations, console grants/sessions and pending console
flows, then requires a zero count for each authority class before writing the
final marker. `recovery-check` repeats those content-free counts under an offline
lock, while preserving organizations, teams, recipient hashes and stable subject
bindings. The activatable marker is written last. Interrupted restoration stays
closed, but a killed restore can leave a hidden owner-only decrypted staging
directory beside the destination; inspect and remove it before leaving
maintenance. Reinvite only currently approved members, require new login and
explicitly regrant administrator roles. No removed user's old credential regains
authority just because it appears in an old snapshot. Point `HORMUZ_CONFIG` at
the reviewed restored profile before switching the service back to active mode.

This deliberately sacrifices login continuity to avoid resurrecting stale access.
It does not restore changes newer than the backup, implement online key migration,
or replace operator access review. Render also warns against treating whole-disk
snapshots as custom-database recovery; its automatic daily snapshot is therefore
not a substitute for this SQLite-consistent archive. A disk is accessible only to
its attached runtime, not builds or one-off jobs, so both export and restore run
from the service Shell. See [Render's disk recovery and access limits](https://render.com/docs/disks).

The least-risk fresh-disk qualification uses a temporary second maintenance-only
service with the same reviewed image and a new smallest disk. Restore the verified
archive there with the original origin/issuer/client/master-key binding, run
`check`, and confirm all memberships, grants, native/console sessions, pending
flows and invitations remain closed. Keep that service unreachable for customer
traffic and delete it after retaining content-free evidence. This proves recovery
onto an independent Render disk; it does not prove public hostname cutover. Render
bills each paid instance and disk capacity, prorated by active time, so review the
concrete quote immediately before creating the temporary resources. Deleting or
restoring over the live disk remains destructive and is not part of this drill.

## Verification and remaining gates

```sh
python -m unittest tests.test_hosted_backup tests.test_hosted_state tests.test_hosted_http -v
docker build --platform linux/amd64 -f deploy/render/gateway/Dockerfile \
  -t hormuz-hosted-staging:development .
python tools/verify_render_gateway.py --image hormuz-hosted-staging:development \
  --output /private/staging-check.json
```

The tests use synthetic identities and disposable volumes. They check uninitialized
startup, private ingress, framing/connection limits, closed provider routes,
credential inheritance, Render-style file permissions, restart/removal behavior,
encrypted export/authentication, stale restore and shutdown. The container verifier
deletes only its own objects and emits content-free results. CI builds this profile
separately from release images and runs no cloud deployment.

Real public HTTPS/Okta code exchange, Safari cookie behavior and Mac Keychain login
have separate staging evidence. The provider implementation now has strict
PostgreSQL bootstrap, deployment, restart, streaming, latency, cancellation and
failover qualification workflows. Those workflows still require successful
protected runs against the exact live candidate. Signed client operations on
clean Apple Silicon and Intel machines, independent review, and external
onboarding also remain separate gates. Local tests do not increase
external-onboarding counts or establish pilot readiness.
