# This stateless test endpoint is not the Hormuz gateway or a release image.
# Official Caddy 2.11.4-alpine multi-platform index, inspected 2026-08-31.
FROM caddy@sha256:df7f1c2fb114453b951de51a98efc010db1655a92c2e86be6706714e2417a78d

LABEL org.opencontainers.image.title="Hormuz HTTPS preflight" \
      org.opencontainers.image.description="Stateless HTTPS setup endpoint; sign-in and model routes remain disabled" \
      org.opencontainers.image.source="https://github.com/Xpounder-com/hormuz"

ENV PORT=10000 \
    XDG_CONFIG_HOME=/tmp/caddy/config \
    XDG_DATA_HOME=/tmp/caddy/data

# Only an unprivileged port is used; inherited low-port file capabilities would
# also prevent execution when the container drops every capability.
RUN setcap -r /usr/bin/caddy

COPY --chmod=0444 deploy/render/preflight/Caddyfile /etc/caddy/Caddyfile
COPY --chmod=0555 deploy/render/preflight/entrypoint.sh /usr/local/bin/hormuz-preflight

USER 65532:65532
EXPOSE 10000
STOPSIGNAL SIGTERM
ENTRYPOINT ["/usr/local/bin/hormuz-preflight"]
CMD []
