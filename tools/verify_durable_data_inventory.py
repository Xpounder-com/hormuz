#!/usr/bin/env python3
"""Verify Hormuz's complete self-hosted durable-data inventory."""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from pathlib import Path


SCHEMA_ID = "hormuz.durable-data-inventory"
SCHEMA_VERSION = 1
INVENTORY_PATH = Path("docs/durable-data-v1.json")
DOCUMENT_PATH = Path("docs/DURABLE_DATA.md")
SQLITE_SCHEMA_PATH = Path("hormuz/_sqlite_schema.py")
SESSION_SCHEMA_PATH = Path("hormuz/session_store.py")
POSTGRES_RUNTIME_PATH = Path("hormuz/postgres.py")
POSTGRES_MIGRATION_PATH = Path("hormuz/migrations/postgresql")
CONTENT_BOUNDARIES = {
    "operational_metadata",
    "metadata_only_evidence",
    "identity_and_policy_metadata",
    "custody_authorization_metadata",
    "custody_lifecycle_metadata",
    "session_identity_and_encrypted_transient_auth",
}
EXPECTED_ARTIFACT_IDS = {
    "audit_chain_checkpoint",
    "audit_export_jsonl",
    "encrypted_custody_envelope",
    "object_lock_audit_artifact",
    "postgresql_schema",
    "public_release_artifacts",
    "sqlite_database_file",
    "session_database_file",
    "client_session_secure_store",
    "team_invitation_file",
    "console_browser_cookies",
    "hosted_profile_file",
    "hosted_backup_key_file",
    "hosted_offsite_backup_archive",
    "hosted_state_marker",
    "hosted_state_snapshot",
}
EXPECTED_EXCLUDED_SYSTEM_IDS = {
    "client_local_history",
    "database_backups_wal_and_snapshots",
    "deployment_logs_metrics_and_traces",
    "identity_provider_data",
    "kms_keys_and_policy",
    "object_lock_retained_versions",
    "provider_platform_data",
}
ARTIFACT_REQUEST_CONTENT_BOUNDARIES = {
    "excluded_by_hormuz_contract",
    "encrypted_operator_supplied_plaintext_not_inspected",
    "not_customer_runtime_data",
}
EXPECTED_RESPONSIBILITIES = ["backup", "deletion", "export", "restore", "retention"]
REQUIRED_SOURCE_ENTRIES = {
    "include docs/durable-data-v1.json",
    "include tools/verify_durable_data_inventory.py",
}
SQLITE_TABLE = re.compile(
    r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?([a-z][a-z0-9_]*)",
    re.I,
)
POSTGRES_TABLE = re.compile(
    r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?"
    r"(?:\{\}|\{schema\})?\.?([a-z][a-z0-9_]*)",
    re.I,
)


class DurableDataInventoryError(ValueError):
    """The durable-data registry is incomplete or unsafe."""


def _read_text(path: Path) -> str:
    try:
        value = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise DurableDataInventoryError(f"cannot read required file: {path}") from exc
    if not value.strip():
        raise DurableDataInventoryError(f"required file is empty: {path}")
    return value


def _object_no_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise DurableDataInventoryError("durable_data_inventory_duplicate_json_member")
        value[key] = item
    return value


def _read_json(path: Path) -> dict[str, object]:
    try:
        value = json.loads(_read_text(path), object_pairs_hook=_object_no_duplicates)
    except json.JSONDecodeError as exc:
        raise DurableDataInventoryError("durable_data_inventory_invalid_json") from exc
    if not isinstance(value, dict):
        raise DurableDataInventoryError("durable_data_inventory_root_invalid")
    return value


def _require_fields(value: dict[str, object], expected: set[str], label: str) -> None:
    if set(value) != expected:
        raise DurableDataInventoryError(f"{label}_fields_invalid")


def _unique_sorted_strings(value: object, label: str) -> list[str]:
    if (
        not isinstance(value, list)
        or any(not isinstance(item, str) or not item for item in value)
        or len(value) != len(set(value))
        or value != sorted(value)
    ):
        raise DurableDataInventoryError(f"{label}_invalid")
    return value


def _schema_tables(root: Path) -> tuple[set[str], set[str]]:
    sqlite = set(SQLITE_TABLE.findall(_read_text(root / SQLITE_SCHEMA_PATH)))
    # The new separate repository owns a literal table declaration catalogue.
    # Inspect literals without importing/executing a checkout under verification.
    for path in (
        "hormuz/_portfolio_schema.py",
        "hormuz/_attribution_schema.py",
        "hormuz/_outcome_schema.py",
        "hormuz/_finance_schema.py",
        "hormuz/_budget_schema.py",
        "hormuz/_provider_reliability_schema.py",
    ):
        registry = ast.parse(_read_text(root / path))
        declaration_name = (
            "APPEND_ONLY_TABLE_DDL"
            if path == "hormuz/_budget_schema.py"
            else "TABLE_DDL"
        )
        declarations = next((node.value for node in registry.body if isinstance(node, ast.Assign)
                             and any(isinstance(target, ast.Name) and target.id == declaration_name for target in node.targets)), None)
        if declarations is None:
            raise DurableDataInventoryError("registry_schema_tables_missing")
        try:
            registry_tables = ast.literal_eval(declarations)
        except (ValueError, TypeError):
            raise DurableDataInventoryError("registry_schema_tables_invalid") from None
        if not isinstance(registry_tables, dict):
            raise DurableDataInventoryError("registry_schema_tables_invalid")
        if path == "hormuz/_budget_schema.py":
            active = next(
                (
                    node.value
                    for node in registry.body
                    if isinstance(node, ast.Assign)
                    and any(
                        isinstance(target, ast.Name)
                        and target.id == "ACTIVE_TABLE"
                        for target in node.targets
                    )
                ),
                None,
            )
            try:
                active_table = ast.literal_eval(active)
            except (ValueError, TypeError):
                raise DurableDataInventoryError(
                    "registry_schema_tables_invalid"
                ) from None
            if not isinstance(active_table, str):
                raise DurableDataInventoryError("registry_schema_tables_invalid")
            registry_tables[active_table] = "active_pointer"
        table_pattern = (
            r"gateway_provider_[a-z_]+"
            if path == "hormuz/_provider_reliability_schema.py"
            else r"portfolio_[a-z_]+"
        )
        if not all(
            isinstance(name, str) and re.fullmatch(table_pattern, name)
            for name in registry_tables
        ):
            raise DurableDataInventoryError("registry_schema_tables_invalid")
        if sqlite.intersection(registry_tables):
            raise DurableDataInventoryError("sqlite_table_owned_more_than_once")
        sqlite.update(registry_tables)
    finance_attempt_registry = ast.parse(
        _read_text(root / "hormuz/_finance_attempt_schema.py")
    )
    finance_attempt_declaration = next(
        (
            node.value
            for node in finance_attempt_registry.body
            if isinstance(node, ast.Assign)
            and any(
                isinstance(target, ast.Name) and target.id == "SQLITE_FINANCE_TABLE"
                for target in node.targets
            )
        ),
        None,
    )
    try:
        finance_attempt_sql = ast.literal_eval(finance_attempt_declaration)
    except (ValueError, TypeError):
        raise DurableDataInventoryError("registry_schema_tables_invalid") from None
    finance_attempt_tables = set(SQLITE_TABLE.findall(finance_attempt_sql))
    if finance_attempt_tables != {"gateway_finance_attempt_evidence"}:
        raise DurableDataInventoryError("registry_schema_tables_invalid")
    if sqlite.intersection(finance_attempt_tables):
        raise DurableDataInventoryError("sqlite_table_owned_more_than_once")
    sqlite.update(finance_attempt_tables)
    session_tables = set(SQLITE_TABLE.findall(_read_text(root / SESSION_SCHEMA_PATH)))
    for path in ("hormuz/_onboarding_schema.py", "hormuz/_console_schema.py"):
        additions = set(SQLITE_TABLE.findall(_read_text(root / path)))
        if session_tables.intersection(additions):
            raise DurableDataInventoryError("sqlite_table_owned_more_than_once")
        session_tables.update(additions)
    if sqlite.intersection(session_tables):
        raise DurableDataInventoryError("sqlite_table_owned_more_than_once")
    sqlite.update(session_tables)
    postgres_sources = [_read_text(root / POSTGRES_RUNTIME_PATH)]
    migration_root = root / POSTGRES_MIGRATION_PATH
    try:
        migrations = sorted(migration_root.glob("*.sql"))
    except OSError as exc:
        raise DurableDataInventoryError("postgres_migrations_unavailable") from exc
    if not migrations:
        raise DurableDataInventoryError("postgres_migrations_unavailable")
    postgres_sources.extend(_read_text(path) for path in migrations)
    postgres = {
        table
        for source in postgres_sources
        for table in POSTGRES_TABLE.findall(source)
    }
    if not sqlite or not postgres:
        raise DurableDataInventoryError("durable_schema_tables_missing")
    return sqlite, postgres


def _validate_database_classes(
    value: object, expected_sqlite: set[str], expected_postgres: set[str]
) -> tuple[int, int, int, list[str]]:
    if not isinstance(value, list) or not value:
        raise DurableDataInventoryError("database_classes_invalid")
    ids: list[str] = []
    sqlite_tables: list[str] = []
    postgres_tables: list[str] = []
    for index, raw in enumerate(value):
        label = f"database_classes_{index}"
        if not isinstance(raw, dict):
            raise DurableDataInventoryError(f"{label}_invalid")
        _require_fields(
            raw,
            {
                "id",
                "content_boundary",
                "contains_prompt_or_response_body",
                "sqlite_tables",
                "postgresql_tables",
            },
            label,
        )
        class_id = raw["id"]
        if not isinstance(class_id, str) or not class_id or class_id in ids:
            raise DurableDataInventoryError(f"{label}_id_invalid")
        if raw["content_boundary"] not in CONTENT_BOUNDARIES:
            raise DurableDataInventoryError(f"{label}_content_boundary_invalid")
        if raw["contains_prompt_or_response_body"] is not False:
            raise DurableDataInventoryError(f"{label}_request_content_boundary_invalid")
        ids.append(class_id)
        sqlite_tables.extend(_unique_sorted_strings(raw["sqlite_tables"], f"{label}_sqlite_tables"))
        postgres_tables.extend(
            _unique_sorted_strings(raw["postgresql_tables"], f"{label}_postgresql_tables")
        )
    if len(sqlite_tables) != len(set(sqlite_tables)):
        raise DurableDataInventoryError("sqlite_table_registered_more_than_once")
    if len(postgres_tables) != len(set(postgres_tables)):
        raise DurableDataInventoryError("postgres_table_registered_more_than_once")
    if set(sqlite_tables) != expected_sqlite:
        raise DurableDataInventoryError("sqlite_table_inventory_mismatch")
    if set(postgres_tables) != expected_postgres:
        raise DurableDataInventoryError("postgres_table_inventory_mismatch")
    return len(value), len(sqlite_tables), len(postgres_tables), ids


def _validate_artifacts(value: object) -> list[str]:
    if not isinstance(value, list) or not value:
        raise DurableDataInventoryError("operator_artifacts_invalid")
    ids: list[str] = []
    for index, raw in enumerate(value):
        label = f"operator_artifacts_{index}"
        if not isinstance(raw, dict):
            raise DurableDataInventoryError(f"{label}_invalid")
        _require_fields(
            raw,
            {
                "id",
                "created_when",
                "storage_location",
                "content_boundary",
                "prompt_or_response_body_boundary",
                "customer_operator_authority",
            },
            label,
        )
        artifact_id = raw["id"]
        strings = (
            artifact_id,
            raw["created_when"],
            raw["storage_location"],
            raw["content_boundary"],
            raw["customer_operator_authority"],
        )
        if any(not isinstance(item, str) or not item for item in strings) or artifact_id in ids:
            raise DurableDataInventoryError(f"{label}_invalid")
        body_boundary = raw["prompt_or_response_body_boundary"]
        if body_boundary not in ARTIFACT_REQUEST_CONTENT_BOUNDARIES:
            raise DurableDataInventoryError(f"{label}_request_content_boundary_invalid")
        expected_body_boundary = (
            "encrypted_operator_supplied_plaintext_not_inspected"
            if artifact_id == "encrypted_custody_envelope"
            else "not_customer_runtime_data"
            if artifact_id == "public_release_artifacts"
            else "excluded_by_hormuz_contract"
        )
        if body_boundary != expected_body_boundary:
            raise DurableDataInventoryError(f"{label}_request_content_boundary_invalid")
        ids.append(artifact_id)
    if set(ids) != EXPECTED_ARTIFACT_IDS:
        raise DurableDataInventoryError("operator_artifact_set_invalid")
    return ids


def _validate_excluded_systems(value: object) -> list[str]:
    if not isinstance(value, list) or not value:
        raise DurableDataInventoryError("excluded_customer_systems_invalid")
    ids: list[str] = []
    for index, raw in enumerate(value):
        label = f"excluded_customer_systems_{index}"
        if not isinstance(raw, dict):
            raise DurableDataInventoryError(f"{label}_invalid")
        _require_fields(raw, {"id", "authority", "hormuz_erasure_authority"}, label)
        system_id = raw["id"]
        if (
            not isinstance(system_id, str)
            or not system_id
            or system_id in ids
            or not isinstance(raw["authority"], str)
            or not raw["authority"]
            or raw["hormuz_erasure_authority"] is not False
        ):
            raise DurableDataInventoryError(f"{label}_invalid")
        ids.append(system_id)
    if set(ids) != EXPECTED_EXCLUDED_SYSTEM_IDS:
        raise DurableDataInventoryError("excluded_customer_system_set_invalid")
    return ids


def validate_durable_data_inventory(root: Path) -> dict[str, object]:
    root = root.resolve()
    inventory = _read_json(root / INVENTORY_PATH)
    _require_fields(
        inventory,
        {
            "schema_id",
            "schema_version",
            "scope",
            "hosted_customer_data_service",
            "database_classes",
            "operator_artifacts",
            "customer_operator_responsibilities",
            "excluded_customer_systems",
            "universal_erasure_claim",
        },
        "inventory",
    )
    if inventory["schema_id"] != SCHEMA_ID or inventory["schema_version"] != SCHEMA_VERSION:
        raise DurableDataInventoryError("durable_data_inventory_schema_invalid")
    if inventory["scope"] != "self_hosted_public_alpha":
        raise DurableDataInventoryError("durable_data_inventory_scope_invalid")
    if inventory["hosted_customer_data_service"] is not False:
        raise DurableDataInventoryError("hosted_customer_data_service_claim_invalid")
    if inventory["universal_erasure_claim"] is not False:
        raise DurableDataInventoryError("universal_erasure_claim_invalid")
    responsibilities = _unique_sorted_strings(
        inventory["customer_operator_responsibilities"],
        "customer_operator_responsibilities",
    )
    if responsibilities != EXPECTED_RESPONSIBILITIES:
        raise DurableDataInventoryError("customer_operator_responsibilities_invalid")

    sqlite, postgres = _schema_tables(root)
    class_count, sqlite_count, postgres_count, class_ids = _validate_database_classes(
        inventory["database_classes"], sqlite, postgres
    )
    artifact_ids = _validate_artifacts(inventory["operator_artifacts"])
    excluded_ids = _validate_excluded_systems(inventory["excluded_customer_systems"])

    document = _read_text(root / DOCUMENT_PATH)
    required_document_values = [
        *class_ids,
        *sorted(sqlite),
        *sorted(postgres),
        *artifact_ids,
        *excluded_ids,
        "does not operate a hosted customer-data service",
        "Hormuz makes no universal-erasure claim",
        "tenant_data_admin",
    ]
    for value in required_document_values:
        if value not in document:
            raise DurableDataInventoryError("durable_data_documentation_incomplete")

    manifest_lines = {
        line.strip()
        for line in _read_text(root / "MANIFEST.in").splitlines()
        if line.strip()
    }
    if not REQUIRED_SOURCE_ENTRIES.issubset(manifest_lines):
        raise DurableDataInventoryError("durable_data_source_distribution_incomplete")

    return {
        "schema_id": SCHEMA_ID,
        "schema_version": SCHEMA_VERSION,
        "status": "passed",
        "database_class_count": class_count,
        "sqlite_table_count": sqlite_count,
        "postgresql_table_count": postgres_count,
        "operator_artifact_count": len(artifact_ids),
        "excluded_customer_system_count": len(excluded_ids),
        "hosted_customer_data_service": False,
        "universal_erasure_claim": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parent.parent,
        help="repository root",
    )
    args = parser.parse_args(argv)
    try:
        result = validate_durable_data_inventory(args.root)
    except DurableDataInventoryError as exc:
        print(f"durable_data_inventory_failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
