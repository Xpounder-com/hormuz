"""GitHub App webhook authentication for the opt-in outcome connector.

The App secret signs the exact body, not GitHub's routing/delivery headers. A
registered channel therefore derives its durable replay identity from signed
bytes with a separate, retained tenant key. The caller must still decide which
event/action shapes, if any, may become outcome observations.
"""

from __future__ import annotations

import hashlib
import hmac
import re
from types import MappingProxyType
from typing import Mapping

from .config import GatewayConfig
from .outcome_ingest import AuthenticatedDelivery, registered_binding
from .outcome_wire import OutcomeKeys, REQUEST_BYTES, decode_source_body
from .portfolio_config import PortfolioConnectorBinding
from .portfolio_wire import PortfolioError, validate


_SIGNATURE = re.compile(r"sha256=[0-9a-f]{64}\Z")
_NUMERIC_ID = re.compile(r"[1-9][0-9]{0,19}\Z")
_HEADER_NAME = re.compile(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+\Z")


def _signature_header(headers: Mapping[str, str]) -> str:
    if not isinstance(headers, Mapping) or len(headers) > 64:
        raise PortfolioError("invalid_request")
    signature = None
    total = 0
    count = 0
    for name, value in headers.items():
        count += 1
        if (count > 64 or type(name) is not str or type(value) is not str or
                len(name) > 128 or _HEADER_NAME.fullmatch(name) is None or
                len(value) > 2048 or any(ord(char) < 32 and char != "\t" or
                                         ord(char) == 127 for char in value)):
            raise PortfolioError("invalid_request")
        normalized = name.lower()
        try:
            total += len(name) + len(value.encode("utf-8"))
        except UnicodeError:
            raise PortfolioError("invalid_request") from None
        if total > 8192:
            raise PortfolioError("invalid_request")
        if normalized == "x-hub-signature-256":
            if signature is not None:
                raise PortfolioError("invalid_request")
            signature = value
    if signature is None or _SIGNATURE.fullmatch(signature) is None:
        raise PortfolioError("unauthenticated")
    return signature


def _signed_numeric_id(body: dict, field: str) -> str:
    item = body.get(field)
    value = item.get("id") if type(item) is dict else None
    if type(value) is not int or _NUMERIC_ID.fullmatch(str(value)) is None:
        raise PortfolioError("forbidden")
    return str(value)


class GitHubWebhookAuthenticator:
    """Verify one server-enrolled installation/repository channel in memory.

    Construction is explicit and limited to strict runtime enrollment. Webhook
    key rotation may overlap two distinct secrets.
    The independent outcome identity key/version must be retained throughout
    the delivery replay horizon, including webhook-secret rotation.
    """

    def __init__(
        self,
        config: GatewayConfig,
        organization: str,
        connector: str,
        webhook_secrets: Mapping[str, bytes],
        identity_keys: OutcomeKeys,
        identity_version: str,
    ) -> None:
        binding = registered_binding(config, organization, connector)
        if binding.provider != "github" or binding.installation_id is None or binding.workspace_id is not None:
            raise PortfolioError("forbidden")
        # A single signed installation/repository pair cannot grant two
        # connector or tenant identities through independent routing paths.
        repositories = set(binding.external_object_ids)
        for other in config.portfolio_control.connectors:
            if other is binding or other.provider != "github":
                continue
            if (other.installation_id == binding.installation_id and
                    not repositories.isdisjoint(other.external_object_ids)):
                raise PortfolioError("forbidden")
        if not isinstance(webhook_secrets, Mapping) or not 1 <= len(webhook_secrets) <= 2:
            raise PortfolioError("invalid_request")
        secrets: dict[str, bytes] = {}
        for version, secret in webhook_secrets.items():
            validate(version, "opaque_id")
            if type(secret) is not bytes or not 32 <= len(secret) <= 128 or secret in secrets.values():
                raise PortfolioError("invalid_request")
            secrets[version] = secret
        if not isinstance(identity_keys, OutcomeKeys):
            raise PortfolioError("invalid_request")
        validate(identity_version, "opaque_id")
        # A missing retained version cannot silently mint a different identity.
        identity_keys.delivery_digest(identity_version, organization, connector, "github-signed-body-v1", b"probe")
        self._binding: PortfolioConnectorBinding = binding
        self._secrets = MappingProxyType(secrets)
        self._identity_keys = identity_keys
        self._identity_version = identity_version

    def authenticate(self, headers: Mapping[str, str], raw: bytes) -> AuthenticatedDelivery:
        """Authenticate raw bytes, then check signed source IDs; never retain content."""
        if type(raw) is not bytes or not 1 <= len(raw) <= REQUEST_BYTES:
            raise PortfolioError("invalid_request")
        signature = _signature_header(headers)
        matched = None
        for version, secret in self._secrets.items():
            expected = "sha256=" + hmac.new(secret, raw, hashlib.sha256).hexdigest()
            if hmac.compare_digest(signature, expected):
                if matched is not None:
                    raise PortfolioError("unavailable")
                matched = version
        if matched is None:
            raise PortfolioError("unauthenticated")

        body = decode_source_body(raw)
        binding = self._binding
        installation = _signed_numeric_id(body, "installation")
        repository = (
            _signed_numeric_id(body, "repository")
            if "repository" in body
            else None
        )
        if (
            installation != binding.installation_id
            or (repository is not None and repository not in binding.external_object_ids)
        ):
            raise PortfolioError("forbidden")
        delivery = self._identity_keys.delivery_digest(
            self._identity_version, binding.organization_id, binding.connector_id,
            "github-signed-body-v1", raw,
        )
        return AuthenticatedDelivery(
            binding.organization_id, binding.connector_id, binding.provider,
            binding.installation_id, None, delivery, matched,
        )
