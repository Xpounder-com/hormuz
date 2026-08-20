from __future__ import annotations

import hmac
import json
import logging
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import jwt

from .config import GatewayConfig, Identity, OIDCIssuerConfig


LOGGER = logging.getLogger("hormuz.auth")
_MAX_TOKEN_BYTES = 64 * 1024
_MAX_DISCOVERY_BYTES = 128 * 1024
_MAX_JWKS_BYTES = 1024 * 1024
_NETWORK_TIMEOUT_SECONDS = 5
_UNKNOWN_KEY_REFRESH_COOLDOWN_SECONDS = 30


class AuthenticationError(ValueError):
    """A deliberately content-free authentication failure."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class _KeySet:
    fetched_at: float
    keys_by_id: dict[str, jwt.PyJWK]


@dataclass(frozen=True)
class OIDCProviderMetadata:
    authorization_endpoint: str
    token_endpoint: str


class Authenticator:
    """Resolve static or OIDC JWT bearer credentials to configured identities."""

    def __init__(self, config: GatewayConfig):
        self._config = config
        self._lock = threading.Lock()
        self._jwks_uris: dict[str, tuple[float, str]] = {}
        self._discovery_documents: dict[str, tuple[float, dict[str, Any]]] = {}
        self._key_sets: dict[str, _KeySet] = {}
        self._unknown_key_refreshes: dict[str, float] = {}

    def authenticate(self, token: str) -> Identity:
        if not token or len(token.encode("utf-8")) > _MAX_TOKEN_BYTES:
            raise AuthenticationError("invalid_credential")
        for configured_token, identity in self._config.identities_by_token.items():
            if hmac.compare_digest(token, configured_token):
                return identity
        if not self._config.oidc_issuers:
            raise AuthenticationError("invalid_credential")
        return self._authenticate_oidc(token)

    def validate_metadata(self) -> dict[str, int]:
        """Fetch and validate discovery/JWKS metadata for configured issuers."""
        result: dict[str, int] = {}
        for issuer in self._config.oidc_issuers.values():
            result[issuer.issuer] = len(self._key_set(issuer, force_refresh=False).keys_by_id)
            if issuer.login is not None:
                self._validate_login_capabilities(issuer)
        return result

    def login_metadata(self, issuer_name: str) -> OIDCProviderMetadata:
        issuer = self._config.oidc_issuers.get(issuer_name)
        if issuer is None or issuer.login is None:
            raise AuthenticationError("login_issuer_unavailable")
        document = self._discovery_document(issuer, force_refresh=False)
        authorization_endpoint = document.get("authorization_endpoint")
        token_endpoint = document.get("token_endpoint")
        if not isinstance(authorization_endpoint, str) or not isinstance(token_endpoint, str):
            raise AuthenticationError("invalid_discovery_document")
        _validate_remote_url(
            authorization_endpoint,
            allow_insecure_http=issuer.allow_insecure_http,
        )
        _validate_remote_url(token_endpoint, allow_insecure_http=issuer.allow_insecure_http)
        return OIDCProviderMetadata(
            authorization_endpoint=authorization_endpoint,
            token_endpoint=token_endpoint,
        )

    def _validate_login_capabilities(self, issuer: OIDCIssuerConfig) -> None:
        """Fail deployment preflight when discovery cannot support the login flow."""
        if issuer.login is None:
            return
        document = self._discovery_document(issuer, force_refresh=False)
        self.login_metadata(issuer.issuer)
        response_types = _metadata_string_set(document, "response_types_supported")
        if "code" not in response_types:
            raise AuthenticationError("oidc_authorization_code_unsupported")
        grant_types = document.get("grant_types_supported")
        if grant_types is not None and "authorization_code" not in _metadata_string_set(
            document,
            "grant_types_supported",
        ):
            raise AuthenticationError("oidc_authorization_code_unsupported")
        if "S256" not in _metadata_string_set(
            document,
            "code_challenge_methods_supported",
        ):
            raise AuthenticationError("oidc_pkce_s256_unsupported")
        signing_algorithms = _metadata_string_set(
            document,
            "id_token_signing_alg_values_supported",
        )
        if not signing_algorithms.intersection(issuer.algorithms):
            raise AuthenticationError("oidc_id_token_signing_unsupported")
        auth_methods = document.get("token_endpoint_auth_methods_supported")
        if auth_methods is None:
            supported_auth_methods = {"client_secret_basic"}
        else:
            supported_auth_methods = _metadata_string_set(
                document,
                "token_endpoint_auth_methods_supported",
            )
        if issuer.login.token_endpoint_auth_method not in supported_auth_methods:
            raise AuthenticationError("oidc_token_endpoint_auth_unsupported")

    def validate_login_id_token(
        self,
        token: str,
        *,
        issuer_name: str,
        nonce: str,
    ) -> str:
        issuer = self._config.oidc_issuers.get(issuer_name)
        if issuer is None or issuer.login is None:
            raise AuthenticationError("login_issuer_unavailable")
        claims = self._validate_jwt(
            token,
            issuer=issuer,
            audiences=(issuer.login.client_id,),
            required_claims=("exp", "iat", "iss", "aud", "sub", "nonce"),
        )
        token_nonce = claims.get("nonce")
        if not isinstance(token_nonce, str) or not hmac.compare_digest(token_nonce, nonce):
            raise AuthenticationError("id_token_nonce_mismatch")
        audience = claims.get("aud")
        authorized_party = claims.get("azp")
        if isinstance(audience, list) and len(audience) > 1:
            if authorized_party != issuer.login.client_id:
                raise AuthenticationError("id_token_authorized_party_mismatch")
        elif authorized_party is not None and authorized_party != issuer.login.client_id:
            raise AuthenticationError("id_token_authorized_party_mismatch")
        subject = claims.get("sub")
        if not isinstance(subject, str) or not subject:
            raise AuthenticationError("invalid_subject")
        if self._config.identity_for_subject(issuer.issuer, subject) is None:
            raise AuthenticationError("unmapped_subject")
        return subject

    def _authenticate_oidc(self, token: str) -> Identity:
        if token.count(".") != 2:
            raise AuthenticationError("malformed_jwt")
        try:
            header = jwt.get_unverified_header(token)
            unverified = jwt.decode(
                token,
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
            raise AuthenticationError("malformed_jwt") from error
        issuer_name = unverified.get("iss")
        algorithm = header.get("alg")
        key_id = header.get("kid")
        if not isinstance(issuer_name, str) or not isinstance(algorithm, str) or not isinstance(key_id, str):
            raise AuthenticationError("invalid_jwt_header_or_issuer")
        issuer = self._config.oidc_issuers.get(issuer_name)
        if issuer is None:
            raise AuthenticationError("untrusted_issuer")
        if algorithm not in issuer.algorithms:
            raise AuthenticationError("disallowed_algorithm")
        if not key_id:
            raise AuthenticationError("missing_key_id")

        claims = self._validate_jwt(
            token,
            issuer=issuer,
            audiences=issuer.audiences,
            required_claims=("exp", "iss", "aud", "sub"),
            parsed_header=(algorithm, key_id),
        )
        subject = claims.get("sub")
        if not isinstance(subject, str) or not subject:
            raise AuthenticationError("invalid_subject")
        identity = self._config.identity_for_subject(issuer.issuer, subject)
        if identity is None:
            raise AuthenticationError("unmapped_subject")
        return identity

    def _validate_jwt(
        self,
        token: str,
        *,
        issuer: OIDCIssuerConfig,
        audiences: tuple[str, ...],
        required_claims: tuple[str, ...],
        parsed_header: tuple[str, str] | None = None,
    ) -> dict[str, Any]:
        if not token or len(token.encode("utf-8")) > _MAX_TOKEN_BYTES or token.count(".") != 2:
            raise AuthenticationError("malformed_jwt")
        if parsed_header is None:
            try:
                header = jwt.get_unverified_header(token)
                unverified = jwt.decode(
                    token,
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
                raise AuthenticationError("malformed_jwt") from error
            algorithm = header.get("alg")
            key_id = header.get("kid")
            if unverified.get("iss") != issuer.issuer:
                raise AuthenticationError("untrusted_issuer")
            if not isinstance(algorithm, str) or not isinstance(key_id, str):
                raise AuthenticationError("invalid_jwt_header_or_issuer")
        else:
            algorithm, key_id = parsed_header
        if algorithm not in issuer.algorithms:
            raise AuthenticationError("disallowed_algorithm")
        if not key_id:
            raise AuthenticationError("missing_key_id")
        key = self._signing_key(issuer, key_id)
        try:
            claims = jwt.decode(
                token,
                key,
                algorithms=list(issuer.algorithms),
                audience=list(audiences),
                issuer=issuer.issuer,
                leeway=issuer.clock_skew_seconds,
                options={"require": list(required_claims)},
            )
        except jwt.PyJWTError as error:
            raise AuthenticationError("jwt_validation_failed") from error
        return claims

    def _signing_key(self, issuer: OIDCIssuerConfig, key_id: str) -> jwt.PyJWK:
        key_set = self._key_set(issuer, force_refresh=False)
        key = key_set.keys_by_id.get(key_id)
        if key is not None:
            return key
        # A single refresh supports normal IdP signing-key rotation without
        # turning every attacker-controlled key identifier into network traffic.
        now = time.monotonic()
        with self._lock:
            last_refresh = self._unknown_key_refreshes.get(issuer.issuer)
            if last_refresh is not None and now - last_refresh < _UNKNOWN_KEY_REFRESH_COOLDOWN_SECONDS:
                raise AuthenticationError("unknown_signing_key")
            self._unknown_key_refreshes[issuer.issuer] = now
        key_set = self._key_set(issuer, force_refresh=True)
        key = key_set.keys_by_id.get(key_id)
        if key is None:
            raise AuthenticationError("unknown_signing_key")
        return key

    def _key_set(self, issuer: OIDCIssuerConfig, *, force_refresh: bool) -> _KeySet:
        now = time.monotonic()
        with self._lock:
            cached = self._key_sets.get(issuer.issuer)
            if (
                not force_refresh
                and cached is not None
                and now - cached.fetched_at < issuer.discovery_cache_seconds
            ):
                return cached
            jwks_uri = self._jwks_uri(issuer, now=now, force_refresh=force_refresh)
            document = _fetch_json(
                jwks_uri,
                allow_insecure_http=issuer.allow_insecure_http,
                maximum_bytes=_MAX_JWKS_BYTES,
            )
            raw_keys = document.get("keys")
            if not isinstance(raw_keys, list):
                raise AuthenticationError("invalid_jwks")
            keys_by_id: dict[str, jwt.PyJWK] = {}
            for raw_key in raw_keys:
                if not isinstance(raw_key, dict):
                    continue
                raw_key_id = raw_key.get("kid")
                if not isinstance(raw_key_id, str) or not raw_key_id or raw_key_id in keys_by_id:
                    continue
                try:
                    parsed_key = jwt.PyJWK.from_dict(raw_key)
                except (jwt.PyJWTError, ValueError, TypeError):
                    continue
                if parsed_key.algorithm_name not in issuer.algorithms:
                    continue
                keys_by_id[raw_key_id] = parsed_key
            if not keys_by_id:
                raise AuthenticationError("no_usable_signing_keys")
            key_set = _KeySet(fetched_at=now, keys_by_id=keys_by_id)
            self._key_sets[issuer.issuer] = key_set
            return key_set

    def _jwks_uri(self, issuer: OIDCIssuerConfig, *, now: float, force_refresh: bool) -> str:
        if issuer.jwks_uri is not None:
            return issuer.jwks_uri
        cached = self._jwks_uris.get(issuer.issuer)
        if (
            not force_refresh
            and cached is not None
            and now - cached[0] < issuer.discovery_cache_seconds
        ):
            return cached[1]
        document = self._discovery_document(issuer, force_refresh=force_refresh)
        jwks_uri = document.get("jwks_uri")
        if not isinstance(jwks_uri, str):
            raise AuthenticationError("invalid_discovery_document")
        _validate_remote_url(jwks_uri, allow_insecure_http=issuer.allow_insecure_http)
        self._jwks_uris[issuer.issuer] = (now, jwks_uri)
        return jwks_uri

    def _discovery_document(
        self,
        issuer: OIDCIssuerConfig,
        *,
        force_refresh: bool,
    ) -> dict[str, Any]:
        now = time.monotonic()
        cached = self._discovery_documents.get(issuer.issuer)
        if (
            not force_refresh
            and cached is not None
            and now - cached[0] < issuer.discovery_cache_seconds
        ):
            return cached[1]
        discovery_url = issuer.issuer.rstrip("/") + "/.well-known/openid-configuration"
        document = _fetch_json(
            discovery_url,
            allow_insecure_http=issuer.allow_insecure_http,
            maximum_bytes=_MAX_DISCOVERY_BYTES,
        )
        if document.get("issuer") != issuer.issuer:
            raise AuthenticationError("invalid_discovery_document")
        self._discovery_documents[issuer.issuer] = (now, document)
        return document


def _fetch_json(url: str, *, allow_insecure_http: bool, maximum_bytes: int) -> dict[str, Any]:
    _validate_remote_url(url, allow_insecure_http=allow_insecure_http)
    request = urllib.request.Request(
        url,
        headers={"Accept": "application/json", "User-Agent": "Hormuz-OIDC/0.1"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=_NETWORK_TIMEOUT_SECONDS) as response:
            _validate_remote_url(response.geturl(), allow_insecure_http=allow_insecure_http)
            body = response.read(maximum_bytes + 1)
    except (urllib.error.URLError, TimeoutError, ValueError) as error:
        LOGGER.warning("oidc_metadata_fetch_failed url_host=%s", urlparse(url).hostname)
        raise AuthenticationError("oidc_metadata_unavailable") from error
    if len(body) > maximum_bytes:
        raise AuthenticationError("oidc_metadata_too_large")
    try:
        document = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError, RecursionError) as error:
        raise AuthenticationError("invalid_oidc_metadata") from error
    if not isinstance(document, dict):
        raise AuthenticationError("invalid_oidc_metadata")
    return document


def _metadata_string_set(document: dict[str, Any], key: str) -> set[str]:
    value = document.get(key)
    if not isinstance(value, list) or not value or not all(
        isinstance(item, str) and item for item in value
    ):
        raise AuthenticationError("invalid_discovery_document")
    return set(value)


def _validate_remote_url(url: str, *, allow_insecure_http: bool) -> None:
    parsed = urlparse(url)
    safe_shape = (
        parsed.netloc
        and not parsed.username
        and not parsed.password
        and not parsed.fragment
        and not any(ord(character) < 32 or ord(character) == 127 for character in url)
    )
    if parsed.scheme == "https" and safe_shape:
        return
    if (
        allow_insecure_http
        and parsed.scheme == "http"
        and parsed.hostname in {"127.0.0.1", "::1", "localhost"}
        and safe_shape
    ):
        return
    raise AuthenticationError("unsafe_oidc_metadata_url")
