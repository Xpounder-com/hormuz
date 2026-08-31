"""Strict registry transport contracts, separate from the frozen v1 manifest.

The installed catalogue is an exact dependency-closed subset of ADR 0010's
digest-pinned wire bundle. Structural validation is not authorization: the
registry service and transaction enforce references and lifecycle invariants.
"""

from __future__ import annotations

import json
import math
import re
import uuid
from datetime import datetime, timezone
from functools import lru_cache
from importlib import resources
from typing import Any
from urllib.parse import parse_qsl


REQUEST_BYTES = 262144
RESPONSE_BYTES = 1048576
PREFIX = "/v1/admin/portfolio"
SCOPES = PREFIX + "/work-scopes"
BINDINGS = PREFIX + "/work-bindings"
ATTRIBUTIONS = PREFIX + "/attributions"
OUTCOMES = PREFIX + "/outcomes"
ERRORS = {
    "invalid_request": (400, "invalid_shape"),
    "unauthenticated": (401, "unauthorized_scope"),
    "forbidden": (403, "unauthorized_scope"),
    "not_found": (404, "invalid_reference"),
    "idempotency_conflict": (409, "conflicting_identity"),
    "version_conflict": (409, "stale_version"),
    "cursor_invalid": (400, "invalid_cursor"),
    "rate_limited": (429, "capacity"),
    "unavailable": (503, "dependency_unavailable"),
}


class PortfolioError(ValueError):
    """Fixed diagnostics only: never echo submitted keys, labels, or values."""

    def __init__(self, code: str, reason: str | None = None):
        self.code = code
        self.status, default_reason = ERRORS[code]
        self.reason = default_reason if reason is None else reason
        super().__init__(code)

    def envelope(self) -> dict[str, Any]:
        return {
            "schema_id": "hormuz.portfolio-error", "schema_version": 1,
            "code": self.code, "request_id": uuid.uuid4().hex,
            "retryable": self.code in {"unavailable", "rate_limited"}, "retry_after_seconds": None,
            "reason_code": self.reason,
        }


def canonical(value: object) -> str:
    try:
        return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError, RecursionError, UnicodeError):
        raise PortfolioError("invalid_request") from None


def decode_body(data: bytes) -> dict[str, Any]:
    def members(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise PortfolioError("invalid_request")
            result[key] = value
        return result

    def nonfinite(_value):
        raise PortfolioError("invalid_request")

    if len(data) > REQUEST_BYTES:
        raise PortfolioError("invalid_request")
    try:
        value = json.loads(data.decode("utf-8"), object_pairs_hook=members, parse_constant=nonfinite)
    except (ValueError, UnicodeError, RecursionError):
        raise PortfolioError("invalid_request") from None
    if not isinstance(value, dict):
        raise PortfolioError("invalid_request")
    pending = [(value, 1)]
    while pending:
        item, depth = pending.pop()
        if depth > 32 or (isinstance(item, float) and not math.isfinite(item)):
            raise PortfolioError("invalid_request")
        if isinstance(item, str):
            try:
                item.encode("utf-8")
            except UnicodeError:
                raise PortfolioError("invalid_request") from None
        if isinstance(item, dict):
            pending.extend((child, depth + 1) for child in item.values())
        elif isinstance(item, list):
            pending.extend((child, depth + 1) for child in item)
    return value


@lru_cache(maxsize=1)
def catalogue() -> dict[str, Any]:
    return json.loads(resources.files("hormuz").joinpath("portfolio-registry-wire-v1.json").read_text("utf-8"))


@lru_cache(maxsize=1)
def attribution_catalogue() -> dict[str, Any]:
    return json.loads(resources.files("hormuz").joinpath("portfolio-attribution-wire-v1.json").read_text("utf-8"))


@lru_cache(maxsize=1)
def outcome_catalogue() -> dict[str, Any]:
    return json.loads(resources.files("hormuz").joinpath("portfolio-outcome-wire-v1.json").read_text("utf-8"))


def validate(value: object, name: str) -> None:
    definitions = catalogue()["$defs"]
    if name not in definitions:
        definitions = attribution_catalogue()["$defs"]
    if name not in definitions:
        definitions = outcome_catalogue()["$defs"]
    if name not in definitions:
        raise PortfolioError("invalid_request")

    def check(item, schema, depth=0):
        if depth > 32:
            raise PortfolioError("invalid_request")
        if "$ref" in schema:
            return check(item, definitions[schema["$ref"].removeprefix("#/$defs/")], depth)
        if "anyOf" in schema:
            for choice in schema["anyOf"]:
                try:
                    check(item, choice, depth)
                    return
                except PortfolioError:
                    pass
            raise PortfolioError("invalid_request")
        kind = schema["type"]
        numeric = type(item) is int or (type(item) is float and math.isfinite(item))
        if not {
            "object": isinstance(item, dict), "array": isinstance(item, list),
            "string": isinstance(item, str), "boolean": type(item) is bool,
            "null": item is None, "number": numeric,
            "integer": numeric and int(item) == item,
        }[kind]:
            raise PortfolioError("invalid_request")
        if "const" in schema and item != schema["const"]:
            raise PortfolioError("invalid_request")
        if "enum" in schema and item not in schema["enum"]:
            raise PortfolioError("invalid_request")
        if kind == "object":
            if set(item) - schema["properties"].keys():
                raise PortfolioError("invalid_request", "unknown_field")
            if not set(schema["required"]).issubset(item):
                raise PortfolioError("invalid_request")
            for field, required in schema.get("dependentRequired", {}).items():
                if field in item and not set(required).issubset(item):
                    raise PortfolioError("invalid_request")
            for field, child in item.items():
                check(child, schema["properties"][field], depth + 1)
        elif kind == "array":
            if not schema["minItems"] <= len(item) <= schema["maxItems"]:
                raise PortfolioError("invalid_request")
            if schema.get("uniqueItems") and len({canonical(child) for child in item}) != len(item):
                raise PortfolioError("invalid_request")
            for child in item:
                check(child, schema["items"], depth + 1)
        elif kind == "string":
            if not schema.get("minLength", 0) <= len(item) <= schema.get("maxLength", 2048):
                raise PortfolioError("invalid_request")
            try:
                item.encode("utf-8")
            except UnicodeError:
                raise PortfolioError("invalid_request") from None
            if "pattern" in schema and not re.search(schema["pattern"], item):
                raise PortfolioError("invalid_request")
            if schema.get("format") == "date-time":
                try:
                    if datetime.fromisoformat(item).tzinfo != timezone.utc:
                        raise ValueError
                except ValueError:
                    raise PortfolioError("invalid_request") from None
        elif kind in {"number", "integer"}:
            if not schema.get("minimum", -math.inf) <= item <= schema.get("maximum", math.inf):
                raise PortfolioError("invalid_request")

    check(value, definitions[name])


def route(method: str, path: str) -> tuple[str, str | None]:
    if path == OUTCOMES and method == "GET":
        return "list_outcomes", None
    if path == ATTRIBUTIONS and method in {"GET", "POST"}:
        return ("list_attributions" if method == "GET" else "attribute", None)
    if method == "GET" and path in {SCOPES, BINDINGS}:
        return ("list_scopes" if path == SCOPES else "list_bindings", None)
    if method == "POST" and path in {SCOPES, BINDINGS}:
        return ("create_scope" if path == SCOPES else "bind", None)
    match = re.fullmatch(re.escape(SCOPES) + r"/([A-Za-z0-9][A-Za-z0-9._:-]{0,127})(/versions)?", path)
    if match:
        if method == "GET" and match[2] is None:
            return "show_scope", match[1]
        if method == "POST" and match[2] == "/versions":
            return "version_scope", match[1]
    raise PortfolioError("not_found")


def query_parameters(raw: str, operation: str) -> dict[str, Any]:
    if len(raw) > 8192 or re.search(r"%(?![0-9A-Fa-f]{2})", raw):
        raise PortfolioError("invalid_request")
    try:
        pairs = parse_qsl(raw, keep_blank_values=True, strict_parsing=True, max_num_fields=12, errors="strict")
    except (ValueError, UnicodeError):
        raise PortfolioError("invalid_request") from None
    allowed = {"version"} if operation == "show_scope" else {"limit", "cursor", "start_at", "end_at", "work_scope_id"}
    if operation in {"list_bindings", "list_outcomes"}:
        allowed.add("connector_id")
    if operation not in {"show_scope", "list_scopes", "list_bindings", "list_attributions", "list_outcomes"}:
        allowed = set()
    result = {}
    for key, value in pairs:
        if key not in allowed or key in result:
            raise PortfolioError("invalid_request", "unknown_field")
        if key in {"limit", "version"}:
            if not re.fullmatch(r"(?:0|[1-9][0-9]{0,9})", value):
                raise PortfolioError("invalid_request")
            value = int(value)
        result[key] = value
    validate(result, "hormuz.portfolio-query")
    if "cursor" in result and set(result) - {"cursor", "limit"}:
        raise PortfolioError("cursor_invalid")
    if "start_at" in result and datetime.fromisoformat(result["start_at"]) >= datetime.fromisoformat(result["end_at"]):
        raise PortfolioError("invalid_request")
    return result
