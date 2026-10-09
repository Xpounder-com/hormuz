"""Additive owned bindings and consented campaign handoff transport."""
import base64
import hashlib
import hmac
import json
import time
from urllib.parse import parse_qsl
import re

from .work_runtime import WorkRuntimeError
from .work_workflow import CAMPAIGN_FIELDS, campaign_labels
from .portfolio_wire import PortfolioError

HANDOFF_COOKIE = "__Secure-hormuz_work_handoff"
HANDOFF_SECONDS = 600


def request_labels(request):
    if len(request.query) > 1024:
        raise WorkRuntimeError("invalid_campaign_labels")
    try:
        pairs = parse_qsl(request.query, strict_parsing=True, max_num_fields=4)
    except ValueError:
        raise WorkRuntimeError("invalid_campaign_labels") from None
    if len(dict(pairs)) != len(pairs):
        raise WorkRuntimeError("invalid_campaign_labels")
    return campaign_labels(dict(pairs))


def _cookie(value, *, clear=False):
    return f"{HANDOFF_COOKIE}={value}; Path=/work; Max-Age={0 if clear else HANDOFF_SECONDS}; Secure; HttpOnly; SameSite=Lax"


def clear_handoff():
    return _cookie("", clear=True)


def _encode_handoff(labels, host, key, now):
    value = {"labels": campaign_labels(labels), "host": host, "created": now, "expires": now + HANDOFF_SECONDS}
    payload = base64.urlsafe_b64encode(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).rstrip(b"=").decode()
    signature = hmac.new(key, b"hormuz/work-campaign-handoff/v1\x00" + payload.encode(), hashlib.sha256).hexdigest()
    return "v1." + payload + "." + signature


def _decode_handoff(token, host, key, now):
    if not isinstance(token, str) or re.fullmatch(r"v1\.[A-Za-z0-9_-]{1,2048}\.[a-f0-9]{64}", token) is None:
        raise WorkRuntimeError("work_handoff_invalid")
    _, payload, signature = token.split(".")
    expected = hmac.new(key, b"hormuz/work-campaign-handoff/v1\x00" + payload.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature):
        raise WorkRuntimeError("work_handoff_invalid")
    try:
        raw = base64.b64decode(payload + "=" * (-len(payload) % 4), altchars=b"-_", validate=True)
        value = json.loads(raw)
        if base64.urlsafe_b64encode(raw).rstrip(b"=").decode() != payload or not isinstance(value, dict) or set(value) != {"labels", "host", "created", "expires"} or value["host"] != host or type(value["created"]) is not int or type(value["expires"]) is not int or value["expires"] - value["created"] != HANDOFF_SECONDS or value["created"] > now + 30 or value["expires"] <= now:
            raise ValueError()
        return campaign_labels(value["labels"])
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise WorkRuntimeError("work_handoff_invalid") from None


def anonymous_handoff(handler, labels):
    from . import work_http, work_pages
    settings = handler.server.config.session_broker
    if not settings.enabled or not settings.master_key:
        raise WorkRuntimeError("work_handoff_unavailable", 503)
    token = _encode_handoff(labels, handler.headers["Host"], settings.master_key, int(time.time()))
    work_http._send(handler, 200, work_pages.login(message="Sign in, then open AI Work to review whether to attach this campaign source. No source is recorded until you consent.", **work_http._navigation(handler)), set_cookie=_cookie(token))


def consume_handoff(handler, principal):
    """Offer verified labels once; no identity, redirect or acquisition authority."""
    from . import work_http, work_pages
    if not principal.browser:
        return False
    values = []
    for part in handler.headers.get("Cookie", "").split(";"):
        name, separator, value = part.strip().partition("=")
        if name == HANDOFF_COOKIE:
            values.append(value if separator else "")
    if not values:
        return False
    try:
        if len(values) != 1:
            raise WorkRuntimeError("work_handoff_invalid")
        labels = _decode_handoff(values[0], handler.headers["Host"], handler.server.config.session_broker.master_key, int(time.time()))
    except WorkRuntimeError:
        work_http._send(handler, 400, work_pages.failure("This campaign handoff expired or could not be verified. Continue to AI work without attaching it.", **work_http._navigation(handler)), set_cookie=clear_handoff())
        return True
    # Clearing on the consent-page response also makes declining progress.
    # Only the later authenticated, CSRF-checked POST persists acquisition.
    work_http._send(handler, 200, work_pages.acquisition(principal, labels, **work_http._navigation(handler)), set_cookie=clear_handoff())
    return True


def dispatch(handler, principal, request):
    from . import work_http, work_pages
    path = request.path
    workflow = getattr(handler.server, "work_workflow", None)
    binding = re.fullmatch(r"/v1/work/jobs/([A-Za-z0-9][A-Za-z0-9._-]{0,127})/bindings", path)
    if path not in {"/work/acquisition", "/v1/work/acquisition"} and not binding:
        return False
    if workflow is None:
        raise WorkRuntimeError("workflow_not_enabled", 503)
    if path == "/work/acquisition" and handler.command == "GET":
        labels = request_labels(request)
        work_http._send(handler, 200, work_pages.acquisition(principal, labels, **work_http._navigation(handler)), set_cookie=clear_handoff())
        return True
    if binding and handler.command == "GET":
        work_http._json(handler, 200, workflow.bindings(principal.identity, binding[1]))
        return True
    if handler.command != "POST" or request.query:
        raise WorkRuntimeError("work_invalid_request")
    if binding:
        fields = {"provider", "connector_id", "container_id", "object_id", "completion_condition"}
        values = work_http._values(handler, fields | {"csrf_token"}, fields)
        work_http._verify_mutation(handler, principal, values.pop("csrf_token", ""))
        try:
            result = workflow.bind(principal.identity, binding[1], **values)
        except PortfolioError:
            raise WorkRuntimeError("binding_connector_not_qualified", 403) from None
        status = 201
    elif path == "/v1/work/acquisition":
        values = work_http._values(handler, CAMPAIGN_FIELDS | {"csrf_token", "analytics_consent"}, {"analytics_consent"})
        work_http._verify_mutation(handler, principal, values.pop("csrf_token", ""))
        consent = values.pop("analytics_consent")
        result = workflow.acquire(principal.identity, values, consent=consent is True)
        status = 201
    else:
        raise WorkRuntimeError("work_not_found", 404)
    if handler.headers.get_content_type() == "application/x-www-form-urlencoded":
        work_http._dashboard(handler, principal, message="Workflow association saved." if binding else "Consented campaign source saved privately for this account.")
    else:
        work_http._json(handler, status, result)
    return True
