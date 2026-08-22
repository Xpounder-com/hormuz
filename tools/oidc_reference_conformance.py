#!/usr/bin/env python3
"""Run a bounded, content-free external OIDC resource-server conformance proof.

This is deliberately a release-gate harness, not a Hormuz browser-session
broker.  It uses a one-time authorization-code + PKCE exchange with
``response_mode=form_post`` only to obtain one external access token in
memory, proves that the current gateway accepts it through its normal OIDC
verification path, and discards all credentials before the process exits.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import secrets
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Mapping

import jwt

from hormuz.auth import AuthenticationError, Authenticator
from hormuz.config import GatewayConfig, ListenConfig
from hormuz.server import GatewayServer, serve_in_thread


_MAX_METADATA_BYTES = 128 * 1024
_MAX_TOKEN_BYTES = 64 * 1024
_DEFAULT_REDIRECT_URI = "http://127.0.0.1:8765/callback"
_DEFAULT_TIMEOUT_SECONDS = 180


class ConformanceError(ValueError):
    """A content-free external-provider conformance failure."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class ProviderMetadata:
    issuer: str
    authorization_endpoint: str
    token_endpoint: str
    jwks_uri: str
    id_token_algorithms: tuple[str, ...]
    response_modes: tuple[str, ...]


@dataclass(frozen=True)
class CallbackResult:
    code: str | None = None
    failure_code: str | None = None


class _CallbackServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, *, host: str, port: int, path: str, state: str):
        self.callback_path = path
        self.expected_state = state
        self.callback_event = threading.Event()
        self.callback_result: CallbackResult | None = None
        super().__init__((host, port), _CallbackHandler)


class _CallbackHandler(BaseHTTPRequestHandler):
    server: _CallbackServer

    def do_GET(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlsplit(self.path)
        if parsed.path != self.server.callback_path:
            self.send_error(HTTPStatus.NOT_FOUND)
            return

        # The reference flow explicitly requests response_mode=form_post, so
        # an authorization code must never arrive in the browser callback URL.
        # Do not parse or retain any query parameters if a provider ignores it.
        self._finish(HTTPStatus.METHOD_NOT_ALLOWED, CallbackResult(failure_code="callback_response_mode_invalid"))

    def do_POST(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlsplit(self.path)
        if parsed.path != self.server.callback_path or parsed.query or parsed.fragment:
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        content_type = self.headers.get_content_type()
        try:
            content_length = int(self.headers.get("Content-Length", ""))
        except ValueError:
            self._finish(HTTPStatus.BAD_REQUEST, CallbackResult(failure_code="callback_request_invalid"))
            return
        if content_type != "application/x-www-form-urlencoded" or not 0 < content_length <= _MAX_TOKEN_BYTES:
            self._finish(HTTPStatus.BAD_REQUEST, CallbackResult(failure_code="callback_request_invalid"))
            return
        try:
            body = self.rfile.read(content_length).decode("utf-8", "strict")
        except UnicodeDecodeError:
            self._finish(HTTPStatus.BAD_REQUEST, CallbackResult(failure_code="callback_request_invalid"))
            return
        try:
            form = urllib.parse.parse_qs(body, keep_blank_values=True, strict_parsing=True)
        except ValueError:
            self._finish(HTTPStatus.BAD_REQUEST, CallbackResult(failure_code="callback_request_invalid"))
            return
        state = _single_form_value(form, "state")
        code = _single_form_value(form, "code")
        provider_error = _single_form_value(form, "error")
        if provider_error is not None:
            result = CallbackResult(failure_code="authorization_denied")
        elif state is None or not hmac.compare_digest(state, self.server.expected_state):
            result = CallbackResult(failure_code="callback_state_mismatch")
        elif code is None or not code:
            result = CallbackResult(failure_code="authorization_code_missing")
        else:
            result = CallbackResult(code=code)
        self._finish(HTTPStatus.OK, result)

    def _finish(self, status: HTTPStatus, result: CallbackResult) -> None:
        self.server.callback_result = result
        self.server.callback_event.set()
        body = b"Hormuz OIDC reference validation received the callback. You may close this tab."
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Referrer-Policy", "no-referrer")
        if status == HTTPStatus.METHOD_NOT_ALLOWED:
            self.send_header("Allow", "POST")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format: str, *_args: object) -> None:
        # Authorization codes and state values can be present in the request
        # target.  Never delegate default HTTP request logging for callbacks.
        return


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run a content-free external OIDC resource-server conformance proof."
    )
    parser.add_argument("--issuer", required=True, help="Exact external OIDC issuer URL")
    parser.add_argument("--client-id", required=True, help="Public native OIDC client identifier")
    parser.add_argument("--audience", required=True, help="Expected JWT access-token audience")
    parser.add_argument("--redirect-uri", default=_DEFAULT_REDIRECT_URI, help="Registered loopback callback URI")
    parser.add_argument("--timeout-seconds", type=int, default=_DEFAULT_TIMEOUT_SECONDS)
    parser.add_argument("--actor-id", default="okta-reference-user")
    parser.add_argument("--actor-name", default="Okta Reference User")
    parser.add_argument("--team-id", default="platform")
    parser.add_argument("--team-name", default="Platform")
    parser.add_argument("--organization-id", default="xpounder")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        _run(args)
    except ConformanceError as error:
        print(f"external_oidc_resource_server_conformance=failed code={error.code}", file=sys.stderr)
        return 1
    print("external_oidc_resource_server_conformance=passed")
    print(
        "checks=discovery_jwks,authorization_code_pkce_s256_form_post,id_token_nonce,"
        "access_token_signature,issuer_audience_expiry_subject,configured_subject_mapping,"
        "gateway_whoami,tampered_access_token_denied"
    )
    print("credential_retention=none")
    return 0


def _run(args: argparse.Namespace) -> None:
    if not 30 <= args.timeout_seconds <= 600:
        raise ConformanceError("invalid_timeout")
    issuer = _normalize_issuer(args.issuer)
    redirect = _loopback_redirect(args.redirect_uri)
    client_id = _required_short_value(args.client_id, "invalid_client_id")
    audience = _required_short_value(args.audience, "invalid_audience")

    metadata = _discover(issuer)
    verifier = _pkce_verifier()
    state = _random_urlsafe(32)
    nonce = _random_urlsafe(32)
    authorization_url = _authorization_url(
        metadata=metadata,
        client_id=client_id,
        redirect_uri=args.redirect_uri,
        verifier=verifier,
        state=state,
        nonce=nonce,
    )

    try:
        callback_server = _CallbackServer(
            host=redirect.hostname,
            port=redirect.port,
            path=redirect.path,
            state=state,
        )
    except OSError as error:
        raise ConformanceError("callback_bind_failed") from error
    callback_thread = threading.Thread(
        target=callback_server.serve_forever,
        name="hormuz-oidc-reference-callback",
        daemon=True,
    )
    callback_thread.start()
    try:
        try:
            browser_opened = webbrowser.open(authorization_url, new=1, autoraise=True)
        except (OSError, webbrowser.Error) as error:
            raise ConformanceError("browser_open_failed") from error
        if not browser_opened:
            raise ConformanceError("browser_open_failed")
        if not callback_server.callback_event.wait(timeout=args.timeout_seconds):
            raise ConformanceError("callback_timeout")
        callback = callback_server.callback_result
        if callback is None:
            raise ConformanceError("callback_missing")
        if callback.failure_code is not None:
            raise ConformanceError(callback.failure_code)
        if callback.code is None:
            raise ConformanceError("authorization_code_missing")
        token_response = _exchange_code(
            metadata=metadata,
            client_id=client_id,
            redirect_uri=args.redirect_uri,
            verifier=verifier,
            code=callback.code,
        )
    finally:
        callback_server.shutdown()
        callback_server.server_close()
        callback_thread.join(timeout=5)

    access_token = _token_value(token_response, "access_token", "access_token_missing")
    id_token = _token_value(token_response, "id_token", "id_token_missing")
    _validate_id_token(metadata=metadata, token=id_token, client_id=client_id, nonce=nonce)
    subject = _unverified_subject(access_token)

    _verify_with_hormuz(
        issuer=issuer,
        audience=audience,
        subject=subject,
        access_token=access_token,
        expected_identity={
            "actor_id": _required_short_value(args.actor_id, "invalid_actor_id"),
            "team_id": _required_short_value(args.team_id, "invalid_team_id"),
            "organization_id": _required_short_value(args.organization_id, "invalid_organization_id"),
        },
        display_identity={
            "actor_name": _required_short_value(args.actor_name, "invalid_actor_name"),
            "team_name": _required_short_value(args.team_name, "invalid_team_name"),
        },
    )


def _normalize_issuer(value: str) -> str:
    value = _required_short_value(value, "invalid_issuer").rstrip("/")
    _https_url(value, "invalid_issuer")
    return value


def _required_short_value(value: object, failure_code: str) -> str:
    if not isinstance(value, str) or not value or len(value.encode("utf-8")) > 1024:
        raise ConformanceError(failure_code)
    if "\x00" in value or "\n" in value or "\r" in value:
        raise ConformanceError(failure_code)
    return value


@dataclass(frozen=True)
class _LoopbackRedirect:
    hostname: str
    port: int
    path: str


def _loopback_redirect(value: str) -> _LoopbackRedirect:
    parsed = urllib.parse.urlsplit(value)
    try:
        port = parsed.port
    except ValueError as error:
        raise ConformanceError("unsafe_redirect_uri") from error
    if (
        parsed.scheme != "http"
        or parsed.hostname != "127.0.0.1"
        or port is None
        or not 1 <= port <= 65535
        or not parsed.path.startswith("/")
        or parsed.query
        or parsed.fragment
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise ConformanceError("unsafe_redirect_uri")
    return _LoopbackRedirect(hostname=parsed.hostname, port=port, path=parsed.path)


def _https_url(value: str, failure_code: str) -> None:
    parsed = urllib.parse.urlsplit(value)
    if (
        parsed.scheme != "https"
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
    ):
        raise ConformanceError(failure_code)


def _discover(issuer: str) -> ProviderMetadata:
    document = _fetch_json(f"{issuer}/.well-known/openid-configuration", "oidc_metadata_unavailable")
    if document.get("issuer") != issuer:
        raise ConformanceError("issuer_mismatch")
    authorization_endpoint = _metadata_url(document, "authorization_endpoint")
    token_endpoint = _metadata_url(document, "token_endpoint")
    jwks_uri = _metadata_url(document, "jwks_uri")
    supported_pkce = document.get("code_challenge_methods_supported")
    if not isinstance(supported_pkce, list) or "S256" not in supported_pkce:
        raise ConformanceError("pkce_s256_unsupported")
    response_types = document.get("response_types_supported")
    if isinstance(response_types, list) and "code" not in response_types:
        raise ConformanceError("authorization_code_unsupported")
    grants = document.get("grant_types_supported")
    if isinstance(grants, list) and "authorization_code" not in grants:
        raise ConformanceError("authorization_code_unsupported")
    response_modes = document.get("response_modes_supported")
    if not isinstance(response_modes, list) or "form_post" not in response_modes:
        raise ConformanceError("form_post_unsupported")
    algorithms = document.get("id_token_signing_alg_values_supported")
    if not isinstance(algorithms, list) or not all(isinstance(value, str) for value in algorithms):
        raise ConformanceError("id_token_signing_algorithms_unavailable")
    usable_algorithms = tuple(value for value in algorithms if value in {"RS256", "RS384", "RS512", "PS256", "PS384", "PS512", "ES256", "ES384", "ES512"})
    if not usable_algorithms:
        raise ConformanceError("id_token_signing_algorithms_unavailable")
    return ProviderMetadata(
        issuer=issuer,
        authorization_endpoint=authorization_endpoint,
        token_endpoint=token_endpoint,
        jwks_uri=jwks_uri,
        id_token_algorithms=usable_algorithms,
        response_modes=tuple(value for value in response_modes if isinstance(value, str)),
    )


def _metadata_url(document: Mapping[str, Any], field: str) -> str:
    value = document.get(field)
    if not isinstance(value, str):
        raise ConformanceError("invalid_discovery_document")
    _https_url(value, "invalid_discovery_document")
    return value


def _fetch_json(url: str, failure_code: str) -> dict[str, Any]:
    _https_url(url, failure_code)
    request = urllib.request.Request(
        url,
        headers={"Accept": "application/json", "User-Agent": "Hormuz-OIDC-Reference/0.1"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            _https_url(response.geturl(), failure_code)
            body = response.read(_MAX_METADATA_BYTES + 1)
    except urllib.error.HTTPError as error:
        error.close()
        raise ConformanceError(failure_code) from error
    except (urllib.error.URLError, TimeoutError, ValueError) as error:
        raise ConformanceError(failure_code) from error
    if len(body) > _MAX_METADATA_BYTES:
        raise ConformanceError(failure_code)
    try:
        document = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError, RecursionError) as error:
        raise ConformanceError(failure_code) from error
    if not isinstance(document, dict):
        raise ConformanceError(failure_code)
    return document


def _pkce_verifier() -> str:
    # RFC 7636 permits 43–128 URL-safe characters.  No padding avoids a
    # non-portable verifier representation.
    return _random_urlsafe(64)


def _random_urlsafe(bytes_count: int) -> str:
    return secrets.token_urlsafe(bytes_count).rstrip("=")


def _pkce_challenge(verifier: str) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).rstrip(b"=").decode("ascii")


def _authorization_url(
    *,
    metadata: ProviderMetadata,
    client_id: str,
    redirect_uri: str,
    verifier: str,
    state: str,
    nonce: str,
) -> str:
    query = {
        "client_id": client_id,
        "response_type": "code",
        "response_mode": "form_post",
        "scope": "openid profile",
        "redirect_uri": redirect_uri,
        "state": state,
        "nonce": nonce,
        "code_challenge": _pkce_challenge(verifier),
        "code_challenge_method": "S256",
    }
    separator = "&" if urllib.parse.urlsplit(metadata.authorization_endpoint).query else "?"
    return f"{metadata.authorization_endpoint}{separator}{urllib.parse.urlencode(query)}"


def _single_form_value(form: Mapping[str, list[str]], key: str) -> str | None:
    values = form.get(key)
    if values is None or len(values) != 1:
        return None
    value = values[0]
    if not isinstance(value, str) or len(value.encode("utf-8")) > _MAX_TOKEN_BYTES:
        return None
    return value


def _exchange_code(
    *,
    metadata: ProviderMetadata,
    client_id: str,
    redirect_uri: str,
    verifier: str,
    code: str,
) -> dict[str, Any]:
    if len(code.encode("utf-8")) > _MAX_TOKEN_BYTES:
        raise ConformanceError("authorization_code_invalid")
    encoded = urllib.parse.urlencode(
        {
            "grant_type": "authorization_code",
            "client_id": client_id,
            "code": code,
            "redirect_uri": redirect_uri,
            "code_verifier": verifier,
        }
    ).encode("ascii")
    request = urllib.request.Request(
        metadata.token_endpoint,
        data=encoded,
        headers={
            "Accept": "application/json",
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": "Hormuz-OIDC-Reference/0.1",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            _https_url(response.geturl(), "token_exchange_failed")
            body = response.read(_MAX_METADATA_BYTES + 1)
    except urllib.error.HTTPError as error:
        error.close()
        raise ConformanceError("token_exchange_failed") from error
    except (urllib.error.URLError, TimeoutError, ValueError) as error:
        raise ConformanceError("token_exchange_failed") from error
    if len(body) > _MAX_METADATA_BYTES:
        raise ConformanceError("token_exchange_failed")
    try:
        result = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError, RecursionError) as error:
        raise ConformanceError("token_exchange_failed") from error
    if not isinstance(result, dict):
        raise ConformanceError("token_exchange_failed")
    return result


def _token_value(response: Mapping[str, Any], field: str, failure_code: str) -> str:
    value = response.get(field)
    if not isinstance(value, str) or not value or len(value.encode("utf-8")) > _MAX_TOKEN_BYTES:
        raise ConformanceError(failure_code)
    return value


def _validate_id_token(*, metadata: ProviderMetadata, token: str, client_id: str, nonce: str) -> None:
    try:
        header = jwt.get_unverified_header(token)
        key_id = header.get("kid")
        algorithm = header.get("alg")
    except jwt.PyJWTError as error:
        raise ConformanceError("id_token_invalid") from error
    if not isinstance(key_id, str) or not key_id or algorithm not in metadata.id_token_algorithms:
        raise ConformanceError("id_token_invalid")
    document = _fetch_json(metadata.jwks_uri, "jwks_unavailable")
    raw_keys = document.get("keys")
    if not isinstance(raw_keys, list):
        raise ConformanceError("jwks_unavailable")
    key: jwt.PyJWK | None = None
    for candidate in raw_keys:
        if not isinstance(candidate, dict) or candidate.get("kid") != key_id:
            continue
        try:
            parsed = jwt.PyJWK.from_dict(candidate)
        except (jwt.PyJWTError, TypeError, ValueError):
            continue
        if parsed.algorithm_name == algorithm:
            key = parsed
            break
    if key is None:
        raise ConformanceError("id_token_signing_key_unavailable")
    try:
        claims = jwt.decode(
            token,
            key,
            algorithms=[algorithm],
            audience=client_id,
            issuer=metadata.issuer,
            options={"require": ["exp", "iss", "aud", "sub", "nonce"]},
        )
    except jwt.PyJWTError as error:
        raise ConformanceError("id_token_invalid") from error
    if claims.get("nonce") != nonce:
        raise ConformanceError("id_token_nonce_mismatch")
    _validate_authorized_party(claims, client_id)


def _validate_authorized_party(claims: Mapping[str, Any], client_id: str) -> None:
    audience = claims.get("aud")
    authorized_party = claims.get("azp")
    if isinstance(audience, list) and len(audience) > 1:
        if not isinstance(authorized_party, str) or not hmac.compare_digest(authorized_party, client_id):
            raise ConformanceError("id_token_authorized_party_mismatch")
    elif authorized_party is not None and (
        not isinstance(authorized_party, str) or not hmac.compare_digest(authorized_party, client_id)
    ):
        raise ConformanceError("id_token_authorized_party_mismatch")


def _unverified_subject(access_token: str) -> str:
    try:
        claims = jwt.decode(
            access_token,
            options={
                "verify_signature": False,
                "verify_exp": False,
                "verify_nbf": False,
                "verify_iat": False,
                "verify_aud": False,
                "verify_iss": False,
            },
        )
    except jwt.PyJWTError as error:
        raise ConformanceError("access_token_invalid") from error
    subject = claims.get("sub")
    return _required_short_value(subject, "access_token_subject_missing")


def _verify_with_hormuz(
    *,
    issuer: str,
    audience: str,
    subject: str,
    access_token: str,
    expected_identity: Mapping[str, str],
    display_identity: Mapping[str, str],
) -> None:
    with tempfile.TemporaryDirectory(prefix="hormuz-oidc-reference-") as temporary:
        root = Path(temporary)
        config_path = root / "reference.json"
        config_path.write_text(
            json.dumps(
                _gateway_config_value(
                    issuer=issuer,
                    audience=audience,
                    subject=subject,
                    expected_identity=expected_identity,
                    display_identity=display_identity,
                ),
                separators=(",", ":"),
            ),
            encoding="utf-8",
        )
        config_path.chmod(0o600)
        try:
            config = GatewayConfig.load(config_path)
            Authenticator(config).validate_metadata()
        except (AuthenticationError, ValueError) as error:
            raise ConformanceError("hormuz_metadata_validation_failed") from error
        gateway = GatewayServer(config=_with_loopback_listener(config))
        gateway_thread = serve_in_thread(gateway)
        try:
            base_url = f"http://127.0.0.1:{gateway.server_address[1]}"
            identity = _gateway_whoami(base_url=base_url, token=access_token)
            for field, expected in expected_identity.items():
                if identity.get(field) != expected:
                    raise ConformanceError("gateway_identity_mapping_mismatch")
            if identity.get("authentication_source") != f"oidc:{issuer}":
                raise ConformanceError("gateway_authentication_source_mismatch")
            if "token" in identity or "subject" in identity:
                raise ConformanceError("gateway_identity_disclosure")
            if _gateway_status(base_url=base_url, token=_tamper_jwt(access_token)) != HTTPStatus.UNAUTHORIZED:
                raise ConformanceError("tampered_access_token_accepted")
        finally:
            gateway.shutdown()
            gateway.server_close()
            gateway_thread.join(timeout=5)


def _with_loopback_listener(config: GatewayConfig) -> GatewayConfig:
    return GatewayConfig(
        source_path=config.source_path,
        listen=ListenConfig(host="127.0.0.1", port=0),
        database_path=config.database_path,
        upstreams=config.upstreams,
        identities_by_token=config.identities_by_token,
        model_routes=config.model_routes,
        organization_policy=config.organization_policy,
        oidc_issuers=config.oidc_issuers,
        identities_by_subject=config.identities_by_subject,
        secret_controls=config.secret_controls,
        team_policies=config.team_policies,
        actor_policies=config.actor_policies,
        max_request_bytes=config.max_request_bytes,
        upstream_timeout_seconds=config.upstream_timeout_seconds,
        usage_storage=config.usage_storage,
        policy_control=config.policy_control,
    )


def _gateway_config_value(
    *,
    issuer: str,
    audience: str,
    subject: str,
    expected_identity: Mapping[str, str],
    display_identity: Mapping[str, str],
) -> dict[str, Any]:
    return {
        "listen": {"host": "127.0.0.1", "port": 8787},
        "database": "./usage.sqlite3",
        "upstreams": {
            "openai": {"base_url": "https://api.openai.invalid", "api_key_env": "OPENAI_API_KEY"},
            "anthropic": {"base_url": "https://api.anthropic.invalid", "api_key_env": "ANTHROPIC_API_KEY"},
        },
        "authentication": {
            "oidc": {
                "issuers": [
                    {
                        "issuer": issuer,
                        "audiences": [audience],
                        "algorithms": ["RS256"],
                        "clock_skew_seconds": 60,
                        "subjects": [
                            {
                                "subject": subject,
                                "actor_id": expected_identity["actor_id"],
                                "actor_name": display_identity["actor_name"],
                                "team_id": expected_identity["team_id"],
                                "team_name": display_identity["team_name"],
                                "organization_id": expected_identity["organization_id"],
                                "clearance": "confidential",
                                "allowed_clients": ["codex", "claude-code"],
                            }
                        ],
                    }
                ]
            }
        },
        "model_routes": {
            "gpt-reference": {"protocol": "openai", "upstream_model": "gpt-reference"},
            "claude-reference": {"protocol": "anthropic", "upstream_model": "claude-reference"},
        },
        "policies": {
            "organization": {
                "allowed_clients": ["codex", "claude-code"],
                "allowed_models": ["gpt-reference", "claude-reference"],
                "max_output_tokens": 100,
            },
            "teams": {},
            "actors": {},
        },
    }


def _gateway_whoami(*, base_url: str, token: str) -> dict[str, Any]:
    request = urllib.request.Request(
        f"{base_url}/v1/gateway/whoami",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            if response.status != HTTPStatus.OK:
                raise ConformanceError("gateway_identity_request_failed")
            body = response.read(_MAX_METADATA_BYTES + 1)
    except urllib.error.HTTPError as error:
        error.close()
        raise ConformanceError("gateway_identity_request_failed") from error
    except (urllib.error.URLError, TimeoutError, ValueError) as error:
        raise ConformanceError("gateway_identity_request_failed") from error
    if len(body) > _MAX_METADATA_BYTES:
        raise ConformanceError("gateway_identity_request_failed")
    try:
        value = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError, RecursionError) as error:
        raise ConformanceError("gateway_identity_request_failed") from error
    if not isinstance(value, dict):
        raise ConformanceError("gateway_identity_request_failed")
    return value


def _gateway_status(*, base_url: str, token: str) -> int:
    request = urllib.request.Request(
        f"{base_url}/v1/gateway/whoami",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status
    except urllib.error.HTTPError as error:
        try:
            return error.code
        finally:
            error.close()
    except (urllib.error.URLError, TimeoutError, ValueError) as error:
        raise ConformanceError("gateway_identity_request_failed") from error


def _tamper_jwt(token: str) -> str:
    if token.count(".") != 2:
        raise ConformanceError("access_token_invalid")
    header, payload, signature = token.split(".")
    if not header or not payload or not signature:
        raise ConformanceError("access_token_invalid")
    replacement = "A" if signature[0] != "A" else "B"
    return f"{header}.{payload}.{replacement}{signature[1:]}"


if __name__ == "__main__":  # pragma: no cover - exercised as an executable tool
    raise SystemExit(main())
