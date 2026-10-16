"""Native account model discovery; credentials never select an API-key route."""
import http.client
from http import HTTPStatus
import hmac
from email.utils import parsedate_to_datetime
import re
from urllib.parse import parse_qsl, urlencode, urlsplit
from urllib.request import Request

from .auth import AuthenticationError
from ._contract_schemas.constants import ERROR_SCHEMA_ID
from .compaction_formats import strict_json_loads
from .local_transport import open_local_upstream

CATALOG_PATH = "/backend-api/codex/models"
NATIVE_ORIGIN = "https://chatgpt.com/backend-api/codex"
CATALOG_URL = NATIVE_ORIGIN + "/models"
MAX_CATALOG_BYTES = 1024 * 1024


def _retry_after(headers):
    values = headers.get_all("Retry-After", [])
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
        if (authorization[:7].lower() != "bearer " or not authorization[7:]
                or any(char.isspace() for char in authorization[7:])
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
    if ("codex" not in identity.allowed_clients
            or policy.allowed_clients is not None and "codex" not in policy.allowed_clients
            or server.config.route_admission.mode == "local_only" or requested_mode):
        handler._send_error("hormuz_provider_policy_denied", "Native cloud discovery is not permitted by the selected policy.",
                            HTTPStatus.FORBIDDEN)
        return
    allowed = {route.upstream_model for alias, route in server.config.model_routes.items()
               if route.protocol == "openai" and server.config.upstreams[route.destination_id].locality == "cloud"
               and server.config.upstreams[route.destination_id].authentication == "codex_chatgpt"
               and (policy.allowed_models is None or alias in policy.allowed_models)}
    if not allowed:
        handler._send_error("hormuz_provider_policy_denied", "No configured cloud model is permitted for native discovery.",
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
    headers = {"Authorization": authorization, "Accept": "application/json"}
    if account is not None:
        headers["ChatGPT-Account-ID"] = account
    if not server.native_catalog_slots.acquire(blocking=False):
        handler._send_error("gateway_upstream_error", "Native catalog capacity is unavailable.", HTTPStatus.TOO_MANY_REQUESTS)
        return
    try:
        request = Request(CATALOG_URL + ("?" + urlencode(fields) if fields else ""), headers=headers, method="GET")
        with open_local_upstream(request, timeout=3, absolute_timeout=3) as response:
            if response.status != 200:
                status = response.status if response.status in {401, 403, 429} else HTTPStatus.BAD_GATEWAY
                handler._send_contract_json(status, ERROR_SCHEMA_ID,
                    {"error": {"code": "gateway_upstream_error", "message": "Native catalog access is unavailable; no alternate billing path was selected."}},
                    extra_headers=_retry_after(response.headers) if status == 429 else None)
                return
            data = response.read(MAX_CATALOG_BYTES + 1)
        if len(data) > MAX_CATALOG_BYTES:
            raise ValueError()
        value = strict_json_loads(data.decode("utf-8"))
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
        handler._send_json(HTTPStatus.OK, {"models": models}, extra_headers={"X-Hormuz-Native-Catalog-Version": "1"})
    except (OSError, http.client.HTTPException, ValueError, RecursionError):
        handler._send_error("gateway_upstream_error", "Native catalog access is unavailable; no alternate billing path was selected.",
                            HTTPStatus.BAD_GATEWAY)
    finally:
        server.native_catalog_slots.release()
