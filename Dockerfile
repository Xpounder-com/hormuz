# syntax=docker/dockerfile:1@sha256:ecfaec9ed6d810b56388c508f4121597bfbba70d41a6dfeee4d8cad5f295fc32
# Python 3.14.7-slim-bookworm multi-platform index, inspected 2026-08-24.
ARG PYTHON_BASE=python@sha256:23c59390fc717bf09f9336908199a0ae75d9c4264bf296123f94ad772fea3b52
ARG SOURCE_DATE_EPOCH=0

FROM ${PYTHON_BASE} AS builder

ARG HORMUZ_VERSION=0.1.3
ARG SOURCE_DATE_EPOCH
ARG TARGETPLATFORM

WORKDIR /build

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_NO_COMPILE=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    SOURCE_DATE_EPOCH=${SOURCE_DATE_EPOCH}

# The first signed contract is deliberately linux/amd64 only. A distinct
# native ARM64 release gate must close before this assertion can expand.
RUN test "${TARGETPLATFORM}" = "linux/amd64"

COPY requirements/oci-build-linux-amd64.lock requirements/oci-runtime-linux-amd64.lock ./requirements/

RUN python -m pip install \
        --only-binary=:all: \
        --require-hashes \
        --no-deps \
        --requirement requirements/oci-build-linux-amd64.lock \
    && python -m pip download \
        --only-binary=:all: \
        --require-hashes \
        --dest /wheelhouse \
        --requirement requirements/oci-runtime-linux-amd64.lock

# The Docker build context allowlist admits only these core packaging inputs.
# Configuration, credentials, usage data, tests, and the context experiment
# never enter either build stage.
COPY pyproject.toml README.md LICENSE ./
COPY hormuz/ ./hormuz/

RUN python -m pip wheel \
        --no-build-isolation \
        --no-deps \
        --wheel-dir /wheelhouse \
        . \
    && python -m venv /opt/hormuz \
    && /opt/hormuz/bin/pip install \
        --no-index \
        --no-compile \
        --find-links=/wheelhouse \
        "hormuz[postgres]==${HORMUZ_VERSION}" \
    && /opt/hormuz/bin/pip uninstall --yes pip setuptools

FROM ${PYTHON_BASE} AS runtime

ARG HORMUZ_VERSION=0.1.3
ARG SOURCE_DATE_EPOCH
ARG VCS_REF=unknown

LABEL org.opencontainers.image.title="Hormuz" \
      org.opencontainers.image.description="Non-root reference runtime for the Hormuz enterprise AI policy gateway" \
      org.opencontainers.image.vendor="NeuralInt" \
      org.opencontainers.image.source="https://github.com/Xpounder-com/hormuz" \
      org.opencontainers.image.licenses="Apache-2.0" \
      org.opencontainers.image.version="${HORMUZ_VERSION}" \
      org.opencontainers.image.revision="${VCS_REF}"

ENV PATH="/opt/hormuz/bin:${PATH}" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HORMUZ_CONFIG=/etc/hormuz/hormuz.json

# The pinned base digest supplies the complete OS state. Never resolve moving
# Debian package indexes inside a reproducible release build; refresh the base
# digest through a reviewed security update instead. Remove the unused global
# installer so it is not shipped or scanned as a reachable application package.
RUN rm -rf /usr/local/bin/pip /usr/local/bin/pip3 /usr/local/bin/pip3.14 \
        /usr/local/lib/python3.14/ensurepip \
        /usr/local/lib/python3.14/site-packages/pip \
        /usr/local/lib/python3.14/site-packages/pip-*.dist-info \
    && install --directory --owner=65532 --group=65532 --mode=0750 /var/lib/hormuz \
    && install --directory --owner=root --group=root --mode=0755 /etc/hormuz

COPY --from=builder /opt/hormuz /opt/hormuz

# Keep the runtime identity numeric so it stays stable across deployment
# platforms and does not depend on a mutable passwd/group entry.
USER 65532:65532
WORKDIR /var/lib/hormuz

EXPOSE 8787

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
  CMD ["/opt/hormuz/bin/python", "-I", "-c", "import os; from urllib.request import Request, urlopen; credential=os.environ.get('HORMUZ_INGRESS_CREDENTIAL'); headers={'X-Hormuz-Ingress-Credential': credential} if credential else {}; request=Request('http://127.0.0.1:8787/health', headers=headers); raise SystemExit(0 if urlopen(request, timeout=2).status == 200 else 1)"]

ENTRYPOINT ["hormuz"]
CMD ["serve"]
