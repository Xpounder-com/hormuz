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
class ControlPrincipal:
    """Credential identity suitable for governed policy-control authorization.

    Runtime gateway authorization still resolves a full configured ``Identity``.
    The policy service deliberately uses a narrower stable key: a configured
    static actor, or an OIDC issuer/subject pair. It never derives root policy
    authority from e-mail, username, group, or arbitrary token claims.
    """

    authentication_kind: str
    actor_id: str | None = None
    organization_id: str | None = None
    issuer: str | None = None
    subject: str | None = None


class Authenticator:
    """Resolve static or OIDC JWT bearer credentials to configured identities."""

    def __init__(self, config: GatewayConfig):
        self._config = config
        self._lock = threading.Lock()
        self._jwks_uris: dict[str, tuple[float, str]] = {}
        self._key_sets: dict[str, _KeySet] = {}
        self._unknown_key_refreshes: dict[str, float] = {}

    def authenticate(self, token: str) -> Identity:
        principal = self.authenticate_control(token)
        if principal.authentication_kind == "static":
            identity = self._config.identity_for_token(token)
            if identity is None:  # pragma: no cover - static lookup was just authenticated
                raise AuthenticationError("invalid_credential")
            return identity
        assert principal.issuer is not None and principal.subject is not None
        identity = self._config.identity_for_subject(principal.issuer, principal.subject)
        if identity is None:
            # A verified OIDC token is not automatically a gateway identity.
            # The runtime policy boundary remains the configured subject map.
            raise AuthenticationError("unmapped_subject")
        return identity

    def authenticate_control(self, token: str) -> ControlPrincipal:
        """Verify a credential for the governed policy-control service.

        OIDC callers are returned as a verified issuer/subject principal even
        when they have no runtime inference identity. The policy service then
        resolves root authority from its tenant-scoped PostgreSQL records.
        """

        if not token or len(token.encode("utf-8")) > _MAX_TOKEN_BYTES:
            raise AuthenticationError("invalid_credential")
        for configured_token, identity in self._config.identities_by_token.items():
            if hmac.compare_digest(token, configured_token):
                return ControlPrincipal(
                    authentication_kind="static",
                    actor_id=identity.actor_id,
                    organization_id=identity.organization_id,
                )
        if not self._config.oidc_issuers:
            raise AuthenticationError("invalid_credential")
        issuer, subject = self._verify_oidc_subject(token)
        return ControlPrincipal(authentication_kind="oidc", issuer=issuer, subject=subject)

    def validate_metadata(self) -> dict[str, int]:
        """Fetch and validate discovery/JWKS metadata for configured issuers."""
        result: dict[str, int] = {}
        for issuer in self._config.oidc_issuers.values():
            result[issuer.issuer] = len(self._key_set(issuer, force_refresh=False).keys_by_id)
        return result

    def _verify_oidc_subject(self, token: str) -> tuple[str, str]:
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

        key = self._signing_key(issuer, key_id)
        try:
            claims = jwt.decode(
                token,
                key,
                algorithms=list(issuer.algorithms),
                audience=list(issuer.audiences),
                issuer=issuer.issuer,
                leeway=issuer.clock_skew_seconds,
                options={"require": ["exp", "iss", "aud", "sub"]},
            )
        except jwt.PyJWTError as error:
            raise AuthenticationError("jwt_validation_failed") from error
        subject = claims.get("sub")
        if not isinstance(subject, str) or not subject:
            raise AuthenticationError("invalid_subject")
        return issuer.issuer, subject

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
        discovery_url = issuer.issuer.rstrip("/") + "/.well-known/openid-configuration"
        document = _fetch_json(
            discovery_url,
            allow_insecure_http=issuer.allow_insecure_http,
            maximum_bytes=_MAX_DISCOVERY_BYTES,
        )
        discovered_issuer = document.get("issuer")
        jwks_uri = document.get("jwks_uri")
        if discovered_issuer != issuer.issuer or not isinstance(jwks_uri, str):
            raise AuthenticationError("invalid_discovery_document")
        _validate_remote_url(jwks_uri, allow_insecure_http=issuer.allow_insecure_http)
        self._jwks_uris[issuer.issuer] = (now, jwks_uri)
        return jwks_uri


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


def _validate_remote_url(url: str, *, allow_insecure_http: bool) -> None:
    parsed = urlparse(url)
    if parsed.scheme == "https" and parsed.netloc and not parsed.username and not parsed.password:
        return
    if (
        allow_insecure_http
        and parsed.scheme == "http"
        and parsed.hostname in {"127.0.0.1", "::1", "localhost"}
        and parsed.netloc
        and not parsed.username
        and not parsed.password
    ):
        return
    raise AuthenticationError("unsafe_oidc_metadata_url")
