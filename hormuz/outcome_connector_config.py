"""Strict runtime enrollment for provider-signed outcome connectors.

The public portfolio registry owns tenant and source bindings.  This module
adds only the process-local credential references needed to activate a signed
provider channel.  Secret values are resolved from the caller-supplied
environment after all non-secret configuration has been validated.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
import re
from types import MappingProxyType
from typing import Mapping

from .outcome_wire import OutcomeKeys
from .portfolio_config import PortfolioConfig
from .portfolio_wire import PortfolioError, validate


_ENVIRONMENT_VARIABLE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,127}\Z")
_PRINTABLE_SECRET = re.compile(rb"[!-~]{32,128}\Z")


@dataclass(frozen=True)
class VersionedSecretReference:
    version: str
    environment_variable: str
    value: bytes | None = field(default=None, repr=False)


@dataclass(frozen=True)
class GitHubOutcomeChannelConfig:
    organization_id: str
    connector_id: str
    webhook_secrets: tuple[VersionedSecretReference, ...]
    identity_keys: tuple[VersionedSecretReference, ...]
    current_key_version: str
    delivery_identity_key_version: str

    def resolved_webhook_secrets(self) -> Mapping[str, bytes]:
        values = {
            reference.version: reference.value
            for reference in self.webhook_secrets
        }
        if any(value is None for value in values.values()):
            raise PortfolioError("unavailable")
        return MappingProxyType({version: value for version, value in values.items() if value is not None})

    def resolved_identity_keys(self) -> OutcomeKeys:
        values = {
            reference.version: reference.value
            for reference in self.identity_keys
        }
        if any(value is None for value in values.values()):
            raise PortfolioError("unavailable")
        return OutcomeKeys(
            self.current_key_version,
            {version: value for version, value in values.items() if value is not None},
        )


@dataclass(frozen=True)
class OutcomeConnectorConfig:
    github: tuple[GitHubOutcomeChannelConfig, ...]

    def protected_values(self) -> tuple[tuple[str, str], ...]:
        values: list[tuple[str, str]] = []
        for channel in self.github:
            values.extend(
                ("github_webhook_secret", reference.value.decode("ascii"))
                for reference in channel.webhook_secrets
                if reference.value is not None
            )
            values.extend(
                ("outcome_identity_key", reference.value.decode("ascii"))
                for reference in channel.identity_keys
                if reference.value is not None
            )
        return tuple(values)


def build_outcome_connector_config(
    value: object,
    portfolio: PortfolioConfig | None,
) -> OutcomeConnectorConfig | None:
    """Validate an opt-in runtime channel without resolving any credential."""

    from .config import ConfigError

    def fail() -> None:
        raise ConfigError("outcome_connector_configuration_invalid")

    def exact(item: object, fields: set[str]) -> dict:
        if not isinstance(item, dict) or set(item) != fields:
            fail()
        return item

    def bounded_list(item: object, minimum: int, maximum: int) -> list:
        if not isinstance(item, list) or not minimum <= len(item) <= maximum:
            fail()
        return item

    def opaque(item: object) -> str:
        try:
            validate(item, "opaque_id")
        except PortfolioError:
            fail()
        assert isinstance(item, str)
        return item

    if value is None:
        return None
    root = exact(value, {"schema_id", "schema_version", "github"})
    if root["schema_id"] != "hormuz.outcome-connectors" or root["schema_version"] != 1:
        fail()
    if portfolio is None:
        fail()
    bindings = {
        (binding.organization_id, binding.connector_id): binding
        for binding in portfolio.connectors
    }
    channels: list[GitHubOutcomeChannelConfig] = []
    channel_ids: set[tuple[str, str]] = set()
    environment_variables: set[str] = set()
    for item in bounded_list(root["github"], 1, 8):
        channel = exact(item, {
            "organization_id", "connector_id", "webhook_secrets",
            "identity_keys", "current_key_version", "delivery_identity_key_version",
        })
        organization = opaque(channel["organization_id"])
        connector = opaque(channel["connector_id"])
        channel_id = (organization, connector)
        binding = bindings.get(channel_id)
        if (
            channel_id in channel_ids
            or binding is None
            or binding.provider != "github"
            or binding.installation_id is None
            or binding.workspace_id is not None
            or not binding.external_object_ids
        ):
            fail()
        channel_ids.add(channel_id)

        def references(name: str, maximum: int) -> tuple[VersionedSecretReference, ...]:
            result: list[VersionedSecretReference] = []
            versions: set[str] = set()
            for raw in bounded_list(channel[name], 1, maximum):
                reference = exact(raw, {"version", "environment_variable"})
                version = opaque(reference["version"])
                variable = reference["environment_variable"]
                if (
                    version in versions
                    or not isinstance(variable, str)
                    or _ENVIRONMENT_VARIABLE.fullmatch(variable) is None
                    or variable in environment_variables
                ):
                    fail()
                versions.add(version)
                environment_variables.add(variable)
                result.append(VersionedSecretReference(version, variable))
            return tuple(result)

        webhook_secrets = references("webhook_secrets", 2)
        identity_keys = references("identity_keys", 8)
        current = opaque(channel["current_key_version"])
        delivery_identity = opaque(channel["delivery_identity_key_version"])
        versions = {reference.version for reference in identity_keys}
        if current not in versions or delivery_identity not in versions:
            fail()
        channels.append(GitHubOutcomeChannelConfig(
            organization,
            connector,
            webhook_secrets,
            identity_keys,
            current,
            delivery_identity,
        ))
    return OutcomeConnectorConfig(tuple(channels))


def resolve_outcome_connector_credentials(
    config: OutcomeConnectorConfig | None,
    environ: Mapping[str, str],
) -> OutcomeConnectorConfig | None:
    """Resolve printable high-entropy values and reject cross-channel reuse."""

    from .config import ConfigError

    if config is None:
        return None
    seen_values: set[bytes] = set()

    def resolve(reference: VersionedSecretReference) -> VersionedSecretReference:
        value = environ.get(reference.environment_variable)
        try:
            encoded = value.encode("ascii") if isinstance(value, str) else b""
        except UnicodeError:
            encoded = b""
        if _PRINTABLE_SECRET.fullmatch(encoded) is None or encoded in seen_values:
            raise ConfigError("outcome_connector_credentials_unavailable")
        seen_values.add(encoded)
        return replace(reference, value=encoded)

    channels = tuple(
        replace(
            channel,
            webhook_secrets=tuple(resolve(reference) for reference in channel.webhook_secrets),
            identity_keys=tuple(resolve(reference) for reference in channel.identity_keys),
        )
        for channel in config.github
    )
    return OutcomeConnectorConfig(channels)
