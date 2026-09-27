"""Provider-free example of the public Hormuz personal adapter contract."""

from __future__ import annotations

import json

from .adapters import (
    ADAPTER_API_VERSION,
    AdapterCapabilities,
    AgentIdentity,
    LaunchPlan,
    conformance_report,
)


class ExampleAgentAdapter:
    api_version = ADAPTER_API_VERSION
    identity = AgentIdentity("example-agent", "Example Agent", "example-agent", "1.0.0")
    capabilities = AdapterCapabilities(("responses",), True, True)

    def protocol_for_path(self, path: str):
        return "responses" if path == "/v1/responses" else None

    def launch_plan(
        self,
        *,
        executable: str,
        relay_origin: str,
        local_credential: str,
        model: str,
        inherited_values: dict[str, str] | None = None,
    ) -> LaunchPlan:
        values = {} if inherited_values is None else dict(inherited_values)
        for name in (
            "OPENAI_API_KEY",
            "OPENAI_API_BASE",
            "OPENAI_BASE_URL",
            "OPENAI_ORGANIZATION",
            "OPENAI_PROJECT",
            "CODEX_API_KEY",
            "AIDER_OPENAI_API_KEY",
            "AIDER_OPENAI_API_BASE",
        ):
            values.pop(name, None)
        values["EXAMPLE_AGENT_BASE_URL"] = relay_origin + "/v1"
        values["EXAMPLE_AGENT_TOKEN"] = local_credential
        return LaunchPlan((executable, "--model", model), values)

    def conversation_boundary(self, payload: object) -> str | None:
        return None

    def execution_steps(self, payload: object):
        return ()


def main() -> int:
    print(json.dumps(conformance_report(ExampleAgentAdapter()), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
