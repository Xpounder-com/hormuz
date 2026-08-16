# Hormuz reference container

The reference image is a repeatable single-node deployment artifact for the current alpha. It proves a restricted process boundary; it does not prove high availability, shared persistence, zero-downtime upgrades, disaster recovery, or a supported public registry release.

## Image contract

The root `Dockerfile`:

- pins the official Python 3.14.6 Alpine 3.23 multi-platform image by registry digest;
- installs only exact, hash-verified binary Python dependencies from `deploy/container/requirements.lock`;
- copies only the Hormuz package and the dependency lock through a default-deny `.dockerignore`;
- runs as numeric UID and GID `65532:65532` with no login shell or home directory;
- writes application data only beneath `/var/lib/hormuz`;
- exposes port `8787` and probes `GET /health/ready`; and
- runs `python -m hormuz --config /etc/hormuz/hormuz.json serve` by default.

CI builds the image, runs `scripts/container_smoke.py`, generates a CycloneDX SBOM, and fails on any Trivy high or critical OS or Python-package finding, including findings without a current fix. The scanner action commit and scanner release are explicitly pinned CI inputs. The SBOM, vulnerability JSON, and image inspection are retained as workflow artifacts for seven days.

Tag-driven private GHCR publication, multi-architecture promotion, GitHub OIDC keyless signing, exact-identity verification, and a signed SLSA provenance predicate are implemented as a release contract, but no image is claimed as published until an eligible tag run succeeds. See [RELEASES.md](RELEASES.md). Tag governance, identity-based pull authorization, retention, customer-registry/KMS custody, and an observed release remain release work.

## Build

From a clean checkout:

```bash
docker build \
  --pull=false \
  --build-arg "HORMUZ_REVISION=$(git rev-parse HEAD)" \
  --tag hormuz:local \
  .
python3 scripts/container_smoke.py --image hormuz:local
```

`--pull=false` is intentional: the Dockerfile already selects the reviewed base image by digest. Updating that digest or `deploy/container/requirements.lock` is a dependency-review change, not an incidental build action.

Regenerate the lock only when the declared runtime dependencies change, then review the complete diff and rerun all container and Python gates:

```bash
uv pip compile pyproject.toml \
  --universal \
  --generate-hashes \
  --no-annotate \
  --no-header \
  --output-file deploy/container/requirements.lock
```

## Configure

Start from `config.example.json` and make these container-specific changes:

- set `listen.host` to `0.0.0.0`;
- set `database` to `/var/lib/hormuz/usage.sqlite3`;
- set `context_database` to `/var/lib/hormuz/context.sqlite3`; and
- keep `listen.shutdown_grace_seconds` below the deployment platform's termination grace.

Mount that file read-only at `/etc/hormuz/hormuz.json`. Do not bake provider keys, identity credentials, OIDC secrets, approval keys, or customer configuration into the image.

Hormuz currently reads server credentials from configured environment-variable names. In a shared environment, use the platform's secret injector and restrict who can inspect the workload definition or process environment. A local `--env-file` is suitable only for a controlled test, must be mode `0600`, must stay outside the repository, and remains visible to administrators with Docker-inspection access. File-mounted and KMS-backed secret sources are still an open production gate.

## Run the restricted reference process

The following is a single-host example. It deliberately publishes only to host loopback because the built-in server is plain HTTP:

```bash
docker volume create hormuz-data

docker run --detach \
  --name hormuz \
  --read-only \
  --tmpfs /tmp:rw,noexec,nosuid,nodev,size=16m \
  --cap-drop ALL \
  --security-opt no-new-privileges:true \
  --publish 127.0.0.1:8787:8787 \
  --mount type=bind,src=/absolute/path/hormuz.json,dst=/etc/hormuz/hormuz.json,readonly \
  --mount type=volume,src=hormuz-data,dst=/var/lib/hormuz \
  --env-file /secure/absolute/path/hormuz.env \
  hormuz:local
```

An empty named volume inherits the image directory ownership and is writable by UID/GID 65532. A bind-mounted data directory must already grant that numeric identity read/write access. Do not run the image as root to work around a host permission problem.

Verify the live process and stop it through the normal bounded drain path:

```bash
curl --fail-with-body http://127.0.0.1:8787/health/live
curl --fail-with-body http://127.0.0.1:8787/health/ready
docker stop --time 35 hormuz
```

Set the stop timeout above the configured Hormuz shutdown grace. A non-zero container exit after that period means admitted work did not finish within the bound.

## Network and telemetry boundary

For any shared deployment, keep Hormuz on a private network behind a separately hardened TLS terminator. Permit egress only to the configured OpenAI and Anthropic API hosts and explicitly configured OIDC discovery/JWKS hosts. Hormuz refuses a non-loopback plaintext provider URL and rejects provider base URLs containing user credentials, a query, or a fragment, but this application check does not replace a workload egress firewall or private endpoint policy. The current server does not interpret forwarded headers as client identity, so a proxy must not invent an employee identity or expose the private listener directly.

Disable body, raw URL, query, header, and process-dump collection in the load balancer, proxy, service mesh, container runtime, and log shipper. Hormuz's routine telemetry remains content-free inside the container, but provider-bound content is still inspected and relayed transiently, and the governed-context database intentionally contains reusable context. Mount, encrypt, retain, back up, and authorize that data accordingly.

## Current boundary

This reference process still uses SQLite and is single-node. Multiple replicas must not share the SQLite volume. PostgreSQL/shared-store topology, distributed budgets and throttles, HA sessions and approvals, backup/PITR, RPO/RTO, key rotation without restart, immutable external audit export, TLS reference configuration, executed upgrade/rollback proof, tag/environment governance, and an observed signed registry release remain open enterprise gates.
