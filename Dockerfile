ARG PYTHON_IMAGE=python:3.14.6-alpine3.23@sha256:b165067c5afc37fa5608a3c05609cc3d51aafd808a30fbfd822ee594fef55ad4
FROM ${PYTHON_IMAGE}

ARG HORMUZ_REVISION=unknown
ARG HORMUZ_VERSION=unknown
ARG SOURCE_DATE_EPOCH=0

LABEL org.opencontainers.image.title="Hormuz" \
      org.opencontainers.image.description="Enterprise AI policy, usage, and governed-context control plane" \
      org.opencontainers.image.source="https://github.com/Xpounder-com/hormuz" \
      org.opencontainers.image.revision="${HORMUZ_REVISION}" \
      org.opencontainers.image.version="${HORMUZ_VERSION}"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

RUN addgroup --system --gid 65532 hormuz \
    && adduser --system --disabled-password --no-create-home \
        --uid 65532 --ingroup hormuz --shell /sbin/nologin hormuz \
    && mkdir --parents /var/lib/hormuz /opt/hormuz \
    && chown 65532:65532 /var/lib/hormuz /opt/hormuz \
    && chmod 0700 /var/lib/hormuz /opt/hormuz \
    && rm -rf /root/.cache

WORKDIR /opt/hormuz

COPY deploy/container/requirements.lock /tmp/hormuz-requirements.lock
RUN SOURCE_DATE_EPOCH="${SOURCE_DATE_EPOCH}" python -m pip install \
        --require-hashes \
        --only-binary=:all: \
        --requirement /tmp/hormuz-requirements.lock \
    && rm /tmp/hormuz-requirements.lock \
    && rm -rf /root/.cache

COPY --chown=65532:65532 hormuz/ ./hormuz/

USER 65532:65532

VOLUME ["/var/lib/hormuz"]
EXPOSE 8787

HEALTHCHECK --interval=10s --timeout=3s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request; response = urllib.request.urlopen('http://127.0.0.1:8787/health/ready', timeout=2); raise SystemExit(0 if response.status == 200 else 1)"]

ENTRYPOINT ["python", "-m", "hormuz"]
CMD ["--config", "/etc/hormuz/hormuz.json", "serve"]
