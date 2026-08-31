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
from typing import Any

from .auth import AuthenticationError, Authenticator, _validate_remote_url
from .config import GatewayConfig, Identity
from .onboarding import TeamDirectory
from .session_store import (
    Enrollment,
    SQLiteSessionStore,
    SessionCredentialPair,
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
        self.directory = TeamDirectory(config, store)
        self.callback_url = config.session_broker.public_base_url + "/v1/auth/callback"

    def create_enrollment(
        self,
        *,
        issuer_name: str | None,
        organization_id: str | None = None,
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
        try:
            organizations = list(self.authenticator.organizations_for_issuer(issuer.issuer))
        except AuthenticationError as error:
            raise SessionBrokerError(error.code) from None
        if self.config.session_broker.onboarding_enabled:
            organizations = sorted(set(organizations).union(self.directory.organizations_for_issuer(issuer.issuer)))
        if organization_id is None:
            if len(organizations) != 1:
                raise SessionBrokerError("organization_required")
            organization_id = organizations[0]
        elif organization_id not in organizations:
            raise SessionBrokerError("organization_not_configured_for_issuer")
        enrollment = self.store.create_enrollment(
            issuer=issuer.issuer,
            client_name=client_name,
            enrollment_secret=enrollment_secret,
            organization_id=organization_id,
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
        return self._authorization_url(flow, state), browser_cookie

    def attach_invitation(self, *, enrollment_id: str, state: str, browser_cookie: str, code: str) -> str:
        if not self.config.session_broker.onboarding_enabled:
            raise SessionBrokerError("onboarding_disabled")
        flow = self.directory.attach_invitation(enrollment_id=enrollment_id, state=state, browser_cookie=browser_cookie, code=code)
        return self._authorization_url(flow, state)

    def _authorization_url(self, flow, state: str) -> str:
        enrollment_id = flow.enrollment_id
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
            hashlib.sha256(flow.pkce_verifier.encode("ascii")).digest()
        ).rstrip(b"=").decode("ascii")
        try:
            authorization_url = _build_authorization_url(
                metadata.authorization_endpoint,
                {
                    "response_type": "code",
                    "response_mode": "form_post",
                    "client_id": issuer.login.client_id,
                    "redirect_uri": self.callback_url,
                    "scope": " ".join(issuer.login.scopes),
                    "state": state,
                    "nonce": flow.nonce,
                    "code_challenge": challenge,
                    "code_challenge_method": "S256",
                    **({"claims": json.dumps({"id_token": {"email": {"essential": True}, "email_verified": {"essential": True}}}, separators=(",", ":"))} if flow.invitation_id else {}),
                },
            )
        except SessionBrokerError:
            self.store.fail_enrollment(enrollment_id=enrollment_id)
            raise
        return authorization_url

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
            claims = self.authenticator.validate_login_claims(
                id_token,
                issuer_name=flow.issuer,
                nonce=flow.nonce,
            )
            if self.directory.manages_organization(flow.organization_id):
                if not self.config.session_broker.onboarding_enabled:
                    raise SessionBrokerError("onboarding_disabled")
                self.directory.authorize_enrollment(flow=flow, claims=claims)
                return
            subject = claims["sub"]
            identity = self.authenticator.identity_for_subject(flow.issuer, subject)
            if identity.identity_type != "human":
                raise AuthenticationError("human_subject_required")
            if (
                identity.allowed_clients and flow.client_name not in identity.allowed_clients
            ):
                raise AuthenticationError("client_not_allowed_for_subject")
            if flow.organization_id is not None and identity.organization_id != flow.organization_id:
                raise AuthenticationError("organization_mismatch_for_subject")
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
            pair = self.store.redeem_enrollment(
                enrollment_id=enrollment_id,
                enrollment_secret=enrollment_secret,
            )
            self.authenticate(pair.access_token)
            return pair
        except (SessionStoreError, AuthenticationError) as error:
            raise SessionBrokerError(error.code) from error

    def refresh(self, refresh_token: str) -> SessionCredentialPair:
        try:
            pair = self.store.refresh(refresh_token)
            self.authenticate(pair.access_token)
            return pair
        except (SessionStoreError, AuthenticationError) as error:
            raise SessionBrokerError(error.code) from error

    def revoke(self, credential: str) -> bool:
        try:
            return self.store.revoke(credential)
        except SessionStoreError as error:
            raise SessionBrokerError(error.code) from error

    def authenticate(self, access_token: str) -> Identity:
        try:
            principal = self.store.authenticate_access(access_token)
        except SessionStoreError as error:
            raise AuthenticationError(error.code) from error
        try:
            if principal.membership_id is not None:
                if not self.config.session_broker.onboarding_enabled:
                    raise AuthenticationError("session_authorization_removed")
                identity = self.directory.identity_for_session(principal)
            else:
                identity = self.authenticator.identity_for_subject(principal.issuer, principal.subject)
        except (AuthenticationError, SessionStoreError):
            identity = None
        issuer = self.config.oidc_issuers.get(principal.issuer)
        if issuer is None or issuer.login is None or identity is None or identity.identity_type != "human" or (
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
                organization_id=principal.organization_id,
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
        "code_verifier": pkce_verifier,
    }
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/x-www-form-urlencoded",
        "User-Agent": "Hormuz-OIDC/0.1",
    }
    if auth_method == "client_secret_basic":
        # Keep client authentication in one location. Strict providers reject
        # even a duplicate client_id in the body alongside HTTP Basic.
        encoded_id = urllib.parse.quote_plus(client_id, safe="")
        encoded_secret = urllib.parse.quote_plus(client_secret, safe="")
        basic = base64.b64encode(
            f"{encoded_id}:{encoded_secret}".encode("ascii")
        ).decode("ascii")
        headers["Authorization"] = "Basic " + basic
    elif auth_method == "client_secret_post":
        fields["client_id"] = client_id
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
