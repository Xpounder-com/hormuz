"""Versioned PostgreSQL schema support for Hormuz durable evidence.

The PostgreSQL adapter is optional. Runtime processes use a restricted role;
schema migration uses a separate operator credential. Both paths fail closed
when the schema is missing, partial, or newer than the running binary.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from importlib import resources
import re
from typing import Any, Iterator


POSTGRES_SCHEMA_VERSION = 2
_IDENTIFIER_PATTERN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")


class PostgresStorageError(RuntimeError):
    """A stable, content-free PostgreSQL storage failure."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class PostgresSchemaStatus:
    version: int
    complete: bool


def validate_postgres_identifier(value: str, field: str) -> str:
    if not isinstance(value, str) or _IDENTIFIER_PATTERN.fullmatch(value) is None:
        raise PostgresStorageError(f"{field}_invalid")
    return value


def migrate_postgres(
    dsn: str,
    *,
    schema: str = "hormuz",
    runtime_role: str = "hormuz_runtime",
    policy_control_role: str = "hormuz_policy_control",
) -> PostgresSchemaStatus:
    """Apply all bundled PostgreSQL migrations atomically and idempotently."""

    schema = validate_postgres_identifier(schema, "postgres_schema")
    runtime_role = validate_postgres_identifier(runtime_role, "postgres_runtime_role")
    policy_control_role = validate_postgres_identifier(policy_control_role, "postgres_policy_control_role")
    quoted_schema = _quote_identifier(schema)
    quoted_role = _quote_identifier(runtime_role)
    quoted_policy_control_role = _quote_identifier(policy_control_role)
    psycopg, sql = _driver()
    try:
        connection = psycopg.connect(dsn)
    except psycopg.Error as error:
        raise _storage_error(error) from None
    try:
        with connection.transaction():
            with connection.cursor() as cursor:
                cursor.execute(sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(sql.Identifier(schema)))
                cursor.execute(
                    sql.SQL(
                        """
                        CREATE TABLE IF NOT EXISTS {}.hormuz_schema_migrations (
                            version INTEGER PRIMARY KEY,
                            state TEXT NOT NULL,
                            applied_at TIMESTAMPTZ
                        )
                        """
                    ).format(sql.Identifier(schema))
                )
                cursor.execute(
                    sql.SQL(
                        "SELECT version, state FROM {}.hormuz_schema_migrations ORDER BY version"
                    ).format(sql.Identifier(schema))
                )
                rows = cursor.fetchall()
                states = {int(version): str(state) for version, state in rows}
                if any(state != "applied" for state in states.values()):
                    raise PostgresStorageError("storage_schema_partial_upgrade")
                if states and max(states) > POSTGRES_SCHEMA_VERSION:
                    raise PostgresStorageError("storage_schema_newer_than_binary")
                for version in range(1, POSTGRES_SCHEMA_VERSION + 1):
                    if version in states:
                        continue
                    cursor.execute(
                        sql.SQL(
                            "INSERT INTO {}.hormuz_schema_migrations (version, state) VALUES (%s, 'applying')"
                        ).format(sql.Identifier(schema)),
                        (version,),
                    )
                    cursor.execute(
                        _migration_sql(
                            version,
                            quoted_schema,
                            quoted_role,
                            quoted_policy_control_role,
                        )
                    )
                    cursor.execute(
                        sql.SQL(
                            """
                            UPDATE {}.hormuz_schema_migrations
                            SET state = 'applied', applied_at = CURRENT_TIMESTAMP
                            WHERE version = %s
                            """
                        ).format(sql.Identifier(schema)),
                        (version,),
                    )
        return PostgresSchemaStatus(version=POSTGRES_SCHEMA_VERSION, complete=True)
    except PostgresStorageError:
        raise
    except psycopg.Error as error:
        raise _storage_error(error) from None
    finally:
        connection.close()


def verify_postgres_schema(
    dsn: str,
    *,
    schema: str = "hormuz",
    runtime_role: str = "hormuz_runtime",
) -> PostgresSchemaStatus:
    """Verify a runtime credential sees the complete supported schema only."""

    schema = validate_postgres_identifier(schema, "postgres_schema")
    runtime_role = validate_postgres_identifier(runtime_role, "postgres_runtime_role")
    psycopg, sql = _driver()
    try:
        connection = psycopg.connect(dsn)
    except psycopg.Error as error:
        raise _storage_error(error) from None
    try:
        with connection.transaction():
            with connection.cursor() as cursor:
                cursor.execute(sql.SQL("SET LOCAL ROLE {}").format(sql.Identifier(runtime_role)))
                cursor.execute(
                    sql.SQL(
                        "SELECT version, state FROM {}.hormuz_schema_migrations ORDER BY version"
                    ).format(sql.Identifier(schema))
                )
                rows = cursor.fetchall()
        states = {int(version): str(state) for version, state in rows}
        if any(state != "applied" for state in states.values()):
            raise PostgresStorageError("storage_schema_partial_upgrade")
        if not states:
            raise PostgresStorageError("storage_schema_unavailable")
        maximum = max(states)
        if maximum > POSTGRES_SCHEMA_VERSION:
            raise PostgresStorageError("storage_schema_newer_than_binary")
        if maximum != POSTGRES_SCHEMA_VERSION:
            raise PostgresStorageError("storage_schema_unavailable")
        return PostgresSchemaStatus(version=maximum, complete=True)
    except PostgresStorageError:
        raise
    except psycopg.Error as error:
        raise _storage_error(error) from None
    finally:
        connection.close()


@contextmanager
def postgres_transaction(
    dsn: str,
    *,
    schema: str,
    runtime_role: str,
    organization_id: str,
) -> Iterator[Any]:
    """Open a transaction with a restricted role and transaction-local tenant key."""

    schema = validate_postgres_identifier(schema, "postgres_schema")
    runtime_role = validate_postgres_identifier(runtime_role, "postgres_runtime_role")
    if not isinstance(organization_id, str) or not organization_id:
        raise PostgresStorageError("storage_organization_invalid")
    psycopg, sql = _driver()
    try:
        connection = psycopg.connect(dsn, row_factory=psycopg.rows.dict_row)
    except psycopg.Error as error:
        raise _storage_error(error) from None
    try:
        with connection.transaction():
            with connection.cursor() as cursor:
                cursor.execute(sql.SQL("SET LOCAL ROLE {}").format(sql.Identifier(runtime_role)))
                cursor.execute(
                    sql.SQL("SET LOCAL search_path TO {}, pg_catalog").format(sql.Identifier(schema))
                )
                cursor.execute(
                    "SELECT set_config('hormuz.organization_id', %s, true)",
                    (organization_id,),
                )
                row = cursor.fetchone()
                if not row or next(iter(row.values())) != organization_id:
                    raise PostgresStorageError("storage_organization_context_unavailable")
            yield connection
    except PostgresStorageError:
        raise
    except psycopg.Error as error:
        raise _storage_error(error) from None
    finally:
        connection.close()


def _migration_sql(
    version: int,
    quoted_schema: str,
    quoted_runtime_role: str,
    quoted_policy_control_role: str,
) -> str:
    filenames = {
        1: "0001_usage_evidence.sql",
        2: "0002_policy_control.sql",
    }
    filename = filenames.get(version)
    if filename is None:
        raise PostgresStorageError("storage_schema_migration_unsupported")
    try:
        template = (
            resources.files("hormuz.migrations.postgresql")
            .joinpath(filename)
            .read_text(encoding="utf-8")
        )
    except (FileNotFoundError, ModuleNotFoundError):
        raise PostgresStorageError("storage_schema_migration_unavailable") from None
    return template.format(
        schema=quoted_schema,
        runtime_role=quoted_runtime_role,
        policy_control_role=quoted_policy_control_role,
    )


def _driver() -> tuple[Any, Any]:
    try:
        import psycopg
        from psycopg import sql
    except ImportError:
        raise PostgresStorageError("postgres_driver_unavailable") from None
    return psycopg, sql


def _quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _storage_error(error: BaseException) -> PostgresStorageError:
    sqlstate = getattr(error, "sqlstate", None)
    if sqlstate in {"3F000", "42P01"}:
        return PostgresStorageError("storage_schema_unavailable")
    if sqlstate in {"42501", "0LP01"}:
        return PostgresStorageError("storage_access_denied")
    return PostgresStorageError("storage_unavailable")
