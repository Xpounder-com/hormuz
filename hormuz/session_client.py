from __future__ import annotations

import json
import http.client
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from datetime import datetime, timedelta, timezone
from typing import Callable

from .credential_store import (
    CredentialLock,
    CredentialStoreError,
    SecureCredentialStore,
    StoredSession,
    validate_profile,
)


_MAX_RESPONSE_BYTES = 128 * 1024


class SessionClientError(RuntimeError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class SessionGatewayClient:
    def __init__(
        self,
        gateway: str,
        *,
        allow_insecure_http: bool = False,
        timeout_seconds: float = 10,
    ):
        self.gateway = validate_session_gateway(
            gateway,
            allow_insecure_http=allow_insecure_http,
        )
        self.timeout_seconds = timeout_seconds
        self._opener = urllib.request.build_opener(_NoRedirect)

    def post(self, path: str, value: dict[str, object]) -> tuple[int, dict[str, object]]:
        body = json.dumps(value, separators=(",", ":")).encode("utf-8")
        request = urllib.request.Request(
            self.gateway + path,
            data=body,
            headers={"Accept": "application/json", "Content-Type": "application/json"},
            method="POST",
        )
        try:
            response = self._opener.open(request, timeout=self.timeout_seconds)
        except urllib.error.HTTPError as error:
            response = error
        except (urllib.error.URLError, TimeoutError, OSError, ValueError, http.client.HTTPException) as error:
            raise SessionClientError("gateway_unavailable") from error
        try:
            with response:
                status = int(response.getcode())
                final_url = response.geturl()
                if not _same_origin(self.gateway, final_url):
                    raise SessionClientError("unexpected_gateway_redirect")
                response_body = response.read(_MAX_RESPONSE_BYTES + 1)
        except (OSError, http.client.HTTPException):
            # Never retry a refresh after a lost response: its predecessor may
            # already be consumed and reusing it must revoke the family.
            raise SessionClientError("gateway_response_unavailable") from None
        if len(response_body) > _MAX_RESPONSE_BYTES:
            raise SessionClientError("gateway_response_too_large")
        try:
            response_value = json.loads(response_body)
        except (json.JSONDecodeError, UnicodeDecodeError, RecursionError) as error:
            raise SessionClientError("invalid_gateway_response") from error
        if not isinstance(response_value, dict):
            raise SessionClientError("invalid_gateway_response")
        return status, response_value


def login(
    *,
    gateway: str,
    profile: str,
    client: str,
    issuer: str | None,
    organization: str | None = None,
    no_open: bool,
    allow_insecure_http: bool,
    wait_seconds: int,
    store: SecureCredentialStore | None = None,
    browser_open: Callable[[str], bool] | None = None,
) -> str:
    with CredentialLock(profile):
        validate_profile(profile)
        credential_store = store or SecureCredentialStore()
        if credential_store.get(profile) is not None:
            raise SessionClientError("profile_already_logged_in")
        gateway_client = SessionGatewayClient(
            gateway,
            allow_insecure_http=allow_insecure_http,
        )
        enrollment_secret = secrets.token_urlsafe(48)
        request: dict[str, object] = {
            "client": client,
            "enrollment_secret": enrollment_secret,
        }
        if issuer is not None:
            request["issuer"] = issuer
        if organization is not None:
            request["organization_id"] = organization
        status, response = gateway_client.post("/v1/auth/enrollments", request)
        if status != 201:
            raise SessionClientError("enrollment_rejected")
        enrollment_id = _response_string(response, "enrollment_id")
        login_url = _response_string(response, "login_url")
        if not _same_origin(gateway_client.gateway, login_url):
            raise SessionClientError("invalid_login_url")
        if no_open:
            opened = False
        else:
            opened = (browser_open or webbrowser.open)(login_url)
        if not opened:
            print(f"Open this URL in your browser:\n{login_url}")
        deadline = time.monotonic() + wait_seconds
        while True:
            status, response = gateway_client.post(
                f"/v1/auth/enrollments/{urllib.parse.quote(enrollment_id, safe='')}/redeem",
                {"enrollment_secret": enrollment_secret},
            )
            if status == 200:
                session = _stored_session_from_response(
                    response,
                    gateway=gateway_client.gateway,
                    client=client,
                )
                _save_or_revoke(
                    credential_store,
                    profile,
                    session,
                    gateway_client=gateway_client,
                )
                return session.access_expires_at.isoformat()
            if status != 409:
                raise SessionClientError("login_failed")
            if time.monotonic() >= deadline:
                raise SessionClientError("login_timed_out")
            time.sleep(1)


def access_token(
    *,
    gateway: str,
    profile: str,
    allow_insecure_http: bool,
    force_refresh: bool = False,
    store: SecureCredentialStore | None = None,
    lock_factory: Callable[[str], CredentialLock] = CredentialLock,
) -> str:
    validate_profile(profile)
    credential_store = store or SecureCredentialStore()
    normalized_gateway = validate_session_gateway(
        gateway,
        allow_insecure_http=allow_insecure_http,
    )
    with lock_factory(profile):
        session = credential_store.get(profile)
        if session is None:
            raise SessionClientError("login_required")
        if session.gateway != normalized_gateway:
            raise SessionClientError("profile_gateway_mismatch")
        now = datetime.now(timezone.utc)
        if session.session_expires_at <= now:
            raise SessionClientError("login_required")
        if not force_refresh and session.access_expires_at > now + timedelta(seconds=60):
            return session.access_token
        gateway_client = SessionGatewayClient(
            normalized_gateway,
            allow_insecure_http=allow_insecure_http,
        )
        status, response = gateway_client.post(
            "/v1/auth/refresh",
            {"refresh_token": session.refresh_token},
        )
        if status != 200:
            if status == 401:
                credential_store.delete(profile)
            raise SessionClientError("session_refresh_failed")
        updated = _stored_session_from_response(
            response,
            gateway=normalized_gateway,
            client=session.client,
        )
        _save_or_revoke(
            credential_store,
            profile,
            updated,
            gateway_client=gateway_client,
        )
        return updated.access_token


def logout(
    *,
    gateway: str,
    profile: str,
    allow_insecure_http: bool,
    store: SecureCredentialStore | None = None,
    lock_factory: Callable[[str], CredentialLock] = CredentialLock,
) -> bool:
    validate_profile(profile)
    credential_store = store or SecureCredentialStore()
    normalized_gateway = validate_session_gateway(
        gateway,
        allow_insecure_http=allow_insecure_http,
    )
    with lock_factory(profile):
        session = credential_store.get(profile)
        if session is None:
            return False
        if session.gateway != normalized_gateway:
            raise SessionClientError("profile_gateway_mismatch")
        status, _response = SessionGatewayClient(
            normalized_gateway,
            allow_insecure_http=allow_insecure_http,
        ).post(
            "/v1/auth/logout",
            {"credential": session.refresh_token},
        )
        if status != 200:
            raise SessionClientError("logout_failed")
        credential_store.delete(profile)
        return True


def validate_session_gateway(value: str, *, allow_insecure_http: bool) -> str:
    result = value.rstrip("/")
    parsed = urllib.parse.urlparse(result)
    try:
        parsed.port
    except ValueError as error:
        raise SessionClientError("invalid_gateway_url") from error
    if (
        not parsed.netloc
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
        or any(character in result for character in ("\n", "\r", "\x00"))
    ):
        raise SessionClientError("invalid_gateway_url")
    if parsed.scheme == "https":
        return result
    if (
        allow_insecure_http
        and parsed.scheme == "http"
        and parsed.hostname in {"127.0.0.1", "::1", "localhost"}
    ):
        return result
    raise SessionClientError("gateway_requires_https")


def _stored_session_from_response(
    value: dict[str, object],
    *,
    gateway: str,
    client: str,
) -> StoredSession:
    raw = {
        "version": 1,
        "gateway": gateway,
        "client": client,
        "access_token": value.get("access_token"),
        "refresh_token": value.get("refresh_token"),
        "access_expires_at": value.get("access_expires_at"),
        "session_expires_at": value.get("session_expires_at"),
    }
    try:
        return StoredSession.from_dict(raw)
    except CredentialStoreError as error:
        raise SessionClientError("invalid_gateway_credential_response") from error


def _response_string(value: dict[str, object], key: str) -> str:
    result = value.get(key)
    if (
        not isinstance(result, str)
        or not result
        or len(result.encode("utf-8")) > 4096
        or any(character in result for character in ("\n", "\r", "\x00"))
    ):
        raise SessionClientError("invalid_gateway_response")
    return result


def _same_origin(expected: str, actual: str) -> bool:
    try:
        left = urllib.parse.urlparse(expected)
        right = urllib.parse.urlparse(actual)
        if right.username or right.password or any(ord(c) < 32 or ord(c) == 127 for c in actual):
            return False
        return (left.scheme.lower(), left.hostname, left.port or (443 if left.scheme == "https" else 80)) == (
            right.scheme.lower(), right.hostname, right.port or (443 if right.scheme == "https" else 80)
        )
    except ValueError:
        return False


def _save_or_revoke(
    store: SecureCredentialStore,
    profile: str,
    session: StoredSession,
    *,
    gateway_client: SessionGatewayClient,
) -> None:
    try:
        store.set(profile, session)
    except CredentialStoreError:
        try:
            gateway_client.post(
                "/v1/auth/logout",
                {"credential": session.refresh_token},
            )
        except SessionClientError:
            pass
        raise
