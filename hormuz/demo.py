"""Disposable provider-free proof of the Hormuz gateway boundary.

The public quickstart must exercise the same HTTP, policy, redaction, request-
attempt, and evidence path as a configured gateway without requiring a model
provider account.  This module supplies only a loopback provider simulator and
synthetic inputs; it never changes the production provider contract.
"""

from __future__ import annotations

import http.client
import json
import tempfile
import threading
import time
from dataclasses import dataclass, replace
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from .config import GatewayConfig
from .contracts import (
    COVERAGE_GATEWAY_CAPTURED_REQUESTS_ONLY,
    validate_audit_event,
    validate_contract,
)
from .server import GatewayServer, serve_in_thread


_SINCE_ALL_EVENTS = "2000-01-01T00:00:00+00:00"
_REDACTION_MARKER = "[REDACTED:HORMUZ_SECRET]"


class ProviderFreeDemoError(RuntimeError):
    """A stable, content-free failure from the synthetic quickstart."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class ProviderFreeDemoResult:
    elapsed_seconds: float
    provider_simulator_calls: int
    usage_events: int
    security_events: int


class _LoopbackProviderServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self) -> None:
        self.requests: list[dict[str, object]] = []
        self.requests_lock = threading.Lock()
        super().__init__(("127.0.0.1", 0), _LoopbackProviderHandler)


class _LoopbackProviderHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_POST(self) -> None:  # noqa: N802
        server = self.server
        if not isinstance(server, _LoopbackProviderServer):  # pragma: no cover - construction invariant
            self.send_error(HTTPStatus.INTERNAL_SERVER_ERROR)
            return
        request_path = self.path.partition("?")[0]
        if request_path != "/v1/responses":
            self._send_json({"error": "unsupported synthetic route"}, status=HTTPStatus.NOT_FOUND)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            body = json.loads(self.rfile.read(length))
        except (ValueError, json.JSONDecodeError):
            self._send_json({"error": "invalid synthetic request"}, status=HTTPStatus.BAD_REQUEST)
            return
        if not isinstance(body, dict):
            self._send_json({"error": "invalid synthetic request"}, status=HTTPStatus.BAD_REQUEST)
            return
        with server.requests_lock:
            server.requests.append(
                {
                    "headers": {name.lower(): value for name, value in self.headers.items()},
                    "body": body,
                }
            )
        self._send_json(
            {
                "id": "response_provider_free_demo",
                "object": "response",
                "status": "completed",
                "model": body.get("model"),
                "output": [
                    {
                        "type": "message",
                        "role": "assistant",
                        "content": [
                            {
                                "type": "output_text",
                                "text": "synthetic-provider-output-must-not-be-persisted",
                            }
                        ],
                    }
                ],
                "usage": {
                    "input_tokens": 12,
                    "output_tokens": 4,
                    "input_tokens_details": {"cached_tokens": 2},
                    "output_tokens_details": {"reasoning_tokens": 1},
                    "total_tokens": 16,
                },
            },
            request_id="request_provider_free_demo",
        )

    def _send_json(
        self,
        value: dict[str, object],
        *,
        status: HTTPStatus = HTTPStatus.OK,
        request_id: str | None = None,
    ) -> None:
        body = json.dumps(value, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        if request_id is not None:
            self.send_header("x-request-id", request_id)
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        return


def run_provider_free_demo() -> ProviderFreeDemoResult:
    """Run one real gateway proof against a disposable loopback simulator."""

    started = time.monotonic()
    provider = _LoopbackProviderServer()
    provider_thread = threading.Thread(
        target=provider.serve_forever,
        name="hormuz-demo-provider",
        daemon=True,
    )
    provider_thread.start()
    gateway: GatewayServer | None = None
    gateway_thread: threading.Thread | None = None
    try:
        with tempfile.TemporaryDirectory(prefix="hormuz-provider-free-") as temporary:
            root = Path(temporary)
            synthetic_values = _demo_secret_values()
            config_path = root / "hormuz.json"
            config_path.write_text(
                json.dumps(_demo_config(provider.server_port), separators=(",", ":")),
                encoding="utf-8",
            )
            config = GatewayConfig.load(config_path, environ=synthetic_values)
            # Port zero is an internal loopback-only test fixture. Config files
            # retain the strict 1..65535 public validation boundary.
            config = replace(config, listen=replace(config.listen, port=0))
            gateway = GatewayServer(config, environ=synthetic_values)
            gateway_thread = serve_in_thread(gateway)
            gateway_port = gateway.server_port

            forbidden_evidence_values = _exercise_gateway(
                gateway_port=gateway_port,
                provider=provider,
                synthetic_values=synthetic_values,
            )
            usage_events, security_events = _verify_evidence(
                gateway,
                forbidden_values=forbidden_evidence_values,
            )
    except ProviderFreeDemoError:
        raise
    except Exception as error:
        raise ProviderFreeDemoError("provider_free_demo_internal_failure") from error
    finally:
        if gateway is not None:
            gateway.shutdown()
        if gateway_thread is not None:
            gateway_thread.join(timeout=5)
        if gateway is not None:
            gateway.server_close()
        provider.shutdown()
        provider_thread.join(timeout=5)
        provider.server_close()

    return ProviderFreeDemoResult(
        elapsed_seconds=time.monotonic() - started,
        provider_simulator_calls=len(provider.requests),
        usage_events=usage_events,
        security_events=security_events,
    )


def _exercise_gateway(
    *,
    gateway_port: int,
    provider: _LoopbackProviderServer,
    synthetic_values: dict[str, str],
) -> tuple[str, ...]:
    allowed_content = "synthetic-allowed-request-must-not-be-persisted"
    routed_content = "synthetic-rerouted-request-must-not-be-persisted"
    redacted_content = "synthetic-redacted-request-must-not-be-persisted"
    denied_content = "synthetic-denied-request-must-not-be-persisted"
    detected_secret = "-".join(("sk", "proj", "A" * 24))

    status, headers, _ = _post(
        gateway_port,
        token=synthetic_values["HORMUZ_DEMO_ENGINEER_TOKEN"],
        body={"model": "demo-fast", "input": allowed_content, "max_output_tokens": 8},
    )
    _require(status == HTTPStatus.OK, "allowed_request_failed")
    _require(headers.get("x-hormuz-policy-decision") == "allowed", "allowed_policy_action_mismatch")
    _require(len(provider.requests) == 1, "allowed_request_did_not_reach_simulator")
    allowed_headers = provider.requests[-1]["headers"]
    _require(isinstance(allowed_headers, dict), "simulator_request_shape_invalid")
    _require(
        allowed_headers.get("authorization")
        == f"Bearer {synthetic_values['HORMUZ_DEMO_PROVIDER_CREDENTIAL']}",
        "provider_credential_forwarding_mismatch",
    )
    _require(
        synthetic_values["HORMUZ_DEMO_ENGINEER_TOKEN"]
        not in json.dumps(allowed_headers, sort_keys=True),
        "identity_credential_reached_simulator",
    )

    status, headers, _ = _post(
        gateway_port,
        token=synthetic_values["HORMUZ_DEMO_ENGINEER_TOKEN"],
        body={"model": "demo-deep", "input": routed_content, "max_output_tokens": 1000},
    )
    _require(status == HTTPStatus.OK, "rerouted_request_failed")
    _require(
        headers.get("x-hormuz-policy-decision") == "fallback+capped",
        "rerouted_policy_action_mismatch",
    )
    _require(len(provider.requests) == 2, "rerouted_request_did_not_reach_simulator")
    routed_body = provider.requests[-1]["body"]
    _require(isinstance(routed_body, dict), "simulator_request_shape_invalid")
    _require(routed_body.get("model") == "demo-provider-fast", "rerouted_model_mismatch")
    _require(routed_body.get("max_output_tokens") == 16, "output_cap_mismatch")

    status, headers, _ = _post(
        gateway_port,
        token=synthetic_values["HORMUZ_DEMO_ENGINEER_TOKEN"],
        body={
            "model": "demo-fast",
            "input": f"{redacted_content}: {detected_secret}",
            "max_output_tokens": 8,
        },
    )
    _require(status == HTTPStatus.OK, "redacted_request_failed")
    _require(
        headers.get("x-hormuz-policy-decision") == "allowed+redacted",
        "redacted_policy_action_mismatch",
    )
    _require(headers.get("x-hormuz-redactions") == "1", "redaction_count_mismatch")
    _require(len(provider.requests) == 3, "redacted_request_did_not_reach_simulator")
    redacted_body = json.dumps(provider.requests[-1]["body"], sort_keys=True)
    _require(detected_secret not in redacted_body, "secret_reached_simulator")
    _require(_REDACTION_MARKER in redacted_body, "redaction_marker_missing")

    before_denial = len(provider.requests)
    status, _, response = _post(
        gateway_port,
        token=synthetic_values["HORMUZ_DEMO_REVIEWER_TOKEN"],
        body={"model": "demo-deep", "input": denied_content, "max_output_tokens": 8},
    )
    _require(status == HTTPStatus.FORBIDDEN, "denied_request_was_not_blocked")
    _require(len(provider.requests) == before_denial, "denied_request_reached_simulator")
    _require(response.get("error", {}).get("code") == "hormuz_policy_denied", "denied_error_mismatch")

    status, _, usage = _get(
        gateway_port,
        token=synthetic_values["HORMUZ_DEMO_ENGINEER_TOKEN"],
        path="/v1/gateway/usage",
    )
    _require(status == HTTPStatus.OK, "usage_summary_failed")
    try:
        validate_contract(usage)
    except Exception as error:
        raise ProviderFreeDemoError("usage_summary_contract_invalid") from error
    _require(usage.get("requests") == 3, "usage_summary_request_count_mismatch")
    _require(usage.get("redactions") == 1, "usage_summary_redaction_count_mismatch")
    _require(usage.get("coverage") == COVERAGE_GATEWAY_CAPTURED_REQUESTS_ONLY, "usage_coverage_mismatch")

    return (
        allowed_content,
        routed_content,
        redacted_content,
        denied_content,
        detected_secret,
        "synthetic-provider-output-must-not-be-persisted",
        *synthetic_values.values(),
    )


def _verify_evidence(
    gateway: GatewayServer,
    *,
    forbidden_values: tuple[str, ...],
) -> tuple[int, int]:
    events = gateway.store.audit_events(
        since=_SINCE_ALL_EVENTS,
        organization_id="provider-free-demo",
    )
    for event in events:
        try:
            validate_audit_event(event)
        except Exception as error:
            raise ProviderFreeDemoError("audit_event_contract_invalid") from error
    usage_events = [event for event in events if event["event_type"] == "usage"]
    security_events = [event for event in events if event["event_type"] == "security.secret"]
    _require(len(usage_events) == 4, "usage_evidence_count_mismatch")
    _require(len(security_events) == 1, "security_evidence_count_mismatch")
    _require(
        {event["policy_action"] for event in usage_events}
        == {"allowed", "fallback+capped", "allowed+redacted", "denied"},
        "usage_evidence_actions_mismatch",
    )
    _require(security_events[0]["action"] == "redacted", "security_evidence_action_mismatch")
    _require(
        all(event.get("coverage") == COVERAGE_GATEWAY_CAPTURED_REQUESTS_ONLY for event in events),
        "audit_coverage_mismatch",
    )

    serialized = json.dumps(events, sort_keys=True, separators=(",", ":"))
    for forbidden in forbidden_values:
        _require(forbidden not in serialized, "content_entered_evidence")
    _require(_REDACTION_MARKER not in serialized, "transformed_content_entered_evidence")
    return len(usage_events), len(security_events)


def _post(
    port: int,
    *,
    token: str,
    body: dict[str, object],
) -> tuple[int, dict[str, str], dict[str, Any]]:
    return _request(port, method="POST", path="/v1/responses", token=token, body=body)


def _get(
    port: int,
    *,
    token: str,
    path: str,
) -> tuple[int, dict[str, str], dict[str, Any]]:
    return _request(port, method="GET", path=path, token=token)


def _request(
    port: int,
    *,
    method: str,
    path: str,
    token: str,
    body: dict[str, object] | None = None,
) -> tuple[int, dict[str, str], dict[str, Any]]:
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    encoded = None if body is None else json.dumps(body, separators=(",", ":"))
    headers = {"Authorization": f"Bearer {token}"}
    if encoded is not None:
        headers["Content-Type"] = "application/json"
    try:
        connection.request(method, path, body=encoded, headers=headers)
        response = connection.getresponse()
        response_body = response.read()
        response_headers = {name.lower(): value for name, value in response.getheaders()}
        try:
            payload = json.loads(response_body)
        except json.JSONDecodeError as error:
            raise ProviderFreeDemoError("gateway_response_invalid") from error
        if not isinstance(payload, dict):
            raise ProviderFreeDemoError("gateway_response_invalid")
        return response.status, response_headers, payload
    finally:
        connection.close()


def _demo_secret_values() -> dict[str, str]:
    return {
        "HORMUZ_DEMO_ENGINEER_TOKEN": "demo-engineer-identity-token-never-forward",
        "HORMUZ_DEMO_REVIEWER_TOKEN": "demo-reviewer-identity-token-never-forward",
        "HORMUZ_DEMO_PROVIDER_CREDENTIAL": "demo-provider-credential-never-expose",
        "HORMUZ_DEMO_UNUSED_ANTHROPIC_CREDENTIAL": "demo-unused-anthropic-credential",
    }


def _demo_config(provider_port: int) -> dict[str, object]:
    provider_url = f"http://127.0.0.1:{provider_port}"
    return {
        "listen": {"host": "127.0.0.1", "port": 8787},
        "database": "./usage.sqlite3",
        "max_request_bytes": 65536,
        "upstream_timeout_seconds": 10,
        "upstreams": {
            "openai": {
                "base_url": provider_url,
                "api_key_env": "HORMUZ_DEMO_PROVIDER_CREDENTIAL",
                "allow_response_storage": False,
                "allow_background": False,
            },
            "anthropic": {
                "base_url": provider_url,
                "api_key_env": "HORMUZ_DEMO_UNUSED_ANTHROPIC_CREDENTIAL",
            },
        },
        "identities": [
            {
                "token_env": "HORMUZ_DEMO_ENGINEER_TOKEN",
                "actor_id": "demo-engineer",
                "actor_name": "Demo Engineer",
                "team_id": "engineering",
                "team_name": "Engineering",
                "organization_id": "provider-free-demo",
                "identity_type": "human",
                "clearance": "internal",
                "allowed_clients": ["codex"],
            },
            {
                "token_env": "HORMUZ_DEMO_REVIEWER_TOKEN",
                "actor_id": "demo-reviewer",
                "actor_name": "Demo Reviewer",
                "team_id": "review",
                "team_name": "Review",
                "organization_id": "provider-free-demo",
                "identity_type": "human",
                "clearance": "internal",
                "allowed_clients": ["codex"],
            },
        ],
        "model_routes": {
            "demo-fast": {
                "protocol": "openai",
                "upstream_model": "demo-provider-fast",
                "input_cost_per_million": 1,
                "cache_read_cost_per_million": 0.1,
                "output_cost_per_million": 2,
            },
            "demo-deep": {
                "protocol": "openai",
                "upstream_model": "demo-provider-deep",
                "input_cost_per_million": 2,
                "cache_read_cost_per_million": 0.2,
                "output_cost_per_million": 4,
            },
        },
        "egress_controls": {
            "secrets": {"mode": "redact", "builtins": True, "custom_secret_envs": []},
        },
        "policies": {
            "organization": {
                "allowed_clients": ["codex"],
                "allowed_models": ["demo-fast", "demo-deep"],
                "max_output_tokens": 64,
            },
            "teams": {
                "engineering": {
                    "allowed_models": ["demo-fast"],
                    "fallback_models": {"openai": "demo-fast"},
                    "max_output_tokens": 16,
                },
                "review": {
                    "allowed_models": ["demo-fast"],
                    "max_output_tokens": 16,
                },
            },
            "actors": {},
        },
    }


def _require(condition: bool, code: str) -> None:
    if not condition:
        raise ProviderFreeDemoError(code)
