"""Authenticated registry dispatch shared by the HTTP and local CLI boundaries."""

from __future__ import annotations

from .auth import AuthenticationError, Authenticator
from .config import GatewayConfig
from .portfolio_config import PortfolioPrincipal, authorize
from .portfolio_repository import PortfolioRepository
from .portfolio_wire import PortfolioError, decode_body, query_parameters, route


class PortfolioService:
    def __init__(self, config: GatewayConfig, repository: PortfolioRepository, authenticator: Authenticator | None = None):
        self.config = config
        self.repository = repository
        self.authenticator = authenticator or Authenticator(config)

    def authenticate(self, token: str) -> PortfolioPrincipal:
        if not isinstance(token, str) or not token.isascii():
            raise PortfolioError("unauthenticated")
        try:
            identity = self.authenticator.authenticate(token)
        except AuthenticationError:
            raise PortfolioError("unauthenticated") from None
        return authorize(self.config.portfolio_control, identity)

    def dispatch(self, token: str, method: str, path: str, *, query: str = "", body: bytes = b"", idempotency_key: str | None = None):
        principal = self.authenticate(token)
        return self.dispatch_authorized(principal, method, path, query=query, body=body, idempotency_key=idempotency_key)

    def dispatch_authorized(self, principal, method, path, *, query="", body=b"", idempotency_key=None):
        operation, scope_id = route(method, path)
        parameters = query_parameters(query, operation)
        if method == "GET" and body:
            raise PortfolioError("invalid_request")
        value = decode_body(body) if method == "POST" else None
        return self.repository.execute(principal, operation, path=path, scope_id=scope_id, query=parameters,
                                       body=value, idempotency_key=idempotency_key)
