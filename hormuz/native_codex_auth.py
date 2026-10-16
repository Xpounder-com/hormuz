"""The shared native gateway credential boundary; no credential persistence."""
from dataclasses import dataclass, field
from email.utils import parsedate_to_datetime
import hmac
import re

from .config import Identity


@dataclass(frozen=True)
class NativeCredentials:
    identity: Identity
    authorization: str = field(repr=False)
    account: str | None = field(repr=False)


def retry_after_headers(values):
    if len(values) != 1 or len(values[0]) > 64 or any(not 32 <= ord(char) <= 126 for char in values[0]):
        return {}
    value = values[0]
    if re.fullmatch(r"[0-9]{1,10}", value) is None:
        try:
            if parsedate_to_datetime(value).tzinfo is None:
                return {}
        except (TypeError, ValueError, OverflowError):
            return {}
    return {"Retry-After": value}


def single_header(headers, name, *, required=True, limit=16384):
    values = headers.get_all(name, [])
    if not values and not required:
        return None
    if len(values) != 1 or not 1 <= len(values[0]) <= limit or any(
            not 32 <= ord(char) <= 126 for char in values[0]):
        raise ValueError("invalid native credential header")
    return values[0]


def gateway_identity(handler):
    return handler.server.authenticator.authenticate(single_header(handler.headers, "X-Gateway-Key"))


def native_credentials(handler):
    identity = gateway_identity(handler)
    authorization = single_header(handler.headers, "Authorization")
    server = handler.server
    known = (*server.config.identities_by_token, *server.upstream_credentials.values(), server.config.ingress.credential)
    if (re.fullmatch(r"(?i:Bearer) [A-Za-z0-9._~+/-]+=*", authorization) is None
            or any(hmac.compare_digest(authorization[7:].encode(), key.encode()) for key in known if key)):
        raise ValueError("invalid native credential")
    account = single_header(handler.headers, "ChatGPT-Account-ID", required=False, limit=256)
    return NativeCredentials(identity, authorization, account)
