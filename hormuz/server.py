from __future__ import annotations

import hmac
import http.client
import ipaddress
import json
import logging
import sqlite3
import threading
import time
import urllib.error
import urllib.request
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Mapping
from urllib.parse import urlsplit

from . import __version__
from .auth import AuthenticationError, Authenticator
from .attribution_admission import (
    Admission,
    AdmissionError,
    REQUEST_HEADER as ATTRIBUTION_REQUEST_HEADER,
    RESULT_HEADER as ATTRIBUTION_RESULT_HEADER,
    select_admission,
)
from .budget_runtime import configured_route_rate_card
from .config import GatewayConfig, Identity, ModelRoute, UpstreamConfig
from .contracts import (
    ALLOCATION_BASIS_DIRECT_GATEWAY_REQUEST,
    COST_BASIS_CONFIGURED_RATE_CARD_ESTIMATE,
    COVERAGE_GATEWAY_CAPTURED_REQUESTS_ONLY,
    ERROR_SCHEMA_ID,
    HEALTH_SCHEMA_ID,
    IDENTITY_SCHEMA_ID,
    READINESS_SCHEMA_ID,
    USAGE_SUMMARY_SCHEMA_ID,
    contract_envelope,
    relay_contract_header,
)
from .custody_runtime import resolve_upstream_credentials
from .custody_runtime_projection import CustodyRuntimeProjection, CustodyRuntimeProjectionError
from .evidence import EvidenceStorageError
from .finance_attempts import (
    ConfiguredRateCardBinding,
    configured_rate_card_binding,
    estimate_configured_route,
)
from .policy import PolicyDecision, PolicyEngine
from .policy_document import local_policy_content_sha256
from .policy_runtime import PolicyRuntime
from .portfolio_http import handle_registry
from .portfolio_repository import create_portfolio_repository
from .portfolio_service import PortfolioService
from .portfolio_wire import PREFIX as PORTFOLIO_PREFIX
from .postgres import PostgresStorageError
from .provider_reliability import (
    ProviderAttemptMetrics,
    ProviderFailoverContext,
    failover_reason,
)
from .redaction import RedactionError, SecretRedactor
from .store import RequestAttempt, ReservationDenied, StorageSchemaError, UsageRepository, WorkBudgetContext
from .store_router import (
    create_postgres_runtime_pool,
    create_provider_reliability_repository,
    create_repository_bundle,
    create_usage_store,
    create_work_budget_request_repository,
)
from .session import SessionBroker
from .session_http import SessionRequestLimit, handle_session_request
from .session_store import SQLiteSessionStore, SessionStoreError
from .console import ConsoleService
from .console_http import handle_console_request
from .usage import ResponseUsageParser


LOGGER = logging.getLogger("hormuz")
_STORAGE_FAILURES = (sqlite3.Error, EvidenceStorageError, PostgresStorageError, StorageSchemaError)
_INGRESS_CREDENTIAL_HEADER = "X-Hormuz-Ingress-Credential"
_RELAY_CHUNK_BYTES = 16 * 1024
_OPENAI_PROVIDER_STATE_FIELDS = frozenset({
    "conversation", "previous_response_id", "prompt",
})
_ANTHROPIC_PROVIDER_STATE_FIELDS = frozenset({"container", "mcp_servers"})
_OPENAI_INLINE_TOOL_TYPES = frozenset({"custom", "function"})
_ANTHROPIC_INLINE_TOOL_TYPES = frozenset({"custom"})
_INLINE_ANTHROPIC_SOURCE_TYPES = frozenset({"base64", "content", "text"})


def _provider_input_tokens_bounded(protocol: str, request: Mapping[str, Any]) -> bool:
    """Recognize request inputs whose billed content is self-contained.

    Serialized bytes conservatively bound inline text/data, but cannot bound
    content a provider resolves from stored state, a URL, a file identifier,
    or a server-side tool. Unknown reference forms fail closed when a work
    budget is active; this classifier never fetches or inspects content.
    """

    if protocol == "openai":
        if any(request.get(field) is not None for field in _OPENAI_PROVIDER_STATE_FIELDS):
            return False
        inline_tool_types = _OPENAI_INLINE_TOOL_TYPES
    elif protocol == "anthropic":
        if any(request.get(field) is not None for field in _ANTHROPIC_PROVIDER_STATE_FIELDS):
            return False
        inline_tool_types = _ANTHROPIC_INLINE_TOOL_TYPES
    else:
        return False

    tools = request.get("tools")
    if tools is not None:
        if type(tools) is not list:
            return False
        for tool in tools:
            if type(tool) is not dict:
                return False
            tool_type = tool.get("type")
            if protocol == "anthropic" and tool_type is None:
                continue
            if type(tool_type) is not str or tool_type not in inline_tool_types:
                return False

    pending: list[object] = [request]
    while pending:
        value = pending.pop()
        if type(value) is list:
            pending.extend(value)
            continue
        if type(value) is not dict:
            continue
        kind = value.get("type")
        if kind == "item_reference":
            return False
        if kind == "input_image":
            if value.get("file_id") is not None:
                return False
            if "image_url" in value:
                image_url = value.get("image_url")
                if type(image_url) is not str or not image_url.startswith("data:"):
                    return False
        if kind == "input_file" and (
            value.get("file_id") is not None or value.get("file_url") is not None
        ):
            return False
        if kind in {"image", "document"}:
            source = value.get("source")
            if type(source) is not dict or source.get("type") not in _INLINE_ANTHROPIC_SOURCE_TYPES:
                return False
        pending.extend(value.values())
    return True


def _configured_route_binding(decision: PolicyDecision) -> ConfiguredRateCardBinding:
    """Freeze the exact configured prices selected for one provider attempt."""

    route = decision.route
    assert route is not None
    return configured_rate_card_binding(configured_route_rate_card(
        alias=decision.resolved_alias or route.alias,
        protocol=route.protocol,
        upstream_model=route.upstream_model,
        input_cost_per_million=route.input_cost_per_million,
        cache_read_cost_per_million=route.cache_read_cost_per_million,
        cache_write_cost_per_million=route.cache_write_cost_per_million,
        output_cost_per_million=route.output_cost_per_million,
    ))


class GatewayServer(ThreadingHTTPServer):
    # ThreadingMixIn joins non-daemon handler threads from ``server_close``.
    # Keep that default so a graceful listener shutdown cannot close the
    # runtime pool between a provider response and its final evidence write.
    daemon_threads = False
    block_on_close = True
    allow_reuse_address = True
    # ``TCPServer`` defaults to a five-connection accept backlog. That can
    # reset otherwise bounded concurrent requests before Hormuz reaches its
    # governed PostgreSQL pool and returns the stable storage-unavailable
    # response. Keep the listener queue bounded, but large enough for the
    # runtime's explicitly bounded backpressure path.
    request_queue_size = 128

    def __init__(self, config: GatewayConfig, *, environ: Mapping[str, str] | None = None):
        self.config = config
        self._accepting_requests = threading.Event()
        self.authenticator = Authenticator(config)
        self.session_broker: SessionBroker | None = None
        self.session_request_limit = SessionRequestLimit()
        self.console_request_limit = SessionRequestLimit()
        self.console: ConsoleService | None = None
        self.postgres_pool = create_postgres_runtime_pool(config)
        try:
            if config.session_broker.enabled:
                settings = config.session_broker
                self.session_broker = SessionBroker(config, self.authenticator, SQLiteSessionStore(
                    settings.database_path,
                    master_key=settings.master_key,
                    audience=settings.public_base_url,
                    access_ttl_seconds=settings.access_ttl_seconds,
                    absolute_ttl_seconds=settings.absolute_ttl_seconds,
                    enrollment_ttl_seconds=settings.enrollment_ttl_seconds,
                ))
            self.store: UsageRepository
            if config.portfolio_control is None:
                self.store = create_usage_store(config, connection_pool=self.postgres_pool)
                portfolio = create_portfolio_repository(config, connection_pool=self.postgres_pool, environ=environ)
            else:
                repositories = create_repository_bundle(
                    config, portfolio_factory=create_portfolio_repository, connection_pool=self.postgres_pool,
                )
                self.store, portfolio = repositories.usage, repositories.portfolio
            if self.session_broker is not None and config.session_broker.console_enabled:
                self.console = ConsoleService(self.session_broker, self.store)
            self.portfolio_service = PortfolioService(config, portfolio, self.authenticator)
            self.attribution_repository = portfolio.attributions
            self.budget_repository = portfolio.budgets
            provider_reliability_store = create_provider_reliability_repository(self.store)
            if provider_reliability_store is None:
                raise StorageSchemaError("storage_schema_partial_upgrade")
            self.provider_reliability_store = provider_reliability_store
            policy_runtime = PolicyRuntime(config, connection_pool=self.postgres_pool)
            self.policy_engine = PolicyEngine(
                config,
                self.store,
                policy_runtime=policy_runtime,
                work_budget_requests=create_work_budget_request_repository(self.store),
                provider_reliability_requests=self.provider_reliability_store,
            )
            self.policy_engine.policy_runtime.verify_active_policies()
            recovered_attempts = self.store.sweep_stale_request_attempts()
            if recovered_attempts:
                LOGGER.warning("request_attempts_marked_outcome_unknown count=%d", recovered_attempts)
            self.custody_runtime_projection = CustodyRuntimeProjection(
                config,
                connection_pool=self.postgres_pool,
            )
            self.upstream_credentials = resolve_upstream_credentials(
                config,
                environ=environ,
                selection_allowed=self._upstream_credential_selection_allowed,
            )
            protected_values = [
                ("hormuz_identity_token", identity.token)
                for identity in config.identities_by_token.values()
                if identity.token
            ]
            protected_values.extend(
                ("provider_credential", value)
                for value in self.upstream_credentials.values()
                if len(value) >= 8
            )
            protected_values.extend(
                ("oidc_login_secret", issuer.login.client_secret)
                for issuer in config.oidc_issuers.values()
                if issuer.login is not None and len(issuer.login.client_secret) >= 8
            )
            self.secret_redactor = SecretRedactor(config.secret_controls, tuple(protected_values))
            super().__init__((config.listen.host, config.listen.port), GatewayRequestHandler)
        except Exception:
            self._close_postgres_pool()
            raise
        self._accepting_requests.set()
        if self.postgres_pool is not None:
            settings = self.postgres_pool.settings
            LOGGER.info(
                "postgres_pool_started min_connections=%d max_connections=%d max_waiting=%d "
                "acquire_timeout_seconds=%d",
                settings.min_connections,
                settings.max_connections,
                settings.max_waiting,
                settings.acquire_timeout_seconds,
            )

    def _upstream_credential_selection_allowed(self, protocol: str) -> bool:
        """Avoid loading a credential generation already restricted by custody.

        A normal request repeats this check immediately before egress. This
        startup guard is narrower: it prevents a freshly retired encrypted
        envelope from being decrypted into a newly started gateway process,
        while still allowing the gateway to start and return the stable 403
        custody result for that protocol.
        """

        if not self.custody_runtime_projection.enabled:
            return True
        organization_ids = self.config.organization_ids
        if len(organization_ids) != 1:
            raise CustodyRuntimeProjectionError("custody_runtime_projection_configuration_invalid")
        try:
            self.custody_runtime_projection.require_provider_usable(
                organization_id=organization_ids[0],
                protocol=protocol,
            )
        except CustodyRuntimeProjectionError as error:
            if error.code in {"custody_provider_credential_disabled", "custody_envelope_retired"}:
                return False
            raise
        return True

    def shutdown(self) -> None:
        """Stop advertising readiness before the listener begins draining."""

        self.begin_drain()
        super().shutdown()

    def begin_drain(self) -> None:
        """Stop advertising readiness without blocking the serving thread."""

        self._accepting_requests.clear()

    def server_close(self) -> None:
        self._accepting_requests.clear()
        try:
            super().server_close()
        finally:
            self._close_postgres_pool()

    def readiness_reason(self) -> str | None:
        """Return a content-free reason when this process must not receive traffic."""

        if not self._accepting_requests.is_set():
            return "draining"
        try:
            self.store.verify_ready()
            if self.session_broker is not None:
                self.session_broker.store.check_available()
            self.policy_engine.policy_runtime.verify_active_policies()
            if not self.custody_runtime_projection.readiness_healthy():
                LOGGER.warning("readiness_custody_projection_stale")
                return "dependency_unavailable"
        except (*_STORAGE_FAILURES, SessionStoreError):
            LOGGER.warning("readiness_dependency_unavailable")
            return "dependency_unavailable"
        # A shutdown may have started while the read-only checks ran. Never
        # report ready after the drain marker is cleared.
        if not self._accepting_requests.is_set():
            return "draining"
        return None

    def _close_postgres_pool(self) -> None:
        projection = getattr(self, "custody_runtime_projection", None)
        if projection is not None:
            projection.close()
        if self.postgres_pool is None:
            return
        try:
            self.postgres_pool.close()
        except PostgresStorageError:
            LOGGER.error("postgres_pool_close_failed")


class GatewayRequestHandler(BaseHTTPRequestHandler):
    server: GatewayServer
    protocol_version = "HTTP/1.1"

    def parse_request(self) -> bool:
        """Reject an untrusted proxy hop before any route-specific behavior.

        This happens after Python has parsed the HTTP request but before it
        dispatches *any* method.  It therefore covers health checks and
        unknown methods as well as the employee-facing API routes.
        """

        if not super().parse_request():
            return False
        ingress = self.server.config.ingress
        if ingress.mode == "local":
            return True

        try:
            peer = ipaddress.ip_address(self.client_address[0])
        except ValueError:
            reason = "peer_address_invalid"
        else:
            if not any(peer in network for network in ingress.trusted_proxy_networks):
                reason = "peer_not_trusted"
            else:
                credentials = self.headers.get_all(_INGRESS_CREDENTIAL_HEADER, [])
                if len(credentials) != 1 or not hmac.compare_digest(credentials[0], ingress.credential):
                    reason = "credential_rejected"
                else:
                    return True

        LOGGER.info("ingress_denied reason=%s method=%s", reason, self.command)
        self.close_connection = True
        self._send_error(
            "unauthorized",
            "Missing or invalid Hormuz ingress credential",
            HTTPStatus.UNAUTHORIZED,
        )
        return False

    def do_GET(self) -> None:  # noqa: N802
        self._attribution_result = None
        path = urlsplit(self.path).path
        if path.startswith(PORTFOLIO_PREFIX + "/"):
            handle_registry(self)
            return
        if path == "/console" or path.startswith(("/console/", "/v1/admin/")):
            handle_console_request(self)
            return
        if path.startswith("/v1/auth/"):
            handle_session_request(self)
            return
        if path == "/health":
            self._send_contract_json(
                HTTPStatus.OK,
                HEALTH_SCHEMA_ID,
                {
                    "status": "ok",
                    "service": "hormuz",
                    "protocols": ["openai-responses", "anthropic-messages"],
                },
            )
            return
        if path == "/ready":
            reason = self.server.readiness_reason()
            if reason is None:
                self._send_contract_json(
                    HTTPStatus.OK,
                    READINESS_SCHEMA_ID,
                    {
                        "status": "ready",
                        "service": "hormuz",
                        "reason": None,
                    },
                )
                return
            self._send_contract_json(
                HTTPStatus.SERVICE_UNAVAILABLE,
                READINESS_SCHEMA_ID,
                {
                    "status": "not_ready",
                    "service": "hormuz",
                    "reason": reason,
                },
            )
            return
        identity = self._authenticate()
        if identity is None:
            return
        if path == "/v1/gateway/whoami":
            self._send_contract_json(
                HTTPStatus.OK,
                IDENTITY_SCHEMA_ID,
                {
                    "actor_id": identity.actor_id,
                    "actor_name": identity.actor_name,
                    "team_id": identity.team_id,
                    "team_name": identity.team_name,
                    "organization_id": identity.organization_id,
                    "identity_type": identity.identity_type,
                    "allowed_clients": list(identity.allowed_clients),
                    "authentication_source": identity.authentication_source,
                },
            )
            return
        if path == "/v1/gateway/usage":
            try:
                totals = self.server.store.monthly_totals(
                    actor_id=identity.actor_id,
                    organization_id=identity.organization_id,
                )
                secret_totals = self.server.store.monthly_secret_totals(
                    actor_id=identity.actor_id,
                    organization_id=identity.organization_id,
                )
            except _STORAGE_FAILURES as error:
                self._send_storage_failure(None, error)
                return
            self._send_contract_json(
                HTTPStatus.OK,
                USAGE_SUMMARY_SCHEMA_ID,
                {
                    "month": "current",
                    "requests": totals.requests,
                    "denied_requests": totals.denied_requests,
                    "rate_limited_requests": totals.rate_limited_requests,
                    "input_tokens": totals.input_tokens,
                    "output_tokens": totals.output_tokens,
                    "cache_read_tokens": totals.cache_read_tokens,
                    "cache_write_tokens": totals.cache_write_tokens,
                    "reasoning_tokens": totals.reasoning_tokens,
                    "cost_usd": totals.cost_usd,
                    "cost_basis": COST_BASIS_CONFIGURED_RATE_CARD_ESTIMATE,
                    "allocation_basis": ALLOCATION_BASIS_DIRECT_GATEWAY_REQUEST,
                    "coverage": COVERAGE_GATEWAY_CAPTURED_REQUESTS_ONLY,
                    "redactions": totals.redaction_count,
                    "secret_events": secret_totals.events,
                    "secret_detections": secret_totals.detections,
                    "secret_denied_requests": secret_totals.denied_requests,
                },
            )
            return
        self._send_error("not_found", "Route not found", HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:  # noqa: N802
        self._response_started = False
        self._attribution_result = None
        path = urlsplit(self.path).path
        if path.startswith(PORTFOLIO_PREFIX + "/"):
            handle_registry(self)
            return
        if path == "/console" or path.startswith(("/console/", "/v1/admin/")):
            handle_console_request(self)
            return
        if path.startswith("/v1/auth/"):
            handle_session_request(self)
            return
        routes = {
            "/v1/responses": ("openai", "codex", True),
            "/v1/responses/compact": ("openai", "codex", True),
            "/v1/messages": ("anthropic", "claude-code", True),
            "/v1/messages/count_tokens": ("anthropic", "claude-code", False),
        }
        route = routes.get(path)
        if route is None:
            self._send_error("not_found", "Route not found", HTTPStatus.NOT_FOUND)
            return
        identity = self._authenticate()
        if identity is None:
            return
        protocol, default_client, account_usage = route
        try:
            self._proxy_generation(
                identity=identity,
                protocol=protocol,
                # Do not trust a caller-supplied application name for enforcement.
                # The compatibility endpoint determines the policy client.
                client=default_client,
                account_usage=account_usage,
            )
        except AdmissionError as error:
            self._reject_attribution(identity, default_client, protocol, error)
        except _STORAGE_FAILURES as error:
            self._send_storage_failure(protocol, error)

    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(HTTPStatus.NO_CONTENT)
        self.send_header("Allow", "GET, POST, OPTIONS")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _proxy_generation(
        self,
        *,
        identity: Identity,
        protocol: str,
        client: str,
        account_usage: bool,
    ) -> None:
        admission = select_admission(
            self.server.config, identity, client, self.headers.get_all(ATTRIBUTION_REQUEST_HEADER, []),
            account_usage=account_usage,
        )
        if admission is not None:
            self.server.attribution_repository.preflight(identity, client, protocol, admission)
        request_body = self._read_json_body()
        if request_body is None:
            return
        requested_model = request_body.get("model")
        if not isinstance(requested_model, str) or not requested_model.strip():
            self._send_protocol_error(protocol, "Request field model must be a non-empty string", HTTPStatus.BAD_REQUEST)
            return
        requested_model = requested_model.strip()
        output_field = "max_output_tokens" if protocol == "openai" else "max_tokens"
        requested_output = request_body.get(output_field)
        if requested_output is not None and (
            isinstance(requested_output, bool) or not isinstance(requested_output, int) or requested_output <= 0
        ):
            self._send_protocol_error(protocol, f"Request field {output_field} must be a positive integer", HTTPStatus.BAD_REQUEST)
            return

        decision = self.server.policy_engine.evaluate(
            identity=identity,
            client=client,
            protocol=protocol,
            requested_model=requested_model,
            requested_output_tokens=requested_output,
        )
        if not decision.allowed or decision.route is None:
            if account_usage:
                self.server.store.record(
                    identity=identity,
                    client=client,
                    protocol=protocol,
                    requested_model=requested_model,
                    resolved_alias=None,
                    upstream_model=None,
                    policy_version=decision.policy_version,
                    policy_action="denied",
                    status="denied",
                )
            LOGGER.info(
                "policy_denied actor=%s team=%s client=%s protocol=%s requested_model=%s reason=%s",
                identity.actor_id,
                identity.team_id,
                client,
                protocol,
                requested_model,
                decision.reason,
            )
            self._send_protocol_error(protocol, decision.reason, HTTPStatus.FORBIDDEN, code="hormuz_policy_denied")
            return

        is_responses_create = protocol == "openai" and urlsplit(self.path).path == "/v1/responses"
        if (
            is_responses_create
            and request_body.get("background") is True
            and not decision.snapshot.openai_egress.allow_background
        ):
            if account_usage:
                self.server.store.record(
                    identity=identity,
                    client=client,
                    protocol=protocol,
                    requested_model=decision.requested_model,
                    resolved_alias=decision.resolved_alias,
                    upstream_model=decision.route.upstream_model,
                    policy_version=decision.policy_version,
                    policy_action="provider_policy_denied",
                    status="denied",
                )
            LOGGER.info(
                "provider_policy_denied actor=%s team=%s client=%s protocol=%s requested_model=%s reason=background_disabled",
                identity.actor_id,
                identity.team_id,
                client,
                protocol,
                decision.requested_model,
            )
            self._send_protocol_error(
                protocol,
                "Organization policy does not allow OpenAI background response storage.",
                HTTPStatus.FORBIDDEN,
                code="hormuz_provider_policy_denied",
            )
            return
        if is_responses_create and not decision.snapshot.openai_egress.allow_response_storage:
            request_body["store"] = False

        request_body["model"] = decision.route.upstream_model
        if decision.max_output_tokens is not None:
            current_output = request_body.get(output_field)
            if current_output is None or current_output > decision.max_output_tokens:
                request_body[output_field] = decision.max_output_tokens
        try:
            redaction = self.server.secret_redactor.inspect(request_body, mode=decision.snapshot.secret_mode)
        except RedactionError as error:
            self._send_protocol_error(protocol, str(error), HTTPStatus.BAD_REQUEST)
            return

        if redaction.count:
            self.server.store.record_secret_event(
                identity=identity,
                client=client,
                protocol=protocol,
                requested_model=decision.requested_model,
                policy_version=decision.policy_version,
                action="denied" if decision.snapshot.secret_mode == "deny" else "redacted",
                detection_count=redaction.count,
                rules=redaction.rules,
            )

        if redaction.count and decision.snapshot.secret_mode == "deny":
            if account_usage:
                self.server.store.record(
                    identity=identity,
                    client=client,
                    protocol=protocol,
                    requested_model=decision.requested_model,
                    resolved_alias=decision.resolved_alias,
                    upstream_model=decision.route.upstream_model,
                    policy_version=decision.policy_version,
                    policy_action="secret_denied",
                    status="denied",
                    redaction_count=redaction.count,
                    redaction_rules=redaction.rules,
                )
            LOGGER.warning(
                "secret_egress_denied actor=%s team=%s client=%s protocol=%s requested_model=%s detections=%d rules=%s",
                identity.actor_id,
                identity.team_id,
                client,
                protocol,
                decision.requested_model,
                redaction.count,
                ",".join(redaction.rules),
            )
            self._send_protocol_error(
                protocol,
                "Request blocked because Hormuz detected protected secret material.",
                HTTPStatus.FORBIDDEN,
                code="hormuz_secret_detected",
            )
            return

        policy_action = decision.action
        if redaction.count:
            policy_action = f"{policy_action}+redacted"
        body = self._provider_body(redaction.value, decision.route)
        try:
            self.server.custody_runtime_projection.require_provider_usable(
                organization_id=identity.organization_id,
                protocol=protocol,
            )
        except CustodyRuntimeProjectionError as error:
            if error.code in {"custody_provider_credential_disabled", "custody_envelope_retired"}:
                self._send_protocol_error(
                    protocol,
                    "Organization custody policy does not permit this provider credential.",
                    HTTPStatus.FORBIDDEN,
                    code="hormuz_custody_restricted",
                )
                return
            self._send_protocol_error(
                protocol,
                "Hormuz custody projection is temporarily unavailable.",
                HTTPStatus.SERVICE_UNAVAILABLE,
                code="hormuz_storage_unavailable",
            )
            return
        upstream_key = self.server.upstream_credentials.get(protocol, "")
        if not upstream_key:
            self._send_protocol_error(
                protocol,
                "Gateway upstream credential is unavailable",
                HTTPStatus.SERVICE_UNAVAILABLE,
                code="gateway_upstream_not_configured",
            )
            return
        attempt: RequestAttempt | None = None
        if account_usage:
            try:
                attempt = self._begin_governed_attempt(
                    identity=identity,
                    decision=decision,
                    client=client,
                    protocol=protocol,
                    policy_action=policy_action,
                    redaction_count=redaction.count,
                    redaction_rules=redaction.rules,
                    request_value=redaction.value,
                    output_field=output_field,
                    body=body,
                    admission=admission,
                )
            except ReservationDenied as error:
                self._deny_budget_reservation(
                    identity=identity,
                    decision=decision,
                    client=client,
                    protocol=protocol,
                    redaction_count=redaction.count,
                    redaction_rules=redaction.rules,
                    error=error,
                )
                return
        failover_decision = self.server.policy_engine.operational_failover(decision)
        if (
            not account_usage
            or self._request_precludes_failover(protocol, redaction.value)
        ):
            failover_decision = None
        self._forward(
            identity=identity,
            protocol=protocol,
            client=client,
            decision=decision,
            body=body,
            request_value=redaction.value,
            output_field=output_field,
            admission=admission,
            account_usage=account_usage,
            policy_action=policy_action,
            redaction_count=redaction.count,
            redaction_rules=redaction.rules,
            attempt=attempt,
            upstream_key=upstream_key,
            reservation_ttl_seconds=self.server.config.upstream_timeout_seconds + 60,
            failover_decision=failover_decision,
            failover_applied_reason=None,
        )

    def _forward(
        self,
        *,
        identity: Identity,
        protocol: str,
        client: str,
        decision: PolicyDecision,
        body: bytes,
        request_value: dict[str, Any],
        output_field: str,
        admission: Admission | None,
        account_usage: bool,
        policy_action: str,
        redaction_count: int,
        redaction_rules: tuple[str, ...],
        attempt: RequestAttempt | None,
        upstream_key: str,
        reservation_ttl_seconds: int,
        failover_decision: PolicyDecision | None,
        failover_applied_reason: str | None,
    ) -> None:
        route = decision.route
        assert route is not None
        upstream = self.server.config.upstreams[protocol]
        request_url = self._upstream_url(upstream)
        headers = self._upstream_headers(protocol, upstream_key)
        request = urllib.request.Request(request_url, data=body, headers=headers, method="POST")

        started_ns = time.monotonic_ns()
        try:
            response = urllib.request.urlopen(request, timeout=self.server.config.upstream_timeout_seconds)
        except urllib.error.HTTPError as error:
            response = error
        except (urllib.error.URLError, http.client.HTTPException, TimeoutError, OSError) as error:
            if account_usage and attempt is not None:
                self.server.provider_reliability_store.mark_request_attempt_outcome_unknown(
                    attempt=attempt,
                    organization_id=identity.organization_id,
                    reason_code="provider_transport_ambiguous",
                    provider_metrics=self._provider_metrics(
                        started_ns=started_ns,
                        provider_status=None,
                        response_headers_us=None,
                        first_body_byte_us=None,
                        provider_bytes_read=0,
                        downstream_bytes_sent=0,
                    ),
                )
            self._send_protocol_error(
                protocol,
                f"Upstream provider is unavailable: {error}",
                HTTPStatus.BAD_GATEWAY,
                code="gateway_upstream_error",
            )
            return

        response_headers_us = self._elapsed_us(started_ns)
        status = getattr(response, "status", response.getcode())
        content_type = response.headers.get("Content-Type", "application/json")
        provider_request_id = response.headers.get("x-request-id") or response.headers.get("request-id")
        reason = failover_reason(status)
        if (
            reason is not None
            and failover_decision is not None
            and account_usage
            and attempt is not None
        ):
            response.close()
            request_status = "rate_limited" if status == HTTPStatus.TOO_MANY_REQUESTS else "failed"
            self.server.provider_reliability_store.finalize_request_attempt(
                attempt=attempt,
                organization_id=identity.organization_id,
                status=request_status,
                cost_microusd=0,
                provider_request_id=provider_request_id,
                provider_metrics=self._provider_metrics(
                    started_ns=started_ns,
                    provider_status=status,
                    response_headers_us=response_headers_us,
                    first_body_byte_us=None,
                    provider_bytes_read=0,
                    downstream_bytes_sent=0,
                ),
            )
            failover_route = failover_decision.route
            assert failover_route is not None
            failover_body = self._provider_body(request_value, failover_route)
            try:
                failover_attempt = self._begin_governed_attempt(
                    identity=identity,
                    decision=failover_decision,
                    client=client,
                    protocol=protocol,
                    policy_action=policy_action,
                    redaction_count=redaction_count,
                    redaction_rules=redaction_rules,
                    request_value=request_value,
                    output_field=output_field,
                    body=failover_body,
                    admission=admission,
                    provider_failover=ProviderFailoverContext(
                        original_attempt_id=attempt.attempt_id,
                        trigger_status=status,
                        reason_code=reason,
                    ),
                )
            except ReservationDenied as error:
                self._deny_budget_reservation(
                    identity=identity,
                    decision=failover_decision,
                    client=client,
                    protocol=protocol,
                    redaction_count=redaction_count,
                    redaction_rules=redaction_rules,
                    error=error,
                )
                return
            LOGGER.info(
                "provider_failover actor=%s team=%s client=%s protocol=%s requested_model=%s "
                "from_model=%s to_model=%s reason=%s",
                identity.actor_id,
                identity.team_id,
                client,
                protocol,
                decision.requested_model,
                route.upstream_model,
                failover_route.upstream_model,
                reason,
            )
            self._forward(
                identity=identity,
                protocol=protocol,
                client=client,
                decision=failover_decision,
                body=failover_body,
                request_value=request_value,
                output_field=output_field,
                admission=admission,
                account_usage=account_usage,
                policy_action=policy_action,
                redaction_count=redaction_count,
                redaction_rules=redaction_rules,
                attempt=failover_attempt,
                upstream_key=upstream_key,
                reservation_ttl_seconds=reservation_ttl_seconds,
                failover_decision=None,
                failover_applied_reason=reason,
            )
            return

        is_event_stream = "text/event-stream" in content_type.lower()
        parser = ResponseUsageParser(protocol, is_event_stream=is_event_stream)

        self._response_started = True
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.send_header("X-Hormuz-Contract", relay_contract_header())
        self.send_header("X-Hormuz-Policy-Decision", policy_action)
        self.send_header("X-Hormuz-Requested-Model", decision.requested_model)
        self.send_header("X-Hormuz-Routed-Model", route.upstream_model)
        self.send_header(
            "Server-Timing",
            f"hormuz_upstream_headers;dur={response_headers_us / 1000:.3f}",
        )
        if failover_applied_reason is not None:
            self.send_header("X-Hormuz-Failover", f"v1;reason={failover_applied_reason}")
        self._send_attribution_header()
        if redaction_count:
            self.send_header("X-Hormuz-Redactions", str(redaction_count))
        for name, value in response.headers.items():
            lowered = name.lower()
            if lowered in {"x-request-id", "request-id", "openai-processing-ms"} or lowered.startswith(
                ("x-ratelimit-", "anthropic-ratelimit-")
            ):
                self.send_header(name, value)
        self.end_headers()
        self.close_connection = True

        downstream_ok = True
        first_body_byte_us: int | None = None
        provider_bytes_read = 0
        downstream_bytes_sent = 0
        refresh_at = time.monotonic() + max(1, reservation_ttl_seconds // 2)
        # ``HTTPResponse.read(size)`` waits for the requested byte count or
        # EOF. Use at most one underlying read whenever the response exposes
        # ``read1`` so both ordinary JSON and event streams release available
        # bytes promptly and record first-byte latency when data actually
        # arrives.
        read_chunk = getattr(response, "read1", None)
        if not callable(read_chunk):
            read_chunk = response.read
        try:
            while True:
                chunk = read_chunk(_RELAY_CHUNK_BYTES)
                if not chunk:
                    break
                if first_body_byte_us is None:
                    first_body_byte_us = self._elapsed_us(started_ns)
                provider_bytes_read += len(chunk)
                if attempt is not None and time.monotonic() >= refresh_at:
                    self.server.store.refresh_budget_reservation(
                        attempt.reservation_id,
                        ttl_seconds=reservation_ttl_seconds,
                        organization_id=identity.organization_id,
                    )
                    refresh_at = time.monotonic() + max(1, reservation_ttl_seconds // 2)
                parser.feed(chunk)
                self._write_downstream_chunk(chunk)
                downstream_bytes_sent += len(chunk)
        except (BrokenPipeError, ConnectionResetError):
            downstream_ok = False
        except http.client.IncompleteRead as error:
            partial = error.partial
            if partial:
                if first_body_byte_us is None:
                    first_body_byte_us = self._elapsed_us(started_ns)
                provider_bytes_read += len(partial)
                parser.feed(partial)
            downstream_ok = False
            LOGGER.warning(
                "upstream_stream_failed actor=%s team=%s client=%s protocol=%s requested_model=%s error=%s",
                identity.actor_id,
                identity.team_id,
                client,
                protocol,
                decision.requested_model,
                type(error).__name__,
            )
        except (http.client.HTTPException, TimeoutError, OSError) as error:
            downstream_ok = False
            LOGGER.warning(
                "upstream_stream_failed actor=%s team=%s client=%s protocol=%s requested_model=%s error=%s",
                identity.actor_id,
                identity.team_id,
                client,
                protocol,
                decision.requested_model,
                type(error).__name__,
            )
        finally:
            response.close()

        provider_metrics = self._provider_metrics(
            started_ns=started_ns,
            provider_status=status,
            response_headers_us=response_headers_us,
            first_body_byte_us=first_body_byte_us,
            provider_bytes_read=provider_bytes_read,
            downstream_bytes_sent=downstream_bytes_sent,
        )
        parsed_usage = parser.finish_with_finance()
        usage = parsed_usage.usage
        if account_usage and attempt is not None:
            transport_succeeded = 200 <= status < 300 and downstream_ok
            provider_terminal_failed = parsed_usage.provider_terminal_state in {
                "failed",
                "incomplete",
            }
            if transport_succeeded:
                request_status = "failed" if provider_terminal_failed else "succeeded"
            elif status == HTTPStatus.TOO_MANY_REQUESTS:
                request_status = "rate_limited"
            elif 200 <= status < 300:
                self.server.provider_reliability_store.mark_request_attempt_outcome_unknown(
                    attempt=attempt,
                    organization_id=identity.organization_id,
                    reason_code="provider_stream_interrupted",
                    provider_metrics=provider_metrics,
                    finance_observation=parsed_usage.finance,
                )
                LOGGER.warning(
                    "request_outcome_unknown actor=%s team=%s client=%s protocol=%s requested_model=%s reason=provider_stream_interrupted",
                    identity.actor_id,
                    identity.team_id,
                    client,
                    protocol,
                    decision.requested_model,
                )
                return
            else:
                request_status = "failed"
            if request_status == "succeeded" and not usage.evidence_complete:
                self.server.provider_reliability_store.mark_request_attempt_outcome_unknown(
                    attempt=attempt,
                    organization_id=identity.organization_id,
                    reason_code="provider_transport_ambiguous",
                    provider_metrics=provider_metrics,
                    finance_observation=parsed_usage.finance,
                )
                LOGGER.warning(
                    "request_outcome_unknown actor=%s team=%s client=%s protocol=%s "
                    "requested_model=%s reason=provider_usage_unavailable",
                    identity.actor_id,
                    identity.team_id,
                    client,
                    protocol,
                    decision.requested_model,
                )
                return
            cost = route.estimate_cost_microusd(
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                cache_read_tokens=usage.cache_read_tokens,
                cache_write_tokens=usage.cache_write_tokens,
            )
            rate_card_binding = _configured_route_binding(decision)
            configured_estimate = estimate_configured_route(
                rate_card_binding,
                parsed_usage.finance,
                input_cost_per_million=route.input_cost_per_million,
                cache_read_cost_per_million=route.cache_read_cost_per_million,
                cache_write_cost_per_million=route.cache_write_cost_per_million,
                output_cost_per_million=route.output_cost_per_million,
            )
            if configured_estimate.availability == "available":
                assert configured_estimate.amount_microusd is not None
                cost = configured_estimate.amount_microusd
            self.server.provider_reliability_store.finalize_request_attempt(
                attempt=attempt,
                organization_id=identity.organization_id,
                status=request_status,
                provider_reported_model=usage.provider_reported_model,
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                cache_read_tokens=usage.cache_read_tokens,
                cache_write_tokens=usage.cache_write_tokens,
                reasoning_tokens=usage.reasoning_tokens,
                cost_microusd=cost,
                provider_request_id=provider_request_id,
                provider_metrics=provider_metrics,
                finance_observation=parsed_usage.finance,
                configured_estimate=configured_estimate,
            )
            LOGGER.info(
                "request_complete actor=%s team=%s client=%s protocol=%s action=%s requested_model=%s routed_model=%s status=%s input_tokens=%d output_tokens=%d cost_microusd=%d redactions=%d",
                identity.actor_id,
                identity.team_id,
                client,
                protocol,
                policy_action,
                decision.requested_model,
                route.upstream_model,
                request_status,
                usage.input_tokens,
                usage.output_tokens,
                cost,
                redaction_count,
            )

    def _begin_governed_attempt(
        self,
        *,
        identity: Identity,
        decision: PolicyDecision,
        client: str,
        protocol: str,
        policy_action: str,
        redaction_count: int,
        redaction_rules: tuple[str, ...],
        request_value: dict[str, Any],
        output_field: str,
        body: bytes,
        admission: Admission | None,
        provider_failover: ProviderFailoverContext | None = None,
    ) -> RequestAttempt:
        route = decision.route
        assert route is not None
        # Absence is not a zero-token ceiling: without either a request or
        # effective-policy bound, work-budget cost cannot be reserved.
        reserved_output_tokens = request_value.get(output_field)
        output_tokens_bounded = (
            type(reserved_output_tokens) is int and reserved_output_tokens >= 0
        )
        if not output_tokens_bounded:
            reserved_output_tokens = 0
        reserved_input_tokens = len(body)
        input_tokens_bounded = _provider_input_tokens_bounded(protocol, request_value)
        reserved_cost_microusd = route.estimate_reservation_cost_microusd(
            input_tokens=reserved_input_tokens,
            output_tokens=max(0, reserved_output_tokens),
        )
        rate_card_binding = _configured_route_binding(decision)
        policy_digest = (
            decision.snapshot.content_sha256
            or local_policy_content_sha256(self.server.config)
        )
        try:
            attempt = self.server.policy_engine.begin_request_attempt(
                identity=identity,
                decision=decision,
                client=client,
                protocol=protocol,
                policy_action=policy_action,
                redaction_count=redaction_count,
                redaction_rules=redaction_rules,
                reserved_tokens=reserved_input_tokens + max(0, reserved_output_tokens),
                reserved_cost_microusd=reserved_cost_microusd,
                ttl_seconds=self.server.config.upstream_timeout_seconds + 60,
                work_budget=(
                    None
                    if admission is None
                    else WorkBudgetContext(
                        work_scope_id=(
                            None
                            if admission.work_scope is None
                            else admission.work_scope.work_scope_id
                        ),
                        work_scope_version=(
                            None
                            if admission.work_scope is None
                            else admission.work_scope.version
                        ),
                        confidence=admission.confidence,
                        reason_code=admission.reason,
                        reserved_output_tokens=max(0, reserved_output_tokens),
                        output_tokens_bounded=output_tokens_bounded,
                        input_tokens_bounded=input_tokens_bounded,
                        policy_version=decision.policy_version,
                        policy_digest=policy_digest,
                        rate_card_id=rate_card_binding.rate_card_id,
                        rate_card_version=rate_card_binding.rate_card_version,
                        rate_card_digest=rate_card_binding.rate_card_digest,
                        rate_card_currency=rate_card_binding.currency,
                    )
                ),
                provider_failover=provider_failover,
                configured_rate_card=rate_card_binding,
            )
        except _STORAGE_FAILURES:
            if admission is not None:
                raise AdmissionError("dependency_unavailable", 503) from None
            raise
        if admission is not None and attempt.attribution_event_id is None:
            try:
                self.server.attribution_repository.admit(
                    identity,
                    client,
                    protocol,
                    admission,
                    attempt.attempt_id,
                )
            except AdmissionError as error:
                if error.status < 500:
                    # No provider call has started. A known attribution
                    # rejection can settle at zero; an uncertain storage
                    # failure keeps its conservative hold.
                    try:
                        self.server.store.finalize_request_attempt(
                            attempt=attempt,
                            organization_id=identity.organization_id,
                            status="failed",
                            cost_microusd=0,
                        )
                    except _STORAGE_FAILURES:
                        raise AdmissionError("dependency_unavailable", 503) from None
                raise
        if admission is not None:
            self._attribution_result = admission.result_header
        return attempt

    def _deny_budget_reservation(
        self,
        *,
        identity: Identity,
        decision: PolicyDecision,
        client: str,
        protocol: str,
        redaction_count: int,
        redaction_rules: tuple[str, ...],
        error: ReservationDenied,
    ) -> None:
        route = decision.route
        assert route is not None
        self.server.store.record(
            identity=identity,
            client=client,
            protocol=protocol,
            requested_model=decision.requested_model,
            resolved_alias=decision.resolved_alias,
            upstream_model=route.upstream_model,
            policy_version=decision.policy_version,
            policy_action="budget_reservation_denied",
            status="denied",
            redaction_count=redaction_count,
            redaction_rules=redaction_rules,
        )
        LOGGER.info(
            "budget_reservation_denied actor=%s team=%s client=%s protocol=%s "
            "requested_model=%s reason=%s",
            identity.actor_id,
            identity.team_id,
            client,
            protocol,
            decision.requested_model,
            str(error),
        )
        self._send_protocol_error(
            protocol,
            str(error),
            HTTPStatus.FORBIDDEN,
            code="hormuz_budget_denied",
        )

    @staticmethod
    def _provider_body(request_value: dict[str, Any], route: ModelRoute) -> bytes:
        routed = dict(request_value)
        routed["model"] = route.upstream_model
        return json.dumps(routed, separators=(",", ":")).encode("utf-8")

    @staticmethod
    def _request_precludes_failover(protocol: str, request_value: dict[str, Any]) -> bool:
        return protocol == "openai" and (
            request_value.get("background") is True or request_value.get("store") is True
        )

    @staticmethod
    def _elapsed_us(started_ns: int) -> int:
        return max(0, (time.monotonic_ns() - started_ns) // 1_000)

    @classmethod
    def _provider_metrics(
        cls,
        *,
        started_ns: int,
        provider_status: int | None,
        response_headers_us: int | None,
        first_body_byte_us: int | None,
        provider_bytes_read: int,
        downstream_bytes_sent: int,
    ) -> ProviderAttemptMetrics:
        return ProviderAttemptMetrics(
            provider_status=provider_status,
            response_headers_us=response_headers_us,
            first_body_byte_us=first_body_byte_us,
            total_us=cls._elapsed_us(started_ns),
            provider_bytes_read=provider_bytes_read,
            downstream_bytes_sent=downstream_bytes_sent,
        )

    def _write_downstream_chunk(self, chunk: bytes) -> None:
        self.wfile.write(chunk)
        self.wfile.flush()

    def _authenticate(self) -> Identity | None:
        candidates: list[str] = []
        authorization = self.headers.get("Authorization", "")
        if authorization.lower().startswith("bearer "):
            candidates.append(authorization[7:].strip())
        api_key = self.headers.get("X-Api-Key", "").strip()
        if api_key:
            candidates.append(api_key)
        for candidate in candidates:
            try:
                if candidate.startswith("hox_a_") and self.server.session_broker is not None:
                    return self.server.session_broker.authenticate(candidate)
                identity = self.server.authenticator.authenticate(candidate)
                if self.server.session_broker is not None and self.server.session_broker.directory.manages_organization(identity.organization_id):
                    raise AuthenticationError("managed_organization_session_required")
                return identity
            except AuthenticationError as error:
                if error.code.startswith("session_store_"):
                    self._send_error("hormuz_storage_unavailable", "Session authentication is unavailable", HTTPStatus.SERVICE_UNAVAILABLE)
                    return None
                LOGGER.info("authentication_denied reason=%s", error.code)
            except SessionStoreError:
                self._send_error("hormuz_storage_unavailable", "Session authentication is unavailable", HTTPStatus.SERVICE_UNAVAILABLE)
                return None
        self._send_error("unauthorized", "Missing or invalid Hormuz identity credential", HTTPStatus.UNAUTHORIZED)
        return None

    def _read_json_body(self) -> dict[str, Any] | None:
        content_length_value = self.headers.get("Content-Length")
        if content_length_value is None:
            self._send_error("length_required", "Content-Length is required", HTTPStatus.LENGTH_REQUIRED)
            return None
        try:
            content_length = int(content_length_value)
        except ValueError:
            self._send_error("invalid_content_length", "Content-Length must be an integer", HTTPStatus.BAD_REQUEST)
            return None
        if content_length < 0 or content_length > self.server.config.max_request_bytes:
            self._send_error("request_too_large", "Request body exceeds the configured limit", HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
            return None
        data = self.rfile.read(content_length)
        try:
            value = json.loads(data)
        except (json.JSONDecodeError, UnicodeDecodeError, RecursionError):
            self._send_error("invalid_json", "Request body must be valid JSON", HTTPStatus.BAD_REQUEST)
            return None
        if not isinstance(value, dict):
            self._send_error("invalid_request", "Request body must be a JSON object", HTTPStatus.BAD_REQUEST)
            return None
        return value

    def _upstream_url(self, upstream: UpstreamConfig) -> str:
        request_parts = urlsplit(self.path)
        request_path = request_parts.path
        base = upstream.base_url
        if base.endswith("/v1") and request_path.startswith("/v1/"):
            request_path = request_path[3:]
        query = f"?{request_parts.query}" if request_parts.query else ""
        return f"{base}{request_path}{query}"

    def _upstream_headers(self, protocol: str, upstream_key: str) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Accept": self.headers.get("Accept", "application/json"),
            "User-Agent": self.headers.get("User-Agent", f"Hormuz/{__version__}"),
        }
        if protocol == "openai":
            headers["Authorization"] = f"Bearer {upstream_key}"
            for name in ("OpenAI-Beta",):
                value = self.headers.get(name)
                if value:
                    headers[name] = value
        else:
            headers["X-Api-Key"] = upstream_key
            headers["Anthropic-Version"] = self.headers.get("Anthropic-Version", "2023-06-01")
            beta = self.headers.get("Anthropic-Beta")
            if beta:
                headers["Anthropic-Beta"] = beta
        return headers

    def _send_protocol_error(
        self,
        protocol: str,
        message: str,
        status: HTTPStatus,
        *,
        code: str = "invalid_request",
    ) -> None:
        if protocol == "anthropic":
            payload = {"type": "error", "error": {"type": "permission_error" if status == 403 else code, "message": message}}
        else:
            payload = {"error": {"message": message, "type": "policy_error" if status == 403 else code, "code": code}}
        self._send_json(
            status,
            payload,
            contract_header_value=relay_contract_header(),
            error_code=code,
        )

    def _reject_attribution(self, identity, client, protocol, error: AdmissionError) -> None:
        try:
            self.server.attribution_repository.record_rejection(identity, client, protocol, error)
        except AdmissionError as storage_error:
            error = storage_error
        self.close_connection = True
        self._attribution_result = error.result_header
        self._send_protocol_error(protocol, "Work attribution admission was not accepted.",
                                  HTTPStatus(error.status), code="hormuz_attribution_" + error.reason)

    def _send_attribution_header(self) -> None:
        value = getattr(self, "_attribution_result", None)
        if value is not None:
            self.send_header(ATTRIBUTION_RESULT_HEADER, value)

    def _send_error(self, code: str, message: str, status: HTTPStatus) -> None:
        self._send_contract_json(status, ERROR_SCHEMA_ID, {"error": {"code": code, "message": message}})

    def _send_storage_failure(self, protocol: str | None, error: BaseException) -> None:
        """Fail closed without exposing a database error or request content."""

        code = getattr(error, "code", "storage_unavailable")
        LOGGER.error(
            "storage_failure code=%s relay_started=%s",
            code,
            getattr(self, "_response_started", False),
        )
        self.close_connection = True
        if getattr(self, "_response_started", False):
            return
        message = "Hormuz durable policy storage is temporarily unavailable."
        if protocol is None:
            self._send_error("hormuz_storage_unavailable", message, HTTPStatus.SERVICE_UNAVAILABLE)
            return
        self._send_protocol_error(
            protocol,
            message,
            HTTPStatus.SERVICE_UNAVAILABLE,
            code="hormuz_storage_unavailable",
        )

    def _send_contract_json(
        self,
        status: HTTPStatus,
        schema_id: str,
        value: Mapping[str, Any],
    ) -> None:
        payload = contract_envelope(schema_id, value)
        self._send_json(
            status,
            payload,
            contract_header_value=f"{schema_id};v={payload['schema_version']}",
        )

    def _send_json(
        self,
        status: HTTPStatus,
        value: Any,
        *,
        contract_header_value: str | None = None,
        error_code: str | None = None,
    ) -> None:
        body = json.dumps(value, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        if contract_header_value is not None:
            self.send_header("X-Hormuz-Contract", contract_header_value)
        if error_code is not None:
            self.send_header("X-Hormuz-Error-Code", error_code)
        self._send_attribution_header()
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        # BaseHTTPRequestHandler's arguments can contain the entire URL or a
        # malformed request line. Never write OAuth callbacks or user input.
        LOGGER.debug("http request_boundary")


def serve_in_thread(server: GatewayServer) -> threading.Thread:
    thread = threading.Thread(target=server.serve_forever, name="hormuz", daemon=True)
    thread.start()
    return thread
