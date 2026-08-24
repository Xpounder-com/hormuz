#!/usr/bin/env python3
"""Run pinned Codex and Claude Code through Hormuz to their real providers.

This release-gate harness is intentionally opt-in. It starts the normal Hormuz
gateway on loopback, gives each official client only a synthetic Hormuz
identity credential, and keeps the provider credentials inside the gateway
process. The retained artifact is a strict metadata-only summary; client
prompts, responses, provider request IDs, and credentials are never written.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import secrets
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from hormuz.config import GatewayConfig
from hormuz.contracts import (
    ALLOCATION_BASIS_DIRECT_GATEWAY_REQUEST,
    COST_BASIS_CONFIGURED_RATE_CARD_ESTIMATE,
    COVERAGE_GATEWAY_CAPTURED_REQUESTS_ONLY,
    validate_audit_event,
)
from hormuz.server import GatewayRequestHandler, GatewayServer, serve_in_thread


SCHEMA_ID = "hormuz.live-client-conformance"
SCHEMA_VERSION = 1
ACKNOWLEDGEMENT = "I_UNDERSTAND_LIVE_PROVIDER_CALLS_HAVE_COST_AND_USE_DEDICATED_KEYS"
SUPPORTED_CODEX_VERSION = "0.147.0"
SUPPORTED_CLAUDE_CODE_VERSION = "2.1.233"

_SINCE_ALL_EVENTS = "2000-01-01T00:00:00+00:00"
_ORGANIZATION_ID = "hormuz-live-client-conformance"
_TEAM_ID = "release-conformance"
_TEAM_NAME = "Release Conformance"
_OUTPUT_TOKEN_CAP = 64
_REDACTION_MARKER = "[REDACTED:HORMUZ_SECRET]"
_MODEL_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
_VERSION_RE = re.compile(r"(?:^|\s)(\d+\.\d+\.\d+)(?:\s|$)")
_REVISION_RE = re.compile(r"[0-9a-f]{40}\Z")
_ENV_NAME_RE = re.compile(r"[A-Z_][A-Z0-9_]*\Z")
_MAX_CREDENTIAL_BYTES = 64 * 1024

_CHECKS = (
    "authenticated_identity",
    "policy_selection",
    "model_routing",
    "output_cap_before_egress",
    "secret_redaction_before_egress",
    "real_provider_egress",
    "streaming_request",
    "provider_reported_usage",
    "configured_rate_card_cost",
    "content_free_audit",
    "provider_credentials_absent_from_client",
)
_NONCLAIMS = (
    "not_provider_invoice",
    "not_provider_key_scope_introspection",
    "not_all_client_features",
    "not_traffic_bypassing_hormuz",
    "not_enterprise_production_readiness",
)
_ROOT_FIELDS = {
    "schema_id",
    "schema_version",
    "generated_at",
    "source_revision",
    "scope",
    "credential_scope",
    "runner",
    "checks",
    "nonclaims",
    "results",
}
_RUNNER_FIELDS = {"os", "architecture"}
_RESULT_FIELDS = {
    "provider",
    "client",
    "client_version",
    "client_entrypoint_sha256",
    "client_runtime_sha256",
    "requested_model",
    "routed_model",
    "provider_reported_model",
    "policy_version",
    "policy_action",
    "status",
    "request_count",
    "input_tokens",
    "output_tokens",
    "cache_read_tokens",
    "cache_write_tokens",
    "reasoning_tokens",
    "cost_microusd",
    "cost_basis",
    "allocation_basis",
    "coverage",
    "redaction_count",
    "secret_event_count",
    "provider_request_id_present",
    "streaming_request_observed",
    "output_cap_observed",
    "redaction_before_egress_observed",
    "provider_credential_in_client_environment",
    "organization_id",
    "actor_id",
    "team_id",
    "authentication_source",
}


class LiveClientConformanceError(RuntimeError):
    """A stable, content-free live conformance failure."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class _ClientSpec:
    provider: str
    client: str
    protocol: str
    model: str
    executable: Path
    runtime: Path
    expected_version: str
    identity_env: str
    identity_token: str
    actor_id: str
    marker: str
    synthetic_secret: str


@dataclass(frozen=True)
class _EgressObservation:
    protocol: str
    client: str
    model: str | None
    streaming: bool
    output_limit: int | None
    secret_absent: bool
    redaction_marker_present: bool


class _ObservedGatewayRequestHandler(GatewayRequestHandler):
    """Capture allowlisted post-policy metadata immediately before egress."""

    def _forward(self, **kwargs: Any) -> None:
        protocol = kwargs.get("protocol")
        client = kwargs.get("client")
        body = kwargs.get("body")
        if isinstance(protocol, str) and isinstance(client, str) and isinstance(body, bytes):
            server = self.server
            secret_by_protocol = getattr(server, "_live_conformance_secret_by_protocol", {})
            try:
                value = json.loads(body)
            except (UnicodeDecodeError, json.JSONDecodeError):
                value = None
            if isinstance(value, dict):
                output_field = "max_output_tokens" if protocol == "openai" else "max_tokens"
                serialized = json.dumps(value, sort_keys=True, separators=(",", ":"))
                secret = secret_by_protocol.get(protocol)
                observation = _EgressObservation(
                    protocol=protocol,
                    client=client,
                    model=value.get("model") if isinstance(value.get("model"), str) else None,
                    streaming=value.get("stream") is True,
                    output_limit=(
                        value.get(output_field)
                        if isinstance(value.get(output_field), int)
                        and not isinstance(value.get(output_field), bool)
                        else None
                    ),
                    secret_absent=isinstance(secret, str) and secret not in serialized,
                    redaction_marker_present=_REDACTION_MARKER in serialized,
                )
                lock = getattr(server, "_live_conformance_observation_lock")
                observations = getattr(server, "_live_conformance_observations")
                with lock:
                    observations.append(observation)
        super()._forward(**kwargs)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run a content-free live Codex/Claude Code BYO-provider conformance proof."
    )
    parser.add_argument(
        "--provider",
        action="append",
        choices=("openai", "anthropic"),
        required=True,
        help="Provider to exercise; repeat to run both release-gated clients",
    )
    parser.add_argument("--openai-model", help="Exact OpenAI model ID available to the dedicated key")
    parser.add_argument("--anthropic-model", help="Exact Anthropic model ID available to the dedicated key")
    parser.add_argument("--credential-env-file", type=Path)
    parser.add_argument(
        "--openai-credential-env",
        default="HORMUZ_LIVE_OPENAI_PROVIDER_KEY",
        help="Environment entry containing the gateway-only OpenAI credential",
    )
    parser.add_argument(
        "--anthropic-credential-env",
        default="HORMUZ_LIVE_ANTHROPIC_PROVIDER_KEY",
        help="Environment entry containing the gateway-only Anthropic credential",
    )
    parser.add_argument("--codex-command", default="codex")
    parser.add_argument("--claude-command", default="claude")
    parser.add_argument("--expected-codex-version", default=SUPPORTED_CODEX_VERSION)
    parser.add_argument("--expected-claude-code-version", default=SUPPORTED_CLAUDE_CODE_VERSION)
    parser.add_argument("--source-revision")
    parser.add_argument("--timeout-seconds", type=int, default=180)
    parser.add_argument("--evidence-out", type=Path, required=True)
    parser.add_argument(
        "--acknowledgement",
        default=os.environ.get("HORMUZ_LIVE_CLIENT_CONFORMANCE_ACKNOWLEDGEMENT", ""),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        _require_evidence_path_available(args.evidence_out)
        evidence, forbidden_values = run_conformance(args)
        validate_evidence(evidence)
        _assert_content_free(evidence, forbidden_values=forbidden_values)
        _write_private_json(args.evidence_out, evidence)
    except LiveClientConformanceError as error:
        print(f"live_client_conformance=failed code={error.code}", file=sys.stderr)
        return 1
    except Exception:
        print("live_client_conformance=failed code=conformance_internal_failure", file=sys.stderr)
        return 1
    print("live_client_conformance=passed")
    print(f"scope={evidence['scope']}")
    print("providers=" + ",".join(result["provider"] for result in evidence["results"]))
    print(f"evidence_schema={SCHEMA_ID};v={SCHEMA_VERSION}")
    print("credential_retention=none")
    return 0


def run_conformance(args: argparse.Namespace) -> tuple[dict[str, object], tuple[str, ...]]:
    providers = _selected_providers(args.provider)
    if args.acknowledgement != ACKNOWLEDGEMENT:
        raise LiveClientConformanceError("acknowledgement_required")
    if not 30 <= args.timeout_seconds <= 600:
        raise LiveClientConformanceError("timeout_invalid")
    credential_names = {
        "openai": _environment_name(args.openai_credential_env),
        "anthropic": _environment_name(args.anthropic_credential_env),
    }
    credential_source = dict(os.environ)
    if args.credential_env_file is not None:
        credential_source.update(_read_credential_env_file(args.credential_env_file))
    provider_credentials = {
        provider: _credential(credential_source.get(credential_names[provider], ""), provider)
        for provider in providers
    }

    models = {
        "openai": _model(args.openai_model, "openai_model_required") if "openai" in providers else "unused-openai",
        "anthropic": (
            _model(args.anthropic_model, "anthropic_model_required")
            if "anthropic" in providers
            else "unused-anthropic"
        ),
    }
    if len({models[provider] for provider in providers}) != len(providers):
        raise LiveClientConformanceError("provider_models_must_be_distinct")

    specs = _client_specs(args, providers=providers, models=models)
    source_revision = _source_revision(args.source_revision)
    gateway_values = {
        "HORMUZ_LIVE_CODEX_IDENTITY_TOKEN": next(
            (spec.identity_token for spec in specs if spec.provider == "openai"),
            "unused-codex-identity-token",
        ),
        "HORMUZ_LIVE_CLAUDE_IDENTITY_TOKEN": next(
            (spec.identity_token for spec in specs if spec.provider == "anthropic"),
            "unused-claude-identity-token",
        ),
        "HORMUZ_LIVE_RUNTIME_OPENAI_CREDENTIAL": provider_credentials.get(
            "openai", "unused-openai-provider-credential"
        ),
        "HORMUZ_LIVE_RUNTIME_ANTHROPIC_CREDENTIAL": provider_credentials.get(
            "anthropic", "unused-anthropic-provider-credential"
        ),
    }

    gateway: GatewayServer | None = None
    gateway_thread: threading.Thread | None = None
    results: list[dict[str, object]] = []
    with tempfile.TemporaryDirectory(prefix="hormuz-live-client-conformance-") as temporary:
        root = Path(temporary)
        config_path = root / "hormuz.json"
        config_path.write_text(
            json.dumps(_gateway_config(specs), sort_keys=True, separators=(",", ":")),
            encoding="utf-8",
        )
        try:
            config = GatewayConfig.load(config_path, environ=gateway_values)
            config = replace(config, listen=replace(config.listen, port=0))
            gateway = GatewayServer(config, environ=gateway_values)
            gateway.RequestHandlerClass = _ObservedGatewayRequestHandler
            gateway._live_conformance_observations = []  # type: ignore[attr-defined]
            gateway._live_conformance_observation_lock = threading.Lock()  # type: ignore[attr-defined]
            gateway._live_conformance_secret_by_protocol = {  # type: ignore[attr-defined]
                spec.protocol: spec.synthetic_secret for spec in specs
            }
            gateway_thread = serve_in_thread(gateway)
            for spec in specs:
                _run_client(
                    spec,
                    gateway_port=gateway.server_port,
                    root=root,
                    timeout_seconds=args.timeout_seconds,
                    provider_credentials=tuple(provider_credentials.values()),
                    credential_names=tuple(credential_names.values()),
                )
                results.append(_provider_result(gateway, spec))
        finally:
            if gateway is not None:
                gateway.shutdown()
            if gateway_thread is not None:
                gateway_thread.join(timeout=5)
            if gateway is not None:
                gateway.server_close()

    evidence: dict[str, object] = {
        "schema_id": SCHEMA_ID,
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_revision": source_revision,
        "scope": "complete" if set(providers) == {"openai", "anthropic"} else "partial",
        "credential_scope": "operator_attested_dedicated_least_privilege",
        "runner": {
            "os": platform.system().lower(),
            "architecture": platform.machine().lower(),
        },
        "checks": list(_CHECKS),
        "nonclaims": list(_NONCLAIMS),
        "results": results,
    }
    forbidden_values = tuple(
        value
        for value in (
            *gateway_values.values(),
            *provider_credentials.values(),
            *(spec.marker for spec in specs),
            *(spec.synthetic_secret for spec in specs),
            _REDACTION_MARKER,
        )
        if value
    )
    return evidence, forbidden_values


def _selected_providers(values: Sequence[str]) -> tuple[str, ...]:
    providers = tuple(values)
    if not providers or len(set(providers)) != len(providers):
        raise LiveClientConformanceError("providers_invalid")
    return tuple(provider for provider in ("openai", "anthropic") if provider in providers)


def _client_specs(
    args: argparse.Namespace,
    *,
    providers: tuple[str, ...],
    models: Mapping[str, str],
) -> tuple[_ClientSpec, ...]:
    specs: list[_ClientSpec] = []
    if "openai" in providers:
        codex_executable = _client_executable(args.codex_command, "codex_not_found")
        specs.append(
            _ClientSpec(
                provider="openai",
                client="codex",
                protocol="openai",
                model=models["openai"],
                executable=codex_executable,
                runtime=_client_runtime(codex_executable, "codex"),
                expected_version=_version(args.expected_codex_version),
                identity_env="HORMUZ_LIVE_CODEX_IDENTITY_TOKEN",
                identity_token="hormuz-codex-identity-" + secrets.token_urlsafe(24),
                actor_id="live-codex-reviewer",
                marker="HORMUZ_CODEX_LIVE_OK",
                synthetic_secret="sk-proj-" + secrets.token_hex(18),
            )
        )
    if "anthropic" in providers:
        claude_executable = _client_executable(args.claude_command, "claude_code_not_found")
        specs.append(
            _ClientSpec(
                provider="anthropic",
                client="claude-code",
                protocol="anthropic",
                model=models["anthropic"],
                executable=claude_executable,
                runtime=_client_runtime(claude_executable, "claude-code"),
                expected_version=_version(args.expected_claude_code_version),
                identity_env="HORMUZ_LIVE_CLAUDE_IDENTITY_TOKEN",
                identity_token="hormuz-claude-identity-" + secrets.token_urlsafe(24),
                actor_id="live-claude-reviewer",
                marker="HORMUZ_CLAUDE_LIVE_OK",
                synthetic_secret="sk-ant-" + secrets.token_hex(18),
            )
        )
    for spec in specs:
        observed = _client_version(spec.executable)
        if observed != spec.expected_version:
            raise LiveClientConformanceError(f"{spec.client.replace('-', '_')}_version_mismatch")
    return tuple(specs)


def _run_client(
    spec: _ClientSpec,
    *,
    gateway_port: int,
    root: Path,
    timeout_seconds: int,
    provider_credentials: tuple[str, ...],
    credential_names: tuple[str, ...],
) -> None:
    client_root = root / spec.client
    client_root.mkdir(mode=0o700)
    codex_home = client_root / ".codex"
    claude_home = client_root / ".claude"
    codex_home.mkdir(mode=0o700)
    claude_home.mkdir(mode=0o700)
    environment = _sanitized_client_environment(
        os.environ,
        provider_credentials=provider_credentials,
        credential_names=credential_names,
    )
    environment["HOME"] = str(client_root)
    environment["CODEX_HOME"] = str(codex_home)
    environment["CLAUDE_CONFIG_DIR"] = str(claude_home)
    environment[spec.identity_env] = spec.identity_token
    _assert_client_environment_isolated(
        environment,
        provider_credentials=provider_credentials,
        credential_names=credential_names,
    )
    prompt = (
        f"Reply with exactly {spec.marker} and do not call tools. "
        f"The following synthetic credential must be redacted before inference: {spec.synthetic_secret}"
    )
    if spec.provider == "openai":
        command = [
            str(spec.executable),
            "exec",
            "--ignore-user-config",
            "--skip-git-repo-check",
            "--ephemeral",
            "--sandbox",
            "read-only",
            "-C",
            str(client_root),
            "-m",
            spec.model,
            "-c",
            'model_provider="hormuz_live"',
            "-c",
            'model_providers.hormuz_live.name="Hormuz live conformance"',
            "-c",
            f'model_providers.hormuz_live.base_url="http://127.0.0.1:{gateway_port}/v1"',
            "-c",
            f'model_providers.hormuz_live.env_key="{spec.identity_env}"',
            "-c",
            'model_providers.hormuz_live.wire_api="responses"',
            prompt,
        ]
    else:
        environment.update(
            {
                "ANTHROPIC_BASE_URL": f"http://127.0.0.1:{gateway_port}",
                "ANTHROPIC_AUTH_TOKEN": spec.identity_token,
                "DISABLE_AUTOUPDATER": "1",
                "DISABLE_TELEMETRY": "1",
                "CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY": "0",
                "CLAUDE_CODE_DISABLE_UNKNOWN_MODEL_WINDOW_ENFORCEMENT": "1",
            }
        )
        environment.pop("ANTHROPIC_API_KEY", None)
        command = [
            str(spec.executable),
            "-p",
            "--bare",
            "--no-session-persistence",
            "--tools",
            "",
            "--model",
            spec.model,
            prompt,
        ]
    try:
        completed = subprocess.run(
            command,
            cwd=client_root,
            env=environment,
            text=True,
            capture_output=True,
            timeout=timeout_seconds,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        raise LiveClientConformanceError(f"{spec.client.replace('-', '_')}_execution_failed") from None
    if completed.returncode != 0:
        raise LiveClientConformanceError(
            _client_failure_code(spec.client, completed.stdout + "\n" + completed.stderr)
        )
    if spec.marker not in completed.stdout + completed.stderr:
        raise LiveClientConformanceError(f"{spec.client.replace('-', '_')}_marker_missing")


def _sanitized_client_environment(
    source: Mapping[str, str],
    *,
    provider_credentials: tuple[str, ...],
    credential_names: tuple[str, ...],
) -> dict[str, str]:
    forbidden_names = {
        *credential_names,
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_AUTH_TOKEN",
        "CLAUDE_CODE_OAUTH_TOKEN",
        "CODEX_API_KEY",
    }
    result: dict[str, str] = {}
    for name, value in source.items():
        if name in forbidden_names or any(
            credential and credential in value for credential in provider_credentials
        ):
            continue
        result[name] = value
    return result


def _assert_client_environment_isolated(
    environment: Mapping[str, str],
    *,
    provider_credentials: Sequence[str],
    credential_names: Sequence[str],
) -> None:
    if any(name in environment for name in credential_names) or any(
        credential and credential in value
        for value in environment.values()
        for credential in provider_credentials
    ):
        raise LiveClientConformanceError("provider_credential_entered_client_environment")


def _client_failure_code(client: str, output: str) -> str:
    """Reduce potentially content-bearing client output to one fixed code."""

    prefix = client.replace("-", "_")
    lowered = output.lower()
    if "429" in lowered or "rate limit" in lowered or "rate_limit" in lowered:
        suffix = "provider_rate_limited"
    elif "401" in lowered or "invalid_api_key" in lowered or "authentication failed" in lowered:
        suffix = "provider_authentication_failed"
    elif "403" in lowered or "permission denied" in lowered:
        suffix = "provider_forbidden"
    elif "model" in lowered and any(
        fragment in lowered for fragment in ("not found", "does not exist", "unsupported", "unknown")
    ):
        suffix = "model_unavailable"
    elif any(fragment in lowered for fragment in ("connection refused", "failed to connect", "connection error")):
        suffix = "gateway_connection_failed"
    else:
        suffix = "request_failed"
    return f"{prefix}_{suffix}"


def _provider_result(gateway: GatewayServer, spec: _ClientSpec) -> dict[str, object]:
    events = gateway.store.audit_events(since=_SINCE_ALL_EVENTS, organization_id=_ORGANIZATION_ID)
    try:
        for event in events:
            validate_audit_event(event)
    except Exception:
        raise LiveClientConformanceError("audit_event_contract_invalid") from None
    usage = [
        event
        for event in events
        if event.get("event_type") == "usage"
        and event.get("client") == spec.client
        and event.get("actor_id") == spec.actor_id
    ]
    security = [
        event
        for event in events
        if event.get("event_type") == "security.secret"
        and event.get("client") == spec.client
        and event.get("actor_id") == spec.actor_id
    ]
    if not usage or any(event.get("status") != "succeeded" for event in usage):
        raise LiveClientConformanceError(f"{spec.client.replace('-', '_')}_usage_evidence_invalid")
    if not security or any(event.get("action") != "redacted" for event in security):
        raise LiveClientConformanceError(f"{spec.client.replace('-', '_')}_security_evidence_invalid")
    if any(
        event.get("organization_id") != _ORGANIZATION_ID
        or event.get("team_id") != _TEAM_ID
        or event.get("authentication_source") != "static"
        or event.get("requested_model") != spec.model
        or event.get("routed_model") != spec.model
        or event.get("coverage") != COVERAGE_GATEWAY_CAPTURED_REQUESTS_ONLY
        or event.get("cost_basis") != COST_BASIS_CONFIGURED_RATE_CARD_ESTIMATE
        or event.get("allocation_basis") != ALLOCATION_BASIS_DIRECT_GATEWAY_REQUEST
        for event in usage
    ):
        raise LiveClientConformanceError(f"{spec.client.replace('-', '_')}_usage_evidence_invalid")
    provider_models = {event.get("provider_reported_model") for event in usage}
    policy_versions = {event.get("policy_version") for event in usage}
    policy_actions = {event.get("policy_action") for event in usage}
    if len(provider_models) != 1 or not all(isinstance(value, str) and value for value in provider_models):
        raise LiveClientConformanceError(f"{spec.client.replace('-', '_')}_provider_model_evidence_invalid")
    if len(policy_versions) != 1 or not all(isinstance(value, str) and value for value in policy_versions):
        raise LiveClientConformanceError(f"{spec.client.replace('-', '_')}_policy_version_evidence_invalid")
    # A client that omits an output limit receives the configured limit before
    # egress, but the stable policy action is not "capped" because Hormuz did
    # not reduce a larger caller-supplied value. The pre-egress observation
    # below is the authoritative proof that the limit was applied.
    if not any(isinstance(action, str) and "redacted" in action for action in policy_actions):
        raise LiveClientConformanceError(f"{spec.client.replace('-', '_')}_policy_evidence_invalid")

    observations = [
        observation
        for observation in getattr(gateway, "_live_conformance_observations", [])
        if observation.protocol == spec.protocol and observation.client == spec.client
    ]
    matching_observations = [
        observation
        for observation in observations
        if observation.model == spec.model
        and observation.streaming
        and observation.output_limit == _OUTPUT_TOKEN_CAP
        and observation.secret_absent
        and observation.redaction_marker_present
    ]
    if not matching_observations:
        raise LiveClientConformanceError(f"{spec.client.replace('-', '_')}_pre_egress_evidence_invalid")

    def total(name: str) -> int:
        values = [event.get(name) for event in usage]
        if any(isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in values):
            raise LiveClientConformanceError(f"{spec.client.replace('-', '_')}_usage_evidence_invalid")
        return sum(values)  # type: ignore[arg-type]

    input_tokens = total("input_tokens")
    output_tokens = total("output_tokens")
    cost_microusd = total("cost_microusd")
    redaction_count = total("redaction_count")
    if input_tokens <= 0 or output_tokens <= 0 or cost_microusd <= 0 or redaction_count <= 0:
        raise LiveClientConformanceError(f"{spec.client.replace('-', '_')}_usage_evidence_invalid")

    return {
        "provider": spec.provider,
        "client": spec.client,
        "client_version": spec.expected_version,
        "client_entrypoint_sha256": _sha256_file(spec.executable),
        "client_runtime_sha256": _sha256_file(spec.runtime),
        "requested_model": spec.model,
        "routed_model": spec.model,
        "provider_reported_model": next(iter(provider_models)),
        "policy_version": next(iter(policy_versions)),
        "policy_action": sorted(str(value) for value in policy_actions),
        "status": "succeeded",
        "request_count": len(usage),
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cache_read_tokens": total("cache_read_tokens"),
        "cache_write_tokens": total("cache_write_tokens"),
        "reasoning_tokens": total("reasoning_tokens"),
        "cost_microusd": cost_microusd,
        "cost_basis": COST_BASIS_CONFIGURED_RATE_CARD_ESTIMATE,
        "allocation_basis": ALLOCATION_BASIS_DIRECT_GATEWAY_REQUEST,
        "coverage": COVERAGE_GATEWAY_CAPTURED_REQUESTS_ONLY,
        "redaction_count": redaction_count,
        "secret_event_count": len(security),
        "provider_request_id_present": all(bool(event.get("provider_request_id")) for event in usage),
        "streaming_request_observed": True,
        "output_cap_observed": True,
        "redaction_before_egress_observed": True,
        "provider_credential_in_client_environment": False,
        "organization_id": _ORGANIZATION_ID,
        "actor_id": spec.actor_id,
        "team_id": _TEAM_ID,
        "authentication_source": "static",
    }


def _gateway_config(specs: Sequence[_ClientSpec]) -> dict[str, object]:
    routes: dict[str, dict[str, object]] = {}
    for spec in specs:
        routes[spec.model] = {
            "protocol": spec.protocol,
            "upstream_model": spec.model,
            # Fixed conformance rates prove accounting without claiming current
            # provider pricing or invoiced cost.
            "input_cost_per_million": 1,
            "cache_read_cost_per_million": 0.1,
            "cache_write_cost_per_million": 1.25,
            "output_cost_per_million": 2,
        }
    identities = [
        {
            "token_env": spec.identity_env,
            "actor_id": spec.actor_id,
            "actor_name": "Live Client Reviewer",
            "team_id": _TEAM_ID,
            "team_name": _TEAM_NAME,
            "organization_id": _ORGANIZATION_ID,
            "identity_type": "human",
            "clearance": "internal",
            "allowed_clients": [spec.client],
        }
        for spec in specs
    ]
    allowed_clients = [spec.client for spec in specs]
    allowed_models = [spec.model for spec in specs]
    return {
        "listen": {"host": "127.0.0.1", "port": 8787},
        "database": "./usage.sqlite3",
        "max_request_bytes": 4 * 1024 * 1024,
        "upstream_timeout_seconds": 180,
        "upstreams": {
            "openai": {
                "base_url": "https://api.openai.com/v1",
                "api_key_env": "HORMUZ_LIVE_RUNTIME_OPENAI_CREDENTIAL",
                "allow_response_storage": False,
                "allow_background": False,
            },
            "anthropic": {
                "base_url": "https://api.anthropic.com",
                "api_key_env": "HORMUZ_LIVE_RUNTIME_ANTHROPIC_CREDENTIAL",
            },
        },
        "identities": identities,
        "model_routes": routes,
        "egress_controls": {
            "secrets": {"mode": "redact", "builtins": True, "custom_secret_envs": []},
        },
        "policies": {
            "organization": {
                "allowed_clients": allowed_clients,
                "allowed_models": allowed_models,
                "max_output_tokens": _OUTPUT_TOKEN_CAP,
            },
            "teams": {
                _TEAM_ID: {
                    "allowed_models": allowed_models,
                    "max_output_tokens": _OUTPUT_TOKEN_CAP,
                }
            },
            "actors": {},
        },
    }


def validate_evidence(value: object) -> None:
    if not isinstance(value, dict) or set(value) != _ROOT_FIELDS:
        raise LiveClientConformanceError("evidence_schema_invalid")
    if value.get("schema_id") != SCHEMA_ID or value.get("schema_version") != SCHEMA_VERSION:
        raise LiveClientConformanceError("evidence_schema_invalid")
    if value.get("scope") not in {"partial", "complete"}:
        raise LiveClientConformanceError("evidence_schema_invalid")
    if value.get("credential_scope") != "operator_attested_dedicated_least_privilege":
        raise LiveClientConformanceError("evidence_schema_invalid")
    if value.get("checks") != list(_CHECKS) or value.get("nonclaims") != list(_NONCLAIMS):
        raise LiveClientConformanceError("evidence_schema_invalid")
    if not _timestamp(value.get("generated_at")) or not _revision(value.get("source_revision")):
        raise LiveClientConformanceError("evidence_schema_invalid")
    runner = value.get("runner")
    if (
        not isinstance(runner, dict)
        or set(runner) != _RUNNER_FIELDS
        or not all(_safe_text(runner.get(name), 64) for name in _RUNNER_FIELDS)
    ):
        raise LiveClientConformanceError("evidence_schema_invalid")
    results = value.get("results")
    if not isinstance(results, list) or not 1 <= len(results) <= 2:
        raise LiveClientConformanceError("evidence_schema_invalid")
    providers: set[str] = set()
    for result in results:
        _validate_result(result)
        assert isinstance(result, dict)
        provider = result["provider"]
        if provider in providers:
            raise LiveClientConformanceError("evidence_schema_invalid")
        providers.add(provider)  # type: ignore[arg-type]
    if (value["scope"] == "complete") != (providers == {"openai", "anthropic"}):
        raise LiveClientConformanceError("evidence_schema_invalid")


def _validate_result(value: object) -> None:
    if not isinstance(value, dict) or set(value) != _RESULT_FIELDS:
        raise LiveClientConformanceError("evidence_schema_invalid")
    provider = value.get("provider")
    client = value.get("client")
    if (provider, client) not in {("openai", "codex"), ("anthropic", "claude-code")}:
        raise LiveClientConformanceError("evidence_schema_invalid")
    for name in (
        "client_version",
        "requested_model",
        "routed_model",
        "provider_reported_model",
        "policy_version",
        "organization_id",
        "actor_id",
        "team_id",
        "authentication_source",
    ):
        if not _safe_text(value.get(name), 256):
            raise LiveClientConformanceError("evidence_schema_invalid")
    for name in ("client_entrypoint_sha256", "client_runtime_sha256"):
        digest = value.get(name)
        if not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
            raise LiveClientConformanceError("evidence_schema_invalid")
    actions = value.get("policy_action")
    if (
        not isinstance(actions, list)
        or not actions
        or any(not _safe_text(item, 128) for item in actions)
        or actions != sorted(set(actions))
        or not any("redacted" in item for item in actions)
    ):
        raise LiveClientConformanceError("evidence_schema_invalid")
    if value.get("status") != "succeeded":
        raise LiveClientConformanceError("evidence_schema_invalid")
    for name in (
        "request_count",
        "input_tokens",
        "output_tokens",
        "cache_read_tokens",
        "cache_write_tokens",
        "reasoning_tokens",
        "cost_microusd",
        "redaction_count",
        "secret_event_count",
    ):
        item = value.get(name)
        if isinstance(item, bool) or not isinstance(item, int) or item < 0:
            raise LiveClientConformanceError("evidence_schema_invalid")
    if any(value.get(name) <= 0 for name in ("request_count", "input_tokens", "output_tokens", "cost_microusd", "redaction_count", "secret_event_count")):
        raise LiveClientConformanceError("evidence_schema_invalid")
    if value.get("cost_basis") != COST_BASIS_CONFIGURED_RATE_CARD_ESTIMATE:
        raise LiveClientConformanceError("evidence_schema_invalid")
    if value.get("allocation_basis") != ALLOCATION_BASIS_DIRECT_GATEWAY_REQUEST:
        raise LiveClientConformanceError("evidence_schema_invalid")
    if value.get("coverage") != COVERAGE_GATEWAY_CAPTURED_REQUESTS_ONLY:
        raise LiveClientConformanceError("evidence_schema_invalid")
    required_true = (
        "provider_request_id_present",
        "streaming_request_observed",
        "output_cap_observed",
        "redaction_before_egress_observed",
    )
    if any(value.get(name) is not True for name in required_true):
        raise LiveClientConformanceError("evidence_schema_invalid")
    if value.get("provider_credential_in_client_environment") is not False:
        raise LiveClientConformanceError("evidence_schema_invalid")


def _assert_content_free(value: object, *, forbidden_values: Sequence[str]) -> None:
    serialized = json.dumps(value, sort_keys=True, separators=(",", ":"))
    if any(forbidden and forbidden in serialized for forbidden in forbidden_values):
        raise LiveClientConformanceError("content_entered_evidence")
    lowered = serialized.lower()
    for fragment in (
        "-----begin private key-----",
        '"prompt":',
        '"response":',
        '"provider_request_id":',
        '"credential_value":',
    ):
        if fragment in lowered:
            raise LiveClientConformanceError("content_entered_evidence")


def _read_credential_env_file(path: Path) -> dict[str, str]:
    descriptor: int | None = None
    try:
        no_follow = getattr(os, "O_NOFOLLOW", 0)
        if not no_follow and path.is_symlink():
            raise LiveClientConformanceError("credential_file_unsafe")
        descriptor = os.open(path, os.O_RDONLY | no_follow)
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_mode & 0o077:
            raise LiveClientConformanceError("credential_file_unsafe")
        with os.fdopen(descriptor, "rb") as source:
            descriptor = None
            encoded = source.read(_MAX_CREDENTIAL_BYTES + 1)
        raw = encoded.decode("utf-8")
    except LiveClientConformanceError:
        raise
    except (OSError, UnicodeError):
        raise LiveClientConformanceError("credential_file_unavailable") from None
    finally:
        if descriptor is not None:
            os.close(descriptor)
    if len(encoded) > _MAX_CREDENTIAL_BYTES:
        raise LiveClientConformanceError("credential_file_invalid")
    values: dict[str, str] = {}
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        name, separator, value = stripped.partition("=")
        if not separator or not _ENV_NAME_RE.fullmatch(name):
            raise LiveClientConformanceError("credential_file_invalid")
        if value.startswith(('"', "'")):
            if len(value) < 2 or value[-1] != value[0]:
                raise LiveClientConformanceError("credential_file_invalid")
            value = value[1:-1]
        if name in values or not value or "\x00" in value or "\r" in value or "\n" in value:
            raise LiveClientConformanceError("credential_file_invalid")
        values[name] = value
    return values


def _credential(value: object, provider: str) -> str:
    if not isinstance(value, str):
        raise LiveClientConformanceError(f"{provider}_credential_missing")
    size = len(value.encode("utf-8"))
    if not 8 <= size <= _MAX_CREDENTIAL_BYTES or any(character in value for character in "\r\n\x00"):
        raise LiveClientConformanceError(f"{provider}_credential_missing")
    return value


def _environment_name(value: object) -> str:
    if not isinstance(value, str) or _ENV_NAME_RE.fullmatch(value) is None:
        raise LiveClientConformanceError("credential_environment_name_invalid")
    return value


def _model(value: object, code: str) -> str:
    if not isinstance(value, str) or _MODEL_RE.fullmatch(value) is None:
        raise LiveClientConformanceError(code)
    return value


def _version(value: object) -> str:
    if not isinstance(value, str) or re.fullmatch(r"\d+\.\d+\.\d+", value) is None:
        raise LiveClientConformanceError("client_version_invalid")
    return value


def _client_executable(value: str, code: str) -> Path:
    candidate = shutil.which(value)
    if candidate is None:
        raise LiveClientConformanceError(code)
    path = Path(candidate).resolve()
    if not path.is_file() or not os.access(path, os.X_OK):
        raise LiveClientConformanceError(code)
    return path


def _client_runtime(executable: Path, client: str) -> Path:
    if client == "claude-code":
        return executable
    node_modules = next((parent for parent in executable.parents if parent.name == "node_modules"), None)
    if node_modules is None:
        raise LiveClientConformanceError("codex_runtime_not_found")
    candidates = sorted(
        candidate.resolve()
        for candidate in node_modules.glob("@openai/codex-*/vendor/**/codex")
        if candidate.is_file() and os.access(candidate, os.X_OK)
    )
    if len(candidates) != 1:
        raise LiveClientConformanceError("codex_runtime_not_found")
    return candidates[0]


def _client_version(executable: Path) -> str:
    try:
        completed = subprocess.run(
            [str(executable), "--version"],
            text=True,
            capture_output=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        raise LiveClientConformanceError("client_version_unavailable") from None
    if completed.returncode != 0:
        raise LiveClientConformanceError("client_version_unavailable")
    match = _VERSION_RE.search(completed.stdout + " " + completed.stderr)
    if match is None:
        raise LiveClientConformanceError("client_version_unavailable")
    return match.group(1)


def _source_revision(value: object) -> str:
    expected = value if value is not None else os.environ.get("GITHUB_SHA")
    try:
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            text=True,
            capture_output=True,
            timeout=10,
            check=False,
        )
        status = subprocess.run(
            ["git", "status", "--porcelain=v1", "--untracked-files=all"],
            text=True,
            capture_output=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        raise LiveClientConformanceError("source_revision_invalid") from None
    observed = head.stdout.strip() if head.returncode == 0 else ""
    if _REVISION_RE.fullmatch(observed) is None:
        raise LiveClientConformanceError("source_revision_invalid")
    if expected is not None and (not isinstance(expected, str) or expected != observed):
        raise LiveClientConformanceError("source_revision_mismatch")
    if status.returncode != 0:
        raise LiveClientConformanceError("source_revision_invalid")
    if status.stdout:
        raise LiveClientConformanceError("source_worktree_dirty")
    return observed


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as source:
            for block in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(block)
    except OSError:
        raise LiveClientConformanceError("client_binary_unreadable") from None
    return digest.hexdigest()


def _write_private_json(path: Path, value: object) -> None:
    temporary: Path | None = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        _require_evidence_path_available(path)
        temporary = path.with_name(f".{path.name}.{secrets.token_hex(8)}.tmp")
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as destination:
                json.dump(value, destination, indent=2, sort_keys=True)
                destination.write("\n")
                destination.flush()
                os.fsync(destination.fileno())
        except Exception:
            temporary.unlink(missing_ok=True)
            raise
        # A hard-link publication is atomic and fails if the target appeared
        # after the initial check. Unlike os.replace(), it cannot silently
        # overwrite evidence from an earlier or concurrent run.
        os.link(temporary, path, follow_symlinks=False)
        temporary.unlink()
    except LiveClientConformanceError:
        raise
    except OSError:
        raise LiveClientConformanceError("evidence_write_failed") from None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _require_evidence_path_available(path: Path) -> None:
    try:
        if path.is_symlink():
            raise LiveClientConformanceError("evidence_path_unsafe")
        if path.exists():
            raise LiveClientConformanceError("evidence_path_exists")
    except LiveClientConformanceError:
        raise
    except OSError:
        raise LiveClientConformanceError("evidence_path_unsafe") from None


def _timestamp(value: object) -> bool:
    if not isinstance(value, str) or not value.endswith("+00:00"):
        return False
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return False
    return parsed.tzinfo is not None and parsed.utcoffset().total_seconds() == 0


def _revision(value: object) -> bool:
    return isinstance(value, str) and _REVISION_RE.fullmatch(value) is not None


def _safe_text(value: object, maximum: int) -> bool:
    return (
        isinstance(value, str)
        and 0 < len(value) <= maximum
        and all(character not in value for character in "\r\n\x00")
    )


if __name__ == "__main__":
    raise SystemExit(main())
