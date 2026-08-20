from __future__ import annotations

import http.client
import json
import os
import platform
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from . import __version__
from .config import is_model_identifier
from .usage import MAX_PROVIDER_USAGE_JSON_BYTES, ResponseUsageParser


RESULT_SCHEMA = "hormuz.provider-conformance.v1"
PROBE_VERSION = "hormuz-fixed-text-v1"
REDACTION_RESULT_SCHEMA = "hormuz.redaction-conformance.v1"
REDACTION_PROBE_VERSION = "hormuz-fixed-synthetic-secret-v1"
MAX_GATEWAY_RESPONSE_BYTES = min(MAX_PROVIDER_USAGE_JSON_BYTES, 1024 * 1024)
MAX_CREDENTIAL_BYTES = 64 * 1024
MAX_OUTPUT_TOKENS = 64
_MARKER = "HORMUZ_OK_1"
_PROMPT = "Reply with exactly HORMUZ_OK_1 and no other text."
_SYNTHETIC_SECRET = "sk-" + "proj-" + ("H" * 24)
_REDACTION_MARKER = "[REDACTED:HORMUZ_SECRET]"
_REDACTION_PROMPT = f"Repeat exactly and only: {_SYNTHETIC_SECRET}"
_PROBES = {"connectivity", "secret-redaction"}


class ProviderConformanceError(RuntimeError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class ProviderConformanceClient:
    """Run a bounded, fixed-content provider probe through a Hormuz gateway."""

    def __init__(
        self,
        provider: str,
        *,
        gateway: str,
        credential: str,
        timeout_seconds: float = 30,
        allow_insecure_http: bool = False,
        opener=None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if provider not in {"openai", "anthropic"}:
            raise ProviderConformanceError("invalid_provider")
        if not _valid_credential(credential):
            raise ProviderConformanceError("invalid_credential")
        if (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, (int, float))
            or not 1 <= timeout_seconds <= 300
        ):
            raise ProviderConformanceError("invalid_timeout")
        self.provider = provider
        self.credential = credential
        self.timeout_seconds = float(timeout_seconds)
        self.gateway, self.gateway_transport = _validate_gateway(
            gateway,
            allow_insecure_http=allow_insecure_http,
        )
        self._opener = opener or urllib.request.build_opener(_NoRedirect)
        self._clock = clock

    def run(
        self,
        *,
        model: str,
        max_output_tokens: int = 16,
        probe: str = "connectivity",
    ) -> dict[str, Any]:
        if not isinstance(model, str) or not is_model_identifier(model):
            raise ProviderConformanceError("invalid_model")
        if (
            isinstance(max_output_tokens, bool)
            or not isinstance(max_output_tokens, int)
            or not 1 <= max_output_tokens <= MAX_OUTPUT_TOKENS
        ):
            raise ProviderConformanceError("invalid_output_cap")
        if not isinstance(probe, str) or probe not in _PROBES:
            raise ProviderConformanceError("invalid_probe")

        endpoint, body, headers = self._request_contract(
            model=model,
            max_output_tokens=max_output_tokens,
            probe=probe,
        )
        request = urllib.request.Request(
            endpoint,
            data=json.dumps(body, separators=(",", ":")).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        started_at = self._clock()
        try:
            response = self._opener.open(request, timeout=self.timeout_seconds)
        except urllib.error.HTTPError as error:
            status = int(error.code)
            error.close()
            raise ProviderConformanceError(_http_error_code(status)) from None
        except (
            urllib.error.URLError,
            http.client.HTTPException,
            TimeoutError,
            OSError,
            ValueError,
        ):
            raise ProviderConformanceError("gateway_unavailable") from None

        try:
            with response:
                status = int(response.getcode())
                if not _same_origin(endpoint, response.geturl()):
                    raise ProviderConformanceError("unexpected_gateway_redirect")
                if not 200 <= status < 300:
                    raise ProviderConformanceError(_http_error_code(status))
                response_headers = response.headers
                content_type = _header_value(response_headers, "Content-Type")
                if content_type is None or content_type.split(";", 1)[0].strip().lower() != "application/json":
                    raise ProviderConformanceError("invalid_gateway_response")
                payload = response.read(MAX_GATEWAY_RESPONSE_BYTES + 1)
        except ProviderConformanceError:
            raise
        except (http.client.HTTPException, OSError, TimeoutError, ValueError):
            raise ProviderConformanceError("gateway_unavailable") from None
        if len(payload) > MAX_GATEWAY_RESPONSE_BYTES:
            raise ProviderConformanceError("gateway_response_too_large")

        policy_decision = _bounded_policy_value(
            _header_value(response_headers, "X-Hormuz-Policy-Decision")
        )
        requested_model = _header_value(response_headers, "X-Hormuz-Requested-Model")
        routed_model = _header_value(response_headers, "X-Hormuz-Routed-Model")
        if (
            policy_decision is None
            or requested_model != model
            or not isinstance(routed_model, str)
            or not is_model_identifier(routed_model)
        ):
            raise ProviderConformanceError("gateway_policy_mismatch")
        redaction_count: int | None = None
        if probe == "secret-redaction":
            redaction_header = _header_value(response_headers, "X-Hormuz-Redactions")
            if redaction_header != "1" or "redacted" not in policy_decision.split("+"):
                raise ProviderConformanceError("gateway_redaction_mismatch")
            redaction_count = 1
            if _SYNTHETIC_SECRET.encode("utf-8") in payload:
                raise ProviderConformanceError("synthetic_secret_echoed")

        value = _strict_json_object(payload)
        expected_marker = (
            _REDACTION_MARKER if probe == "secret-redaction" else _MARKER
        )
        if not _marker_verified(self.provider, value, expected=expected_marker):
            raise ProviderConformanceError("marker_mismatch")
        usage_parser = ResponseUsageParser(self.provider, is_event_stream=False)
        usage_parser.feed(payload)
        usage = usage_parser.finish()
        if not usage.usage_reported:
            raise ProviderConformanceError("missing_provider_usage")
        if usage.actual_model is None:
            raise ProviderConformanceError("missing_actual_model")

        elapsed_milliseconds = max(
            0,
            min(2**31 - 1, int(round((self._clock() - started_at) * 1000))),
        )
        interface = (
            "POST /v1/responses"
            if self.provider == "openai"
            else "POST /v1/messages"
        )
        result: dict[str, Any] = {
            "schema_version": (
                REDACTION_RESULT_SCHEMA if probe == "secret-redaction" else RESULT_SCHEMA
            ),
            "status": "verified",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "runner": {
                "hormuz_version": __version__,
                "python_version": platform.python_version(),
            },
            "probe_version": (
                REDACTION_PROBE_VERSION if probe == "secret-redaction" else PROBE_VERSION
            ),
            "provider": self.provider,
            "interface": interface,
            "gateway_transport": self.gateway_transport,
            "requested_model": model,
            "routed_model": routed_model,
            "actual_model": usage.actual_model,
            "policy_decision": policy_decision,
            "http_status": status,
            "latency_milliseconds": elapsed_milliseconds,
            "usage": {
                "input_tokens": usage.input_tokens,
                "output_tokens": usage.output_tokens,
                "cache_read_tokens": usage.cache_read_tokens,
                "cache_write_tokens": usage.cache_write_tokens,
                "reasoning_tokens": usage.reasoning_tokens,
                "billable_tokens": usage.billable_tokens,
            },
        }
        if probe == "secret-redaction":
            result["redaction_count"] = redaction_count
            result["assurances"] = {
                "fixed_synthetic_secret_probe": True,
                "gateway_policy_headers_verified": True,
                "gateway_redaction_header_verified": True,
                "provider_sanitized_echo_verified": True,
                "provider_usage_verified": True,
                "credential_retained": False,
                "gateway_url_retained": False,
                "prompt_retained": False,
                "response_content_retained": False,
                "provider_request_id_retained": False,
                "synthetic_secret_retained": False,
            }
        else:
            result["assurances"] = {
                "fixed_content_probe": True,
                "marker_verified": True,
                "gateway_policy_headers_verified": True,
                "provider_usage_verified": True,
                "credential_retained": False,
                "gateway_url_retained": False,
                "prompt_retained": False,
                "response_content_retained": False,
                "provider_request_id_retained": False,
            }
        return result

    def _request_contract(
        self,
        *,
        model: str,
        max_output_tokens: int,
        probe: str,
    ) -> tuple[str, dict[str, Any], dict[str, str]]:
        prompt = _REDACTION_PROMPT if probe == "secret-redaction" else _PROMPT
        common_headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": f"Hormuz/{__version__} provider-conformance",
        }
        if self.provider == "openai":
            common_headers["Authorization"] = "Bearer " + self.credential
            return (
                self.gateway + "/responses",
                {
                    "model": model,
                    "input": [
                        {
                            "role": "user",
                            "content": [{"type": "input_text", "text": prompt}],
                        }
                    ],
                    "max_output_tokens": max_output_tokens,
                    "store": False,
                },
                common_headers,
            )
        common_headers["x-api-key"] = self.credential
        common_headers["anthropic-version"] = "2023-06-01"
        return (
            self.gateway + "/messages",
            {
                "model": model,
                "messages": [
                    {
                        "role": "user",
                        "content": [{"type": "text", "text": prompt}],
                    }
                ],
                "max_tokens": max_output_tokens,
            },
            common_headers,
        )


def write_conformance_result(
    value: dict[str, Any],
    output: str,
    *,
    force: bool = False,
) -> None:
    try:
        serialized = json.dumps(
            value,
            indent=2,
            sort_keys=True,
            ensure_ascii=False,
            allow_nan=False,
        ) + "\n"
    except (TypeError, ValueError, RecursionError):
        raise ProviderConformanceError("invalid_evidence") from None
    if output == "-":
        sys.stdout.write(serialized)
        return

    path = Path(output).expanduser().absolute()
    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | (os.O_TRUNC if force else os.O_EXCL)
    )
    try:
        descriptor = os.open(path, flags, 0o600)
    except FileExistsError:
        raise ProviderConformanceError("evidence_output_exists") from None
    except OSError:
        raise ProviderConformanceError("evidence_open_failed") from None
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            descriptor = -1
            stream.write(serialized)
            stream.flush()
            os.fsync(stream.fileno())
    except OSError:
        if descriptor >= 0:
            try:
                os.close(descriptor)
            except OSError:
                pass
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
        raise ProviderConformanceError("evidence_write_failed") from None


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _validate_gateway(gateway: str, *, allow_insecure_http: bool) -> tuple[str, str]:
    if not isinstance(gateway, str) or not gateway:
        raise ProviderConformanceError("invalid_gateway")
    try:
        parsed = urllib.parse.urlparse(gateway)
        port = parsed.port
    except ValueError:
        raise ProviderConformanceError("invalid_gateway") from None
    if (
        parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.params
        or not parsed.hostname
        or parsed.path not in {"", "/", "/v1", "/v1/"}
    ):
        raise ProviderConformanceError("invalid_gateway")
    host = parsed.hostname.lower()
    loopback = host in {"localhost", "127.0.0.1", "::1"}
    if parsed.scheme == "https":
        transport = "https"
    elif parsed.scheme == "http" and loopback and allow_insecure_http:
        transport = "loopback_http"
    else:
        raise ProviderConformanceError("insecure_gateway")
    netloc = f"[{host}]" if ":" in host else host
    if port is not None:
        netloc += f":{port}"
    return f"{parsed.scheme.lower()}://{netloc}/v1", transport


def _valid_credential(value: object) -> bool:
    return (
        isinstance(value, str)
        and bool(value)
        and len(value.encode("utf-8")) <= MAX_CREDENTIAL_BYTES
        and not any(ord(character) < 0x21 or ord(character) == 0x7F for character in value)
    )


def _same_origin(expected: str, actual: str) -> bool:
    try:
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
    except ValueError:
        return False


def _header_value(headers: object, name: str) -> str | None:
    get_all = getattr(headers, "get_all", None)
    if callable(get_all):
        values = get_all(name)
        if values is not None:
            if len(values) != 1 or not isinstance(values[0], str):
                return None
            return values[0]
    get = getattr(headers, "get", None)
    if callable(get):
        value = get(name)
        if isinstance(value, str):
            return value
    items = getattr(headers, "items", None)
    if callable(items):
        values = [
            value
            for key, value in items()
            if isinstance(key, str) and key.lower() == name.lower()
        ]
        if len(values) == 1 and isinstance(values[0], str):
            return values[0]
    return None


def _bounded_policy_value(value: object) -> str | None:
    if (
        not isinstance(value, str)
        or not 1 <= len(value.encode("ascii", errors="ignore")) <= 128
        or not value.replace("+", "").replace("-", "").replace("_", "").isalnum()
        or any(ord(character) > 0x7F for character in value)
    ):
        return None
    return value


def _strict_json_object(payload: bytes) -> dict[str, Any]:
    def reject_duplicate(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate")
            result[key] = value
        return result

    try:
        value = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=reject_duplicate,
            parse_constant=lambda _value: (_ for _ in ()).throw(ValueError("constant")),
        )
    except (UnicodeDecodeError, ValueError, RecursionError):
        raise ProviderConformanceError("invalid_gateway_response") from None
    if not isinstance(value, dict):
        raise ProviderConformanceError("invalid_gateway_response")
    return value


def _marker_verified(provider: str, value: dict[str, Any], *, expected: str) -> bool:
    texts: list[str] = []
    if provider == "openai":
        if value.get("object") != "response" or value.get("status") != "completed":
            return False
        output = value.get("output")
        if not isinstance(output, list):
            return False
        for item in output:
            if not isinstance(item, dict) or item.get("type") != "message":
                continue
            content = item.get("content")
            if not isinstance(content, list):
                return False
            for block in content:
                if isinstance(block, dict) and block.get("type") == "output_text":
                    text = block.get("text")
                    if not isinstance(text, str):
                        return False
                    texts.append(text)
    else:
        if value.get("type") != "message":
            return False
        content = value.get("content")
        if not isinstance(content, list):
            return False
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                text = block.get("text")
                if not isinstance(text, str):
                    return False
                texts.append(text)
    return len(texts) == 1 and texts[0].strip() == expected


def _http_error_code(status: int) -> str:
    if 300 <= status < 400:
        return "unexpected_gateway_redirect"
    if status == 401:
        return "gateway_authentication_failed"
    if 400 <= status < 500:
        return "gateway_request_rejected"
    return "gateway_unavailable"
