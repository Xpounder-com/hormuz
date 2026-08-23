# External OIDC resource-server conformance

Hormuz is currently a JWT **resource server**. It verifies a provider-issued access token and maps the stable `(issuer, subject)` pair to an already configured Hormuz identity. It is not yet a browser-login or session-broker product.

`tools/oidc_reference_conformance.py` is a one-time release-gate harness for proving that behavior against an external provider. It:

1. reads the provider's discovery document and JWKS;
2. performs a one-time native-client authorization-code exchange with PKCE S256 and `response_mode=form_post` on loopback;
3. receives the authorization code in the local HTTP POST body rather than the browser callback URL, and keeps it, the verifier, access token, ID token, and subject in process memory only;
4. starts a disposable local Hormuz instance with an ephemeral, mode-0600 configuration;
5. proves the actual Hormuz verifier accepts the signed access token through `GET /v1/gateway/whoami` and resolves the explicitly configured identity; and
6. proves a tampered access token receives `401`.

It prints only a bounded pass/fail code and named checks. It does not write a credential, authorization code, issuer URL, subject, client ID, browser URL, or provider response to a repository artifact.

## Generic provider requirements

The reference provider must expose a standards-based OIDC discovery document and support:

- HTTPS issuer, authorization, token, and JWKS endpoints;
- authorization code with PKCE `S256` for a public native client;
- `response_mode=form_post`, so an authorization code is not returned in the browser callback URL;
- a signed JWT ID token and a signed JWT access token with `iss`, `aud`, `exp`, and `sub` claims;
- a loopback redirect URI; and
- an access-token audience configured exactly in Hormuz.

Do not grant the reference client a client secret, administrative APIs, offline access, refresh tokens, SCIM access, or provider-management permissions. A public native client with only authorization-code + PKCE is sufficient.

## Okta Integrator Free Plan reference

Okta is a reference profile, not a Hormuz product dependency. The generic contract remains usable with any conforming identity provider.

In an Okta Integrator Free Plan organization:

1. Open **Applications → Applications → Create App Integration**.
2. Choose **OIDC - OpenID Connect** and **Native Application**.
3. Name it `Hormuz OIDC Reference (non-production)`.
4. Enable only the **Authorization Code** grant. Do not enable Refresh Token for this proof. The authorization server must support `response_mode=form_post`.
5. Register exactly this sign-in redirect URI:

   ```text
   http://127.0.0.1:8765/callback
   ```

6. Assign only the designated non-production test user.
7. In **Security → API → Authorization Servers**, use the default custom authorization server if it is enabled for the organization. Its issuer is normally `https://<your-okta-domain>/oauth2/default` and its default audience is normally `api://default`. Confirm the authorization-server access policy permits the reference application and test user.

The client ID is public but should still not be published in Hormuz evidence. The Okta domain, subject, browser URL, authorization code, and all tokens remain private operational configuration.

## Run the proof

Run the tool from a clean Hormuz checkout. It opens the authorization request in the default browser, waits for the loopback callback, then exits after one exchange. Use Safari as the default browser when validating the macOS reference path.

```bash
python tools/oidc_reference_conformance.py \
  --issuer "https://<your-okta-domain>/oauth2/default" \
  --client-id "<public-native-client-id>" \
  --audience "api://default" \
  --actor-id okta-reference-user \
  --team-id platform \
  --organization-id xpounder
```

Expected output contains only:

```text
external_oidc_resource_server_conformance=passed
checks=discovery_jwks,authorization_code_pkce_s256_form_post,id_token_nonce,access_token_signature,issuer_audience_expiry_subject,configured_subject_mapping,gateway_whoami,tampered_access_token_denied
credential_retention=none
```

Record only those content-free lines, the tool commit, and the relevant test/CI results in issue evidence. Do not record the command, issuer URL, tenant, subject, token, client ID, authorization code, browser redirect, or configuration file.

## Boundary

This evidence validates external generic OIDC resource-server compatibility. It does **not** establish browser SSO, a Hormuz-managed authorization-code callback, refresh-token custody or rotation, employee session storage, SCIM provisioning, live deprovisioning, KMS custody, HA, or a production IdP certification. Those require separate approved work and evidence.
