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

CI first exports the exact checked-out commit twice and requires two clean `linux/amd64` BuildKit OCI layouts to be byte-identical. It then builds the runtime image, runs `scripts/container_smoke.py`, generates a CycloneDX SBOM, and fails on any Trivy high or critical OS or Python-package finding, including findings without a current fix. The reproducibility manifest, deterministic OCI tar, SBOM, vulnerability JSON, and image inspection are retained as workflow artifacts for seven days. Scanner actions and releases are explicitly pinned CI inputs.

Tag-driven private GHCR publication, multi-architecture promotion, GitHub OIDC keyless signing, exact-identity verification, and a signed SLSA provenance predicate are implemented as a release contract, but no image is claimed as published until an eligible tag run succeeds. See [RELEASES.md](RELEASES.md). Tag governance, identity-based pull authorization, retention, customer-registry/KMS custody, and an observed release remain release work.

## Build

From a clean checkout:

```bash
docker build \
  --pull=false \
  --build-arg "SOURCE_DATE_EPOCH=$(git show -s --format=%ct HEAD)" \
  --build-arg "HORMUZ_REVISION=$(git rev-parse HEAD)" \
  --tag hormuz:local \
  .
python3 scripts/container_smoke.py --image hormuz:local
```

`--pull=false` is intentional: the Dockerfile already selects the reviewed base image by digest. Updating that digest or `deploy/container/requirements.lock` is a dependency-review change, not an incidental build action.

## Exact-source reproducibility gate

Run the same gate used by ordinary and tag verification from a committed checkout:

```bash
docker buildx create \
  --name hormuz-reproducer \
  --driver docker-container \
  --driver-opt image=moby/buildkit:v0.32.2@sha256:28a898719c18a33f4e8000685287fa36fd0dd9560c6440227d3a732d79bb41d8 \
  --use
docker buildx inspect --bootstrap

HORMUZ_SOURCE_SHA="$(git rev-parse HEAD)"
python3 scripts/reproducible_image.py \
  --source-sha "$HORMUZ_SOURCE_SHA" \
  --outdir oci-reproducibility
```

The command supports only `linux/amd64` in this first bounded contract. It fails closed unless the active builder uses the `docker-container` driver and reviewed BuildKit version `v0.32.2`; CI and the command above additionally bind that version to the reviewed multi-platform image digest. It verifies that the supplied full SHA is checked-out `HEAD`, obtains the epoch from that commit, exports tracked files rather than the working tree, and builds twice with no cache, no pull, and no publishing. It disables provenance and SBOM attestations for this byte-comparison build because their run-specific envelopes are not image-layer reproducibility inputs.

Both OCI layouts must contain one `linux/amd64` manifest and pass bounded validation of every referenced digest, byte size, config, root-filesystem diff ID, and layer count. File sets, sizes, and SHA-256 digests must then match exactly. Only after that comparison succeeds does Hormuz publish a canonical versioned `hormuz-X.Y.Z-linux-amd64.oci.tar` and content-free `hormuz-oci-reproducibility.json` into an initially empty, non-symlink output directory.

This proves same-source byte equality under the exercised BuildKit contract. It does not prove `linux/arm64` equality, equality across every builder or operating system, offline availability of the pinned base and PyPI inputs, reproducibility of run-specific signatures/attestations, or that any registry artifact was published. The signed release path separately generates and verifies provenance and SBOM evidence.

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
- keep `listen.shutdown_grace_seconds` below the deployment platform's termination grace;
- set `listen.accept_backlog` to an intentional burst queue no larger than the surrounding ingress and operating-system policy permit; the default `256` aligns with the default application connection ceiling, but the operating system may cap the hint;
- size `listen.max_connections` above `listen.max_concurrent_requests` so probes and ordinary header parsing retain headroom;
- keep the absolute `listen.request_header_timeout_seconds` aligned with the outer proxy's stricter header deadline; and
- keep `listen.request_body_timeout_seconds` and `max_request_bytes` within the outer proxy's independently enforced body-time and body-size limits.

Mount that file read-only at `/etc/hormuz/hormuz.json`. Do not bake provider keys, identity credentials, OIDC secrets, approval keys, or customer configuration into the image.

Run the candidate image's `doctor` command first and place the printed exact-file digest in the separately controlled workload environment as `HORMUZ_CONFIG_SHA256`. The ordinary container entrypoint then refuses to start if the mounted bytes differ, including a whitespace-only replacement. The digest is not a secret or signature; do not store it only beside the file it is meant to constrain.

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

For any shared deployment, keep Hormuz on a private network behind a separately hardened TLS terminator. Permit egress only to the configured OpenAI and Anthropic API hosts and explicitly configured OIDC discovery/JWKS hosts. Hormuz refuses a non-loopback plaintext provider URL, rejects provider base URLs containing user credentials, a query, or a fragment, and never follows a provider redirect. These application checks bind the server-held credential to the configured origin but do not replace a workload egress firewall or private endpoint policy. The current server does not interpret forwarded headers as client identity, so a proxy must not invent an employee identity or expose the private listener directly.

Disable body, raw URL, query, header, and process-dump collection in the load balancer, proxy, service mesh, container runtime, and log shipper. Hormuz's routine telemetry remains content-free inside the container, but provider-bound content is still inspected and relayed transiently, and the governed-context database intentionally contains reusable context. Mount, encrypt, retain, back up, and authorize that data accordingly.

## Current boundary

This reference process still uses SQLite and is single-node. Multiple replicas must not share the SQLite volume. PostgreSQL/shared-store topology, distributed budgets and throttles, HA sessions and approvals, backup/PITR, RPO/RTO, key rotation without restart, immutable external audit export, TLS reference configuration, executed upgrade/rollback proof, tag/environment governance, and an observed signed registry release remain open enterprise gates.
