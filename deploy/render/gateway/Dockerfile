# syntax=docker/dockerfile:1@sha256:ecfaec9ed6d810b56388c508f4121597bfbba70d41a6dfeee4d8cad5f295fc32
# Separate hosted qualification image; inference needs explicit provider-pilot mode.
ARG PYTHON_BASE=python@sha256:23c59390fc717bf09f9336908199a0ae75d9c4264bf296123f94ad772fea3b52
FROM caddy@sha256:df7f1c2fb114453b951de51a98efc010db1655a92c2e86be6706714e2417a78d AS proxy
RUN setcap -r /usr/bin/caddy

FROM ${PYTHON_BASE} AS builder
ARG TARGETPLATFORM
WORKDIR /build
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_NO_CACHE_DIR=1 PIP_NO_COMPILE=1 PYTHONDONTWRITEBYTECODE=1
RUN test "${TARGETPLATFORM}" = "linux/amd64"
COPY requirements/oci-build-linux-amd64.lock requirements/oci-runtime-linux-amd64.lock ./requirements/
RUN python -m pip install --only-binary=:all: --require-hashes --no-deps -r requirements/oci-build-linux-amd64.lock \
    && python -m pip download --only-binary=:all: --require-hashes --dest /wheelhouse -r requirements/oci-runtime-linux-amd64.lock
COPY pyproject.toml README.md LICENSE ./
COPY hormuz/ ./hormuz/
RUN python -m pip wheel --no-build-isolation --no-deps --wheel-dir /wheelhouse . \
    && python -m venv /opt/hormuz \
    && /opt/hormuz/bin/pip install --no-index --no-compile --require-hashes \
        --find-links=/wheelhouse -r requirements/oci-runtime-linux-amd64.lock \
    && /opt/hormuz/bin/pip install --no-index --no-compile --no-deps \
        --find-links=/wheelhouse hormuz==1.0.0 \
    && /opt/hormuz/bin/pip uninstall --yes pip setuptools

FROM ${PYTHON_BASE}
LABEL org.opencontainers.image.title="Hormuz hosted single-node pilot" \
      org.opencontainers.image.description="Maintenance-first login and opt-in provider-pilot qualification" \
      org.opencontainers.image.source="https://github.com/Xpounder-com/hormuz"
ENV PATH="/opt/hormuz/bin:${PATH}" HOME=/home/hormuz PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    HORMUZ_CONFIG=/etc/secrets/hormuz-hosted.json \
    HORMUZ_PROVIDER_CONFIG=/etc/secrets/hormuz-provider.json \
    HORMUZ_HOSTED_MODE=maintenance PORT=10000
RUN rm -rf /usr/local/bin/pip /usr/local/bin/pip3 /usr/local/bin/pip3.14 \
        /usr/local/lib/python3.14/ensurepip /usr/local/lib/python3.14/site-packages/pip \
        /usr/local/lib/python3.14/site-packages/pip-*.dist-info \
    && groupadd --gid 1000 hormuz-runtime \
    && useradd --uid 65532 --gid 1000 --home-dir /home/hormuz --shell /bin/sh \
        --no-create-home --no-log-init hormuz \
    && install --directory --owner=65532 --group=1000 --mode=0700 \
        /home/hormuz /home/hormuz/.ssh \
    && install --directory --owner=65532 --group=65532 --mode=0700 /var/lib/hormuz \
    && install --directory --owner=root --group=root --mode=0755 /etc/hormuz/caddy
COPY --from=builder /opt/hormuz /opt/hormuz
COPY --from=proxy /usr/bin/caddy /usr/bin/caddy
COPY --chmod=0444 deploy/render/gateway/active.Caddyfile deploy/render/gateway/maintenance.Caddyfile deploy/render/gateway/provider-pilot.Caddyfile /etc/hormuz/caddy/
# Render mounts secret files for group 1000. State stays owner-only (UID 65532).
# The named shell account and private home enable Render's injected SSH transport;
# the image neither installs nor runs an SSH server, and the disk does not cover HOME.
USER 65532:1000
WORKDIR /var/lib/hormuz
EXPOSE 10000
STOPSIGNAL SIGTERM
ENTRYPOINT ["/opt/hormuz/bin/python", "-I", "-m", "hormuz.hosted"]
CMD ["serve"]
