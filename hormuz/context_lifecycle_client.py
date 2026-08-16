from __future__ import annotations

import json
import math
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from .context import ContextError
from .context_api import (
    CONTEXT_EVIDENCE_RESULT_SCHEMA,
    CONTEXT_REVALIDATION_REQUEST_SCHEMA,
    CONTEXT_REVALIDATION_RESULT_SCHEMA,
    CONTEXT_SNAPSHOT_RESULT_SCHEMA,
    CONTEXT_SNAPSHOT_WRITE_SCHEMA,
    ContextRevalidationBatchRequest,
    ContextSnapshotWriteRequest,
)
from .context_lifecycle import ContextEvidence
from .session_client import SessionClientError, validate_session_gateway


_MAX_RESPONSE_BYTES = 256 * 1024
_MAX_EVIDENCE_REQUEST_BYTES = 64 * 1024
_MAX_SNAPSHOT_REQUEST_BYTES = 8 * 1024 * 1024
_MAX_REVALIDATION_REQUEST_BYTES = 8 * 1024
_LIFECYCLE_ENVELOPE_SCHEMA = "hormuz.context-lifecycle-envelope.v1"


class ContextLifecycleClientError(RuntimeError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class ContextLifecycleClient:
    def __init__(
        self,
        gateway: str,
        *,
        credential: str,
        allow_insecure_http: bool = False,
        timeout_seconds: float = 10,
    ):
        try:
            self.gateway = validate_session_gateway(
                gateway,
                allow_insecure_http=allow_insecure_http,
            )
        except SessionClientError as error:
            raise ContextLifecycleClientError(error.code) from error
        if (
            not credential
            or len(credential.encode("utf-8")) > 64 * 1024
            or any(character in credential for character in ("\n", "\r", "\x00"))
        ):
            raise ContextLifecycleClientError("invalid_credential")
        if (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, (int, float))
            or not math.isfinite(timeout_seconds)
            or not 0 < timeout_seconds <= 300
        ):
            raise ContextLifecycleClientError("invalid_timeout")
        self.credential = credential
        self.timeout_seconds = timeout_seconds
        self._opener = urllib.request.build_opener(_NoRedirect)

    def record_evidence(self, envelope: dict[str, object]) -> dict[str, object]:
        try:
            ContextEvidence.from_dict(envelope)
        except ValueError as error:
            raise ContextLifecycleClientError("invalid_context_evidence") from error
        response = self._request(
            "POST",
            "/v1/context/evidence",
            envelope,
            max_request_bytes=_MAX_EVIDENCE_REQUEST_BYTES,
        )
        _validate_evidence_response(response)
        return response

    def put_snapshot(
        self,
        envelope: dict[str, object],
        *,
        expected_version: int | None = None,
    ) -> dict[str, object]:
        request = _snapshot_write_request(envelope, expected_version=expected_version)
        try:
            ContextSnapshotWriteRequest.from_dict(request)
        except ContextError as error:
            raise ContextLifecycleClientError("invalid_context_snapshot") from error
        response = self._request(
            "PUT",
            "/v1/context/lifecycle-snapshots",
            request,
            max_request_bytes=_MAX_SNAPSHOT_REQUEST_BYTES,
        )
        _validate_snapshot_response(response)
        return response

    def revalidate(
        self,
        *,
        repository_id: str,
        branch: str,
        batch_size: int | None = None,
    ) -> dict[str, object]:
        request: dict[str, object] = {
            "schema_version": CONTEXT_REVALIDATION_REQUEST_SCHEMA,
            "repository_id": repository_id,
            "branch": branch,
        }
        if batch_size is not None:
            request["batch_size"] = batch_size
        try:
            ContextRevalidationBatchRequest.from_dict(request)
        except ContextError as error:
            raise ContextLifecycleClientError("invalid_context_revalidation") from error
        response = self._request(
            "POST",
            "/v1/context/revalidation-batches",
            request,
            max_request_bytes=_MAX_REVALIDATION_REQUEST_BYTES,
        )
        _validate_revalidation_response(response)
        return response

    def _request(
        self,
        method: str,
        path: str,
        value: dict[str, object],
        *,
        max_request_bytes: int,
    ) -> dict[str, object]:
        body = json.dumps(value, separators=(",", ":")).encode("utf-8")
        if len(body) > max_request_bytes:
            raise ContextLifecycleClientError("context_request_too_large")
        request = urllib.request.Request(
            self.gateway + path,
            data=body,
            headers={
                "Accept": "application/json",
                "Authorization": "Bearer " + self.credential,
                "Content-Type": "application/json",
            },
            method=method,
        )
        try:
            response = self._opener.open(request, timeout=self.timeout_seconds)
        except urllib.error.HTTPError as error:
            response = error
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as error:
            raise ContextLifecycleClientError("gateway_unavailable") from error
        with response:
            status = int(response.getcode())
            final_url = response.geturl()
            if not _same_origin(self.gateway, final_url):
                raise ContextLifecycleClientError("unexpected_gateway_redirect")
            response_body = response.read(_MAX_RESPONSE_BYTES + 1)
        if len(response_body) > _MAX_RESPONSE_BYTES:
            raise ContextLifecycleClientError("gateway_response_too_large")
        try:
            response_value = json.loads(
                response_body,
                parse_constant=_invalid_json_constant,
                object_pairs_hook=_unique_json_object,
            )
        except (json.JSONDecodeError, UnicodeDecodeError, RecursionError, ValueError) as error:
            raise ContextLifecycleClientError("invalid_gateway_response") from error
        if not isinstance(response_value, dict):
            raise ContextLifecycleClientError("invalid_gateway_response")
        if not 200 <= status < 300:
            error_value = response_value.get("error")
            code = error_value.get("code") if isinstance(error_value, dict) else None
            if not isinstance(code, str) or not code:
                code = "gateway_request_rejected"
            raise ContextLifecycleClientError(code)
        return response_value


def _snapshot_write_request(
    envelope: dict[str, object],
    *,
    expected_version: int | None,
) -> dict[str, object]:
    if not isinstance(envelope, dict):
        raise ContextLifecycleClientError("invalid_context_snapshot")
    allowed = {"schema_version", "organization_id", "repository_id", "branch", "snapshot"}
    if set(envelope) != allowed or envelope.get("schema_version") != _LIFECYCLE_ENVELOPE_SCHEMA:
        raise ContextLifecycleClientError("invalid_context_snapshot")
    return {
        "schema_version": CONTEXT_SNAPSHOT_WRITE_SCHEMA,
        "organization_id": envelope.get("organization_id"),
        "repository_id": envelope.get("repository_id"),
        "branch": envelope.get("branch"),
        "snapshot": envelope.get("snapshot"),
        "expected_version": expected_version,
    }


def _validate_evidence_response(value: dict[str, object]) -> None:
    required = {
        "schema_version",
        "created",
        "evidence_id",
        "organization_id",
        "record_id",
        "record_version",
        "signal",
        "signal_family",
        "observed_at",
        "policy_version",
        "raw_evidence_ref_retained",
    }
    if (
        set(value) != required
        or value.get("schema_version") != CONTEXT_EVIDENCE_RESULT_SCHEMA
        or not isinstance(value.get("created"), bool)
        or value.get("raw_evidence_ref_retained") is not False
        or not _positive_integer(value.get("record_version"))
        or not _nonempty_strings(
            value,
            required
            - {
                "created",
                "record_version",
                "raw_evidence_ref_retained",
            },
        )
    ):
        raise ContextLifecycleClientError("invalid_gateway_response")


def _validate_snapshot_response(value: dict[str, object]) -> None:
    required = {
        "schema_version",
        "organization_id",
        "repository_id",
        "branch",
        "repository_revision",
        "snapshot_sha256",
        "version",
        "artifact_count",
        "observed_at",
        "policy_version",
    }
    if (
        set(value) != required
        or value.get("schema_version") != CONTEXT_SNAPSHOT_RESULT_SCHEMA
        or not _positive_integer(value.get("version"))
        or not _nonnegative_integer(value.get("artifact_count"))
        or not _nonempty_strings(
            value,
            required - {"version", "artifact_count"},
        )
    ):
        raise ContextLifecycleClientError("invalid_gateway_response")


def _validate_revalidation_response(value: dict[str, object]) -> None:
    integer_fields = {
        "snapshot_version",
        "total_records",
        "processed_records",
        "promoted_records",
        "invalidated_records",
        "unchanged_records",
        "deferred_records",
    }
    string_fields = {
        "schema_version",
        "job_id",
        "organization_id",
        "repository_id",
        "branch",
        "status",
        "snapshot_sha256",
        "policy_version",
        "policy_sha256",
        "record_set_sha256",
        "evidence_set_sha256",
        "updated_at",
    }
    required = integer_fields | string_fields
    if (
        set(value) != required
        or value.get("schema_version") != CONTEXT_REVALIDATION_RESULT_SCHEMA
        or value.get("status") not in {"pending", "running", "completed", "superseded"}
        or not _nonempty_strings(value, string_fields)
        or not _positive_integer(value.get("snapshot_version"))
        or any(
            not _nonnegative_integer(value.get(name))
            for name in integer_fields - {"snapshot_version"}
        )
    ):
        raise ContextLifecycleClientError("invalid_gateway_response")


def _nonempty_strings(value: dict[str, object], names: set[str]) -> bool:
    return all(isinstance(value.get(name), str) and bool(value[name]) for name in names)


def _positive_integer(value: object) -> bool:
    return not isinstance(value, bool) and isinstance(value, int) and value > 0


def _nonnegative_integer(value: object) -> bool:
    return not isinstance(value, bool) and isinstance(value, int) and value >= 0


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


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate JSON object member")
        value[key] = item
    return value


def _invalid_json_constant(_value: str) -> object:
    raise ValueError("non-standard JSON numeric constant")
