from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request

from .session_client import validate_session_gateway


_MAX_RESPONSE_BYTES = 256 * 1024
_SCOPES = {"session", "actor", "team", "organization"}
_REASON_CODES = {
    "access_change",
    "employment_change",
    "security_incident",
    "administrative",
}
_EVENT_TYPES = {
    "refresh_replay",
    "logout",
    "authorization_mapping_removed",
    "admin_revocation",
}


class SessionAdminClientError(RuntimeError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class SessionAdminClient:
    def __init__(
        self,
        gateway: str,
        *,
        credential: str,
        allow_insecure_http: bool = False,
        timeout_seconds: float = 10,
    ):
        self.gateway = validate_session_gateway(
            gateway,
            allow_insecure_http=allow_insecure_http,
        )
        if (
            not credential
            or len(credential.encode("utf-8")) > 64 * 1024
            or any(character in credential for character in ("\n", "\r", "\x00"))
        ):
            raise SessionAdminClientError("invalid_credential")
        self.credential = credential
        self.timeout_seconds = timeout_seconds
        self._opener = urllib.request.build_opener(_NoRedirect)

    def list_sessions(
        self,
        *,
        actor_id: str | None = None,
        team_id: str | None = None,
        limit: int = 50,
        cursor: str | None = None,
    ) -> dict[str, object]:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
            raise SessionAdminClientError("invalid_session_page_limit")
        query: dict[str, str] = {"limit": str(limit)}
        for key, value in (
            ("actor_id", actor_id),
            ("team_id", team_id),
            ("cursor", cursor),
        ):
            if value is not None:
                _bounded_value(value, "invalid_session_list_filter")
                query[key] = value
        response = self._request(
            "GET",
            "/v1/admin/sessions?" + urllib.parse.urlencode(query),
            None,
        )
        if response.get("schema_version") != 1:
            raise SessionAdminClientError("invalid_gateway_response")
        sessions = response.get("sessions")
        next_cursor = response.get("next_cursor")
        if not isinstance(sessions, list) or not (
            next_cursor is None or isinstance(next_cursor, str)
        ):
            raise SessionAdminClientError("invalid_gateway_response")
        required = {
            "session_id",
            "organization_id",
            "actor_id",
            "team_id",
            "client_name",
            "created_at",
            "refreshed_at",
            "access_expires_at",
            "session_expires_at",
        }
        for item in sessions:
            if (
                not isinstance(item, dict)
                or set(item) != required
                or not all(isinstance(item[key], str) and item[key] for key in required)
            ):
                raise SessionAdminClientError("invalid_gateway_response")
        return response

    def revoke(
        self,
        *,
        scope: str,
        target: str | None,
        reason_code: str,
    ) -> dict[str, object]:
        if scope not in _SCOPES or reason_code not in _REASON_CODES:
            raise SessionAdminClientError("invalid_session_revocation")
        if (scope == "organization") != (target is None):
            raise SessionAdminClientError("invalid_session_revocation")
        request: dict[str, object] = {
            "scope": scope,
            "reason_code": reason_code,
        }
        if target is not None:
            _bounded_value(target, "invalid_session_revocation")
            request["target"] = target
        response = self._request("POST", "/v1/admin/session-revocations", request)
        if (
            response.get("schema_version") != 1
            or response.get("scope") != scope
            or response.get("target") != target
            or response.get("reason_code") != reason_code
            or isinstance(response.get("revoked_sessions"), bool)
            or not isinstance(response.get("revoked_sessions"), int)
            or int(response["revoked_sessions"]) < 0
        ):
            raise SessionAdminClientError("invalid_gateway_response")
        return response

    def list_events(
        self,
        *,
        actor_id: str | None = None,
        team_id: str | None = None,
        event_type: str | None = None,
        since: str | None = None,
        limit: int = 50,
        cursor: str | None = None,
    ) -> dict[str, object]:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
            raise SessionAdminClientError("invalid_session_page_limit")
        if event_type is not None and event_type not in _EVENT_TYPES:
            raise SessionAdminClientError("invalid_session_event_request")
        query: dict[str, str] = {"limit": str(limit)}
        for key, value in (
            ("actor_id", actor_id),
            ("team_id", team_id),
            ("event_type", event_type),
            ("since", since),
            ("cursor", cursor),
        ):
            if value is not None:
                _bounded_value(value, "invalid_session_event_request")
                query[key] = value
        response = self._request(
            "GET",
            "/v1/admin/session-events?" + urllib.parse.urlencode(query),
            None,
        )
        if response.get("schema_version") != 1:
            raise SessionAdminClientError("invalid_gateway_response")
        events = response.get("events")
        next_cursor = response.get("next_cursor")
        if not isinstance(events, list) or not (
            next_cursor is None or isinstance(next_cursor, str)
        ):
            raise SessionAdminClientError("invalid_gateway_response")
        required_strings = {
            "event_id",
            "occurred_at",
            "session_id",
            "event_type",
            "organization_id",
            "target_actor_id",
            "target_team_id",
        }
        nullable_strings = {"decision_actor_id", "decision_scope", "reason_code"}
        required = required_strings | nullable_strings
        for item in events:
            if not isinstance(item, dict) or set(item) != required:
                raise SessionAdminClientError("invalid_gateway_response")
            if not all(
                isinstance(item[key], str) and bool(item[key]) for key in required_strings
            ) or not all(
                item[key] is None or (isinstance(item[key], str) and bool(item[key]))
                for key in nullable_strings
            ):
                raise SessionAdminClientError("invalid_gateway_response")
        return response

    def _request(
        self,
        method: str,
        path: str,
        value: dict[str, object] | None,
    ) -> dict[str, object]:
        body = (
            json.dumps(value, separators=(",", ":")).encode("utf-8")
            if value is not None
            else None
        )
        headers = {
            "Accept": "application/json",
            "Authorization": "Bearer " + self.credential,
        }
        if body is not None:
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(
            self.gateway + path,
            data=body,
            headers=headers,
            method=method,
        )
        try:
            response = self._opener.open(request, timeout=self.timeout_seconds)
        except urllib.error.HTTPError as error:
            response = error
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as error:
            raise SessionAdminClientError("gateway_unavailable") from error
        with response:
            status = int(response.getcode())
            if not _same_origin(self.gateway, response.geturl()):
                raise SessionAdminClientError("unexpected_gateway_redirect")
            response_body = response.read(_MAX_RESPONSE_BYTES + 1)
        if len(response_body) > _MAX_RESPONSE_BYTES:
            raise SessionAdminClientError("gateway_response_too_large")
        try:
            response_value = json.loads(response_body)
        except (json.JSONDecodeError, UnicodeDecodeError, RecursionError) as error:
            raise SessionAdminClientError("invalid_gateway_response") from error
        if not isinstance(response_value, dict):
            raise SessionAdminClientError("invalid_gateway_response")
        if not 200 <= status < 300:
            error_value = response_value.get("error")
            code = error_value.get("code") if isinstance(error_value, dict) else None
            raise SessionAdminClientError(
                code if isinstance(code, str) and code else "gateway_request_rejected"
            )
        return response_value


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _bounded_value(value: str, code: str) -> None:
    if (
        not isinstance(value, str)
        or not value
        or len(value.encode("utf-8")) > 2048
        or any(character in value for character in ("\n", "\r", "\x00"))
    ):
        raise SessionAdminClientError(code)


def _same_origin(expected: str, actual: str) -> bool:
    left = urllib.parse.urlparse(expected)
    right = urllib.parse.urlparse(actual)
    return (
        left.scheme.lower(),
        (left.hostname or "").lower(),
        left.port or (443 if left.scheme.lower() == "https" else 80),
    ) == (
        right.scheme.lower(),
        (right.hostname or "").lower(),
        right.port or (443 if right.scheme.lower() == "https" else 80),
    )
