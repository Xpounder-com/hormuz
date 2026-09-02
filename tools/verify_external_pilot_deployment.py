#!/usr/bin/env python3
"""Create strict, content-free evidence for one live external-pilot deployment."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import re
import ssl
import sys
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, HTTPSHandler, ProxyHandler, Request, build_opener


SCHEMA_ID = "hormuz.external-pilot-deployment-evidence"
SCHEMA_VERSION = 1
MAX_RESPONSE_BYTES = 64 * 1024
COMMIT_RE = re.compile(r"[0-9a-f]{40}\Z")
SERVICE_ID_RE = re.compile(r"srv-[a-z0-9]{16,32}\Z")
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


class DeploymentEvidenceError(ValueError):
    """A stable qualification failure without response or credential content."""


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, file_pointer, code, message, headers, new_url):
        return None


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
        raise DeploymentEvidenceError("gateway_origin_invalid")
    return value


def _request(origin: str, path: str, *, method: str = "GET") -> tuple[int, dict[str, str], bytes]:
    body = b"{}" if method == "POST" else None
    request = Request(
        origin + path,
        data=body,
        method=method,
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "hormuz-external-pilot-deployment-verifier/1",
        },
    )
    opener = build_opener(
        ProxyHandler({}),
        HTTPSHandler(context=ssl.create_default_context()),
        _NoRedirect(),
    )
    try:
        response = opener.open(request, timeout=15)
    except HTTPError as error:
        response = error
    except (URLError, TimeoutError, OSError):
        raise DeploymentEvidenceError("gateway_request_failed") from None
    try:
        length = response.headers.get("Content-Length")
        if length is not None and (not length.isdecimal() or int(length) > MAX_RESPONSE_BYTES):
            raise DeploymentEvidenceError("gateway_response_size_invalid")
        payload = response.read(MAX_RESPONSE_BYTES + 1)
        if len(payload) > MAX_RESPONSE_BYTES:
            raise DeploymentEvidenceError("gateway_response_size_invalid")
        return int(response.status), {name.lower(): value for name, value in response.headers.items()}, payload
    finally:
        response.close()


def _json_response(origin: str, path: str) -> tuple[dict[str, Any], dict[str, str]]:
    status, headers, payload = _request(origin, path)
    if status != 200 or headers.get("content-type", "").split(";", 1)[0].strip() != "application/json":
        raise DeploymentEvidenceError("gateway_readiness_failed")
    try:
        value = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise DeploymentEvidenceError("gateway_response_invalid") from None
    if not isinstance(value, dict):
        raise DeploymentEvidenceError("gateway_response_invalid")
    return value, headers


def build_evidence(
    *,
    origin: str,
    expected_commit: str,
    expected_service_id: str,
    workflow_run_url: str,
    root: Path,
) -> dict[str, Any]:
    origin = _origin(origin)
    if COMMIT_RE.fullmatch(expected_commit) is None:
        raise DeploymentEvidenceError("expected_commit_invalid")
    if SERVICE_ID_RE.fullmatch(expected_service_id) is None:
        raise DeploymentEvidenceError("expected_service_id_invalid")
    if not re.fullmatch(
        r"https://github\.com/Xpounder-com/hormuz/actions/runs/[1-9][0-9]{0,19}",
        workflow_run_url,
    ):
        raise DeploymentEvidenceError("workflow_run_url_invalid")

    health, health_headers = _json_response(origin, "/health")
    ready, ready_headers = _json_response(origin, "/ready")
    for headers in (health_headers, ready_headers):
        if headers.get("cache-control") != "no-store" or headers.get("x-content-type-options") != "nosniff":
            raise DeploymentEvidenceError("gateway_response_controls_invalid")
    expected_base = {
        "schema_id": "hormuz.hosted-provider-pilot",
        "schema_version": 1,
        "status": "provider_pilot",
        "inference_enabled": True,
    }
    if any(health.get(name) != value or ready.get(name) != value for name, value in expected_base.items()):
        raise DeploymentEvidenceError("gateway_profile_not_ready")
    if health.get("contract") != EXPECTED_CONTRACT or ready.get("contract") != EXPECTED_CONTRACT:
        raise DeploymentEvidenceError("gateway_contract_invalid")
    if health.get("deployment") != ready.get("deployment"):
        raise DeploymentEvidenceError("gateway_deployment_identity_changed")
    deployment = health.get("deployment")
    if not isinstance(deployment, dict):
        raise DeploymentEvidenceError("gateway_deployment_identity_invalid")
    expected_deployment = {
        "platform": "render",
        "source_commit": expected_commit,
        "source_branch": "main",
        "repository": "Xpounder-com/hormuz",
        "cpu_count": "0.5",
        "web_concurrency": "1",
        "external_origin": origin,
        "service_id": expected_service_id,
    }
    if any(deployment.get(name) != value for name, value in expected_deployment.items()):
        raise DeploymentEvidenceError("gateway_deployment_identity_invalid")
    if re.fullmatch(r"[0-9a-f]{16}", str(deployment.get("instance_fingerprint", ""))) is None:
        raise DeploymentEvidenceError("gateway_instance_fingerprint_invalid")

    unauthorized_status, unauthorized_headers, _ = _request(origin, "/v1/responses", method="POST")
    if unauthorized_status != 401 or unauthorized_headers.get("cache-control") != "no-store":
        raise DeploymentEvidenceError("unauthenticated_inference_boundary_invalid")
    support = root / "SUPPORT.md"
    if not support.is_file() or support.is_symlink() or not support.read_text(encoding="utf-8").strip():
        raise DeploymentEvidenceError("support_path_unpublished")

    return {
        "schema_id": SCHEMA_ID,
        "schema_version": SCHEMA_VERSION,
        "evidence_kind": "live_external_pilot",
        **EXPECTED_CONTRACT,
        "source_commit": expected_commit,
        "workflow_run_url": workflow_run_url,
        "gateway_origin": origin,
        "render_service_id": expected_service_id,
        "support_path_published": True,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gateway-origin", required=True)
    parser.add_argument("--expected-commit", required=True)
    parser.add_argument("--expected-service-id", required=True)
    parser.add_argument("--workflow-run-url", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    arguments = parser.parse_args(argv)
    try:
        evidence = build_evidence(
            origin=arguments.gateway_origin,
            expected_commit=arguments.expected_commit,
            expected_service_id=arguments.expected_service_id,
            workflow_run_url=arguments.workflow_run_url,
            root=arguments.root.resolve(),
        )
        output = arguments.output.resolve()
        if output.exists() or output.is_symlink() or not output.parent.is_dir():
            raise DeploymentEvidenceError("output_path_unsafe")
        output.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except (DeploymentEvidenceError, OSError, UnicodeError) as error:
        print(str(error), file=sys.stderr)
        return 1
    print(json.dumps({
        "schema_id": SCHEMA_ID,
        "schema_version": SCHEMA_VERSION,
        "status": "passed",
        "source_commit": evidence["source_commit"],
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
