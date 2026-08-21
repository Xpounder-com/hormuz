"""Strict client for the tenant-scoped metadata-only audit-event API."""

from __future__ import annotations

from datetime import datetime
import json
import math
import urllib.error
import urllib.parse
import urllib.request

from .session_client import validate_session_gateway


_MAX_RESPONSE_BYTES = 512 * 1024
_KINDS = {"all", "usage", "security"}
_FORBIDDEN_FIELD_NAMES = {
    "access_token",
    "content",
    "credential",
    "matched_value",
    "prompt",
    "refresh_token",
    "response",
    "secret",
    "token",
}


class AuditAdminClientError(RuntimeError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class AuditAdminClient:
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
            raise AuditAdminClientError("invalid_credential")
        self.credential = credential
        self.timeout_seconds = timeout_seconds
        self._opener = urllib.request.build_opener(_NoRedirect)

    def list_events(
        self,
        *,
        kind: str = "all",
        since: str | None = None,
        limit: int = 50,
        cursor: str | None = None,
    ) -> dict[str, object]:
        if (
            not isinstance(kind, str)
            or kind not in _KINDS
            or isinstance(limit, bool)
            or not isinstance(limit, int)
            or not 1 <= limit <= 100
        ):
            raise AuditAdminClientError("invalid_audit_event_request")
        query = {"kind": kind, "limit": str(limit)}
        if since is not None:
            _validate_timestamp(since)
            query["since"] = since
        if cursor is not None:
            _bounded_value(cursor)
            query["cursor"] = cursor
        response = self._request("/v1/admin/audit-events?" + urllib.parse.urlencode(query))
        if (
            set(response)
            != {"schema_version", "organization_id", "kind", "window", "events", "next_cursor"}
            or response.get("schema_version") != 1
            or response.get("kind") != kind
            or not isinstance(response.get("organization_id"), str)
            or not response["organization_id"]
            or not _valid_window(response.get("window"))
        ):
            raise AuditAdminClientError("invalid_gateway_response")
        events = response.get("events")
        next_cursor = response.get("next_cursor")
        if (
            not isinstance(events, list)
            or len(events) > limit
            or not (next_cursor is None or isinstance(next_cursor, str))
            or any(
                not _valid_event(event, organization_id=str(response["organization_id"]))
                for event in events
            )
        ):
            raise AuditAdminClientError("invalid_gateway_response")
        if isinstance(next_cursor, str):
            try:
                _bounded_value(next_cursor)
            except AuditAdminClientError as error:
                raise AuditAdminClientError("invalid_gateway_response") from error
        return response

    def _request(self, path: str) -> dict[str, object]:
        request = urllib.request.Request(
            self.gateway + path,
            headers={
                "Accept": "application/json",
                "Authorization": "Bearer " + self.credential,
            },
            method="GET",
        )
        try:
            response = self._opener.open(request, timeout=self.timeout_seconds)
        except urllib.error.HTTPError as error:
            response = error
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as error:
            raise AuditAdminClientError("gateway_unavailable") from error
        with response:
            status = int(response.getcode())
            if not _same_origin(self.gateway, response.geturl()):
                raise AuditAdminClientError("unexpected_gateway_redirect")
            body = response.read(_MAX_RESPONSE_BYTES + 1)
        if len(body) > _MAX_RESPONSE_BYTES:
            raise AuditAdminClientError("gateway_response_too_large")
        try:
            value = json.loads(body)
        except (json.JSONDecodeError, UnicodeDecodeError, RecursionError) as error:
            raise AuditAdminClientError("invalid_gateway_response") from error
        if not isinstance(value, dict):
            raise AuditAdminClientError("invalid_gateway_response")
        if not 200 <= status < 300:
            error_value = value.get("error")
            code = error_value.get("code") if isinstance(error_value, dict) else None
            raise AuditAdminClientError(
                code if isinstance(code, str) and code else "gateway_request_rejected"
            )
        return value


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _bounded_value(value: str) -> None:
    if (
        not isinstance(value, str)
        or not value
        or len(value.encode("utf-8")) > 4096
        or any(character in value for character in ("\n", "\r", "\x00"))
    ):
        raise AuditAdminClientError("invalid_audit_event_request")


def _validate_timestamp(value: str) -> None:
    if (
        not isinstance(value, str)
        or not value
        or len(value.encode("utf-8")) > 256
        or any(character in value for character in ("\n", "\r", "\x00"))
    ):
        raise AuditAdminClientError("invalid_audit_event_request")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise AuditAdminClientError("invalid_audit_event_request") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise AuditAdminClientError("invalid_audit_event_request")


def _valid_window(value: object) -> bool:
    if not isinstance(value, dict) or set(value) != {"start", "end", "timezone"}:
        return False
    if value.get("timezone") != "UTC":
        return False
    try:
        _validate_timestamp(str(value["start"]))
        _validate_timestamp(str(value["end"]))
    except AuditAdminClientError:
        return False
    return True


def _valid_event(value: object, *, organization_id: str) -> bool:
    if (
        not isinstance(value, dict)
        or value.get("organization_id") != organization_id
        or not isinstance(value.get("id"), str)
        or not value["id"]
        or not isinstance(value.get("event_type"), str)
        or not value["event_type"]
        or value.get("schema_version") not in {1, 4}
    ):
        return False
    try:
        _validate_timestamp(str(value["occurred_at"]))
    except (AuditAdminClientError, KeyError):
        return False
    return _metadata_only(value, depth=0)


def _metadata_only(value: object, *, depth: int) -> bool:
    if depth > 8:
        return False
    if value is None or isinstance(value, (bool, int, float)):
        return not isinstance(value, float) or math.isfinite(value)
    if isinstance(value, str):
        return len(value.encode("utf-8")) <= 4096 and "\x00" not in value
    if isinstance(value, list):
        return len(value) <= 256 and all(_metadata_only(item, depth=depth + 1) for item in value)
    if isinstance(value, dict):
        return (
            len(value) <= 128
            and all(
                isinstance(key, str)
                and key not in _FORBIDDEN_FIELD_NAMES
                and len(key.encode("utf-8")) <= 128
                and _metadata_only(item, depth=depth + 1)
                for key, item in value.items()
            )
        )
    return False


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
