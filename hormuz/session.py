from __future__ import annotations

import base64
import hashlib
import json
import logging
import secrets
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import replace
from datetime import datetime
from typing import Any

from .auth import AuthenticationError, Authenticator, _validate_remote_url
from .config import GatewayConfig, Identity
from .session_store import (
    Enrollment,
    SQLiteSessionStore,
    SessionCredentialPair,
    SessionSecurityEvent,
    SessionSummary,
    SessionStoreError,
)


LOGGER = logging.getLogger("hormuz.session")
_MAX_TOKEN_RESPONSE_BYTES = 128 * 1024
_TOKEN_EXCHANGE_TIMEOUT_SECONDS = 10
_SUPPORTED_CLIENTS = {"codex", "claude-code"}


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class SessionBrokerError(RuntimeError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class SessionBroker:
    """OIDC authorization-code broker backed by opaque Hormuz credentials."""

    def __init__(
        self,
        config: GatewayConfig,
        authenticator: Authenticator,
        store: SQLiteSessionStore,
    ):
        if not config.session_broker.enabled or config.session_broker.public_base_url is None:
            raise SessionBrokerError("session_broker_disabled")
        self.config = config
        self.authenticator = authenticator
        self.store = store
        self.callback_url = config.session_broker.public_base_url + "/v1/auth/callback"

    def create_enrollment(
        self,
        *,
        issuer_name: str | None,
        client_name: str,
        enrollment_secret: str,
    ) -> tuple[Enrollment, str]:
        if client_name not in _SUPPORTED_CLIENTS:
            raise SessionBrokerError("unsupported_client")
        login_issuers = [
            issuer for issuer in self.config.oidc_issuers.values() if issuer.login is not None
        ]
        if issuer_name is None:
            if len(login_issuers) != 1:
                raise SessionBrokerError("issuer_required")
            issuer = login_issuers[0]
        else:
            issuer = self.config.oidc_issuers.get(issuer_name)
            if issuer is None or issuer.login is None:
                raise SessionBrokerError("login_issuer_unavailable")
        enrollment = self.store.create_enrollment(
            issuer=issuer.issuer,
            client_name=client_name,
            enrollment_secret=enrollment_secret,
        )
        login_url = (
            self.config.session_broker.public_base_url
            + "/v1/auth/login?"
            + urllib.parse.urlencode({"enrollment": enrollment.enrollment_id})
        )
        return enrollment, login_url

    def begin_authorization(self, enrollment_id: str) -> tuple[str, str]:
        state = secrets.token_urlsafe(32)
        browser_cookie = secrets.token_urlsafe(32)
        nonce = secrets.token_urlsafe(32)
        verifier = secrets.token_urlsafe(64)
        flow = self.store.begin_authorization(
            enrollment_id=enrollment_id,
            state=state,
            browser_cookie=browser_cookie,
            nonce=nonce,
            pkce_verifier=verifier,
        )
        issuer = self.config.oidc_issuers.get(flow.issuer)
        if issuer is None or issuer.login is None:
            self.store.fail_enrollment(enrollment_id=enrollment_id)
            raise SessionBrokerError("login_issuer_unavailable")
        try:
            metadata = self.authenticator.login_metadata(flow.issuer)
        except AuthenticationError as error:
            self.store.fail_enrollment(enrollment_id=enrollment_id)
            raise SessionBrokerError(error.code) from error
        challenge = base64.urlsafe_b64encode(
            hashlib.sha256(verifier.encode("ascii")).digest()
        ).rstrip(b"=").decode("ascii")
        try:
            authorization_url = _build_authorization_url(
                metadata.authorization_endpoint,
                {
                    "response_type": "code",
                    "client_id": issuer.login.client_id,
                    "redirect_uri": self.callback_url,
                    "scope": " ".join(issuer.login.scopes),
                    "state": state,
                    "nonce": nonce,
                    "code_challenge": challenge,
                    "code_challenge_method": "S256",
                },
            )
        except SessionBrokerError:
            self.store.fail_enrollment(enrollment_id=enrollment_id)
            raise
        return authorization_url, browser_cookie

    def complete_authorization(
        self,
        *,
        state: str,
        browser_cookie: str,
        code: str | None,
        provider_error: str | None = None,
        response_issuer: str | None = None,
    ) -> None:
        try:
            flow = self.store.consume_callback(state=state, browser_cookie=browser_cookie)
        except SessionStoreError as error:
            raise SessionBrokerError(error.code) from error
        if provider_error is not None:
            self.store.fail_enrollment(enrollment_id=flow.enrollment_id)
            raise SessionBrokerError("authorization_denied")
        if response_issuer is not None and response_issuer != flow.issuer:
            self.store.fail_enrollment(enrollment_id=flow.enrollment_id)
            raise SessionBrokerError("authorization_issuer_mismatch")
        if code is None or not 1 <= len(code.encode("utf-8")) <= 4096:
            self.store.fail_enrollment(enrollment_id=flow.enrollment_id)
            raise SessionBrokerError("invalid_authorization_code")
        issuer = self.config.oidc_issuers.get(flow.issuer)
        if issuer is None or issuer.login is None:
            self.store.fail_enrollment(enrollment_id=flow.enrollment_id)
            raise SessionBrokerError("login_issuer_unavailable")
        try:
            metadata = self.authenticator.login_metadata(flow.issuer)
            token_response = _exchange_code(
                metadata.token_endpoint,
                allow_insecure_http=issuer.allow_insecure_http,
                client_id=issuer.login.client_id,
                client_secret=issuer.login.client_secret,
                auth_method=issuer.login.token_endpoint_auth_method,
                redirect_uri=self.callback_url,
                code=code,
                pkce_verifier=flow.pkce_verifier,
            )
            id_token = token_response.get("id_token")
            if not isinstance(id_token, str):
                raise SessionBrokerError("id_token_missing")
            subject = self.authenticator.validate_login_id_token(
                id_token,
                issuer_name=flow.issuer,
                nonce=flow.nonce,
            )
            identity = self.config.identity_for_subject(flow.issuer, subject)
            if identity is None or (
                identity.allowed_clients and flow.client_name not in identity.allowed_clients
            ):
                raise AuthenticationError("client_not_allowed_for_subject")
            self.store.authorize_enrollment(
                enrollment_id=flow.enrollment_id,
                subject=subject,
                organization_id=identity.organization_id,
                actor_id=identity.actor_id,
                team_id=identity.team_id,
                clearance=identity.clearance,
            )
        except (AuthenticationError, SessionStoreError) as error:
            self.store.fail_enrollment(enrollment_id=flow.enrollment_id)
            raise SessionBrokerError(error.code) from error
        except SessionBrokerError:
            self.store.fail_enrollment(enrollment_id=flow.enrollment_id)
            raise

    def redeem(
        self,
        *,
        enrollment_id: str,
        enrollment_secret: str,
    ) -> SessionCredentialPair:
        try:
            return self.store.redeem_enrollment(
                enrollment_id=enrollment_id,
                enrollment_secret=enrollment_secret,
            )
        except SessionStoreError as error:
            raise SessionBrokerError(error.code) from error

    def refresh(self, refresh_token: str) -> SessionCredentialPair:
        try:
            return self.store.refresh(refresh_token)
        except SessionStoreError as error:
            raise SessionBrokerError(error.code) from error

    def revoke(self, credential: str) -> bool:
        try:
            return self.store.revoke(credential)
        except SessionStoreError as error:
            raise SessionBrokerError(error.code) from error

    def list_active_sessions(
        self,
        *,
        administrator: Identity,
        limit: int,
        cursor: str | None = None,
        actor_id: str | None = None,
        team_id: str | None = None,
    ) -> tuple[tuple[SessionSummary, ...], str | None]:
        if "session_admin" not in administrator.capabilities:
            raise SessionBrokerError("session_admin_capability_required")
        try:
            return self.store.list_active_sessions(
                organization_id=administrator.organization_id,
                limit=limit,
                cursor=cursor,
                actor_id=actor_id,
                team_id=team_id,
            )
        except SessionStoreError as error:
            raise SessionBrokerError(error.code) from error

    def revoke_administratively(
        self,
        *,
        administrator: Identity,
        scope: str,
        target: str | None,
        reason_code: str,
    ) -> int:
        if "session_admin" not in administrator.capabilities:
            raise SessionBrokerError("session_admin_capability_required")
        selectors: dict[str, str | None] = {
            "session_id": target if scope == "session" else None,
            "actor_id": target if scope == "actor" else None,
            "team_id": target if scope == "team" else None,
        }
        if scope not in {"session", "actor", "team", "organization"}:
            raise SessionBrokerError("invalid_admin_revocation_selector")
        if (scope == "organization") != (target is None):
            raise SessionBrokerError("invalid_admin_revocation_selector")
        try:
            return self.store.revoke_administratively(
                organization_id=administrator.organization_id,
                decision_actor_id=administrator.actor_id,
                reason_code=reason_code,
                **selectors,
            )
        except SessionStoreError as error:
            raise SessionBrokerError(error.code) from error

    def list_security_events(
        self,
        *,
        administrator: Identity,
        limit: int,
        cursor: str | None = None,
        actor_id: str | None = None,
        team_id: str | None = None,
        event_type: str | None = None,
        since: datetime | None = None,
    ) -> tuple[tuple[SessionSecurityEvent, ...], str | None]:
        if "session_admin" not in administrator.capabilities:
            raise SessionBrokerError("session_admin_capability_required")
        try:
            return self.store.list_security_events(
                organization_id=administrator.organization_id,
                limit=limit,
                cursor=cursor,
                actor_id=actor_id,
                team_id=team_id,
                event_type=event_type,
                since=since,
            )
        except SessionStoreError as error:
            raise SessionBrokerError(error.code) from error

    def authenticate(self, access_token: str) -> Identity:
        try:
            principal = self.store.authenticate_access(access_token)
        except SessionStoreError as error:
            raise AuthenticationError(error.code) from error
        identity = self.config.identity_for_subject(principal.issuer, principal.subject)
        if identity is None or (
            identity.allowed_clients and principal.client_name not in identity.allowed_clients
        ) or (
            identity.organization_id != principal.organization_id
            or identity.actor_id != principal.actor_id
            or identity.team_id != principal.team_id
            or identity.clearance != principal.clearance
        ):
            self.store.revoke_session(
                principal.session_id,
                event_type="authorization_mapping_removed",
            )
            raise AuthenticationError("session_authorization_removed")
        return replace(
            identity,
            allowed_clients=(principal.client_name,),
            authentication_source=f"session:{principal.issuer}",
        )


def _exchange_code(
    token_endpoint: str,
    *,
    allow_insecure_http: bool,
    client_id: str,
    client_secret: str,
    auth_method: str,
    redirect_uri: str,
    code: str,
    pkce_verifier: str,
) -> dict[str, Any]:
    _validate_remote_url(token_endpoint, allow_insecure_http=allow_insecure_http)
    fields = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "client_id": client_id,
        "code_verifier": pkce_verifier,
    }
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/x-www-form-urlencoded",
        "User-Agent": "Hormuz-OIDC/0.1",
    }
    if auth_method == "client_secret_basic":
        encoded_id = urllib.parse.quote_plus(client_id, safe="")
        encoded_secret = urllib.parse.quote_plus(client_secret, safe="")
        basic = base64.b64encode(
            f"{encoded_id}:{encoded_secret}".encode("ascii")
        ).decode("ascii")
        headers["Authorization"] = "Basic " + basic
    elif auth_method == "client_secret_post":
        fields["client_secret"] = client_secret
    else:
        raise SessionBrokerError("unsupported_token_endpoint_auth_method")
    request = urllib.request.Request(
        token_endpoint,
        data=urllib.parse.urlencode(fields).encode("ascii"),
        headers=headers,
        method="POST",
    )
    try:
        opener = urllib.request.build_opener(_NoRedirect)
        with opener.open(request, timeout=_TOKEN_EXCHANGE_TIMEOUT_SECONDS) as response:
            _validate_remote_url(response.geturl(), allow_insecure_http=allow_insecure_http)
            body = response.read(_MAX_TOKEN_RESPONSE_BYTES + 1)
    except (urllib.error.URLError, TimeoutError, ValueError) as error:
        LOGGER.warning(
            "oidc_token_exchange_failed endpoint_host=%s",
            urllib.parse.urlparse(token_endpoint).hostname,
        )
        raise SessionBrokerError("oidc_token_exchange_failed") from error
    if len(body) > _MAX_TOKEN_RESPONSE_BYTES:
        raise SessionBrokerError("oidc_token_response_too_large")
    try:
        value = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError, RecursionError) as error:
        raise SessionBrokerError("invalid_oidc_token_response") from error
    if not isinstance(value, dict):
        raise SessionBrokerError("invalid_oidc_token_response")
    return value


def _build_authorization_url(endpoint: str, parameters: dict[str, str]) -> str:
    parsed = urllib.parse.urlsplit(endpoint)
    try:
        existing = urllib.parse.parse_qsl(
            parsed.query,
            keep_blank_values=True,
            strict_parsing=True,
            max_num_fields=16,
        )
    except ValueError as error:
        raise SessionBrokerError("invalid_authorization_endpoint") from error
    reserved = set(parameters)
    if any(name in reserved for name, _value in existing):
        raise SessionBrokerError("invalid_authorization_endpoint")
    query = urllib.parse.urlencode([*existing, *parameters.items()])
    return urllib.parse.urlunsplit(
        (parsed.scheme, parsed.netloc, parsed.path, query, "")
    )
