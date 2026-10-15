"""Read server-owned environment credentials without exporting them to clients."""
import os
from .config import GatewayConfig


def resolve_upstream_credentials(config: GatewayConfig, *, environ=None) -> dict[str, str]:
    environment = os.environ if environ is None else environ
    return {name: environment.get(upstream.api_key_env, "")
            if upstream.authentication != "none" and upstream.api_key_env else ""
            for name, upstream in config.upstreams.items()}
