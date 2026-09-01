# Render provider pilot

This is the first deployable bridge between hosted login and governed model
traffic. It is a separate, explicit `provider-pilot` mode. The existing
`active` mode remains authentication staging and still returns 503 for every
inference route. Maintenance remains the container default.

The pilot is intended for one controlled paid customer evaluation. It is not a
production availability tier, a multi-region service, a cross-provider
failover implementation, or an SLA. It must not be activated on the current
staging service until the configuration, credentials, real-provider checks,
recovery drill, and customer scope are approved together.

## Compute envelope

The profile is deliberately sized for Render's 0.5 CPU / 512 MiB service:

- Caddy owns the only public listener and accepts HTTP/1.1 only. It removes
  spoofable control headers, disables retries and keepalive to the backend, and
  flushes response fragments without buffering a complete model response.
- The Python gateway listens on loopback and admits at most eight concurrent
  connections. Every request closes its connection. A stalled client socket is
  bounded to 45 seconds, and a connection cannot outlive the configured
  provider timeout plus 30 seconds, capped at 630 seconds.
- Caddy caps request bodies at `2MB`; Python independently caps them at 2 MiB.
  Response headers are capped at 16 KiB. Provider calls are capped at 600
  seconds.
- SQLite stores sessions, authorization, usage, reservations, provider timing,
  and failover evidence on one persistent disk. No background response storage
  or provider-side asynchronous work is permitted.
- The gateway loads exactly two provider credentials, one for each supported
  protocol. Caddy receives neither. The backend receives no unrelated service
  environment or ambient HTTP proxy setting.

The first saturation point is eight long-running streams. A ninth backend
connection is closed instead of allocating another thread. Large JSON parsing,
secret inspection, and usage parsing share the half CPU. SQLite serializes
writes, and one capacity failover creates two reservations, attempt records,
metric rows, and provider calls. The persistent disk prevents horizontal
scaling and zero-downtime deploys. A process, instance, disk, region, or Render
outage therefore interrupts this pilot. These are the main reasons it cannot be
sold as the availability product yet.

Render CPU, memory, restart, disk, and request-latency observations should be
reviewed after each controlled workload. The current code records content-free
per-attempt provider header, first-byte, and total timing, but it does not yet
export worker saturation or SQLite lock-wait metrics. Those two signals and a
shared PostgreSQL session/authorization store are gates for a horizontally
scalable service.

## Fixed configuration contract

Copy
[`deploy/render/gateway/provider-profile.example.json`](../deploy/render/gateway/provider-profile.example.json)
to a private operator file. The loader accepts it only when all of these
conditions hold:

- its public origin, OIDC issuer, login client, session settings, state paths,
  master key, and proxy credential match the already initialized hosted profile;
- there are no static or pre-mapped OIDC identities; access comes from the
  hosted team directory and revocable sessions;
- the only upstreams are `https://api.openai.com` and
  `https://api.anthropic.com`, using the fixed environment names below;
- each protocol has fixed primary and secondary aliases, and the primary has
  exactly one same-protocol failover hop to a different model;
- all four aliases have positive configured input/output rates so cost figures
  are explicit estimates rather than zeros;
- the organization policy allows only Codex and Claude Code, permits exactly
  those aliases, sets both organization and per-actor monthly spend caps, and
  caps output at no more than 32,768 tokens;
- built-in secret inspection remains in `redact` or `deny` mode; and
- PostgreSQL, static policy administrators, custody, portfolio, attribution,
  audit anchoring, custom secret sources, response storage, and background work
  remain outside this first single-node composition.

The alternate route is a model on the same provider protocol. Hormuz does not
switch an OpenAI request to Anthropic, or vice versa. It retries only a 429 or
529 response before response bytes reach the customer, with one alternate call
maximum. Transport ambiguity, timeouts, generic 5xx responses, partial streams,
and downstream cancellation never replay work. See
[`PROVIDER_RELIABILITY.md`](PROVIDER_RELIABILITY.md) for the exact evidence
contract.

The example contains placeholder model names and rate-card values. Replace them
with approved current model identifiers and prices during a reviewed customer
configuration change. Do not commit the resulting tenant profile.

## Secrets and process custody

Keep the original three hosted credentials and add only:

| Name | Consumer | Purpose |
| --- | --- | --- |
| `HORMUZ_OPENAI_PROVIDER_KEY` | Python backend | OpenAI upstream authorization |
| `HORMUZ_ANTHROPIC_PROVIDER_KEY` | Python backend | Anthropic upstream authorization |

All five credential values must be printable, nonempty, and distinct. Provider
keys must be 16 to 512 ASCII characters with no whitespace. Store them as
service-scoped secret environment values. Never place them in JSON, shell
arguments, logs, CI artifacts, or the Caddy configuration.

Render secret files are symlinks in the observed service, while this loader
requires a regular, single-linked, non-writable file. During maintenance, copy
the approved provider JSON into a new owner-only file such as
`/var/lib/hormuz/operator/hormuz-provider-runtime.json`, using the same guarded
copy pattern as the hosted profile. Set `HORMUZ_PROVIDER_CONFIG` to that private
path.

## Qualification sequence

Keep `HORMUZ_HOSTED_MODE=maintenance` while preparing the profile and secrets.
Run the content-free preflight against the exact paths:

```sh
python -I -m hormuz.hosted \
  --config /var/lib/hormuz/operator/hormuz-runtime.json \
  --provider-config /var/lib/hormuz/operator/hormuz-provider-runtime.json \
  provider-check
```

The command validates the complete provider envelope and the existing state
binding without making a provider request or enabling inference. It must return
`provider_configuration_valid: true`.

Before any customer invitation or traffic, require all of the following:

1. The reviewed image passes the disposable Render container verifier. Its
   `active` mode must still reject inference, and its `provider-pilot` mode must
   start with synthetic keys while an unauthenticated generation stops before
   provider egress.
2. A real IdP login, session refresh, administrator removal, encrypted backup,
   closed restore, reinvitation, and administrator regrant complete on the exact
   candidate revision.
3. Each configured real provider/model pair completes one non-streaming and one
   streaming request. Verify request count, timing rows, model rewriting, token
   accounting, configured-rate cost, cancellation behavior, and the one-hop
   capacity failover. Use synthetic content only.
4. A clean signed Mac installation logs in and drives the official Codex and
   Claude Code clients through the hosted origin. Confirm the documented 401
   refresh/retry behavior without duplicating provider work.
5. The operator records the paid service size, disk size, observed peak CPU and
   memory, longest stream, SQLite lock behavior, restart behavior, rollback
   revision, and customer spend caps.

Only after those checks should an operator set
`HORMUZ_HOSTED_MODE=provider-pilot` and deploy the same reviewed revision.
Changing the mode is a production traffic decision. It is separate from merging
this implementation and must remain manual with auto-deploy disabled.

Rollback sets the mode back to `active` to preserve login and console access
while returning every inference route to 503. Use `maintenance` for schema,
backup, restore, or profile repair work. Do not delete provider keys during an
incident until request/evidence reconciliation is complete; revoke or rotate
them through a separate credential-custody procedure.
