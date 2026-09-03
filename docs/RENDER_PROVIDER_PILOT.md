# Render external provider pilot

This profile connects hosted Okta login to governed OpenAI and Anthropic
traffic. It is an explicit `provider-pilot` mode. The existing `active` mode
continues to serve login and the administrator console while returning 503 for
inference, and `maintenance` remains the container default.

The first deployment is a controlled, single-region external pilot. It has no
availability SLA. Customer invitations, public distribution, and an SLA remain
separate decisions after the evidence gates below pass.

## Compute and data topology

The fixed envelope fits Render's 0.5 CPU / 512 MiB web service and minimizes
idle compute:

- Caddy owns the public HTTP/1.1 listener, streams without response buffering,
  disables retries, and holds no provider or database credential.
- One Python process accepts at most eight concurrent provider requests. A
  ninth connection is reserved for health and readiness. No worker process is
  duplicated through `WEB_CONCURRENCY`.
- Public request bodies are capped at 2,000,000 bytes, provider response
  headers at 16,000 bytes, and
  provider calls at 600 seconds. A client write can block for at most 45
  seconds and a connection for at most 630 seconds.
- A bounded PostgreSQL pool keeps one warm connection and permits four total.
  Each evidence transaction applies the restricted role, schema, and tenant
  context with `SET LOCAL`; pool state cannot carry a tenant into the next
  request.
- PostgreSQL stores usage, reservations, request attempts, provider latency,
  cancellation outcomes, and failover links. The persistent Render disk stores
  only the encrypted login/session database and its state binding.
- Responses and prompts are not retained. Provider-side background work and
  response storage remain disabled.

The main choke point is eight long streams. CPU-heavy JSON parsing, response
usage parsing, and secret inspection share half a CPU. Four short PostgreSQL
connections can queue behind concurrent accounting transactions. The session
database still makes this a single gateway instance, and the attached disk
prevents horizontal scaling and zero-downtime deploys. A process, disk, region,
or Render outage can interrupt active streams.

`GET /v1/gateway/reliability` gives each authenticated actor their own
content-free request, latency, cancellation, failover, worker-pressure, and
pool-pressure counters. A `member_admin` can inspect aggregate pressure at
`GET /v1/admin/operations`. Neither endpoint exposes prompts, responses,
credentials, provider request IDs, tenant names, or DSNs.

## Fixed provider contract

Copy
[`deploy/render/gateway/provider-profile.example.json`](../deploy/render/gateway/provider-profile.example.json)
to an operator-owned file and replace only reviewed tenant values. The strict
loader requires:

- the same public origin, Okta issuer, login client, state paths, master key,
  and ingress credential as the initialized hosted-login profile;
- Render source metadata for the exact `main` commit, the
  `Xpounder-com/hormuz` repository, a web service, 0.5 CPU, and one web worker;
  Render's `RENDER_CPU_COUNT` value may spell that fixed size as `0.50` or
  `0.5`;
- PostgreSQL schema `hormuz`, stable runtime role `hormuz_runtime`, and the
  fixed 1-to-4 connection pool;
- only the OpenAI Responses and Anthropic Messages upstreams;
- one primary and one secondary alias per protocol, with one same-protocol
  failover hop from primary to secondary;
- positive reviewed rates for uncached input, cache read, cache write, and
  output on all four routes;
- Codex and Claude Code as the only clients, explicit organization and actor
  spend caps, and a maximum output cap no larger than 32,768 tokens; and
- built-in secret inspection in `redact` or `deny` mode.

Failover is deliberately narrow. Hormuz retries only a provider 429 or 529,
only before any response byte reaches the client, and only once to the
configured alternate model on the same provider protocol. It never replays a
timeout, transport ambiguity, generic 5xx, partial stream, or downstream
cancellation. See [provider reliability](PROVIDER_RELIABILITY.md).

Model names and rates change. Confirm both model IDs against the provider
account and update the four rates from current provider pricing immediately
before qualification. Never commit the tenant profile.

## Secret boundary

The service uses these secret environment values:

| Name | Runtime consumer | Purpose |
| --- | --- | --- |
| `HORMUZ_INGRESS_CREDENTIAL` | Caddy and Python | Private loopback boundary |
| `HORMUZ_SESSION_MASTER_KEY` | Python | Encrypt hosted session secrets |
| `HORMUZ_OIDC_CLIENT_SECRET` | Python | Okta confidential client |
| `HORMUZ_OPENAI_PROVIDER_KEY` | Provider backend | OpenAI authorization |
| `HORMUZ_ANTHROPIC_PROVIDER_KEY` | Provider backend | Anthropic authorization |
| `HORMUZ_POSTGRES_DSN` | Provider backend | Restricted runtime login, internal URL |
| `HORMUZ_FAILOVER_REHEARSAL_KEY` | Provider backend | Protected deterministic qualification |
| `HORMUZ_POSTGRES_MIGRATION_DSN` | Maintenance command only | Direct migration login, internal URL |

All values must be distinct. The provider backend receives only the first seven
values plus validated Render metadata. Remove the migration DSN from the whole
service after maintenance; `provider-pilot` refuses to start while it is
nonempty because the supervisor and backend share a container UID. Keep the
owner DSN in an approved secret manager for future reviewed migrations.

Never put a credential in JSON, a command argument, logs, artifacts, Caddy, or
GitHub workflow inputs. The protected qualification workflow receives a
dedicated refresh token, rehearsal key, and Render deploy hook only through
environment secrets.

Render secret files appear as symlinks, while Hormuz deliberately accepts only
a regular, single-linked, non-writable provider profile. In maintenance, copy
the approved profile into
`/var/lib/hormuz/operator/hormuz-provider-runtime.json` with the guarded copy
procedure in the [Render gateway runbook](../deploy/render/gateway/README.md),
then set `HORMUZ_PROVIDER_CONFIG` to that path.

## First PostgreSQL deployment

Create a dedicated Render Postgres database in the same region as the gateway
and use only its internal host from the gateway. The Render-managed credentials
observed during qualification authenticated as rotating logins and started with
a shared owner role as `current_user`. Treat Render-managed credentials as
operator credentials for this profile: they fail the required direct-identity
and least-privilege checks and must not be used as either application DSN.

Keep one Render-managed internal URL in the operator secret manager and out of
the web-service environment. Through that connection, create two direct SQL
logins with independently generated passwords and these exact attributes:

- runtime: `LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT`
  `NOREPLICATION NOBYPASSRLS`;
- migration: `LOGIN NOSUPERUSER NOCREATEDB CREATEROLE NOINHERIT`
  `NOREPLICATION NOBYPASSRLS`.

Keep the Render-managed operator role as database owner and grant the direct
migration login only `CREATE` on that database. Create both direct logins through
one disposable `CREATEROLE` builder and drop that builder before bootstrap.
PostgreSQL 16 automatically makes a non-superuser creator an
`ADMIN=TRUE, INHERIT=FALSE, SET=FALSE` member of each role it creates; dropping
the builder removes those otherwise-unexpected edges from both application
logins. Do not create either login through a long-lived operator role. Bind
passwords as parameters; never put them in SQL text, command arguments, or shell
history. Before continuing, prove both DSNs report `session_user=current_user`,
the runtime attributes are exact, both direct logins have no memberships in
either direction, and the database owner is distinct from both application
logins.

Then perform these steps:

1. Keep the web service in `maintenance` and save the direct runtime internal
   URL as `HORMUZ_POSTGRES_DSN`.
2. Save the direct migration internal URL as
   `HORMUZ_POSTGRES_MIGRATION_DSN` only for the maintenance operation.
3. Inject the other provider secrets and prepare the private provider profile.
4. Run the idempotent bootstrap from the Render service Shell:

```sh
python -I -m hormuz.hosted \
  --config /var/lib/hormuz/operator/hormuz-runtime.json \
  --provider-config /var/lib/hormuz/operator/hormuz-provider-runtime.json \
  provider-bootstrap-postgres
```

The command requires distinct direct migration and runtime credentials. It creates
four fixed `NOLOGIN`, `NOINHERIT`, non-superuser authorization roles. On
PostgreSQL 16 it accepts only the migration owner's automatic
`ADMIN=TRUE, INHERIT=FALSE, SET=FALSE` creator edge; no other principal, role, or option
is permitted. It skips an unnecessary `ALTER ROLE` when the runtime login is
already `NOINHERIT`, grants only `hormuz_runtime`, applies all migrations as the
authenticated migration login, removes direct runtime-login and `PUBLIC` grants,
and verifies that the migration login owns every schema object and that every schema, table,
column, sequence, function, and owner-default ACL matches the exact
version-pinned privilege and grant-option boundary. It then proves runtime
access and RLS through the runtime DSN. Its output is content-free and
inference remains disabled. Safe completed work can be rerun.

For a later schema upgrade, temporarily restore the owner DSN while still in
maintenance and run `provider-migrate`. That command never creates roles or
relaxes the bootstrap boundary.

## Preflight and activation

Still in maintenance, run:

```sh
python -I -m hormuz.hosted \
  --config /var/lib/hormuz/operator/hormuz-runtime.json \
  --provider-config /var/lib/hormuz/operator/hormuz-provider-runtime.json \
  provider-check
```

The initialized login state must already contain at least one operator-created
managed organization. `provider-check` reads the exact organization IDs from
that server-local directory, revalidates `session_user` before any `SET ROLE`,
and proves the restricted PostgreSQL runtime path under each tenant's RLS
context. It rejects an owner or superuser DSN, startup-role impersonation,
unexpected memberships, ownership drift, or any unexpected ACL principal,
privilege, or grant option. An empty directory fails closed. The provider
process repeats the credential, ownership, and exact ACL checks and pins the
same tenant allowlist at startup. Creating
another managed organization therefore requires a maintenance preflight and a
fresh deployment before that organization's members can send inference
requests. Member, invitation, and session revocation continue to take effect
without widening
this tenant allowlist.

Require `provider_configuration_valid`, `postgresql_runtime_verified`, and a
pool maximum of four. Remove `HORMUZ_POSTGRES_MIGRATION_DSN`, deploy again in
maintenance, and repeat `provider-check` before changing mode.

Activation is manual: set `HORMUZ_HOSTED_MODE=provider-pilot`, deploy the exact
reviewed `main` commit, and keep auto-deploy disabled. Then run the protected
`External pilot deployment qualification` workflow twice in sequence:

1. `deployment` verifies HTTPS, exact Render service/source/compute identity,
   PostgreSQL readiness, the published support path, and denial of
   unauthenticated inference. Retain its exact artifact and run URL.
2. `qualification` binds a deploy hook to that service and commit, restarts the
   instance, authenticates the successful deployment run and its exact artifact,
   proves the encrypted session survived, runs non-streaming and streaming
   requests through all four aliases, exercises cancellation and one-hop
   failover, verifies latency and pressure counters, revokes the qualification
   session, and emits content-free evidence.

The qualification environment must require review. Pin its non-secret
`HORMUZ_GATEWAY_ORIGIN` and `HORMUZ_RENDER_SERVICE_ID` environment variables to
the approved service, and keep only `HORMUZ_EXTERNAL_PILOT_REFRESH_TOKEN`,
`HORMUZ_FAILOVER_REHEARSAL_KEY`, and `HORMUZ_RENDER_DEPLOY_HOOK_URL` as secrets.
The refresh token must belong to the dedicated qualification member. The
workflow rotates it, writes one governed attempt before the restart, and then
requires the same actor-scoped PostgreSQL counters to survive before it sends
the remaining qualification traffic.

The protected workflow is evidence, not activation authority. Do not invite a
customer until both runs pass and the signed-Mac gates in
[signed Mac pilot qualification](MACOS_PILOT_QUALIFICATION.md) are complete.

## Rollback and operating limits

Set the mode to `active` to preserve hosted login and the console while closing
all inference routes. Use `maintenance` for role work, schema changes, profile
repair, backup, or restore. Keep the last passing commit and notarized Mac
archive available for rollback.

Do not delete provider keys during an unresolved request. First close
admission, reconcile content-free request evidence, then rotate credentials
through a separate custody procedure. A failing PostgreSQL check makes
`/ready` fail and blocks startup; the gateway does not substitute SQLite for
durable provider evidence.

This single instance can support a bounded pilot and measure latency. Selling
an availability SLA requires at least shared session state, more than one
gateway instance, multi-region routing, database HA/recovery evidence, alerting,
and sustained load results beyond this profile.
