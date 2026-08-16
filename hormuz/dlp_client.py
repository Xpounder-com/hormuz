from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request

from .session_client import validate_session_gateway


_MAX_RESPONSE_BYTES = 64 * 1024


class DLPApprovalClientError(RuntimeError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class DLPApprovalClient:
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
            raise DLPApprovalClientError("invalid_credential")
        self.credential = credential
        self.timeout_seconds = timeout_seconds
        self._opener = urllib.request.build_opener(_NoRedirect)

    def show(self, request_id: str) -> dict[str, object]:
        return self._request("GET", _request_path(request_id), None)

    def approve(self, request_id: str) -> dict[str, object]:
        return self._request(
            "POST",
            _request_path(request_id) + "/decisions",
            {"decision": "approve"},
        )

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
            raise DLPApprovalClientError("gateway_unavailable") from error
        with response:
            status = int(response.getcode())
            final_url = response.geturl()
            if not _same_origin(self.gateway, final_url):
                raise DLPApprovalClientError("unexpected_gateway_redirect")
            response_body = response.read(_MAX_RESPONSE_BYTES + 1)
        if len(response_body) > _MAX_RESPONSE_BYTES:
            raise DLPApprovalClientError("gateway_response_too_large")
        try:
            response_value = json.loads(response_body)
        except (json.JSONDecodeError, UnicodeDecodeError, RecursionError) as error:
            raise DLPApprovalClientError("invalid_gateway_response") from error
        if not isinstance(response_value, dict):
            raise DLPApprovalClientError("invalid_gateway_response")
        if not 200 <= status < 300:
            error_value = response_value.get("error")
            code = error_value.get("code") if isinstance(error_value, dict) else None
            if not isinstance(code, str) or not code:
                code = "gateway_request_rejected"
            raise DLPApprovalClientError(code)
        if (
            response_value.get("schema_version") != 1
            or response_value.get("request_id") != path.split("/")[4]
            or response_value.get("status")
            not in {"pending", "approved", "consumed", "expired"}
        ):
            raise DLPApprovalClientError("invalid_gateway_response")
        return response_value


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _request_path(request_id: str) -> str:
    if (
        not request_id.startswith("apr_")
        or len(request_id) != 36
        or any(character not in "0123456789abcdef" for character in request_id[4:])
    ):
        raise DLPApprovalClientError("invalid_approval_request_id")
    return "/v1/dlp/approval-requests/" + urllib.parse.quote(request_id, safe="")


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
