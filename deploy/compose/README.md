# Single-VM Docker Compose reference

This directory is Hormuz's first simple deployment profile: one exact signed
Hormuz OCI digest and one exact PostgreSQL digest on a customer-controlled
`linux/amd64` VM. It is intended for local use, evaluation, and pilots.

It is **not** a production certification or enterprise HA reference. One VM
and one gateway can stop together. This profile makes no claim of high
availability, failure-domain isolation, zero-downtime upgrades, production
backup/PITR, disaster recovery, or certification of Linux, Docker,
PostgreSQL, customer TLS, networking, or operations. Multi-replica customers
belong on the separately gated Kubernetes/Helm enterprise profile.

## Contract

The product artifact is the signed OCI digest, not this registry or Compose
file. The profile pins:

```text
ghcr.io/xpounder-com/hormuz@sha256:1bbcca3490a7a5b004a880f42e8250acb91ce566a9c59f3263d7b279568efb5a
postgres@sha256:57c72fd2a128e416c7fcc499958864df5301e940bca0a56f58fddf30ffc07777
```

Verify the Hormuz signature and attestations using the exact workflow identity
in [OCI.md](../../docs/OCI.md#protected-release-workflow) before deployment.
The wrapper refuses operational commands unless the Docker daemon is native
Linux AMD64. `config` remains available elsewhere for review, but rendering a
model does not prove that it can run.

The bundled profile has exactly two long-running services:

```text
customer TLS proxy on the VM
             |
             | 127.0.0.1:8787 + private-hop credential
             v
       one Hormuz gateway  -------- explicit provider/IdP/custody egress
             |
             | private internal Compose network
             v
       one PostgreSQL service ------ persistent named volume
```

PostgreSQL publishes no host port. The gateway is numeric non-root, has a
read-only root filesystem, drops all capabilities, enables
`no-new-privileges`, uses bounded CPU/memory/PIDs, and exposes only the
loopback-bound gateway port. `/health` and `/ready` are checked through the
authenticated backend-hop contract. The gateway receives the restricted
runtime DSN but never mounts the migration DSN. The one-shot migration/doctor
container receives only the two database DSNs; it cannot read employee,
provider, or ingress credentials.

## Requirements

- a customer-controlled Linux AMD64 VM;
- Docker Engine with Compose 2.24.4 or newer;
- the exact source/release checkout containing this versioned profile;
- enough durable storage for PostgreSQL and operator-managed backups;
- customer-controlled TLS ingress on the same host or a network-restricted
  private path;
- company OpenAI and/or Anthropic credentials for real provider traffic.

The profile reserves `172.30.10.0/24` and `172.30.11.0/24`. Change both the
Compose networks and the matching `trusted_proxy_cidrs` together if they
conflict with customer networks. The proof overlay also reserves
`172.30.12.0/24` but is never part of normal operation.

## First start

From a clean release checkout:

```bash
./deploy/compose/hormuz-compose prepare
./deploy/compose/hormuz-compose config
./deploy/compose/hormuz-compose pull
./deploy/compose/hormuz-compose migrate
./deploy/compose/hormuz-compose doctor
./deploy/compose/hormuz-compose up
./deploy/compose/hormuz-compose ps
```

`prepare` creates `deploy/compose/runtime` with mode `0700`, a strict example
configuration, generated pilot credentials, and two obvious provider
placeholders. The configuration and secret files use mode `0640` inside that
host-only directory. Compose gives the fixed non-root Hormuz processes one
supplemental numeric group that can read their mounts; it gives them no write
permission. PostgreSQL's bounded root entrypoint copies only its two
service-scoped bootstrap inputs into a private tmpfs as `postgres:postgres`
mode `0400`, then delegates to the pinned official entrypoint. The wrapper
derives the Hormuz process group from the invoking operator unless
`HORMUZ_SECRET_GID` is explicitly set. It never overwrites an existing runtime
directory.
Before `migrate` or `up`, use a protected editor or secret-delivery process to
replace these exact mode-`0640` files without placing values in a command,
shell history, URL, configuration JSON, or Compose environment:

```text
deploy/compose/runtime/secrets/openai-api-key
deploy/compose/runtime/secrets/anthropic-api-key
```

Keep an unused provider file at its non-empty placeholder if that provider is
not selected by policy. Preserve mode `0640`, the original group, and the
`0700` parent directory after each replacement. Review `runtime/hormuz.json`;
it contains environment variable names and policy, never secret values. The
runtime directory is ignored by Git.

Compose local secrets are protected host files mounted read-only into the
container. File-backed Compose secrets preserve host ownership rather than
remapping it, so changing the `0640` mode, removing the Hormuz supplemental
group, or bypassing PostgreSQL's private bootstrap copy breaks the reference
security model. They are not an encrypted secret manager. Root on the VM
remains a trusted operator boundary.
The mounted launcher reads them inside the container process so values do not
appear in image layers, rendered Compose output, Docker commands, container
configuration, probes, or intended logs. See Docker's
[file-backed secret permission boundary](https://docs.docker.com/reference/compose-file/services/#secrets).

## Customer-controlled TLS

Hormuz deliberately does not obtain or manage public certificates. The
default publication is `127.0.0.1:8787`; do not change it to a public bind.
Place the customer reverse proxy on the host, terminate HTTPS there, strip any
client-supplied `X-Hormuz-Ingress-Credential`, and inject exactly one value
from the protected `hormuz-ingress-credential` file on the backend hop. Route
both health probes through that authenticated hop. See
[DEPLOYMENT.md](../../docs/DEPLOYMENT.md).

The Compose egress bridge makes required provider, IdP, and optional custody
destinations reachable but is not a destination firewall. Restrict outbound
DNS/IP destinations with customer-controlled host or network policy. Only the
gateway and one-shot operator container join egress; PostgreSQL remains on the
internal database network.

## Configuration replacement and rollback

Hormuz validates the complete configuration before listener startup. Validate
a replacement with `doctor`, then restart the gateway so it reads the new
immutable snapshot:

```bash
./deploy/compose/hormuz-compose doctor
./deploy/compose/hormuz-compose restart
```

Keep the prior protected configuration. Rollback means restoring those exact
reviewed bytes and restarting again. This is a single-replica interruption,
not a zero-downtime rollout. A request admitted before shutdown may finish
during the configured 11-minute grace period; a force-kill can still interrupt
streaming.

## Backup and restore verification

The bundled pilot provides a protected logical-backup convenience command:

```bash
./deploy/compose/hormuz-compose backup
./deploy/compose/hormuz-compose restore-verify \
  deploy/compose/runtime/backups/EXACT-BACKUP-NAME.dump
```

`backup` refuses to overwrite a file and prints its SHA-256 digest.
`restore-verify` restores into a temporary database, verifies the complete
migration ledger, representative least-privilege role grants, effective
runtime-role access, and the snapshot's metadata-only usage-event count, then
drops the temporary database. It does not compare an older snapshot with the
mutable live event count. This proves one bounded logical restore; it does not
establish scheduled backups, encryption, off-host durability, PITR, RPO/RTO, DR, or
recovery from VM loss. Customer operators own protected off-host backup
storage and restore drills.

## External customer PostgreSQL

The same Hormuz image can use a customer-operated PostgreSQL service. Run
`prepare`, replace both `postgres-runtime-dsn` and
`postgres-migration-dsn` with protected customer DSNs, and use the `external`
mode consistently:

```bash
./deploy/compose/hormuz-compose external config
./deploy/compose/hormuz-compose external pull
./deploy/compose/hormuz-compose external migrate
./deploy/compose/hormuz-compose external doctor
./deploy/compose/hormuz-compose external up
```

The rendered external model contains no PostgreSQL service, database volume,
or internal database network. The external database operator owns TLS,
credentials, roles, availability, backup, recovery, and network policy. The
external path does not change or rebuild the Hormuz image.

## Stop and remove

`down` removes containers and networks while preserving PostgreSQL data:

```bash
./deploy/compose/hormuz-compose down
```

After a verified backup, destructive pilot-data removal requires the exact
acknowledgement and removes the named PostgreSQL volume:

```bash
HORMUZ_COMPOSE_PURGE_ACK=I_UNDERSTAND_THIS_DELETES_PILOT_POSTGRES_DATA \
  ./deploy/compose/hormuz-compose purge
```

Neither command deletes `deploy/compose/runtime`. This is intentional: Hormuz
will not silently delete provider credentials, configuration, or backup files.
The customer operator must inventory and securely dispose of those exact files
after confirming they are no longer needed.

## Executable proof

Blocking Linux CI runs the proof only in a clean checkout with synthetic
traffic and an internal fake provider:

```bash
HORMUZ_COMPOSE_PROOF_ACK=I_UNDERSTAND_THIS_IS_A_DISPOSABLE_SINGLE_VM_PILOT_PROOF \
HORMUZ_COMPOSE_EVIDENCE_DIR=/protected/new/output/directory \
  ./tools/verify_compose_profile.sh
```

It validates both rendered modes, immutable images, native architecture,
startup/readiness, authenticated ingress, fallback/capping/redaction, a
configuration-enforced deny, durable metadata evidence, gateway restart,
configuration replacement and rollback, logical backup/restore, secret
non-disclosure, and clean container/network/volume removal. It contacts no AI
provider. The generated synthetic runtime, raw backup, and diagnostic
artifacts are deleted; only strict `hormuz.compose-reference-proof` v1
metadata is retained.
