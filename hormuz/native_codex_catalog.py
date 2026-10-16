"""Native account model discovery; credentials never select an API-key route."""
import http.client
from http import HTTPStatus
import hmac
from email.utils import parsedate_to_datetime
import re
from urllib.parse import parse_qsl, urlencode, urlsplit

from .auth import AuthenticationError
from ._contract_schemas.constants import ERROR_SCHEMA_ID, NATIVE_CATALOG_SCHEMA_ID, NATIVE_CATALOG_SCHEMA_VERSION
from .compaction_formats import strict_json_loads
from ._native_catalog_worker import CATALOG_PATH, NATIVE_ORIGIN
from .native_catalog_transport import fetch_native_catalog


def _retry_after(values):
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


def _one(headers, name, *, required=True, limit=16384):
    values = headers.get_all(name, [])
    if not values and not required:
        return None
    if len(values) != 1 or not 1 <= len(values[0]) <= limit or any(
            not 32 <= ord(char) <= 126 for char in values[0]):
        raise ValueError("invalid native credential header")
    return values[0]


def handle_native_catalog(handler):
    server = handler.server
    if not server.config.native_codex_catalog:
        handler._send_error("not_found", "Route not found", HTTPStatus.NOT_FOUND)
        return
    try:
        key = _one(handler.headers, "X-Gateway-Key")
        identity = server.authenticator.authenticate(key)
        authorization = _one(handler.headers, "Authorization")
        known_credentials = (*server.config.identities_by_token, *server.upstream_credentials.values(),
                             server.config.ingress.credential)
        if (re.fullmatch(r"(?i:Bearer) [A-Za-z0-9._~+/-]+=*", authorization) is None
                or any(hmac.compare_digest(authorization[7:].encode(), credential.encode())
                       for credential in known_credentials if credential)):
            raise ValueError("invalid native credential")
        account = _one(handler.headers, "ChatGPT-Account-ID", required=False, limit=256)
    except (AuthenticationError, ValueError):
        handler._send_error("unauthorized", "Separate gateway and native ChatGPT credentials are required.",
                            HTTPStatus.UNAUTHORIZED)
        return
    policy = server.policy_engine.policy_runtime.snapshot_for(identity).effective_policy
    requested_mode = handler.headers.get_all("X-Hormuz-Routing-Mode", [])
    if len(requested_mode) > 1 or requested_mode and requested_mode[0] != "local_only":
        handler._send_error("invalid_request", "The routing mode is unsupported.", HTTPStatus.BAD_REQUEST)
        return
    if (identity.allowed_clients and "codex" not in identity.allowed_clients
            or policy.allowed_clients is not None and "codex" not in policy.allowed_clients
            or server.config.route_admission.mode == "local_only" or requested_mode):
        handler._send_error("hormuz_policy_denied", "Native cloud discovery is not permitted by the selected policy.",
                            HTTPStatus.FORBIDDEN)
        return
    allowed = {route.upstream_model for alias, route in server.config.model_routes.items()
               if route.protocol == "openai" and server.config.upstreams[route.destination_id].locality == "cloud"
               and server.config.upstreams[route.destination_id].authentication == "codex_chatgpt"
               and (policy.allowed_models is None or alias in policy.allowed_models)}
    if not allowed:
        handler._send_error("hormuz_policy_denied", "No configured cloud model is permitted for native discovery.",
                            HTTPStatus.FORBIDDEN)
        return
    try:
        query = urlsplit(handler.path).query
        if len(query) > 128:
            raise ValueError()
        fields = parse_qsl(query, keep_blank_values=True, strict_parsing=True, max_num_fields=2)
        if (len(fields) > 1 or any(key != "client_version" or re.fullmatch(r"[0-9]{1,4}\.[0-9]{1,4}\.[0-9]{1,4}", value) is None
                                  for key, value in fields)):
            raise ValueError()
    except ValueError:
        handler._send_error("invalid_request", "The native catalog query is unsupported.", HTTPStatus.BAD_REQUEST)
        return
    if not server.native_catalog_slots.acquire(blocking=False):
        handler._send_error("gateway_upstream_error", "Native catalog capacity is unavailable.", HTTPStatus.TOO_MANY_REQUESTS)
        return
    try:
        response = fetch_native_catalog(authorization=authorization, account=account, query=urlencode(fields))
        status = response.status if response.status in {200, 401, 403, 429} else HTTPStatus.BAD_GATEWAY
        retry_headers = _retry_after(response.retry_after) if status == 429 else None
        if status == 200:
            value = strict_json_loads(response.body.decode("utf-8"))
            if (type(value) is not dict or type(value.get("models")) is not list
                    or len(value["models"]) > 256):
                raise ValueError()
            seen = set()
            models = []
            for model in value["models"]:
                slug = model.get("slug") if type(model) is dict else None
                if (type(slug) is not str or not 1 <= len(slug) <= 256
                        or any(ord(char) <= 32 or ord(char) == 127 for char in slug) or slug in seen):
                    raise ValueError()
                seen.add(slug)
                if slug in allowed:
                    models.append(model)
    except (OSError, http.client.HTTPException, ValueError, RecursionError):
        status, retry_headers = HTTPStatus.BAD_GATEWAY, None
    finally:
        server.native_catalog_slots.release()
    if status == 200:
        handler._send_json(HTTPStatus.OK, {"models": models},
                          contract_header_value=f"{NATIVE_CATALOG_SCHEMA_ID};v={NATIVE_CATALOG_SCHEMA_VERSION}")
    else:
        handler._send_contract_json(status, ERROR_SCHEMA_ID,
            {"error": {"code": "gateway_upstream_error", "message": "Native catalog access is unavailable; no alternate billing path was selected."}},
            extra_headers=retry_headers)
