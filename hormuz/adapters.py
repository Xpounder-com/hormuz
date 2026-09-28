"""Versioned coding-agent adapter contract for the personal optimizer.

Adapters describe transport and launch behavior only.  They never receive an
upstream provider credential; the launcher gives them a short-lived loopback
credential and removes direct-provider selectors from the child environment.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Literal, Protocol, runtime_checkable


ADAPTER_API_VERSION = "1.0"
ProtocolName = Literal["responses", "chat", "anthropic"]


class AdapterError(ValueError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class AgentIdentity:
    key: str
    display_name: str
    executable: str
    version: str


@dataclass(frozen=True)
class AdapterCapabilities:
    protocols: tuple[ProtocolName, ...]
    streaming: bool
    usage: bool
    conversation_boundaries: bool = False
    explicit_execution_steps: bool = False


@dataclass(frozen=True)
class LaunchPlan:
    argv: tuple[str, ...]
    environment: dict[str, str]


@dataclass(frozen=True)
class ExecutionStep:
    """Optional typed point at which an agent yields a bounded operation."""

    step_id: str
    operation: str
    payload: object
    permission: Literal["read", "write", "network"]
    side_effecting: bool


@runtime_checkable
class AgentAdapter(Protocol):
    api_version: str
    identity: AgentIdentity
    capabilities: AdapterCapabilities

    def protocol_for_path(self, path: str) -> ProtocolName | None: ...

    def launch_plan(
        self,
        *,
        executable: str,
        relay_origin: str,
        local_credential: str,
        model: str,
        inherited_values: dict[str, str] | None = None,
    ) -> LaunchPlan: ...

    def conversation_boundary(self, payload: object) -> str | None: ...

    def execution_steps(self, payload: object) -> tuple[ExecutionStep, ...]: ...


class _BaseAdapter:
    api_version = ADAPTER_API_VERSION
    identity: AgentIdentity
    capabilities: AdapterCapabilities
    _paths: dict[str, ProtocolName]
    _blocked_environment: frozenset[str]

    def protocol_for_path(self, path: str) -> ProtocolName | None:
        return self._paths.get(path)

    def conversation_boundary(self, payload: object) -> str | None:
        return None

    def execution_steps(self, payload: object) -> tuple[ExecutionStep, ...]:
        return ()

    def _environment(
        self, inherited_values: dict[str, str] | None
    ) -> dict[str, str]:
        inherited = (
            os.environ.copy()
            if inherited_values is None
            else dict(inherited_values)
        )
        return {
            name: value
            for name, value in inherited.items()
            if name not in self._blocked_environment
        }


class CodexAdapter(_BaseAdapter):
    identity = AgentIdentity("codex", "Codex", "codex", "0.148.0")
    capabilities = AdapterCapabilities(("responses",), True, True)
    _paths = {
        "/v1/responses": "responses",
        "/v1/responses/compact": "responses",
    }
    _blocked_environment = frozenset(
        {
            "OPENAI_API_KEY",
            "OPENAI_API_BASE",
            "OPENAI_BASE_URL",
            "OPENAI_ORGANIZATION",
            "OPENAI_PROJECT",
            "CODEX_API_KEY",
        }
    )

    def launch_plan(
        self,
        *,
        executable: str,
        relay_origin: str,
        local_credential: str,
        model: str,
        inherited_values: dict[str, str] | None = None,
    ) -> LaunchPlan:
        environment = self._environment(inherited_values)
        environment["HORMUZ_LOCAL_RELAY_TOKEN"] = local_credential
        provider = (
            '{name="Hormuz",base_url='
            + json.dumps(relay_origin + "/v1")
            + ',wire_api="responses",requires_openai_auth=false,'
            + 'env_key="HORMUZ_LOCAL_RELAY_TOKEN"}'
        )
        return LaunchPlan(
            (
                executable,
                "-c",
                'model_provider="hormuz_context_relay"',
                "-c",
                "model_providers.hormuz_context_relay=" + provider,
                "-c",
                "model=" + json.dumps(model),
            ),
            environment,
        )


class ClaudeCodeAdapter(_BaseAdapter):
    identity = AgentIdentity("claude-code", "Claude Code", "claude", "2.1.233")
    capabilities = AdapterCapabilities(("anthropic",), True, True)
    _paths = {
        "/v1/messages": "anthropic",
        "/v1/messages/count_tokens": "anthropic",
    }
    _blocked_environment = frozenset(
        {
            "ANTHROPIC_API_KEY",
            "ANTHROPIC_BASE_URL",
            "ANTHROPIC_AUTH_TOKEN",
            "ANTHROPIC_CUSTOM_HEADERS",
            "CLAUDE_CODE_OAUTH_TOKEN",
            "CLAUDE_CODE_USE_BEDROCK",
            "CLAUDE_CODE_USE_VERTEX",
            "CLAUDE_CODE_USE_FOUNDRY",
        }
    )

    def launch_plan(
        self,
        *,
        executable: str,
        relay_origin: str,
        local_credential: str,
        model: str,
        inherited_values: dict[str, str] | None = None,
    ) -> LaunchPlan:
        environment = self._environment(inherited_values)
        environment.update(
            {
                "ANTHROPIC_BASE_URL": relay_origin,
                "ANTHROPIC_API_KEY": "",
                "ANTHROPIC_AUTH_TOKEN": local_credential,
                "ANTHROPIC_MODEL": model,
                "ANTHROPIC_DEFAULT_OPUS_MODEL": model,
                "ANTHROPIC_DEFAULT_SONNET_MODEL": model,
                "ANTHROPIC_DEFAULT_HAIKU_MODEL": model,
            }
        )
        return LaunchPlan((executable, "--model", model), environment)


class AiderAdapter(_BaseAdapter):
    """Aider's documented OpenAI-compatible environment integration.

    Aider 0.86.2 does not expose its local shell transcript as typed tool-result
    messages, so structural compaction is deliberately not claimed.  Requests,
    usage, latency, removal, and zero-benefit reporting remain supported.
    """

    identity = AgentIdentity("aider", "Aider", "aider", "0.86.2")
    capabilities = AdapterCapabilities(("chat",), True, True)
    _paths = {"/v1/chat/completions": "chat"}
    _blocked_environment = frozenset(
        {
            "OPENAI_API_KEY",
            "OPENAI_API_BASE",
            "OPENAI_BASE_URL",
            "OPENAI_ORGANIZATION",
            "OPENAI_PROJECT",
            "OPENAI_API_TYPE",
            "OPENAI_API_VERSION",
            "OPENAI_API_DEPLOYMENT_ID",
            "AIDER_OPENAI_API_KEY",
            "AIDER_OPENAI_API_BASE",
            "AIDER_OPENAI_ORGANIZATION_ID",
            "AIDER_OPENAI_API_TYPE",
            "AIDER_OPENAI_API_VERSION",
            "AIDER_OPENAI_API_DEPLOYMENT_ID",
            "AIDER_SET_ENV",
            "AIDER_API_KEY",
        }
    )

    def launch_plan(
        self,
        *,
        executable: str,
        relay_origin: str,
        local_credential: str,
        model: str,
        inherited_values: dict[str, str] | None = None,
    ) -> LaunchPlan:
        environment = self._environment(inherited_values)
        base = relay_origin + "/v1"
        routed_model = model if model.startswith("openai/") else "openai/" + model
        environment.update(
            {
                "OPENAI_API_KEY": local_credential,
                "OPENAI_API_BASE": base,
                "AIDER_OPENAI_API_KEY": local_credential,
                "AIDER_OPENAI_API_BASE": base,
                "AIDER_ANALYTICS": "false",
                "AIDER_CHECK_UPDATE": "false",
            }
        )
        return LaunchPlan(
            (
                executable,
                "--model",
                routed_model,
                "--openai-api-base",
                base,
                "--weak-model",
                routed_model,
                "--editor-model",
                routed_model,
                "--no-show-model-warnings",
                "--no-analytics",
                "--no-check-update",
            ),
            environment,
        )


_ADAPTERS: dict[str, AgentAdapter] = {
    adapter.identity.key: adapter
    for adapter in (CodexAdapter(), ClaudeCodeAdapter(), AiderAdapter())
}


def adapter_for(key: str) -> AgentAdapter:
    adapter = _ADAPTERS.get(key)
    if adapter is None:
        raise AdapterError("unsupported_agent")
    return adapter


def adapter_catalog() -> tuple[AgentIdentity, ...]:
    return tuple(adapter.identity for adapter in _ADAPTERS.values())


def client_launch_values(
    adapter: AgentAdapter,
    inherited_values: dict[str, str] | None = None,
) -> dict[str, str]:
    """Return inherited values scrubbed only for the selected adapter."""

    if not isinstance(adapter, _BaseAdapter):
        raise AdapterError("adapter_environment_boundary_unavailable")
    return adapter._environment(inherited_values)


def sanitized_client_environment(
    adapter: AgentAdapter,
    inherited_values: dict[str, str] | None = None,
) -> dict[str, str]:
    """Return a broadly provider-sanitized environment for client probing.

    Version discovery executes the selected client before its launch plan is
    assembled, so an executable selected from ``PATH`` must not receive any
    known provider credential.  The actual agent launch uses the selected
    adapter's narrower boundary so unrelated tool integrations keep working.
    Runtime adapters are selected from the built-in registry; refusing an
    unknown implementation keeps this helper from guessing at secret selectors.
    """

    if not isinstance(adapter, _BaseAdapter):
        raise AdapterError("adapter_environment_boundary_unavailable")
    inherited = adapter._environment(inherited_values)
    blocked = frozenset(
        name
        for candidate in _ADAPTERS.values()
        if isinstance(candidate, _BaseAdapter)
        for name in candidate._blocked_environment
    )
    return {name: value for name, value in inherited.items() if name not in blocked}


def conformance_report(adapter: AgentAdapter) -> dict[str, object]:
    """Run the credential/route portion of the public adapter conformance kit."""

    if adapter.api_version != ADAPTER_API_VERSION:
        raise AdapterError("adapter_version_unsupported")
    local = "hox_l_" + "a" * 43
    direct_names: list[str] = []
    if any(protocol in {"responses", "chat"} for protocol in adapter.capabilities.protocols):
        direct_names.extend(
            (
                "OPENAI_API_KEY",
                "OPENAI_API_BASE",
                "OPENAI_BASE_URL",
                "OPENAI_ORGANIZATION",
                "OPENAI_PROJECT",
            )
        )
        if adapter.identity.key == "codex":
            direct_names.append("CODEX_API_KEY")
        if adapter.identity.key == "aider":
            direct_names.extend(("AIDER_OPENAI_API_KEY", "AIDER_OPENAI_API_BASE"))
    if "anthropic" in adapter.capabilities.protocols:
        direct_names.extend(
            (
                "ANTHROPIC_API_KEY",
                "ANTHROPIC_BASE_URL",
                "ANTHROPIC_AUTH_TOKEN",
                "CLAUDE_CODE_OAUTH_TOKEN",
            )
        )
    inherited = {
        name: "must-not-survive-" + name.lower() for name in direct_names
    }
    inherited["TOOL_INTEGRATION_TOKEN"] = "preserved"
    try:
        plan = adapter.launch_plan(
            executable="/qualified/agent",
            relay_origin="http://127.0.0.1:18495",
            local_credential=local,
            model="qualified-model",
            inherited_values=inherited,
        )
        boundary = adapter.conversation_boundary({"synthetic": True})
        steps = adapter.execution_steps({"synthetic": True})
    except Exception:
        return {
            "schema_id": "hormuz.personal-adapter-conformance",
            "schema_version": 1,
            "adapter_api_version": ADAPTER_API_VERSION,
            "agent": adapter.identity.key,
            "passed": False,
            "checks": {"adapter_calls_succeeded": False},
        }
    serialized_argv = "\0".join(plan.argv)
    direct_secrets_absent = (
        not any(value in plan.environment.values() for value in inherited.values() if value != "preserved")
        and not any(value in serialized_argv for value in inherited.values() if value != "preserved")
    )
    local_secret_not_in_argv = local not in serialized_argv
    local_secret_in_environment = local in plan.environment.values()
    preserved_tool_environment = plan.environment.get("TOOL_INTEGRATION_TOKEN") == "preserved"
    declared_paths = (
        "/v1/responses",
        "/v1/responses/compact",
        "/v1/messages",
        "/v1/messages/count_tokens",
        "/v1/chat/completions",
    )
    declared_routes = tuple(
        (path, adapter.protocol_for_path(path))
        for path in declared_paths
        if adapter.protocol_for_path(path) is not None
    )
    route_count = len(declared_routes)
    routes_match_capabilities = all(
        protocol in adapter.capabilities.protocols
        for _path, protocol in declared_routes
    )
    optional_surfaces_typed = (
        (boundary is None or isinstance(boundary, str))
        and isinstance(steps, tuple)
        and all(isinstance(step, ExecutionStep) for step in steps)
    )
    capability_contract_typed = (
        isinstance(adapter.capabilities, AdapterCapabilities)
        and isinstance(adapter.capabilities.protocols, tuple)
        and bool(adapter.capabilities.protocols)
        and len(set(adapter.capabilities.protocols))
        == len(adapter.capabilities.protocols)
        and all(
            protocol in {"responses", "chat", "anthropic"}
            for protocol in adapter.capabilities.protocols
        )
        and all(
            type(value) is bool
            for value in (
                adapter.capabilities.streaming,
                adapter.capabilities.usage,
                adapter.capabilities.conversation_boundaries,
                adapter.capabilities.explicit_execution_steps,
            )
        )
    )
    identity_and_launch_typed = (
        isinstance(adapter.identity, AgentIdentity)
        and isinstance(adapter.identity.key, str)
        and re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", adapter.identity.key) is not None
        and isinstance(adapter.identity.executable, str)
        and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+-]{0,127}", adapter.identity.executable)
        is not None
        and isinstance(adapter.identity.version, str)
        and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.+-]{0,63}", adapter.identity.version)
        is not None
        and isinstance(adapter.identity.display_name, str)
        and bool(adapter.identity.display_name.strip())
        and not any(
            character in adapter.identity.display_name for character in ("\x00", "\r", "\n")
        )
        and plan.argv
        and plan.argv[0] == "/qualified/agent"
        and all(isinstance(value, str) for value in plan.argv)
        and all(isinstance(name, str) and isinstance(value, str) for name, value in plan.environment.items())
    )
    unknown_route_rejected = adapter.protocol_for_path("/v1/unreviewed") is None
    passed = all(
        (
            direct_secrets_absent,
            local_secret_not_in_argv,
            local_secret_in_environment,
            preserved_tool_environment,
            bool(route_count),
            routes_match_capabilities,
            optional_surfaces_typed,
            capability_contract_typed,
            identity_and_launch_typed,
            unknown_route_rejected,
        )
    )
    return {
        "schema_id": "hormuz.personal-adapter-conformance",
        "schema_version": 1,
        "adapter_api_version": ADAPTER_API_VERSION,
        "agent": adapter.identity.key,
        "passed": passed,
        "checks": {
            "adapter_calls_succeeded": True,
            "direct_secrets_absent": direct_secrets_absent,
            "local_secret_not_in_argv": local_secret_not_in_argv,
            "local_secret_in_environment": local_secret_in_environment,
            "preserved_tool_environment": preserved_tool_environment,
            "declared_route_count": route_count,
            "routes_match_capabilities": routes_match_capabilities,
            "optional_surfaces_typed": optional_surfaces_typed,
            "capability_contract_typed": capability_contract_typed,
            "identity_and_launch_typed": bool(identity_and_launch_typed),
            "unknown_route_rejected": unknown_route_rejected,
        },
    }


__all__ = [
    "ADAPTER_API_VERSION",
    "AdapterCapabilities",
    "AdapterError",
    "AgentAdapter",
    "AgentIdentity",
    "ExecutionStep",
    "LaunchPlan",
    "ProtocolName",
    "adapter_catalog",
    "adapter_for",
    "conformance_report",
]
