# Render HTTPS preflight

Establish the actual HTTPS hostname before registering the two hosted-login
callbacks. This is a stateless Caddy endpoint, **not the Hormuz gateway**. It
does not import Hormuz, contact an identity/model provider, accept credentials,
create sessions, send email, or store customer data. Only GET/HEAD `/health`
and `/robots.txt` return 200. All other routes, including `/ready`, `/console`
and both callbacks, return a fixed 503 with `gateway_ready: false`.
That is the container contract; a hosting edge may reject an HTTP method before
it reaches the container, which must be verified separately.

The image uses an inspected official Caddy digest and runs as UID/GID 65532.
The unused low-port binding capability is removed from the Caddy binary.
Its build-context allowlist admits only its Dockerfile, Caddyfile and entrypoint.
No repository configuration, application source, database or secret is copied
into the image. Caddy admin, automatic TLS, saved runtime configuration and
application request logs are disabled. Render supplies the public TLS endpoint.

## Build and verify locally

From the repository root:

```sh
docker build --platform linux/amd64 \
  -f deploy/render/preflight/Dockerfile -t hormuz-https-preflight:local .
docker run --rm --name hormuz-https-preflight-local \
  --read-only --cap-drop ALL --security-opt no-new-privileges \
  --memory 128m --pids-limit 64 \
  --tmpfs /tmp:rw,noexec,nosuid,size=16m \
  --tmpfs /data:rw,noexec,nosuid,size=1m \
  --tmpfs /config:rw,noexec,nosuid,size=1m \
  -p 127.0.0.1:18080:10000 \
  -e RENDER_GIT_COMMIT="$(git rev-parse HEAD)" \
  hormuz-https-preflight:local
```

In another terminal:

```sh
python tools/verify_render_preflight.py \
  --url http://127.0.0.1:18080 --allow-loopback-http \
  --expected-source-commit "$(git rev-parse HEAD)"
docker stop hormuz-https-preflight-local
```

The probe uses synthetic inputs only and never follows redirects or disables
certificate verification. It checks closed gateway routes, absent cookies/CORS,
security headers and the revision reported by the preflight. Its results are
preflight evidence, not real Okta, provider, Mac/Keychain or onboarding evidence.
The runtime revision is Render metadata, not a cryptographic image attestation;
also confirm the actual deployed commit in Render's Events view.

The **Closed Render HTTPS preflight** GitHub CI job builds this image and runs
the same sixteen default-mode checks on loopback under the container restrictions
above. It also verifies the unprivileged user, removed binary capability and
absence of the synthetic request marker in container logs. It retains only the
content-free HTTP report. The job has no cloud credentials and performs no
deployment, public endpoint probe or identity/model-provider request.

## Manual Render setup

The public repository path avoids installing Render's GitHub app or giving it
write access to workflows, pull requests or other repository controls. Use
manual deployment for this test. Do not connect unrelated repositories.

| Field | Value |
| --- | --- |
| Service type | Web service |
| Source | Public Git Repository: `https://github.com/Xpounder-com/hormuz` |
| Name | `hormuz-https-preflight`, after checking availability |
| Branch | `mehrdad/render-https-preflight` |
| Language | Docker |
| Region | Ohio (US East), if available in the workspace |
| Root Directory | Empty |
| Compute | **Free, $0/month** |
| Docker Build Context Directory | `.` |
| Dockerfile Path | `deploy/render/preflight/Dockerfile` |
| Docker Command | Empty; preserve the image entrypoint |
| Health Check Path | `/health` |
| Auto-Deploy | **Off** |
| Environment | `PORT=10000` only; Render supplies `RENDER_GIT_COMMIT` |
| Secret files, disk, databases, registry credential | None |

Inspect the displayed total before creating the service. Render's creation form
defaults to paid compute; selecting a name does not select Free. Do not add a
payment method, upgrade the workspace, or approve a paid resource for this slice.
The optional [Blueprint](render.yaml) makes the same compute/trigger choices
explicit; it is not required for manual deployment.

After creation, use the actual assigned HTTPS origin, never a guessed service
hostname. Run the probe against HTTPS with the deployed source commit and inspect
the Events view. Check that HTTP redirects to HTTPS. Record only the content-free
outcomes and commit; do not put future tenant IDs or credentials in this profile.

The observed Render HTTPS edge rejects `TRACE` with 405 before the preflight
response. For that deployment, add `--trace-response edge-405`. This explicitly
requires a non-reflecting 405 with no redirect, cookie or CORS grant; the report
labels it `edge_method_rejection`, without claiming application-header or
revision verification for that request. The other fifteen checks retain their
exact status, security-header and revision requirements. The default local mode
still requires the container's 503 response to `TRACE`. A 404, timeout or any
other unexpected result remains a failure; failed runs produce partial JSON
evidence and exit nonzero. Do not retry silently or omit failed runs.

If verification fails, keep the endpoint closed and do not configure clients to
use it. Redeploy the previously verified preflight commit or keep the service
suspended until repaired; never roll this endpoint forward into a gateway merely
by overriding its startup command. No rollback may resurrect credential state
because this image has none.

## What remains before real login

Once the actual HTTPS origin is verified, prepare a separate confidential Okta
web app with exactly `/v1/auth/callback` and `/v1/admin/auth/callback` on that
origin. Keep the existing native reference registration unchanged. Creating that
app and its persistent credentials is a separate reviewed action.

The gateway deployment still needs the authenticated private proxy hop, bounded
HTTP transport, protected server-side secret injection, operator bootstrap,
durable session/directory storage and restart/revocation recovery. Replace the
preflight image only after those changes are verified. Keep the same public
origin; origin changes affect Hormuz's session/directory key derivation.

Free compute can sleep and discard local files. A free preflight is not a free
durable customer gateway or an availability/latency promise. A paid disk-backed
single-node stage needs a separate cost decision and does not provide high
availability. Public TLS, real signed invitation claims and the invited Mac
workflow remain separate gates. Independent onboarding counts stay unchanged.

References: [Render web services](https://render.com/docs/web-services),
[Free limits](https://render.com/docs/free),
[Blueprint defaults](https://render.com/docs/blueprint-spec),
[Caddy response routing](https://caddyserver.com/docs/caddyfile/directives/respond)
and [Caddy server controls](https://caddyserver.com/docs/caddyfile/options).
