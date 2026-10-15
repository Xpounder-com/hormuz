"""Authenticate local router callers; provider authentication belongs to destinations."""
from __future__ import annotations
import hmac
from .config import GatewayConfig, Identity


class AuthenticationError(ValueError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class Authenticator:
    def __init__(self, config: GatewayConfig):
        self._config = config

    def authenticate(self, token: str) -> Identity:
        if not isinstance(token, str) or not token or len(token) > 65536:
            raise AuthenticationError("invalid_credential")
        candidate = token.encode("utf-8")
        for expected, identity in self._config.identities_by_token.items():
            if hmac.compare_digest(candidate, expected.encode("utf-8")):
                return identity
        raise AuthenticationError("invalid_credential")
