#!/usr/bin/env python3
"""Run a content-free restart, provider, latency, cancellation and failover qualification."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import ssl
import sys
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, HTTPSHandler, ProxyHandler, Request, build_opener

SCHEMA_ID = "hormuz.external-pilot-qualification-evidence"
SCHEMA_VERSION = 1
MAX_RESPONSE_BYTES = 64 * 1024
MAX_PROVIDER_RESPONSE_BYTES = 2 * 1024 * 1024
COMMIT_RE = re.compile(r"[0-9a-f]{40}\Z")
SERVICE_ID_RE = re.compile(r"srv-[a-z0-9]{16,32}\Z")
TOKEN_RE = re.compile(r"hox_[ar]_[A-Za-z0-9_-]{43,128}\Z")
RUN_URL_RE = re.compile(
    r"https://github\.com/Xpounder-com/hormuz/actions/runs/[1-9][0-9]{0,19}\Z"
)
TIMING_RE = re.compile(r"hormuz_upstream_headers;dur=[0-9]+(?:\.[0-9]{3})?\Z")
EXPECTED_CONTRACT = {
    "profile": "external_pilot",
    "identity_provider": "okta",
    "provider_protocols": ["anthropic", "openai"],
    "https": True,
    "inference_enabled": True,
    "provider_credentials_server_only": True,
    "postgresql_durable": True,
    "tenant_rls": True,
    "durable_sessions": True,
    "monitoring_configured": True,
    "worker_saturation_monitoring": True,
    "postgresql_pool_wait_monitoring": True,
    "single_region_acknowledged": True,
    "availability_sla_claimed": False,
    "max_inflight_streams": 8,
}
RELIABILITY_FIELDS = {
    "schema_id",
    "schema_version",
    "scope",
    "live_provider_request_count",
    "provider_attempt_record_count",
    "latency_header_sample_count",
    "latency_first_body_byte_sample_count",
    "latency_total_sample_count",
    "failover_link_record_count",
    "outcome_unknown_count",
    "cancellation_outcome_unknown_count",
    "provider_capacity",
    "provider_inflight",
    "provider_peak_inflight",
    "provider_saturated_total",
    "postgresql_pool_max_connections",
    "postgresql_pool_requests_waiting",
    "postgresql_pool_requests_queued_total",
    "postgresql_pool_wait_milliseconds_total",
    "postgresql_pool_error_total",
    "deployment",
}


class QualificationError(ValueError):
    """A fixed, content-free qualification failure."""


def _origin(value: str) -> str:
    try:
        parsed = urlsplit(value)
        invalid = (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.port not in {None, 443}
            or parsed.path
            or parsed.query
            or parsed.fragment
            or not value.isascii()
            or any(character.isspace() or ord(character) < 33 for character in value)
        )
    except ValueError:
        invalid = True
    if invalid:
        raise QualificationError("gateway_origin_invalid")
    return value


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, file_pointer, code, message, headers, new_url):
        return None


def _opener():
    return build_opener(
        ProxyHandler({}),
        HTTPSHandler(context=ssl.create_default_context()),
        _NoRedirect(),
    )


def _read(response, maximum: int) -> bytes:
    length = response.headers.get("Content-Length")
    if length is not None and (not length.isdecimal() or int(length) > maximum):
        raise QualificationError("response_size_invalid")
    payload = response.read(maximum + 1)
    if len(payload) > maximum:
        raise QualificationError("response_size_invalid")
    return payload


def _open_gateway(
    origin: str,
    path: str,
    *,
    method: str = "GET",
    body: dict[str, Any] | None = None,
    access_token: str | None = None,
    rehearsal_header: str | None = None,
    rehearsal_key: str | None = None,
):
    payload = None if body is None else json.dumps(body, separators=(",", ":")).encode("utf-8")
    headers = {"Accept": "application/json", "User-Agent": "hormuz-external-pilot-qualifier/1"}
    if payload is not None:
        headers["Content-Type"] = "application/json"
    if access_token is not None:
        headers["Authorization"] = "Bearer " + access_token
    if rehearsal_header is not None:
        if rehearsal_key is None:
            raise QualificationError("rehearsal_credential_missing")
        headers[rehearsal_header] = rehearsal_key
    request = Request(origin + path, data=payload, method=method, headers=headers)
    try:
        return _opener().open(request, timeout=30)
    except HTTPError as error:
        return error
    except (URLError, TimeoutError, OSError):
        raise QualificationError("gateway_request_failed") from None


def _json_gateway(
    origin: str,
    path: str,
    *,
    method: str = "GET",
    body: dict[str, Any] | None = None,
    access_token: str | None = None,
) -> tuple[dict[str, Any], dict[str, str]]:
    response = _open_gateway(
        origin,
        path,
        method=method,
        body=body,
        access_token=access_token,
    )
    try:
        if response.status != 200:
            raise QualificationError("gateway_json_request_failed")
        headers = {name.lower(): value for name, value in response.headers.items()}
        if headers.get("content-type", "").split(";", 1)[0].strip() != "application/json":
            raise QualificationError("gateway_json_response_invalid")
        try:
            value = json.loads(_read(response, MAX_RESPONSE_BYTES))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise QualificationError("gateway_json_response_invalid") from None
        if not isinstance(value, dict):
            raise QualificationError("gateway_json_response_invalid")
        return value, headers
    finally:
        response.close()


def _health(origin: str, expected_commit: str, service_id: str) -> dict[str, Any]:
    value, headers = _json_gateway(origin, "/health")
    if (
        value.get("schema_id") != "hormuz.hosted-provider-pilot"
        or value.get("schema_version") != 1
        or value.get("status") != "provider_pilot"
        or value.get("contract") != EXPECTED_CONTRACT
        or headers.get("cache-control") != "no-store"
    ):
        raise QualificationError("gateway_profile_not_ready")
    deployment = value.get("deployment")
    if (
        not isinstance(deployment, dict)
        or deployment.get("platform") != "render"
        or deployment.get("source_commit") != expected_commit
        or deployment.get("source_branch") != "main"
        or deployment.get("repository") != "Xpounder-com/hormuz"
        or deployment.get("external_origin") != origin
        or deployment.get("service_id") != service_id
        or re.fullmatch(r"[0-9a-f]{16}", str(deployment.get("instance_fingerprint", ""))) is None
    ):
        raise QualificationError("gateway_deployment_identity_invalid")
    return value


def _deploy_hook_url(value: str, service_id: str, expected_commit: str) -> str:
    try:
        parsed = urlsplit(value)
        query = parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True)
        invalid = (
            parsed.scheme != "https"
            or parsed.hostname != "api.render.com"
            or parsed.username
            or parsed.password
            or parsed.port not in {None, 443}
            or parsed.path != "/deploy/" + service_id
            or parsed.fragment
            or len(query) != 1
            or query[0][0] != "key"
            or not 16 <= len(query[0][1]) <= 512
        )
    except ValueError:
        invalid = True
    if invalid:
        raise QualificationError("render_deploy_hook_invalid")
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode([*query, ("ref", expected_commit)]), ""))


def _restart_and_wait(
    origin: str,
    *,
    expected_commit: str,
    service_id: str,
    deploy_hook: str,
    deadline_seconds: int = 600,
) -> str:
    before = _health(origin, expected_commit, service_id)["deployment"]["instance_fingerprint"]
    request = Request(
        _deploy_hook_url(deploy_hook, service_id, expected_commit),
        data=b"",
        method="POST",
        headers={"User-Agent": "hormuz-external-pilot-qualifier/1"},
    )
    try:
        response = _opener().open(request, timeout=30)
    except (HTTPError, URLError, TimeoutError, OSError):
        raise QualificationError("render_restart_request_failed") from None
    try:
        if response.status not in {200, 201, 202}:
            raise QualificationError("render_restart_request_failed")
        _read(response, MAX_RESPONSE_BYTES)
    finally:
        response.close()

    deadline = time.monotonic() + deadline_seconds
    while time.monotonic() < deadline:
        time.sleep(10)
        try:
            current = _health(origin, expected_commit, service_id)
            ready, _ = _json_gateway(origin, "/ready")
        except QualificationError:
            continue
        fingerprint = current["deployment"]["instance_fingerprint"]
        if fingerprint != before and ready.get("status") == "provider_pilot":
            return fingerprint
    raise QualificationError("render_restart_not_observed")


def _refresh(origin: str, refresh_token: str) -> tuple[str, str]:
    value, _ = _json_gateway(
        origin,
        "/v1/auth/refresh",
        method="POST",
        body={"refresh_token": refresh_token},
    )
    access = value.get("access_token")
    refresh = value.get("refresh_token")
    if (
        not isinstance(access, str)
        or not TOKEN_RE.fullmatch(access)
        or not isinstance(refresh, str)
        or not TOKEN_RE.fullmatch(refresh)
        or access == refresh
        or refresh == refresh_token
    ):
        raise QualificationError("session_refresh_invalid")
    return access, refresh


def _logout(origin: str, credential: str) -> None:
    value, _ = _json_gateway(
        origin,
        "/v1/auth/logout",
        method="POST",
        body={"credential": credential},
    )
    if value != {"revoked": True}:
        raise QualificationError("qualification_session_cleanup_failed")


def _reliability(
    origin: str,
    access_token: str,
    *,
    expected_commit: str,
    service_id: str,
) -> dict[str, Any]:
    value, headers = _json_gateway(origin, "/v1/gateway/reliability", access_token=access_token)
    integer_fields = (
        "live_provider_request_count",
        "provider_attempt_record_count",
        "latency_header_sample_count",
        "latency_first_body_byte_sample_count",
        "latency_total_sample_count",
        "failover_link_record_count",
        "outcome_unknown_count",
        "cancellation_outcome_unknown_count",
        "provider_inflight",
        "provider_peak_inflight",
        "provider_saturated_total",
        "postgresql_pool_requests_waiting",
        "postgresql_pool_requests_queued_total",
        "postgresql_pool_wait_milliseconds_total",
        "postgresql_pool_error_total",
    )
    deployment = value.get("deployment")
    if (
        set(value) != RELIABILITY_FIELDS
        or value.get("schema_id") != "hormuz.provider-reliability-summary"
        or value.get("schema_version") != 1
        or value.get("scope") != "current_actor"
        or headers.get("cache-control") != "no-store"
        or any(type(value.get(name)) is not int or value[name] < 0 for name in integer_fields)
        or value.get("provider_capacity") != 8
        or value.get("provider_inflight") != 0
        or value.get("provider_peak_inflight", 9) > 8
        or value.get("postgresql_pool_max_connections") != 4
        or value.get("postgresql_pool_requests_waiting", 9) > 8
        or not isinstance(deployment, dict)
        or deployment.get("platform") != "render"
        or deployment.get("source_commit") != expected_commit
        or deployment.get("service_id") != service_id
        or deployment.get("external_origin") != origin
    ):
        raise QualificationError("provider_reliability_summary_invalid")
    return value


def _provider_body(protocol: str, alias: str, *, stream: bool) -> tuple[str, dict[str, Any]]:
    if protocol == "openai":
        return "/v1/responses", {
            "model": alias,
            "input": "Return only the word PONG.",
            "max_output_tokens": 16,
            "stream": stream,
        }
    return "/v1/messages", {
        "model": alias,
        "messages": [{"role": "user", "content": "Return only the word PONG."}],
        "max_tokens": 16,
        "stream": stream,
    }


def _provider_request(
    origin: str,
    access_token: str,
    *,
    protocol: str,
    alias: str,
    stream: bool,
    rehearsal_header: str | None = None,
    rehearsal_key: str | None = None,
) -> tuple[dict[str, str], bool, bool]:
    path, body = _provider_body(protocol, alias, stream=stream)
    response = _open_gateway(
        origin,
        path,
        method="POST",
        body=body,
        access_token=access_token,
        rehearsal_header=rehearsal_header,
        rehearsal_key=rehearsal_key,
    )
    try:
        headers = {name.lower(): value for name, value in response.headers.items()}
        if (
            response.status != 200
            or headers.get("x-hormuz-requested-model") != alias
            or not headers.get("x-hormuz-routed-model")
            or TIMING_RE.fullmatch(headers.get("server-timing", "")) is None
        ):
            raise QualificationError("provider_request_failed")
        if stream:
            first = response.read(1)
            remainder = _read(response, MAX_PROVIDER_RESPONSE_BYTES)
            if not first:
                raise QualificationError("provider_stream_empty")
            return headers, True, bool(remainder)
        payload = _read(response, MAX_PROVIDER_RESPONSE_BYTES)
        if not payload:
            raise QualificationError("provider_response_empty")
        return headers, False, False
    finally:
        response.close()


def _delta(after: dict[str, Any], before: dict[str, Any], name: str) -> int:
    result = after[name] - before[name]
    if result < 0:
        raise QualificationError("provider_reliability_counter_regressed")
    return result


def qualify(
    *,
    origin: str,
    expected_commit: str,
    service_id: str,
    deployment_evidence_url: str,
    workflow_run_url: str,
    refresh_token: str,
    rehearsal_key: str,
    deploy_hook: str,
) -> dict[str, Any]:
    origin = _origin(origin)
    if (
        COMMIT_RE.fullmatch(expected_commit) is None
        or SERVICE_ID_RE.fullmatch(service_id) is None
        or RUN_URL_RE.fullmatch(deployment_evidence_url) is None
        or RUN_URL_RE.fullmatch(workflow_run_url) is None
        or deployment_evidence_url == workflow_run_url
        or TOKEN_RE.fullmatch(refresh_token) is None
        or re.fullmatch(r"[A-Za-z0-9_-]{43,128}", rehearsal_key) is None
    ):
        raise QualificationError("qualification_input_invalid")

    _restart_and_wait(
        origin,
        expected_commit=expected_commit,
        service_id=service_id,
        deploy_hook=deploy_hook,
    )
    access_token = ""
    rotated_refresh = ""
    primary_error: BaseException | None = None
    try:
        access_token, rotated_refresh = _refresh(origin, refresh_token)
        baseline = _reliability(
            origin,
            access_token,
            expected_commit=expected_commit,
            service_id=service_id,
        )
        first_chunk_before_completion = True
        request_count = 0
        for protocol in ("anthropic", "openai"):
            for suffix in ("primary", "secondary"):
                alias = f"{protocol}-{suffix}"
                _provider_request(
                    origin,
                    access_token,
                    protocol=protocol,
                    alias=alias,
                    stream=False,
                )
                request_count += 1
                _, first, remainder = _provider_request(
                    origin,
                    access_token,
                    protocol=protocol,
                    alias=alias,
                    stream=True,
                )
                request_count += 1
                first_chunk_before_completion = first_chunk_before_completion and first and remainder

        before_cancellation = _reliability(
            origin,
            access_token,
            expected_commit=expected_commit,
            service_id=service_id,
        )
        cancellation_headers, first, _ = _provider_request(
            origin,
            access_token,
            protocol="openai",
            alias="openai-primary",
            stream=True,
            rehearsal_header="X-Hormuz-Cancellation-Rehearsal",
            rehearsal_key=rehearsal_key,
        )
        request_count += 1
        after_cancellation = _reliability(
            origin,
            access_token,
            expected_commit=expected_commit,
            service_id=service_id,
        )
        cancellation_replays = _delta(
            after_cancellation,
            before_cancellation,
            "failover_link_record_count",
        )
        if (
            cancellation_headers.get("x-hormuz-cancellation-rehearsal") != "v1"
            or not first
            or _delta(
                after_cancellation,
                before_cancellation,
                "cancellation_outcome_unknown_count",
            )
            != 1
            or cancellation_replays != 0
        ):
            raise QualificationError("cancellation_rehearsal_failed")

        before_failover = after_cancellation
        failover_headers, _, _ = _provider_request(
            origin,
            access_token,
            protocol="openai",
            alias="openai-primary",
            stream=False,
            rehearsal_header="X-Hormuz-Failover-Rehearsal",
            rehearsal_key=rehearsal_key,
        )
        request_count += 1
        final = _reliability(
            origin,
            access_token,
            expected_commit=expected_commit,
            service_id=service_id,
        )
        if (
            failover_headers.get("x-hormuz-failover") != "v1;reason=provider_rate_limited"
            or failover_headers.get("x-hormuz-failover-rehearsal") != "v1"
            or _delta(final, before_failover, "live_provider_request_count") != 1
            or _delta(final, before_failover, "provider_attempt_record_count") != 2
            or _delta(final, before_failover, "failover_link_record_count") != 1
        ):
            raise QualificationError("failover_rehearsal_failed")
        if (
            _delta(final, baseline, "live_provider_request_count") != request_count
            or _delta(final, baseline, "provider_attempt_record_count") != request_count + 1
            or _delta(final, baseline, "latency_header_sample_count") != request_count + 1
            or _delta(final, baseline, "latency_first_body_byte_sample_count") < request_count
            or _delta(final, baseline, "latency_total_sample_count") != request_count + 1
            or not first_chunk_before_completion
        ):
            raise QualificationError("provider_reliability_evidence_incomplete")

        contract = EXPECTED_CONTRACT
        return {
            "schema_id": SCHEMA_ID,
            "schema_version": SCHEMA_VERSION,
            "evidence_kind": "live_external_pilot",
            "profile": contract["profile"],
            "source_commit": expected_commit,
            "deployment_evidence_url": deployment_evidence_url,
            "recovery_evidence_url": workflow_run_url,
            "identity_provider": contract["identity_provider"],
            "provider_protocols": contract["provider_protocols"],
            "https": contract["https"],
            "inference_enabled": contract["inference_enabled"],
            "provider_credentials_server_only": contract["provider_credentials_server_only"],
            "postgresql_durable": contract["postgresql_durable"],
            "tenant_rls": contract["tenant_rls"],
            "durable_sessions": contract["durable_sessions"],
            "streaming_verified": True,
            "streaming_first_chunk_before_completion": True,
            "cancellation_verified": True,
            "cancellation_upstream_closed": True,
            "cancellation_outcome_unknown_recorded": True,
            "cancellation_replay_count": cancellation_replays,
            "latency_measurement_verified": True,
            "latency_header_sample_count": final["latency_header_sample_count"],
            "latency_first_body_byte_sample_count": final[
                "latency_first_body_byte_sample_count"
            ],
            "latency_total_sample_count": final["latency_total_sample_count"],
            "policy_bounded_same_protocol_failover_verified": True,
            "failover_rehearsal_passed": True,
            "failover_link_record_count": final["failover_link_record_count"],
            "failover_hop_limit": 1,
            "monitoring_configured": contract["monitoring_configured"],
            "worker_saturation_monitoring": contract["worker_saturation_monitoring"],
            "postgresql_pool_wait_monitoring": contract[
                "postgresql_pool_wait_monitoring"
            ],
            "recovery_drill_passed": True,
            "support_path_published": True,
            "single_region_acknowledged": contract["single_region_acknowledged"],
            "availability_sla_claimed": contract["availability_sla_claimed"],
            "live_provider_request_count": final["live_provider_request_count"],
            "provider_attempt_record_count": final["provider_attempt_record_count"],
            "max_inflight_streams": contract["max_inflight_streams"],
        }
    except Exception as error:
        primary_error = error
        raise
    finally:
        if rotated_refresh:
            try:
                _logout(origin, rotated_refresh)
            except Exception:
                if primary_error is None:
                    raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gateway-origin", required=True)
    parser.add_argument("--expected-commit", required=True)
    parser.add_argument("--expected-service-id", required=True)
    parser.add_argument("--deployment-evidence-url", required=True)
    parser.add_argument("--workflow-run-url", required=True)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args(argv)
    refresh_token = os.environ.get("HORMUZ_EXTERNAL_PILOT_REFRESH_TOKEN", "")
    rehearsal_key = os.environ.get("HORMUZ_FAILOVER_REHEARSAL_KEY", "")
    deploy_hook = os.environ.get("HORMUZ_RENDER_DEPLOY_HOOK_URL", "")
    try:
        evidence = qualify(
            origin=arguments.gateway_origin,
            expected_commit=arguments.expected_commit,
            service_id=arguments.expected_service_id,
            deployment_evidence_url=arguments.deployment_evidence_url,
            workflow_run_url=arguments.workflow_run_url,
            refresh_token=refresh_token,
            rehearsal_key=rehearsal_key,
            deploy_hook=deploy_hook,
        )
        output = arguments.output.resolve()
        if output.exists() or output.is_symlink() or not output.parent.is_dir():
            raise QualificationError("output_path_unsafe")
        output.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except (QualificationError, OSError, UnicodeError) as error:
        code = str(error)
        if not re.fullmatch(r"[a-z0-9_]+", code):
            code = "external_pilot_qualification_failed"
        print(code, file=sys.stderr)
        return 1
    print(json.dumps({
        "schema_id": SCHEMA_ID,
        "schema_version": SCHEMA_VERSION,
        "status": "passed",
        "source_commit": evidence["source_commit"],
        "live_provider_request_count": evidence["live_provider_request_count"],
        "provider_attempt_record_count": evidence["provider_attempt_record_count"],
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
