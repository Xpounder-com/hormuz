"""Offline checks for the deliberately small planned JSON Schema vocabulary.

This is a source-contract/fixture tool, not a runtime API validator or a general
JSON Schema implementation. Unsupported keywords and external references fail
closed. Relational, authorization, and temporal domain rules remain the named
implementation gates; structural fixture success never proves those rules.
"""

from __future__ import annotations

import json
import math
import re
from datetime import datetime, timezone
from typing import Any


DIALECT = "https://json-schema.org/draft/2020-12/schema"
KEYWORDS = {
    "type", "$ref", "description", "properties", "required",
    "additionalProperties", "items", "minItems", "maxItems", "uniqueItems",
    "minimum", "maximum", "minLength", "maxLength", "pattern", "format",
    "const", "enum", "anyOf", "default", "dependentRequired",
    "x-hormuz-schema-id", "x-hormuz-schema-version",
}
TYPES = {"object", "array", "string", "integer", "number", "boolean", "null"}
MAX_DEPTH = 32


class PortfolioWireSchemaError(ValueError):
    """A planned schema or synthetic wire fixture is malformed."""


def _fail(code: str) -> None:
    # Never include arbitrary payload values, names, or rejected keys in errors.
    raise PortfolioWireSchemaError(code)


def _reference_name(value: object, definitions: dict[str, Any]) -> str:
    if not isinstance(value, str) or not value.startswith("#/$defs/"):
        _fail("wire_schema_external_or_invalid_reference")
    name = value.removeprefix("#/$defs/")
    if name not in definitions:
        _fail("wire_schema_reference_missing")
    return name


def _json_equal(left: object, right: object) -> bool:
    if isinstance(left, bool) or isinstance(right, bool):
        return type(left) is type(right) and left == right
    if isinstance(left, list) and isinstance(right, list):
        return len(left) == len(right) and all(
            _json_equal(a, b) for a, b in zip(left, right)
        )
    if isinstance(left, dict) and isinstance(right, dict):
        return left.keys() == right.keys() and all(
            _json_equal(left[key], right[key]) for key in left
        )
    return left == right


def _check_schema(
    schema: object, definitions: dict[str, Any], stack: tuple[str, ...]
) -> None:
    if not isinstance(schema, dict) or not schema or set(schema) - KEYWORDS:
        _fail("wire_schema_unsupported_keyword")
    if len(stack) > MAX_DEPTH:
        _fail("wire_schema_reference_depth")
    if "$ref" in schema:
        if set(schema) - {"$ref", "description"}:
            _fail("wire_schema_reference_sibling_unsupported")
        name = _reference_name(schema["$ref"], definitions)
        if name in stack:
            _fail("wire_schema_reference_cycle")
        _check_schema(definitions[name], definitions, (*stack, name))
        return
    if "anyOf" in schema:
        if set(schema) - {"anyOf", "description"}:
            _fail("wire_schema_union_sibling_unsupported")
        variants = schema["anyOf"]
        if not isinstance(variants, list) or not 2 <= len(variants) <= 8:
            _fail("wire_schema_union_invalid")
        for variant in variants:
            _check_schema(variant, definitions, stack)
        return
    kind = schema.get("type")
    if kind not in TYPES:
        _fail("wire_schema_type_missing")
    if kind == "object":
        properties = schema.get("properties")
        required = schema.get("required")
        if (
            schema.get("additionalProperties") is not False
            or not isinstance(properties, dict) or not properties
            or not isinstance(required, list)
            or any(not isinstance(name, str) for name in required)
            or len(required) != len(set(required))
            or not set(required).issubset(properties)
        ):
            _fail("wire_schema_object_not_closed")
        for child in properties.values():
            _check_schema(child, definitions, stack)
        for name, dependencies in schema.get("dependentRequired", {}).items():
            if (
                name not in properties or not isinstance(dependencies, list)
                or any(item not in properties for item in dependencies)
            ):
                _fail("wire_schema_dependency_invalid")
    elif kind == "array":
        low, high = schema.get("minItems"), schema.get("maxItems")
        if type(low) is not int or type(high) is not int or not 0 <= low <= high <= 100:
            _fail("wire_schema_array_unbounded")
        _check_schema(schema.get("items"), definitions, stack)
    elif kind == "string" and not {"enum", "const"} & schema.keys():
        low, high = schema.get("minLength"), schema.get("maxLength")
        if type(low) is not int or type(high) is not int or not 0 <= low <= high <= 2048:
            _fail("wire_schema_string_unbounded")
        if "pattern" in schema:
            try:
                re.compile(schema["pattern"])
            except (TypeError, re.error):
                _fail("wire_schema_pattern_invalid")
        if schema.get("format") not in (None, "date-time"):
            _fail("wire_schema_format_unsupported")
    elif kind in {"integer", "number"} and "const" not in schema:
        low, high = schema.get("minimum"), schema.get("maximum")
        if (
            isinstance(low, bool) or isinstance(high, bool)
            or not isinstance(low, (int, float)) or not isinstance(high, (int, float))
            or not math.isfinite(low) or not math.isfinite(high)
            or not 0 <= low <= high <= 9007199254740991
        ):
            _fail("wire_schema_number_unbounded")


def validate_wire_bundle(bundle: object, schema_ids: set[str]) -> None:
    """Check closed/bounded field definitions and complete local references."""

    if not isinstance(bundle, dict):
        _fail("wire_schema_bundle_fields_invalid")
    base_fields = {
        "$schema", "$id", "title", "description", "$defs", "oneOf",
        "x-hormuz-schema-ids", "x-hormuz-route-query-fields",
        "x-hormuz-transport", "x-hormuz-domain-rules",
    }
    if set(bundle) not in (base_fields, base_fields | {"x-hormuz-schema-versions"}):
        _fail("wire_schema_bundle_fields_invalid")
    if bundle["$schema"] != DIALECT:
        _fail("wire_schema_dialect_changed")
    definitions = bundle["$defs"]
    if not isinstance(definitions, dict):
        _fail("wire_schema_definitions_invalid")
    if (
        bundle["x-hormuz-schema-ids"] != sorted(schema_ids)
        or {name for name in definitions if name.startswith("hormuz.")} != schema_ids
        or bundle["oneOf"] != [{"$ref": f"#/$defs/{name}"} for name in sorted(schema_ids)]
    ):
        _fail("wire_schema_inventory_changed")
    versions = bundle.get("x-hormuz-schema-versions", {name: 1 for name in schema_ids})
    if (
        not isinstance(versions, dict)
        or set(versions) != schema_ids
        or any(type(version) is not int or not 1 <= version <= 2147483647 for version in versions.values())
    ):
        _fail("wire_schema_version_inventory_changed")
    for name, schema in definitions.items():
        _check_schema(schema, definitions, (name,))
    for name in schema_ids:
        schema = definitions[name]
        if schema.get("type") != "object":
            _fail("wire_schema_envelope_invalid")
        if name == "hormuz.portfolio-query":
            if (
                schema.get("x-hormuz-schema-id") != name
                or type(schema.get("x-hormuz-schema-version")) is not int
                or schema["x-hormuz-schema-version"] != versions[name]
            ):
                _fail("wire_schema_query_identity_changed")
        else:
            properties = schema["properties"]
            if (
                properties.get("schema_id", {}).get("const") != name
                or type(properties.get("schema_version", {}).get("const")) is not int
                or properties["schema_version"]["const"] != versions[name]
                or not {"schema_id", "schema_version"}.issubset(schema["required"])
            ):
                _fail("wire_schema_envelope_identity_changed")


def _validate_value(
    value: object, schema: dict[str, Any], definitions: dict[str, Any], depth: int
) -> None:
    if depth > MAX_DEPTH:
        _fail("wire_payload_depth_exceeded")
    if "$ref" in schema:
        name = _reference_name(schema["$ref"], definitions)
        _validate_value(value, definitions[name], definitions, depth)
        return
    if "anyOf" in schema:
        for variant in schema["anyOf"]:
            try:
                _validate_value(value, variant, definitions, depth)
                return
            except PortfolioWireSchemaError:
                continue
        _fail("wire_payload_union_mismatch")
    kind = schema["type"]
    numeric = type(value) is int or (type(value) is float and math.isfinite(value))
    valid_type = {
        "object": isinstance(value, dict), "array": isinstance(value, list),
        "string": isinstance(value, str), "boolean": isinstance(value, bool),
        "null": value is None, "number": numeric,
        "integer": numeric and int(value) == value,
    }[kind]
    if not valid_type:
        _fail("wire_payload_type_invalid")
    if "const" in schema and not _json_equal(value, schema["const"]):
        _fail("wire_payload_const_invalid")
    if "enum" in schema and not any(_json_equal(value, item) for item in schema["enum"]):
        _fail("wire_payload_enum_invalid")
    if kind == "object":
        if set(value) - schema["properties"].keys():
            _fail("wire_payload_unknown_field")
        if not set(schema["required"]).issubset(value):
            _fail("wire_payload_required_field_missing")
        for name, dependencies in schema.get("dependentRequired", {}).items():
            if name in value and not set(dependencies).issubset(value):
                _fail("wire_payload_dependency_missing")
        for name, child in value.items():
            _validate_value(child, schema["properties"][name], definitions, depth + 1)
    elif kind == "array":
        if not schema["minItems"] <= len(value) <= schema["maxItems"]:
            _fail("wire_payload_array_bounds")
        if schema.get("uniqueItems") and any(
            _json_equal(item, other) for index, item in enumerate(value)
            for other in value[index + 1:]
        ):
            _fail("wire_payload_array_duplicate")
        for child in value:
            _validate_value(child, schema["items"], definitions, depth + 1)
    elif kind == "string":
        if not schema.get("minLength", 0) <= len(value) <= schema.get("maxLength", 2048):
            _fail("wire_payload_string_bounds")
        if "pattern" in schema and not re.search(schema["pattern"], value):
            _fail("wire_payload_string_pattern")
        if schema.get("format") == "date-time":
            try:
                parsed = datetime.fromisoformat(value)
                if parsed.tzinfo != timezone.utc:
                    _fail("wire_payload_time_invalid")
            except ValueError:
                _fail("wire_payload_time_invalid")
    elif kind in {"number", "integer"}:
        if not schema.get("minimum", -math.inf) <= value <= schema.get("maximum", math.inf):
            _fail("wire_payload_number_bounds")


def validate_wire_payload(bundle: dict[str, Any], schema_id: str, value: object) -> None:
    """Validate a synthetic payload structurally; not auth or domain behavior."""

    validate_wire_bundle(bundle, set(bundle["x-hormuz-schema-ids"]))
    if schema_id not in bundle["x-hormuz-schema-ids"]:
        _fail("wire_payload_schema_unknown")
    try:
        encoded = json.dumps(value, allow_nan=False, separators=(",", ":")).encode("utf-8")
    except (TypeError, ValueError, RecursionError, UnicodeError):
        _fail("wire_payload_json_invalid")
    if len(encoded) > bundle["x-hormuz-transport"]["response_maximum_bytes"]:
        _fail("wire_payload_bytes_exceeded")
    _validate_value(value, bundle["$defs"][schema_id], bundle["$defs"], 0)
