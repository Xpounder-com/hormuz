from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime

from .policy_projection import policy_projection_sha256
from .session_client import validate_session_gateway


MAX_POLICY_PROJECTION_BYTES = 1_048_576
_MAX_RESPONSE_BYTES = 2 * 1_048_576
_VERSION_ID = re.compile(r"hpv_v1_[0-9a-f]{64}\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_POLICY_PROJECTION_SCHEMAS = frozenset(
    {
        "hormuz.policy-projection.v2",
        "hormuz.policy-projection.v3",
        "hormuz.policy-projection.v4",
    }
)


class PolicyAdminClientError(RuntimeError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class PolicyAdminClient:
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
            raise PolicyAdminClientError("invalid_credential")
        if (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, (int, float))
            or not 1 <= timeout_seconds <= 300
        ):
            raise PolicyAdminClientError("invalid_timeout")
        self.credential = credential
        self.timeout_seconds = float(timeout_seconds)
        self._opener = urllib.request.build_opener(_NoRedirect)

    def stage(self, projection: dict[str, object]) -> dict[str, object]:
        if not isinstance(projection, dict):
            raise PolicyAdminClientError("invalid_policy_projection")
        try:
            expected_fingerprint = policy_projection_sha256(projection)
        except (TypeError, ValueError, RecursionError) as error:
            raise PolicyAdminClientError("invalid_policy_projection") from error
        response = self._request(
            "POST",
            "/v1/admin/policy-versions",
            {"projection": projection},
        )
        if (
            not _valid_policy_version(response)
            or response["projection_sha256"] != expected_fingerprint
        ):
            raise PolicyAdminClientError("invalid_gateway_response")
        return response

    def active(self) -> dict[str, object]:
        response = self._request("GET", "/v1/admin/policy-active", None)
        if not _valid_active_policy(response):
            raise PolicyAdminClientError("invalid_gateway_response")
        return response

    def activate(
        self,
        version_id: str,
        *,
        expected_active_version_id: str | None,
    ) -> dict[str, object]:
        return self._change_active(
            "/v1/admin/policy-activations",
            version_id,
            expected_active_version_id=expected_active_version_id,
        )

    def rollback(
        self,
        version_id: str,
        *,
        expected_active_version_id: str,
    ) -> dict[str, object]:
        return self._change_active(
            "/v1/admin/policy-rollbacks",
            version_id,
            expected_active_version_id=expected_active_version_id,
        )

    def _change_active(
        self,
        path: str,
        version_id: str,
        *,
        expected_active_version_id: str | None,
    ) -> dict[str, object]:
        if _VERSION_ID.fullmatch(version_id) is None or (
            expected_active_version_id is not None
            and _VERSION_ID.fullmatch(expected_active_version_id) is None
        ):
            raise PolicyAdminClientError("invalid_policy_activation")
        response = self._request(
            "POST",
            path,
            {
                "version_id": version_id,
                "expected_active_version_id": expected_active_version_id,
            },
        )
        if not _valid_policy_activation(response):
            raise PolicyAdminClientError("invalid_gateway_response")
        return response

    def _request(
        self,
        method: str,
        path: str,
        value: dict[str, object] | None,
    ) -> dict[str, object]:
        try:
            body = (
                json.dumps(
                    value,
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=False,
                    allow_nan=False,
                ).encode("utf-8")
                if value is not None
                else None
            )
        except (TypeError, ValueError, RecursionError) as error:
            raise PolicyAdminClientError("invalid_policy_projection") from error
        if body is not None and len(body) > MAX_POLICY_PROJECTION_BYTES:
            raise PolicyAdminClientError("policy_projection_too_large")
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
            raise PolicyAdminClientError("gateway_unavailable") from error
        with response:
            status = int(response.getcode())
            if not _same_origin(self.gateway, response.geturl()):
                raise PolicyAdminClientError("unexpected_gateway_redirect")
            response_body = response.read(_MAX_RESPONSE_BYTES + 1)
        if len(response_body) > _MAX_RESPONSE_BYTES:
            raise PolicyAdminClientError("gateway_response_too_large")
        try:
            response_value = json.loads(response_body)
        except (json.JSONDecodeError, UnicodeDecodeError, RecursionError) as error:
            raise PolicyAdminClientError("invalid_gateway_response") from error
        if not isinstance(response_value, dict):
            raise PolicyAdminClientError("invalid_gateway_response")
        if not 200 <= status < 300:
            error_value = response_value.get("error")
            code = error_value.get("code") if isinstance(error_value, dict) else None
            raise PolicyAdminClientError(
                code if isinstance(code, str) and code else "gateway_request_rejected"
            )
        return response_value


def _valid_policy_version(value: object) -> bool:
    if not isinstance(value, dict) or set(value) != {
        "schema",
        "version_id",
        "projection_sha256",
        "projection_schema",
        "created_at",
        "created_by_actor_id",
        "created_by_actor_name",
        "change_summary",
        "staged",
    }:
        return False
    return (
        value.get("schema") == "hormuz.policy-version.v1"
        and _valid_version(value.get("version_id"))
        and isinstance(value.get("projection_sha256"), str)
        and _SHA256.fullmatch(value["projection_sha256"]) is not None
        and value.get("version_id") == "hpv_v1_" + value["projection_sha256"]
        and value.get("projection_schema") in _POLICY_PROJECTION_SCHEMAS
        and _valid_timestamp(value.get("created_at"))
        and _valid_text(value.get("created_by_actor_id"), 256)
        and _valid_text(value.get("created_by_actor_name"), 256)
        and _valid_change_summary(value.get("change_summary"))
        and isinstance(value.get("staged"), bool)
    )


def _valid_active_policy(value: object) -> bool:
    if not isinstance(value, dict) or set(value) != {
        "schema",
        "version_id",
        "projection_sha256",
        "projection",
        "activated_at",
        "activated_by_actor_id",
        "activated_by_actor_name",
        "activation_sequence",
    }:
        return False
    projection = value.get("projection")
    return (
        value.get("schema") == "hormuz.active-policy.v1"
        and _valid_version(value.get("version_id"))
        and isinstance(value.get("projection_sha256"), str)
        and _SHA256.fullmatch(value["projection_sha256"]) is not None
        and value.get("version_id") == "hpv_v1_" + value["projection_sha256"]
        and isinstance(projection, dict)
        and projection.get("schema") in _POLICY_PROJECTION_SCHEMAS
        and _projection_fingerprint(projection) == value["projection_sha256"]
        and _valid_timestamp(value.get("activated_at"))
        and _valid_text(value.get("activated_by_actor_id"), 256)
        and _valid_text(value.get("activated_by_actor_name"), 256)
        and isinstance(value.get("activation_sequence"), int)
        and not isinstance(value.get("activation_sequence"), bool)
        and value["activation_sequence"] > 0
    )


def _valid_policy_activation(value: object) -> bool:
    if not isinstance(value, dict) or set(value) != {
        "schema",
        "version_id",
        "prior_version_id",
        "activated_at",
        "activated_by_actor_id",
        "activated_by_actor_name",
        "activation_sequence",
        "action",
        "changed",
    }:
        return False
    prior = value.get("prior_version_id")
    return (
        value.get("schema") == "hormuz.policy-activation.v1"
        and _valid_version(value.get("version_id"))
        and (prior is None or _valid_version(prior))
        and _valid_timestamp(value.get("activated_at"))
        and _valid_text(value.get("activated_by_actor_id"), 256)
        and _valid_text(value.get("activated_by_actor_name"), 256)
        and isinstance(value.get("activation_sequence"), int)
        and not isinstance(value.get("activation_sequence"), bool)
        and value["activation_sequence"] > 0
        and value.get("action") in {"activated", "rolled_back"}
        and isinstance(value.get("changed"), bool)
    )


def _valid_change_summary(value: object) -> bool:
    if not isinstance(value, dict) or set(value) != {
        "schema",
        "changed_sections",
        "section_count",
    }:
        return False
    sections = value.get("changed_sections")
    return (
        value.get("schema") == "hormuz.policy-change-summary.v1"
        and isinstance(sections, list)
        and all(_valid_text(section, 128) for section in sections)
        and isinstance(value.get("section_count"), int)
        and not isinstance(value.get("section_count"), bool)
        and value["section_count"] == len(sections)
    )


def _valid_version(value: object) -> bool:
    return isinstance(value, str) and _VERSION_ID.fullmatch(value) is not None


def _projection_fingerprint(value: dict[str, object]) -> str | None:
    try:
        return policy_projection_sha256(value)
    except (TypeError, ValueError, RecursionError):
        return None


def _valid_text(value: object, maximum: int) -> bool:
    return (
        isinstance(value, str)
        and bool(value)
        and len(value.encode("utf-8")) <= maximum
        and not any(character in value for character in ("\n", "\r", "\x00"))
    )


def _valid_timestamp(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        parsed = datetime.fromisoformat(value)
    except (ValueError, OverflowError):
        return False
    return parsed.tzinfo is not None and parsed.utcoffset() is not None


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


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
