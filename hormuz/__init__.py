"""Hormuz enterprise AI gateway policy and control plane."""

from __future__ import annotations

from importlib import import_module
from typing import Any


__version__ = "1.2.0"

__all__ = [
    "__version__",
    "ConfigError",
    "GatewayConfig",
    "GatewayServer",
    "PolicyDecision",
    "PolicyEngine",
]

_LAZY_EXPORTS = {
    "ConfigError": ("config", "ConfigError"),
    "GatewayConfig": ("config", "GatewayConfig"),
    "GatewayServer": ("server", "GatewayServer"),
    "PolicyDecision": ("policy", "PolicyDecision"),
    "PolicyEngine": ("policy", "PolicyEngine"),
}


def __getattr__(name: str) -> Any:
    """Keep the public API lazy so client-only helpers do not load the gateway."""
    target = _LAZY_EXPORTS.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attribute = target
    value = getattr(import_module(f"{__name__}.{module_name}"), attribute)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
