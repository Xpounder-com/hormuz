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

from .outcome_wire import OutcomeKeys, timestamp
from .portfolio_config import PortfolioConfig
from .portfolio_wire import PortfolioError, validate


_ENVIRONMENT_VARIABLE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,127}\Z")
_PRINTABLE_SECRET = re.compile(rb"[!-~]{32,128}\Z")
_UUID = re.compile(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\Z")
_NUMERIC_VERSION = re.compile(r"[1-9][0-9]{0,9}\Z")


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
class LinearWebhookSecretReference:
    version: str
    environment_variable: str
    expires_at: str | None
    value: bytes | None = field(default=None, repr=False)


@dataclass(frozen=True)
class LinearOutcomeChannelConfig:
    organization_id: str
    connector_id: str
    binding_version: int
    source_webhook_id: str
    source_team_ids: tuple[str, ...]
    typed_enrollment: Mapping[str, tuple[str, ...]]
    active_webhook_secret: LinearWebhookSecretReference
    previous_webhook_secret: LinearWebhookSecretReference | None
    identity_keys: tuple[VersionedSecretReference, ...]
    current_key_version: str
    body_fingerprint_key_version: str
    source_fact_key_version: str
    registered_by: str

    def resolved_webhook_secrets(self) -> Mapping[str, tuple[bytes, str | None]]:
        references = (self.active_webhook_secret,) + (
            (self.previous_webhook_secret,) if self.previous_webhook_secret is not None else ()
        )
        if any(reference.value is None for reference in references):
            raise PortfolioError("unavailable")
        return MappingProxyType({
            reference.version: (reference.value, reference.expires_at)
            for reference in references
            if reference.value is not None
        })

    def resolved_identity_keys(self) -> OutcomeKeys:
        values = {reference.version: reference.value for reference in self.identity_keys}
        if any(value is None for value in values.values()):
            raise PortfolioError("unavailable")
        return OutcomeKeys(
            self.current_key_version,
            {version: value for version, value in values.items() if value is not None},
        )

    @property
    def identity_key_versions(self) -> tuple[str, ...]:
        return tuple(reference.version for reference in self.identity_keys)


@dataclass(frozen=True)
class OutcomeConnectorConfig:
    github: tuple[GitHubOutcomeChannelConfig, ...]
    linear: tuple[LinearOutcomeChannelConfig, ...] = ()

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
        for channel in self.linear:
            for reference in (channel.active_webhook_secret, channel.previous_webhook_secret):
                if reference is not None and reference.value is not None:
                    values.append(("linear_webhook_secret", reference.value.decode("ascii")))
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
    """Validate opt-in runtime channels without resolving any credential."""

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

    def uuid_value(item: object) -> str:
        if not isinstance(item, str) or _UUID.fullmatch(item) is None:
            fail()
        return item

    def numeric_version(item: object) -> str:
        if (
            not isinstance(item, str)
            or _NUMERIC_VERSION.fullmatch(item) is None
            or int(item) > 2147483647
        ):
            fail()
        return item

    if value is None:
        return None
    if not isinstance(value, dict):
        fail()
    version = value.get("schema_version")
    if value.get("schema_id") != "hormuz.outcome-connectors" or version not in (1, 2):
        fail()
    if version == 1:
        root = exact(value, {"schema_id", "schema_version", "github"})
        github_values = bounded_list(root["github"], 1, 8)
        linear_values: list = []
    else:
        root = exact(value, {"schema_id", "schema_version", "github", "linear"})
        github_values = bounded_list(root["github"], 0, 8)
        linear_values = bounded_list(root["linear"], 0, 8)
        if not github_values and not linear_values:
            fail()
    if portfolio is None:
        fail()
    bindings = {
        (binding.organization_id, binding.connector_id): binding
        for binding in portfolio.connectors
    }
    channels: list[GitHubOutcomeChannelConfig] = []
    linear_channels: list[LinearOutcomeChannelConfig] = []
    channel_ids: set[tuple[str, str]] = set()
    environment_variables: set[str] = set()

    def variable(item: object) -> str:
        if (
            not isinstance(item, str)
            or _ENVIRONMENT_VARIABLE.fullmatch(item) is None
            or item in environment_variables
        ):
            fail()
        environment_variables.add(item)
        return item

    for item in github_values:
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

        def github_references(name: str, maximum: int) -> tuple[VersionedSecretReference, ...]:
            result: list[VersionedSecretReference] = []
            versions: set[str] = set()
            for raw in bounded_list(channel[name], 1, maximum):
                reference = exact(raw, {"version", "environment_variable"})
                reference_version = opaque(reference["version"])
                if reference_version in versions:
                    fail()
                versions.add(reference_version)
                result.append(VersionedSecretReference(
                    reference_version,
                    variable(reference["environment_variable"]),
                ))
            return tuple(result)

        webhook_secrets = github_references("webhook_secrets", 2)
        identity_keys = github_references("identity_keys", 8)
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

    workspace_owners: dict[str, str] = {}
    for binding in portfolio.connectors:
        if binding.provider == "linear" and binding.workspace_id is not None:
            owner = workspace_owners.setdefault(binding.workspace_id, binding.organization_id)
            if owner != binding.organization_id:
                fail()
    webhook_ids: set[str] = set()
    for item in linear_values:
        channel = exact(item, {
            "organization_id", "connector_id", "binding_version",
            "source_webhook_id", "source_team_ids", "typed_enrollment",
            "active_webhook_secret", "previous_webhook_secret", "identity_keys",
            "current_key_version", "body_fingerprint_key_version",
            "source_fact_key_version", "registered_by",
        })
        organization = opaque(channel["organization_id"])
        connector = opaque(channel["connector_id"])
        channel_id = (organization, connector)
        binding = bindings.get(channel_id)
        binding_version = channel["binding_version"]
        webhook_id = uuid_value(channel["source_webhook_id"])
        if (
            channel_id in channel_ids
            or binding is None
            or binding.provider != "linear"
            or binding.installation_id is not None
            or binding.workspace_id is None
            or type(binding_version) is not int
            or not 1 <= binding_version <= 2147483647
            or webhook_id in webhook_ids
        ):
            fail()
        channel_ids.add(channel_id)
        webhook_ids.add(webhook_id)
        teams = tuple(sorted(uuid_value(value) for value in bounded_list(
            channel["source_team_ids"], 1, 100,
        )))
        if len(set(teams)) != len(teams):
            fail()
        typed_raw = exact(channel["typed_enrollment"], {
            "initiative_ids", "project_ids", "cycle_ids", "issue_ids",
        })
        typed: dict[str, tuple[str, ...]] = {}
        for kind, field_name in (
            ("initiative", "initiative_ids"),
            ("project", "project_ids"),
            ("cycle", "cycle_ids"),
            ("issue", "issue_ids"),
        ):
            identifiers = tuple(sorted(
                uuid_value(identifier)
                for identifier in bounded_list(typed_raw[field_name], 0, 1000)
            ))
            if len(set(identifiers)) != len(identifiers):
                fail()
            typed[kind] = identifiers
        if set(typed["project"]) != set(binding.external_object_ids):
            fail()

        active_raw = exact(
            channel["active_webhook_secret"],
            {"version", "environment_variable"},
        )
        active = LinearWebhookSecretReference(
            opaque(active_raw["version"]),
            variable(active_raw["environment_variable"]),
            None,
        )
        previous_raw = channel["previous_webhook_secret"]
        previous = None
        if previous_raw is not None:
            previous_value = exact(
                previous_raw,
                {"version", "environment_variable", "expires_at"},
            )
            try:
                expires_at = timestamp(previous_value["expires_at"])
            except (PortfolioError, TypeError):
                fail()
            previous = LinearWebhookSecretReference(
                opaque(previous_value["version"]),
                variable(previous_value["environment_variable"]),
                expires_at,
            )
            if previous.version == active.version:
                fail()

        identity_keys: list[VersionedSecretReference] = []
        identity_versions: set[str] = set()
        for raw in bounded_list(channel["identity_keys"], 1, 8):
            reference = exact(raw, {"version", "environment_variable"})
            reference_version = numeric_version(reference["version"])
            if reference_version in identity_versions:
                fail()
            identity_versions.add(reference_version)
            identity_keys.append(VersionedSecretReference(
                reference_version,
                variable(reference["environment_variable"]),
            ))
        current = numeric_version(channel["current_key_version"])
        body_version = numeric_version(channel["body_fingerprint_key_version"])
        fact_version = numeric_version(channel["source_fact_key_version"])
        if any(item not in identity_versions for item in (current, body_version, fact_version)):
            fail()
        linear_channels.append(LinearOutcomeChannelConfig(
            organization_id=organization,
            connector_id=connector,
            binding_version=binding_version,
            source_webhook_id=webhook_id,
            source_team_ids=teams,
            typed_enrollment=MappingProxyType(typed),
            active_webhook_secret=active,
            previous_webhook_secret=previous,
            identity_keys=tuple(identity_keys),
            current_key_version=current,
            body_fingerprint_key_version=body_version,
            source_fact_key_version=fact_version,
            registered_by=opaque(channel["registered_by"]),
        ))
    return OutcomeConnectorConfig(tuple(channels), tuple(linear_channels))

def resolve_outcome_connector_credentials(
    config: OutcomeConnectorConfig | None,
    environ: Mapping[str, str],
) -> OutcomeConnectorConfig | None:
    """Resolve printable high-entropy values and reject cross-channel reuse."""

    from .config import ConfigError

    if config is None:
        return None
    seen_values: set[bytes] = set()

    def resolve(reference):
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
    linear_channels = tuple(
        replace(
            channel,
            active_webhook_secret=resolve(channel.active_webhook_secret),
            previous_webhook_secret=(
                resolve(channel.previous_webhook_secret)
                if channel.previous_webhook_secret is not None
                else None
            ),
            identity_keys=tuple(resolve(reference) for reference in channel.identity_keys),
        )
        for channel in config.linear
    )
    return OutcomeConnectorConfig(channels, linear_channels)
