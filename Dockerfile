# syntax=docker/dockerfile:1@sha256:ecfaec9ed6d810b56388c508f4121597bfbba70d41a6dfeee4d8cad5f295fc32
# Python 3.14.7-slim-trixie multi-platform index, inspected 2026-08-21.
ARG PYTHON_BASE=python@sha256:ce40764625a4ff50df3548277632e7f96c4e77fe75fa848aae9885476e7df5a4

FROM ${PYTHON_BASE} AS builder

WORKDIR /build

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

# The Docker build context allowlist admits only these core packaging inputs.
# Configuration, credentials, usage data, tests, and the context experiment
# never enter either build stage.
COPY pyproject.toml README.md ./
COPY hormuz/ ./hormuz/

RUN python -m pip wheel --only-binary=:all: --wheel-dir /wheelhouse ".[postgres]" \
    && python -m venv /opt/hormuz \
    && /opt/hormuz/bin/pip install --no-index --find-links=/wheelhouse "hormuz[postgres]" \
    && /opt/hormuz/bin/pip uninstall --yes pip setuptools

FROM ${PYTHON_BASE} AS runtime

LABEL org.opencontainers.image.title="Hormuz" \
      org.opencontainers.image.description="Non-root reference runtime for the Hormuz enterprise AI policy gateway" \
      org.opencontainers.image.vendor="NeuralInt"

ENV PATH="/opt/hormuz/bin:${PATH}" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HORMUZ_CONFIG=/etc/hormuz/hormuz.json

# The runtime uses the isolated /opt/hormuz virtual environment. Remove the
# unused global installer and its bundled dependencies so they are not shipped
# or scanned as reachable application packages.
RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get upgrade --yes \
    && rm -rf /var/lib/apt/lists/* \
    && rm -rf /usr/local/bin/pip /usr/local/bin/pip3 /usr/local/bin/pip3.14 \
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
