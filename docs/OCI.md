# OCI reference runtime

The Hormuz OCI image is a small **reference runtime**, not a hosted
deployment product. It runs the core `hormuz` package as numeric user and group
`65532:65532`; it does not contain customer configuration or policy data,
credentials, usage data, audit evidence, or the separately packaged context
experiment.

The image uses digest-pinned Dockerfile frontend and Python 3.14 slim bases
named in [`Dockerfile`](../Dockerfile). The bases are pinned to make the
runtime inputs explicit; Hormuz does **not** claim a published image, signed
image, SBOM, vulnerability attestation, registry policy, or fully reproducible
dependency build in this release line.

## Build

```bash
docker build --tag hormuz:local .
```

`Dockerfile` uses a two-stage build. Its `.dockerignore` is an allowlist for
only `pyproject.toml`, `README.md`, and `hormuz/`, so a local `hormuz.json`,
`.env` file, SQLite database, test suite, and `experiments/context/` cannot be
copied into the image by the build.

## Run with explicit runtime inputs

Prepare a deployment configuration outside the image. It names environment
variables such as `OPENAI_API_KEY`; it must not contain their values. For a
SQLite deployment, set the configuration's database path to
`/var/lib/hormuz/hormuz.sqlite3` and bind the listener to `0.0.0.0:8787`.
`config.example.json` is a starting point, not a container-ready configuration
without those two changes.

Create a data directory that the numeric runtime identity can write. The
following initialization is a one-time local-volume setup; normal Hormuz
runtime remains non-root.

```bash
docker volume create hormuz-data
docker run --rm --user 0:0 \
  --mount type=volume,src=hormuz-data,dst=/var/lib/hormuz \
  --entrypoint /usr/local/bin/python \
  hormuz:local -c 'import os; os.chown("/var/lib/hormuz", 65532, 65532)'
```

Place the real configuration in an operator-controlled directory, then inject
credentials through your platform's secret mechanism (shown here as an ignored
local env file):

```bash
docker run --rm --name hormuz \
  --read-only \
  --tmpfs /tmp:mode=1777 \
  --cap-drop=ALL \
  --security-opt=no-new-privileges \
  --mount type=bind,src="$PWD/hormuz-config",dst=/etc/hormuz,readonly \
  --mount type=volume,src=hormuz-data,dst=/var/lib/hormuz \
  --env-file ./hormuz.runtime.env \
  --publish 127.0.0.1:8787:8787 \
  hormuz:local
```

The default `HORMUZ_CONFIG` value is `/etc/hormuz/hormuz.json`; point it at a
different mounted path only with another runtime environment value. Do not
pass secrets in Dockerfile arguments, image labels, command arguments, a
committed env file, or the configuration JSON.

For PostgreSQL-backed usage/evidence, the image still runs as non-root. Mount
configuration as above and supply the separately scoped runtime DSN only at
runtime. Apply PostgreSQL migrations with the documented operator migration
credential before starting the gateway; the reference image does not perform
schema changes at startup.

## Writable paths and probes

With a read-only root filesystem, `/tmp` must be a temporary writable mount and
`/var/lib/hormuz` is the durable SQLite data mount. The image does not declare
an implicit data volume because deployment ownership and retention are an
operator decision.

Use `GET /health` for liveness and `GET /ready` for traffic readiness. Their
versioned, content-free semantics and graceful shutdown behavior are described
in [OPERATIONS.md](OPERATIONS.md). The image health check calls only local
`/health`; it does not test provider availability, policy authorization,
credentials, TLS, or a remote database.

## Executable reference proof

Run the local proof from the repository root:

```bash
./tools/verify_oci_reference.sh
```

It builds the image, confirms the declared numeric user and health check,
proves no default configuration is embedded, checks that the context packages
are absent, starts the gateway with a read-only root filesystem and mounted
runtime inputs, validates `/health` and `/ready`, verifies SQLite lands only on
the durable mount, and requires `SIGTERM` to exit cleanly. It uses fixed
placeholder values and never contacts a model provider.

This proof does not establish registry publication, image signing, SBOM or
vulnerability policy, TLS or trusted-proxy configuration, Kubernetes, high
availability, backup/PITR, multi-instance coordination, customer IdP
conformance, or incident operations. Those remain separate release gates.
