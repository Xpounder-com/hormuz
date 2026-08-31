"""Strict, content-free inventory of active-core secret ownership.

The inventory is a release gate, not a secret store.  It binds every direct
process-environment read in the active ``hormuz`` package to reviewed custody
metadata and separately records ambient cloud credentials and managed
encrypted material.  No value read from the environment is ever passed to
this module or serialized by it.
"""

from __future__ import annotations

import ast
import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from .custody import KEY_PURPOSES


SECRET_INVENTORY_SCHEMA_ID = "hormuz.secret-inventory"
SECRET_INVENTORY_SCHEMA_VERSION = 1
SECRET_INVENTORY_FILENAME = "secret-inventory-v1.json"
SECRET_INVENTORY_CONTENT_BOUNDARY = "content-free identifiers and custody policy metadata only"

_MAX_INVENTORY_BYTES = 256 * 1024
_SAFE_ID = re.compile(r"[a-z0-9][a-z0-9._-]*\Z")
_SAFE_MODULE = re.compile(r"hormuz/[A-Za-z0-9_./-]+\.py\Z")
_SAFE_QUALNAME = re.compile(r"[A-Za-z_][A-Za-z0-9_.]*\Z")
_SAFE_SELECTOR = re.compile(r"[A-Za-z0-9_.:-]+\Z")

_SENSITIVITIES = frozenset({"secret", "non_secret"})
_PURPOSE_STATUSES = frozenset({"active", "reserved"})
_CUSTODY_MODES = frozenset(
    {
        "plain_metadata",
        "external_injection",
        "transient_import",
        "external_workload_identity",
        "hormuz_encrypted_envelope",
        "external_service_encryption",
        "session_flow_aead",
        "keyed_hash",
        "os_secure_store",
        "private_invitation_handoff",
    }
)
_STORAGE_OWNERS = frozenset(
    {
        "deployment_environment",
        "customer_secret_manager",
        "client_secure_environment",
        "operator_process",
        "customer_filesystem",
        "customer_key_service",
        "customer_cloud_identity",
        "customer_object_store",
        "client_os_secure_store",
    }
)
_RUNTIME_CONSUMERS = frozenset(
    {
        "configuration_loader",
        "client_credential_helper",
        "custody_operator_cli",
        "gateway_runtime",
        "custody_runtime",
        "audit_anchor_runtime",
        "policy_control_runtime",
        "policy_control_cli",
        "custody_control_runtime",
        "custody_control_cli",
        "custody_executor_runtime",
        "policy_runtime",
        "portfolio_runtime",
        "portfolio_control_cli",
        "storage_runtime",
        "storage_migration_cli",
        "session_broker",
        "team_operator_cli",
        "not_in_active_core",
    }
)
_ROTATION_AUTHORITIES = frozenset(
    {
        "deployment_operator",
        "provider_credential_operator",
        "identity_operator",
        "database_operator",
        "policy_recovery_operator",
        "custody_operator",
        "object_storage_operator",
        "cloud_identity_operator",
        "future_design_required",
        "session_owner",
    }
)
_MATERIAL_CLASSES = frozenset(
    {
        "configuration_locator",
        "identity_credential",
        "ingress_proxy_credential",
        "protected_redaction_value",
        "provider_credential",
        "database_credential",
        "policy_recovery_credential",
        "policy_administrator_credential",
        "custody_administrator_credential",
        "custody_service_credential",
        "immutable_store_credential",
        "imported_secret_material",
        "cloud_workload_credential",
        "metadata_only_audit_artifact",
        "identity_connector_secret",
        "session_material",
        "approval_fingerprint",
        "data_encryption_key",
    }
)

_TOP_LEVEL_FIELDS = {
    "schema_id",
    "schema_version",
    "scope",
    "content_boundary",
    "key_purposes",
    "environment_reads",
    "ambient_credential_reads",
    "managed_materials",
}
_PURPOSE_FIELDS = {
    "key_purpose",
    "status",
    "material_class",
    "runtime_consumer",
    "rotation_authority",
}
_SOURCE_FIELDS = {
    "id",
    "source_module",
    "source_qualname",
    "selector",
    "sensitivity",
    "material_class",
    "custody_mode",
    "storage_owner",
    "runtime_consumer",
    "rotation_authority",
    "key_purpose",
}
_MANAGED_MATERIAL_FIELDS = _SOURCE_FIELDS.difference({"selector", "sensitivity"})


class SecretInventoryError(RuntimeError):
    """A stable error that never reflects inventory or secret content."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, order=True)
class SourceCoordinate:
    source_module: str
    source_qualname: str
    selector: str


def inventory_path() -> Path:
    return Path(__file__).with_name(SECRET_INVENTORY_FILENAME)


def load_secret_inventory(
    path: Path | None = None,
    *,
    source_root: Path | None = None,
) -> dict[str, object]:
    """Load and validate the packaged inventory against active source code."""

    selected = inventory_path() if path is None else path
    try:
        raw = selected.read_bytes()
    except OSError:
        raise SecretInventoryError("secret_inventory_unavailable") from None
    if not raw or len(raw) > _MAX_INVENTORY_BYTES:
        raise SecretInventoryError("secret_inventory_size_invalid")
    try:
        decoded = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise SecretInventoryError("secret_inventory_json_invalid") from None
    try:
        value = json.loads(
            decoded,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_json_constant,
        )
    except SecretInventoryError:
        raise
    except (TypeError, ValueError, json.JSONDecodeError, RecursionError):
        raise SecretInventoryError("secret_inventory_json_invalid") from None
    if not isinstance(value, dict):
        raise SecretInventoryError("secret_inventory_shape_invalid")
    root = Path(__file__).resolve().parents[1] if source_root is None else source_root
    validate_secret_inventory(value, source_root=root)
    return value


def secret_inventory_sha256(path: Path | None = None) -> str:
    selected = inventory_path() if path is None else path
    try:
        raw = selected.read_bytes()
    except OSError:
        raise SecretInventoryError("secret_inventory_unavailable") from None
    return hashlib.sha256(raw).hexdigest()


def validate_secret_inventory(value: Mapping[str, Any], *, source_root: Path) -> None:
    """Validate strict metadata and exact source coverage without reading secrets."""

    _exact_keys(value, _TOP_LEVEL_FIELDS, "secret_inventory_shape_invalid")
    if value.get("schema_id") != SECRET_INVENTORY_SCHEMA_ID:
        raise SecretInventoryError("secret_inventory_schema_unsupported")
    if value.get("schema_version") != SECRET_INVENTORY_SCHEMA_VERSION:
        raise SecretInventoryError("secret_inventory_schema_unsupported")
    if value.get("scope") != "active_core":
        raise SecretInventoryError("secret_inventory_scope_invalid")
    if value.get("content_boundary") != SECRET_INVENTORY_CONTENT_BOUNDARY:
        raise SecretInventoryError("secret_inventory_content_boundary_invalid")

    purposes = _object_list(value, "key_purposes")
    purpose_statuses = _validate_purposes(purposes)
    environment_entries = _object_list(value, "environment_reads")
    ambient_entries = _object_list(value, "ambient_credential_reads")
    managed_materials = _object_list(value, "managed_materials")

    seen_ids: set[str] = set()
    environment_coordinates = _validate_sources(
        environment_entries,
        seen_ids=seen_ids,
        require_selector=True,
        purpose_statuses=purpose_statuses,
    )
    ambient_coordinates = _validate_sources(
        ambient_entries,
        seen_ids=seen_ids,
        require_selector=True,
        purpose_statuses=purpose_statuses,
    )
    _validate_managed_materials(
        managed_materials,
        seen_ids=seen_ids,
        purpose_statuses=purpose_statuses,
        source_root=source_root,
    )

    discovered_environment = discover_environment_reads(source_root)
    if environment_coordinates != discovered_environment:
        raise SecretInventoryError("secret_inventory_environment_read_mismatch")
    discovered_ambient = discover_ambient_credential_reads(source_root)
    if ambient_coordinates != discovered_ambient:
        raise SecretInventoryError("secret_inventory_ambient_read_mismatch")

    active_purposes = {purpose for purpose, status in purpose_statuses.items() if status == "active"}
    managed_purposes = {
        entry.get("key_purpose")
        for entry in managed_materials
        if isinstance(entry.get("key_purpose"), str)
    }
    if active_purposes.difference(managed_purposes):
        raise SecretInventoryError("secret_inventory_active_purpose_unmapped")


def discover_environment_reads(source_root: Path) -> tuple[SourceCoordinate, ...]:
    """Return every direct environment read in the active Hormuz package."""

    coordinates: list[SourceCoordinate] = []
    for module_path in _python_modules(source_root):
        visitor = _EnvironmentReadVisitor(module_path.relative_to(source_root).as_posix())
        visitor.visit(_parse_module(module_path))
        coordinates.extend(visitor.coordinates)
    return tuple(sorted(coordinates))


def discover_ambient_credential_reads(source_root: Path) -> tuple[SourceCoordinate, ...]:
    """Return AWS SDK ambient-credential entrypoints used by the core."""

    coordinates: list[SourceCoordinate] = []
    for module_path in _python_modules(source_root):
        visitor = _NamedCallVisitor(
            module_path.relative_to(source_root).as_posix(),
            function_name="_aws_session",
            selector="aws_sdk_ambient_chain",
        )
        visitor.visit(_parse_module(module_path))
        coordinates.extend(visitor.coordinates)
    return tuple(sorted(coordinates))


class _ScopedVisitor(ast.NodeVisitor):
    def __init__(self, source_module: str) -> None:
        self.source_module = source_module
        self.scope: list[str] = []

    @property
    def qualname(self) -> str:
        return ".".join(self.scope) or "<module>"

    def visit_ClassDef(self, node: ast.ClassDef) -> None:  # noqa: N802
        self.scope.append(node.name)
        self.generic_visit(node)
        self.scope.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # noqa: N802
        self.scope.append(node.name)
        self.generic_visit(node)
        self.scope.pop()

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:  # noqa: N802
        self.scope.append(node.name)
        self.generic_visit(node)
        self.scope.pop()


class _EnvironmentReadVisitor(_ScopedVisitor):
    def __init__(self, source_module: str) -> None:
        super().__init__(source_module)
        self.coordinates: list[SourceCoordinate] = []

    def visit_Call(self, node: ast.Call) -> None:  # noqa: N802
        selector = _environment_call_selector(node)
        if selector is not None:
            self.coordinates.append(SourceCoordinate(self.source_module, self.qualname, selector))
        self.generic_visit(node)

    def visit_Subscript(self, node: ast.Subscript) -> None:  # noqa: N802
        if _is_environment_value(node.value):
            self.coordinates.append(
                SourceCoordinate(
                    self.source_module,
                    self.qualname,
                    _selector(node.slice),
                )
            )
        self.generic_visit(node)


class _NamedCallVisitor(_ScopedVisitor):
    def __init__(self, source_module: str, *, function_name: str, selector: str) -> None:
        super().__init__(source_module)
        self.function_name = function_name
        self.selector = selector
        self.coordinates: list[SourceCoordinate] = []

    def visit_Call(self, node: ast.Call) -> None:  # noqa: N802
        if isinstance(node.func, ast.Name) and node.func.id == self.function_name:
            self.coordinates.append(SourceCoordinate(self.source_module, self.qualname, self.selector))
        self.generic_visit(node)


def _environment_call_selector(node: ast.Call) -> str | None:
    function = node.func
    if (
        isinstance(function, ast.Attribute)
        and function.attr in {"get", "pop", "setdefault"}
        and _is_environment_value(function.value)
    ):
        if not node.args:
            raise SecretInventoryError("secret_inventory_source_scan_invalid")
        return _selector(node.args[0])
    if (
        isinstance(function, ast.Attribute)
        and function.attr in {"copy", "items", "keys", "popitem", "values"}
        and _is_environment_value(function.value)
    ):
        return "all_environment_values"
    if (
        isinstance(function, ast.Attribute)
        and isinstance(function.value, ast.Name)
        and function.value.id == "os"
        and function.attr == "getenv"
    ):
        if not node.args:
            raise SecretInventoryError("secret_inventory_source_scan_invalid")
        return _selector(node.args[0])
    if (
        isinstance(function, ast.Name)
        and function.id in {"dict", "list", "set", "tuple"}
        and node.args
        and _is_environment_value(node.args[0])
    ):
        return "all_environment_values"
    return None


def _is_environment_value(node: ast.AST) -> bool:
    if isinstance(node, ast.Name):
        return _looks_like_environment_name(node.id)
    if isinstance(node, ast.Attribute):
        if isinstance(node.value, ast.Name) and node.value.id == "os" and node.attr == "environ":
            return True
        return (
            isinstance(node.value, ast.Name)
            and node.value.id == "self"
            and _looks_like_environment_name(node.attr)
        )
    return False


def _looks_like_environment_name(value: str) -> bool:
    normalized = value.lower().lstrip("_")
    return normalized in {"env", "environ", "environment"} or normalized.endswith(
        ("_env", "_environ", "_environment")
    )


def _selector(node: ast.AST) -> str:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        value = f"literal:{node.value}"
    else:
        try:
            value = ast.unparse(node)
        except Exception:
            raise SecretInventoryError("secret_inventory_source_scan_invalid") from None
    if _SAFE_SELECTOR.fullmatch(value) is None:
        raise SecretInventoryError("secret_inventory_source_selector_invalid")
    return value


def _validate_purposes(entries: list[Mapping[str, Any]]) -> dict[str, str]:
    statuses: dict[str, str] = {}
    for entry in entries:
        _exact_keys(entry, _PURPOSE_FIELDS, "secret_inventory_purpose_shape_invalid")
        purpose = entry.get("key_purpose")
        if not isinstance(purpose, str) or purpose not in KEY_PURPOSES or purpose in statuses:
            raise SecretInventoryError("secret_inventory_key_purpose_invalid")
        status = entry.get("status")
        if status not in _PURPOSE_STATUSES:
            raise SecretInventoryError("secret_inventory_purpose_status_invalid")
        _enum(entry, "material_class", _MATERIAL_CLASSES, "secret_inventory_material_class_invalid")
        _enum(entry, "runtime_consumer", _RUNTIME_CONSUMERS, "secret_inventory_consumer_invalid")
        _enum(entry, "rotation_authority", _ROTATION_AUTHORITIES, "secret_inventory_rotation_authority_invalid")
        if status == "reserved" and entry.get("runtime_consumer") != "not_in_active_core":
            raise SecretInventoryError("secret_inventory_reserved_purpose_active")
        statuses[purpose] = status
    if set(statuses) != KEY_PURPOSES:
        raise SecretInventoryError("secret_inventory_key_purpose_invalid")
    return statuses


def _validate_sources(
    entries: list[Mapping[str, Any]],
    *,
    seen_ids: set[str],
    require_selector: bool,
    purpose_statuses: Mapping[str, str],
) -> tuple[SourceCoordinate, ...]:
    coordinates: set[SourceCoordinate] = set()
    expected_fields = _SOURCE_FIELDS if require_selector else _MANAGED_MATERIAL_FIELDS
    for entry in entries:
        _exact_keys(entry, expected_fields, "secret_inventory_entry_shape_invalid")
        _validate_id(entry, seen_ids)
        coordinate = _coordinate(entry, require_selector=require_selector)
        if coordinate in coordinates:
            raise SecretInventoryError("secret_inventory_source_duplicate")
        coordinates.add(coordinate)
        sensitivity = _enum(entry, "sensitivity", _SENSITIVITIES, "secret_inventory_sensitivity_invalid")
        material_class = _enum(
            entry,
            "material_class",
            _MATERIAL_CLASSES,
            "secret_inventory_material_class_invalid",
        )
        custody_mode = _enum(entry, "custody_mode", _CUSTODY_MODES, "secret_inventory_custody_mode_invalid")
        if custody_mode == "private_invitation_handoff":
            raise SecretInventoryError("secret_inventory_secret_custody_invalid")
        _enum(entry, "storage_owner", _STORAGE_OWNERS, "secret_inventory_storage_owner_invalid")
        _enum(entry, "runtime_consumer", _RUNTIME_CONSUMERS, "secret_inventory_consumer_invalid")
        _enum(entry, "rotation_authority", _ROTATION_AUTHORITIES, "secret_inventory_rotation_authority_invalid")
        purpose = _optional_purpose(entry.get("key_purpose"))
        if purpose is not None and purpose_statuses.get(purpose) != "active":
            raise SecretInventoryError("secret_inventory_source_purpose_inactive")
        if sensitivity == "non_secret" and (
            material_class != "configuration_locator" or custody_mode != "plain_metadata"
        ):
            raise SecretInventoryError("secret_inventory_non_secret_classification_invalid")
        if sensitivity == "secret" and custody_mode == "plain_metadata":
            raise SecretInventoryError("secret_inventory_secret_custody_invalid")
    return tuple(sorted(coordinates))


def _validate_managed_materials(
    entries: list[Mapping[str, Any]],
    *,
    seen_ids: set[str],
    purpose_statuses: Mapping[str, str],
    source_root: Path,
) -> None:
    for entry in entries:
        _exact_keys(entry, _MANAGED_MATERIAL_FIELDS, "secret_inventory_entry_shape_invalid")
        _validate_id(entry, seen_ids)
        coordinate = _coordinate(entry, require_selector=False)
        if not _qualname_exists(source_root, coordinate.source_module, coordinate.source_qualname):
            raise SecretInventoryError("secret_inventory_managed_source_missing")
        _enum(entry, "material_class", _MATERIAL_CLASSES, "secret_inventory_material_class_invalid")
        mode = _enum(entry, "custody_mode", _CUSTODY_MODES, "secret_inventory_custody_mode_invalid")
        local_session_mode = mode in {"session_flow_aead", "keyed_hash", "os_secure_store", "private_invitation_handoff"}
        if mode == "private_invitation_handoff" and (
            coordinate.source_module != "hormuz/commands/onboarding.py"
            or coordinate.source_qualname != "_write_invitation"
            or entry.get("storage_owner") != "customer_filesystem"
            or entry.get("runtime_consumer") != "team_operator_cli"
            or entry.get("rotation_authority") != "identity_operator"
        ):
            raise SecretInventoryError("secret_inventory_managed_custody_invalid")
        if local_session_mode and (
            entry.get("key_purpose") != "session_material" or entry.get("material_class") != "session_material"
        ):
            raise SecretInventoryError("secret_inventory_managed_custody_invalid")
        if not local_session_mode and mode not in {"hormuz_encrypted_envelope", "external_service_encryption"}:
            raise SecretInventoryError("secret_inventory_managed_custody_invalid")
        _enum(entry, "storage_owner", _STORAGE_OWNERS, "secret_inventory_storage_owner_invalid")
        _enum(entry, "runtime_consumer", _RUNTIME_CONSUMERS, "secret_inventory_consumer_invalid")
        _enum(entry, "rotation_authority", _ROTATION_AUTHORITIES, "secret_inventory_rotation_authority_invalid")
        purpose = _optional_purpose(entry.get("key_purpose"))
        if purpose is None or purpose_statuses.get(purpose) != "active":
            raise SecretInventoryError("secret_inventory_managed_purpose_invalid")


def _coordinate(entry: Mapping[str, Any], *, require_selector: bool) -> SourceCoordinate:
    module = entry.get("source_module")
    qualname = entry.get("source_qualname")
    selector = entry.get("selector") if require_selector else "managed_material"
    if not isinstance(module, str) or _SAFE_MODULE.fullmatch(module) is None:
        raise SecretInventoryError("secret_inventory_source_module_invalid")
    if not isinstance(qualname, str) or _SAFE_QUALNAME.fullmatch(qualname) is None:
        raise SecretInventoryError("secret_inventory_source_qualname_invalid")
    if not isinstance(selector, str) or _SAFE_SELECTOR.fullmatch(selector) is None:
        raise SecretInventoryError("secret_inventory_source_selector_invalid")
    return SourceCoordinate(module, qualname, selector)


def _qualname_exists(source_root: Path, module: str, qualname: str) -> bool:
    path = source_root / module
    try:
        tree = _parse_module(path)
    except SecretInventoryError:
        return False
    visitor = _DefinitionVisitor()
    visitor.visit(tree)
    return qualname in visitor.qualnames


class _DefinitionVisitor(_ScopedVisitor):
    def __init__(self) -> None:
        super().__init__("")
        self.qualnames: set[str] = set()

    def visit_ClassDef(self, node: ast.ClassDef) -> None:  # noqa: N802
        self.scope.append(node.name)
        self.qualnames.add(self.qualname)
        self.generic_visit(node)
        self.scope.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # noqa: N802
        self.scope.append(node.name)
        self.qualnames.add(self.qualname)
        self.generic_visit(node)
        self.scope.pop()

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:  # noqa: N802
        self.scope.append(node.name)
        self.qualnames.add(self.qualname)
        self.generic_visit(node)
        self.scope.pop()


def _parse_module(path: Path) -> ast.Module:
    try:
        source = path.read_text(encoding="utf-8")
        return ast.parse(source, filename=path.as_posix())
    except (OSError, UnicodeDecodeError, SyntaxError):
        raise SecretInventoryError("secret_inventory_source_scan_invalid") from None


def _python_modules(source_root: Path) -> tuple[Path, ...]:
    package = source_root / "hormuz"
    if not package.is_dir():
        raise SecretInventoryError("secret_inventory_source_root_invalid")
    return tuple(sorted(path for path in package.rglob("*.py") if "__pycache__" not in path.parts))


def _validate_id(entry: Mapping[str, Any], seen_ids: set[str]) -> None:
    identifier = entry.get("id")
    if not isinstance(identifier, str) or _SAFE_ID.fullmatch(identifier) is None:
        raise SecretInventoryError("secret_inventory_id_invalid")
    if identifier in seen_ids:
        raise SecretInventoryError("secret_inventory_duplicate_id")
    seen_ids.add(identifier)


def _optional_purpose(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or value not in KEY_PURPOSES:
        raise SecretInventoryError("secret_inventory_key_purpose_invalid")
    return value


def _enum(entry: Mapping[str, Any], field: str, allowed: frozenset[str], code: str) -> str:
    value = entry.get(field)
    if not isinstance(value, str) or value not in allowed:
        raise SecretInventoryError(code)
    return value


def _object_list(value: Mapping[str, Any], field: str) -> list[Mapping[str, Any]]:
    items = value.get(field)
    if not isinstance(items, list) or not items or any(not isinstance(item, Mapping) for item in items):
        raise SecretInventoryError("secret_inventory_shape_invalid")
    return list(items)


def _exact_keys(value: Mapping[str, Any], expected: set[str], code: str) -> None:
    if set(value) != expected:
        raise SecretInventoryError(code)


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise SecretInventoryError("secret_inventory_json_duplicate_key")
        result[key] = value
    return result


def _reject_json_constant(_: str) -> None:
    raise SecretInventoryError("secret_inventory_json_invalid")
