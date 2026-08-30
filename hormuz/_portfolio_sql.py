"""Low-level portfolio SQL/transaction mechanics, with no domain authority.

Owners must authorize before entering this boundary. They borrow the existing
pool and share the organization lock so scope changes and new feature commits
cannot race. This module never reads environment variables or owns a pool.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator

from ._portfolio_schema import TABLE_DDL, verify_postgres_registry
from ._attribution_schema import TABLE_DDL as ATTRIBUTION_TABLES, verify_postgres_attribution
from ._sqlite_schema import SQLITE_SCHEMA_VERSION, verify_sqlite_schema_ready
from .config import GatewayConfig
from .portfolio_wire import PortfolioError
from .postgres import PostgresConnectionPool, PostgresStorageError, postgres_transaction
from .store import StorageSchemaError


class PortfolioSQL:
    def __init__(self, connection, *, postgres: bool, tables=TABLE_DDL):
        self.connection = connection
        self.postgres = postgres
        self.tables = tables

    def execute(self, statement: str, values: tuple = ()):
        return self.connection.execute(statement.replace("?", "%s") if self.postgres else statement, values)

    def one(self, statement: str, values: tuple = ()) -> dict[str, Any] | None:
        row = self.execute(statement, values).fetchone()
        return dict(row) if row is not None else None

    def insert(self, table: str, row: dict[str, Any]) -> None:
        # Callers choose fields and tables from source constants, never input.
        if table not in self.tables:
            raise PortfolioError("unavailable")
        self.execute(
            f"INSERT INTO {table} ({', '.join(row)}) VALUES ({', '.join('?' for _ in row)})",
            tuple(row.values()),
        )

    def now(self) -> str:
        statement = "SELECT clock_timestamp() AS now" if self.postgres else "SELECT strftime('%Y-%m-%dT%H:%M:%fZ', 'now') AS now"
        value = self.one(statement)["now"]
        instant = value if isinstance(value, datetime) else datetime.fromisoformat(value)
        return instant.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


@contextmanager
def portfolio_transaction(
    config: GatewayConfig, organization_id: str, *, dsn: str,
    connection_pool: PostgresConnectionPool | None,
    tables=TABLE_DDL,
) -> Iterator[PortfolioSQL]:
    storage = config.usage_storage
    try:
        if storage.backend == "sqlite":
            connection = sqlite3.connect(
                f"{config.database_path.resolve().as_uri()}?mode=rw", uri=True, timeout=5,
            )
            connection.row_factory = sqlite3.Row
            try:
                connection.execute("PRAGMA foreign_keys = ON")
                with connection:
                    connection.execute("BEGIN IMMEDIATE")
                    verify_sqlite_schema_ready(
                        connection, schema_version=SQLITE_SCHEMA_VERSION,
                        maximum_supported_schema_version=SQLITE_SCHEMA_VERSION, error_factory=StorageSchemaError,
                    )
                    yield PortfolioSQL(connection, postgres=False, tables=tables)
            finally:
                connection.close()
        elif storage.backend == "postgresql":
            with postgres_transaction(
                dsn, schema=storage.postgres_schema, runtime_role=storage.postgres_runtime_role,
                organization_id=organization_id, connection_pool=connection_pool,
            ) as connection:
                connection.execute("SET LOCAL lock_timeout = '5s'")
                connection.execute("SET LOCAL statement_timeout = '10s'")
                role = connection.execute(
                    "SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname=current_user"
                ).fetchone()
                if not role or role["rolsuper"] or role["rolbypassrls"]:
                    raise PortfolioError("unavailable")
                with connection.cursor() as cursor:
                    verify_postgres_registry(cursor, storage.postgres_schema, PostgresStorageError)
                    if tables is ATTRIBUTION_TABLES:
                        verify_postgres_attribution(cursor, storage.postgres_schema, PostgresStorageError)
                for table in tables:
                    row = connection.execute(
                        "SELECT (has_table_privilege(current_user, %s, 'SELECT') AND "
                        "has_table_privilege(current_user, %s, 'INSERT')) AS usable, "
                        "has_table_privilege(current_user, %s, 'UPDATE,DELETE,TRUNCATE') AS excessive",
                        (f'"{storage.postgres_schema}".{table}',) * 3,
                    ).fetchone()
                    if not row["usable"] or row["excessive"]:
                        raise PortfolioError("unavailable")
                connection.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                    (f"portfolio:{storage.postgres_schema}:{organization_id}",),
                )
                yield PortfolioSQL(connection, postgres=True, tables=tables)
        else:
            raise PortfolioError("unavailable")
    except (sqlite3.Error, PostgresStorageError, StorageSchemaError):
        raise PortfolioError("unavailable") from None
