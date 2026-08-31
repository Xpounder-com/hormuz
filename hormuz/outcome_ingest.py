"""Internal verified-delivery boundary; no generic or provider ingress route.

Only server-registered adapters may implement the verifier/normalizer protocol.
An AuthenticatedDelivery instance is metadata, not a credential or a substitute
for verification. Every call invokes the adapter before parsing or storage.
Real provider verification and transport activation remain #219/#220.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import threading
import time
from typing import Mapping, Protocol

from .config import GatewayConfig
from .outcome_wire import EVENTS_PER_DELIVERY, REQUEST_BYTES, OutcomeKeys, decode_source_body, observation_from_mapping, source_id
from .portfolio_config import PortfolioConnectorBinding
from .portfolio_wire import PortfolioError, validate


_INGEST_SLOTS = threading.BoundedSemaphore(8)


def observed_time() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


@dataclass(frozen=True)
class AuthenticatedDelivery:
    organization_id: str
    connector_id: str
    provider: str
    installation_id: str | None
    workspace_id: str | None
    source_delivery_id: str
    credential_version: str


class OutcomeAdapter(Protocol):
    def verify(self, *, binding: PortfolioConnectorBinding, headers: Mapping[str, str], raw: bytes) -> AuthenticatedDelivery:
        """Verify exact bytes and configured source ownership without JSON parsing."""
        ...

    def normalize(self, *, binding: PortfolioConnectorBinding, verified: AuthenticatedDelivery, body: dict) -> list[dict]:
        """Select allowlisted metadata, check signed parent context, never return content."""
        ...


def registered_binding(config: GatewayConfig, organization: str, connector: str) -> PortfolioConnectorBinding:
    if config.portfolio_control is not None:
        for binding in config.portfolio_control.connectors:
            if (binding.organization_id, binding.connector_id) == (organization, connector):
                return binding
    raise PortfolioError("forbidden")


def validate_delivery(binding: PortfolioConnectorBinding, value: AuthenticatedDelivery) -> None:
    if not isinstance(value, AuthenticatedDelivery):
        raise PortfolioError("unauthenticated")
    expected = (binding.organization_id, binding.connector_id, binding.provider, binding.installation_id, binding.workspace_id)
    if (value.organization_id, value.connector_id, value.provider, value.installation_id, value.workspace_id) != expected:
        raise PortfolioError("forbidden")
    source_id(value.source_delivery_id)
    validate(value.credential_version, "opaque_id")


@contextmanager
def _slot():
    if not _INGEST_SLOTS.acquire(blocking=False):
        raise PortfolioError("rate_limited")
    try:
        yield
    finally:
        _INGEST_SLOTS.release()


class OutcomeIngestor:
    def __init__(self, config: GatewayConfig, repository, organization: str, connector: str, adapter: OutcomeAdapter, keys: OutcomeKeys):
        self.config, self.repository = config, repository
        self.organization, self.connector = organization, connector
        self.adapter, self.keys = adapter, keys

    def ingest(self, headers: Mapping[str, str], raw: bytes) -> dict:
        binding = registered_binding(self.config, self.organization, self.connector)
        with _slot():
            return self._ingest(binding, headers, raw)

    def ingest_stream(self, headers: Mapping[str, str], reader, length: int, set_timeout) -> dict:
        """For later adapters: bounded read1 transport with an absolute deadline.

        The transport owns/restores its socket timeout. Chunked transfer,
        duplicate lengths and header limits must be refused by that transport.
        No source parser or storage runs before verification of all exact bytes.
        """
        binding = registered_binding(self.config, self.organization, self.connector)
        if type(length) is not int or not 1 <= length <= REQUEST_BYTES:
            raise PortfolioError("invalid_request")
        with _slot():
            remaining, chunks, deadline = length, [], time.monotonic() + 10
            try:
                while remaining:
                    budget = deadline - time.monotonic()
                    if budget <= 0:
                        raise PortfolioError("invalid_request")
                    set_timeout(budget)
                    chunk = reader.read1(min(remaining, 65536))
                    if type(chunk) is not bytes or not chunk or len(chunk) > remaining or time.monotonic() > deadline:
                        raise PortfolioError("invalid_request")
                    chunks.append(chunk)
                    remaining -= len(chunk)
            except (OSError, TimeoutError):
                raise PortfolioError("invalid_request") from None
            return self._ingest(binding, headers, b"".join(chunks))

    def _ingest(self, binding, headers, raw):
        if type(raw) is not bytes or not 1 <= len(raw) <= REQUEST_BYTES:
            raise PortfolioError("invalid_request")
        try:
            verified = self.adapter.verify(binding=binding, headers=headers, raw=raw)
        except PortfolioError as error:
            code = error.code if error.code in {"unauthenticated", "forbidden", "unavailable"} else "unavailable"
            raise PortfolioError(code) from None
        except Exception:
            # Adapter exceptions may contain source content/credentials.
            raise PortfolioError("unavailable") from None
        validate_delivery(binding, verified)
        observed_at = observed_time()
        # A replay is independent of the current parser/normalizer version.
        # The repository rechecks inside the insertion transaction for races.
        prior = self.repository._replay_verified(binding=binding, verified=verified, raw=raw, keys=self.keys)
        if prior is not None:
            return prior
        try:
            body = decode_source_body(raw)
            projected = self.adapter.normalize(binding=binding, verified=verified, body=body)
            if not isinstance(projected, list) or len(projected) > EVENTS_PER_DELIVERY:
                raise PortfolioError("invalid_request")
            observations = tuple(observation_from_mapping(value, binding) for value in projected)
            if len({item.source_event_id for item in observations}) != len(observations):
                raise PortfolioError("idempotency_conflict")
        except Exception as error:
            code = error.code if isinstance(error, PortfolioError) and error.code in {
                "invalid_request", "forbidden", "idempotency_conflict", "unavailable",
            } else "unavailable"
            safe = PortfolioError(code)
            receipt = self.repository._record_failure(
                binding=binding, verified=verified, raw=raw, keys=self.keys,
                observed_at=observed_at, reason=safe.reason,
            )
            if receipt is not None:
                return receipt
            raise safe from None
        # No raw JSON object or free-text exception crosses the storage boundary.
        # Exact bytes are used solely for the keyed replay fingerprint.
        try:
            return self.repository._accept_verified(
                binding=binding, verified=verified, raw=raw, keys=self.keys,
                observed_at=observed_at, observations=observations,
            )
        except PortfolioError as error:
            # The rejected transaction has rolled back. Persist a separate
            # content-free delivery failure for domain validation/conflicts;
            # never claim durable failure evidence when storage is unavailable.
            reasons = {"invalid_request": "invalid_shape", "not_found": "invalid_shape",
                       "idempotency_conflict": "conflicting_identity", "version_conflict": "conflicting_identity"}
            if error.code in reasons:
                receipt = self.repository._record_failure(
                    binding=binding, verified=verified, raw=raw, keys=self.keys,
                    observed_at=observed_at, reason=reasons[error.code],
                )
                if receipt is not None:
                    return receipt
            raise
