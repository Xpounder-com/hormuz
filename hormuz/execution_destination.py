"""One immutable owner of destination transport, authentication and identity."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
import hashlib
import json
import secrets
from types import MappingProxyType

from .config import ModelRoute, UpstreamConfig


@dataclass(frozen=True)
class ExecutionDestination:
    name: str
    protocol: str
    upstream: UpstreamConfig
    model: str
    generation: str
    credential: str = field(repr=False)
    model_revision: str | None = None
    capabilities: tuple[str, ...] = ()

    @property
    def locality(self) -> str:
        return self.upstream.locality


class ExecutionDestinations:
    """Capture a startup generation; callers resolve an alias as one choice.

    Endpoint, model and credential cannot be independently recombined. There
    is no reload: changed facts need a new router process and a new generation.
    """

    def __init__(self, upstreams: Mapping[str, UpstreamConfig], routes: Mapping[str, ModelRoute],
                 credentials: Mapping[str, str]):
        resolved = {}
        captured_routes = dict(routes)
        for alias, route in routes.items():
            name = route.destination_id
            upstream = upstreams[name]
            credential = credentials.get(name, "")
            # Opaque, never a public verifier derived from credential bytes.
            # A restarted router cannot silently inherit a credential generation.
            generation = secrets.token_hex(32)
            resolved[alias] = ExecutionDestination(name, route.protocol, upstream,
                                                  route.upstream_model, generation, credential,
                                                  route.model_revision, route.capabilities)
        self._resolved = MappingProxyType(resolved)
        self._routes = MappingProxyType(captured_routes)

    def resolve(self, route: ModelRoute) -> ExecutionDestination:
        if route != self._routes[route.alias]:
            raise ValueError("execution_destination_generation_changed")
        return self._resolved[route.alias]

    def response_headers(self, route: ModelRoute) -> dict[str, str]:
        if not self.uses_metadata(route):
            return {}
        destination = self.resolve(route)
        return {"X-Hormuz-Destination": destination.name,
                "X-Hormuz-Destination-Locality": destination.locality,
                "X-Hormuz-Destination-Generation": destination.generation}

    def uses_metadata(self, route: ModelRoute) -> bool:
        destination = self.resolve(route)
        return bool(route.destination is not None or route.model_revision is not None or route.capabilities
                    or destination.name not in {"openai", "anthropic"} or destination.locality != "cloud"
                    or destination.upstream.authentication != "api_key")

    @property
    def metadata_enabled(self) -> bool:
        return any(self.uses_metadata(route) for route in self._routes.values())

    def contract_header(self, route: ModelRoute) -> str:
        from .contracts import relay_contract_header
        return relay_contract_header(destination_metadata=self.uses_metadata(route))

    def describe(self, route: ModelRoute) -> dict[str, object]:
        destination = self.resolve(route)
        return {"destination": destination.name, "protocol": destination.protocol,
                "locality": destination.locality, "authentication": destination.upstream.authentication,
                "generation": destination.generation, "upstream_model": destination.model,
                "model_revision": destination.model_revision, "capabilities": list(destination.capabilities),
                "credential_configured": bool(destination.credential),
                "eligible": destination.upstream.authentication == "none" or bool(destination.credential)}


def route_fingerprint(route: ModelRoute, *, upstreams, destinations=None) -> str:
    """Bind observational evidence to the same choice as forwarding.

    Standalone, credential-free analysis remains distinct from a resolved live
    generation. Live evidence is conservatively invalidated on router restart.
    """
    upstream = (destinations.resolve(route).upstream if destinations else upstreams.get(route.destination_id))
    facts = [route.alias, route.protocol, route.destination_id, route.upstream_model,
             route.model_revision, route.capabilities,
             route.input_cost_per_million, route.cache_read_cost_per_million,
             route.cache_write_cost_per_million, route.output_cost_per_million,
             getattr(upstream, "base_url", None), getattr(upstream, "locality", None),
             getattr(upstream, "authentication", None),
             destinations.resolve(route).generation if destinations else "unresolved-configuration"]
    digest = hashlib.sha256(json.dumps(facts, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
    return "destination-v1:" + digest


def configured_response_permissions(upstreams, routes, allowed_models) -> dict[str, bool]:
    """Local preflight permits only controls supported by an allowed route.

    Final dispatch intersects these permissions with its resolved destination;
    managed organization policy continues to supply its own preflight limits.
    """
    eligible = [upstreams[route.destination_id] for alias, route in routes.items()
                if route.protocol == "openai" and (allowed_models is None or alias in allowed_models)]
    return {"allow_response_storage": any(upstream.allow_response_storage for upstream in eligible),
            "allow_background": any(upstream.allow_background for upstream in eligible)}
