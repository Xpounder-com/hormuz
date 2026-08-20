"""Versioned PostgreSQL tenancy foundation and fail-closed RLS helpers.

The gateway still uses SQLite repositories for its local alpha execution path.
This module establishes the accepted hosted PostgreSQL security boundary without
silently claiming that the usage, session, or context repositories have already
been ported.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
from importlib import resources
import os
import re
from typing import Any, Callable, Iterator, Protocol


POSTGRES_SCHEMA_VERSION = 2
DEFAULT_POSTGRES_DSN_ENV = "HORMUZ_POSTGRES_DSN"
DEFAULT_POSTGRES_SCHEMA = "hormuz"
DEFAULT_POSTGRES_RUNTIME_ROLE = "hormuz_runtime"

_IDENTIFIER = re.compile(r"[a-z_][a-z0-9_]{0,62}\Z")
_TENANT_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
_MIGRATION_NAME = re.compile(r"([0-9]{4})_([a-z0-9_]+)\.sql\Z")
_EXPECTED_POLICY_EXPRESSION = (
    "(tenant_id=NULLIF(current_setting('hormuz.tenant_id'::text,true),''::text))"
)
_EXPECTED_TRIGGER_SOURCE = (
    "BEGIN IF NEW.tenant_id IS DISTINCT FROM OLD.tenant_id THEN "
    "RAISE EXCEPTION 'tenant_id is immutable' USING ERRCODE = '23514'; "
    "END IF; RETURN NEW; END;"
)

TENANT_TABLES = (
    "tenants",
    "workspaces",
    "projects",
    "teams",
    "principals",
    "external_identities",
    "roles",
    "role_capabilities",
    "team_memberships",
    "gateway_usage_events",
    "gateway_budget_reservations",
    "gateway_admin_access_events",
    "gateway_provider_cost_imports",
    "gateway_provider_cost_items",
    "gateway_provider_cost_sources",
)

RUNTIME_READ_ONLY_TABLES = ("tenants",)
RUNTIME_MUTABLE_TABLES = tuple(
    table for table in TENANT_TABLES if table not in RUNTIME_READ_ONLY_TABLES
)

ACCOUNTING_TABLE_COLUMNS = {
    "gateway_usage_events": (
        "tenant_id", "id", "occurred_at", "actor_id", "actor_name", "team_id",
        "team_name", "client", "protocol", "requested_model", "resolved_alias",
        "upstream_model", "actual_model", "policy_action", "status", "input_tokens",
        "output_tokens", "cache_read_tokens", "cache_write_tokens", "reasoning_tokens",
        "billable_tokens", "cost_microusd", "cost_basis", "currency",
        "rate_card_version", "provider_usage_json", "provider_request_id",
        "redaction_count", "redaction_rules", "context_injection_mode",
        "context_injection_outcome", "context_injection_reason", "context_pack_id",
        "context_record_ids_json", "context_policy_version",
        "context_retrieval_version", "context_render_version",
        "context_repository_revision", "context_estimated_tokens",
        "context_assembly_milliseconds", "context_reuse_status",
        "gateway_latency_milliseconds", "policy_latency_milliseconds",
        "provider_latency_milliseconds",
    ),
    "gateway_budget_reservations": (
        "tenant_id", "id", "created_at", "expires_at", "actor_id", "team_id",
        "model_alias", "reserved_tokens", "reserved_cost_microusd",
    ),
    "gateway_admin_access_events": (
        "tenant_id", "id", "occurred_at", "decision_actor_id",
        "decision_actor_name", "action", "group_by", "actor_filter_sha256",
        "team_filter_sha256", "window_start", "window_end", "result_count",
    ),
    "gateway_provider_cost_imports": (
        "tenant_id", "id", "imported_at", "provider", "source_sha256",
        "report_start", "report_end", "page_count", "bucket_count", "item_count",
    ),
    "gateway_provider_cost_items": (
        "tenant_id", "id", "import_id", "item_ordinal", "bucket_start",
        "bucket_end", "amount_usd", "currency", "provider_scope_kind",
        "provider_scope_id", "line_item", "cost_type", "model", "service_tier",
        "token_type", "context_window", "inference_geo",
    ),
    "gateway_provider_cost_sources": (
        "tenant_id", "id", "import_id", "observed_at", "source_kind",
        "api_contract", "query_start", "query_end", "query_scope",
    ),
}


class PostgresStorageError(RuntimeError):
    """Content-free PostgreSQL setup or verification failure."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class _Cursor(Protocol):
    def execute(self, query: str, params: object | None = None) -> Any: ...

    def fetchone(self) -> object: ...

    def fetchall(self) -> list[object]: ...

    def __enter__(self) -> "_Cursor": ...

    def __exit__(self, *args: object) -> None: ...


class _Connection(Protocol):
    def cursor(self) -> _Cursor: ...

    def transaction(self) -> Any: ...

    def close(self) -> None: ...


Connect = Callable[..., _Connection]


@dataclass(frozen=True)
class PostgresMigration:
    version: int
    name: str
    sha256: str
    sql: str


@dataclass(frozen=True)
class PostgresFoundationStatus:
    schema: str
    runtime_role: str
    target_version: int
    applied_versions: tuple[int, ...]
    verified: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.schema,
            "runtime_role": self.runtime_role,
            "target_version": self.target_version,
            "applied_versions": list(self.applied_versions),
            "verified": self.verified,
        }


@dataclass(frozen=True)
class TenantContext:
    """Authenticated application scope bound to one database transaction."""

    tenant_id: str
    principal_id: str
    client_id: str
    authorization_version: int

    def __post_init__(self) -> None:
        validate_tenant_id(self.tenant_id)
        _validate_scope_value(self.principal_id, "principal_id")
        _validate_scope_value(self.client_id, "client_id")
        if (
            isinstance(self.authorization_version, bool)
            or not isinstance(self.authorization_version, int)
            or not 1 <= self.authorization_version <= 2**63 - 1
        ):
            raise PostgresStorageError("invalid_authorization_version")


def validate_postgres_identifier(value: str, field: str) -> str:
    if not isinstance(value, str) or _IDENTIFIER.fullmatch(value) is None:
        raise PostgresStorageError(f"invalid_{field}")
    return value


def validate_tenant_id(value: str) -> str:
    if not isinstance(value, str) or _TENANT_ID.fullmatch(value) is None:
        raise PostgresStorageError("invalid_tenant_id")
    return value


def _validate_scope_value(value: str, field: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value.encode("utf-8")) > 512
        or any(ord(character) < 0x20 or ord(character) == 0x7F for character in value)
    ):
        raise PostgresStorageError(f"invalid_{field}")
    return value


def postgres_dsn_from_env(
    environ: dict[str, str] | None = None,
    *,
    dsn_env: str = DEFAULT_POSTGRES_DSN_ENV,
) -> str:
    environment = os.environ if environ is None else environ
    if not isinstance(dsn_env, str) or re.fullmatch(r"[A-Z][A-Z0-9_]{0,127}", dsn_env) is None:
        raise PostgresStorageError("invalid_dsn_environment")
    value = environment.get(dsn_env)
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value.encode("utf-8")) > 8192
        or any(character in value for character in ("\n", "\r", "\x00"))
    ):
        raise PostgresStorageError("postgres_dsn_unavailable")
    return value


def load_postgres_migrations() -> tuple[PostgresMigration, ...]:
    root = resources.files("hormuz.migrations.postgresql")
    migrations: list[PostgresMigration] = []
    for item in sorted(root.iterdir(), key=lambda candidate: candidate.name):
        match = _MIGRATION_NAME.fullmatch(item.name)
        if match is None:
            continue
        raw = item.read_bytes()
        if not raw or len(raw) > 1024 * 1024:
            raise PostgresStorageError("migration_source_invalid")
        try:
            sql = raw.decode("utf-8")
        except UnicodeDecodeError:
            raise PostgresStorageError("migration_source_invalid") from None
        migrations.append(
            PostgresMigration(
                version=int(match.group(1)),
                name=match.group(2),
                sha256=hashlib.sha256(raw).hexdigest(),
                sql=sql,
            )
        )
    versions = [migration.version for migration in migrations]
    if versions != list(range(1, len(migrations) + 1)):
        raise PostgresStorageError("migration_sequence_invalid")
    if not migrations or migrations[-1].version != POSTGRES_SCHEMA_VERSION:
        raise PostgresStorageError("migration_target_mismatch")
    return tuple(migrations)


def _quote_identifier(value: str) -> str:
    return '"' + value + '"'


def _driver_connect() -> Connect:
    try:
        import psycopg  # type: ignore[import-not-found]
    except ImportError:
        raise PostgresStorageError("postgres_driver_unavailable") from None
    return psycopg.connect


def _open_connection(dsn: str, connect: Connect | None) -> _Connection:
    connector = _driver_connect() if connect is None else connect
    try:
        return connector(dsn, autocommit=False, connect_timeout=10)
    except PostgresStorageError:
        raise
    except Exception:
        raise PostgresStorageError("postgres_connection_failed") from None


def _bootstrap_migration_ledger(cursor: _Cursor, schema: str) -> None:
    quoted_schema = _quote_identifier(schema)
    cursor.execute(f"CREATE SCHEMA IF NOT EXISTS {quoted_schema}")
    cursor.execute(
        "SELECT pg_get_userbyid(nspowner), current_user "
        "FROM pg_namespace WHERE nspname = %s",
        (schema,),
    )
    row = cursor.fetchone()
    if not isinstance(row, (tuple, list)) or len(row) != 2 or row[0] != row[1]:
        raise PostgresStorageError("migration_role_not_schema_owner")
    cursor.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {quoted_schema}.schema_migrations (
          version integer PRIMARY KEY CHECK (version > 0),
          name text NOT NULL,
          sha256 char(64) NOT NULL,
          applied_at timestamptz NOT NULL DEFAULT statement_timestamp()
        )
        """
    )
    cursor.execute(
        f"REVOKE ALL ON TABLE {quoted_schema}.schema_migrations FROM PUBLIC"
    )


def _validate_runtime_role(cursor: _Cursor, runtime_role: str) -> None:
    cursor.execute(
        "SELECT rolsuper, rolcreatedb, rolcreaterole, rolbypassrls, current_user "
        "FROM pg_roles WHERE rolname = %s",
        (runtime_role,),
    )
    row = cursor.fetchone()
    if not isinstance(row, (tuple, list)) or len(row) != 5:
        raise PostgresStorageError("runtime_role_unavailable")
    if any(bool(value) for value in row[:4]):
        raise PostgresStorageError("runtime_role_privileged")
    if row[4] == runtime_role:
        raise PostgresStorageError("migration_role_is_runtime_role")
    cursor.execute(
        "SELECT count(*) FROM pg_auth_members membership "
        "JOIN pg_roles member ON member.oid = membership.member "
        "WHERE member.rolname = %s",
        (runtime_role,),
    )
    membership_row = cursor.fetchone()
    if (
        not isinstance(membership_row, (tuple, list))
        or len(membership_row) != 1
        or int(membership_row[0]) != 0
    ):
        raise PostgresStorageError("runtime_role_has_memberships")


def _applied_migrations(cursor: _Cursor, schema: str) -> dict[int, tuple[str, str]]:
    cursor.execute(
        f"SELECT version, name, sha256 FROM {_quote_identifier(schema)}.schema_migrations "
        "ORDER BY version"
    )
    rows = cursor.fetchall()
    result: dict[int, tuple[str, str]] = {}
    for row in rows:
        if not isinstance(row, (tuple, list)) or len(row) != 3:
            raise PostgresStorageError("migration_ledger_invalid")
        version = int(row[0])
        if version in result:
            raise PostgresStorageError("migration_ledger_invalid")
        result[version] = (str(row[1]), str(row[2]))
    return result


def _verify_migration_prefix(
    applied: dict[int, tuple[str, str]],
    migrations: tuple[PostgresMigration, ...],
) -> None:
    expected = {migration.version: migration for migration in migrations}
    if sorted(applied) != list(range(1, len(applied) + 1)):
        raise PostgresStorageError("migration_ledger_gap")
    for version, (name, sha256) in applied.items():
        migration = expected.get(version)
        if migration is None:
            raise PostgresStorageError("schema_newer_than_binary")
        if migration.name != name or migration.sha256 != sha256:
            raise PostgresStorageError("migration_checksum_mismatch")


def _grant_runtime_access(cursor: _Cursor, schema: str, runtime_role: str) -> None:
    quoted_schema = _quote_identifier(schema)
    quoted_role = _quote_identifier(runtime_role)
    cursor.execute(f"REVOKE CREATE ON SCHEMA {quoted_schema} FROM PUBLIC")
    cursor.execute(f"REVOKE ALL ON SCHEMA {quoted_schema} FROM {quoted_role}")
    cursor.execute(f"GRANT USAGE ON SCHEMA {quoted_schema} TO {quoted_role}")
    cursor.execute(
        f"GRANT SELECT ON TABLE {quoted_schema}.tenants TO {quoted_role}"
    )
    mutable = ", ".join(
        f"{quoted_schema}.{_quote_identifier(table)}" for table in RUNTIME_MUTABLE_TABLES
    )
    cursor.execute(
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE {mutable} TO {quoted_role}"
    )
    cursor.execute(
        f"REVOKE ALL ON TABLE {quoted_schema}.schema_migrations FROM {quoted_role}"
    )


def _verify_foundation(cursor: _Cursor, schema: str, runtime_role: str) -> None:
    quoted_schema = _quote_identifier(schema)
    cursor.execute("SELECT current_user")
    current_user_row = cursor.fetchone()
    if not isinstance(current_user_row, (tuple, list)) or len(current_user_row) != 1:
        raise PostgresStorageError("migration_role_unavailable")
    migration_role = current_user_row[0]
    cursor.execute(
        "SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity, "
        "pg_get_userbyid(c.relowner) "
        "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname = %s AND c.relkind = 'r' AND c.relname = ANY(%s) "
        "ORDER BY c.relname",
        (schema, list(TENANT_TABLES)),
    )
    rows = cursor.fetchall()
    observed = {str(row[0]): row for row in rows}
    if set(observed) != set(TENANT_TABLES):
        raise PostgresStorageError("tenant_table_set_invalid")
    for row in observed.values():
        if not bool(row[1]) or not bool(row[2]):
            raise PostgresStorageError("tenant_rls_not_forced")
        if row[3] != migration_role:
            raise PostgresStorageError("migration_role_does_not_own_tenant_table")

    cursor.execute(
        "SELECT table_name, column_name FROM information_schema.columns "
        "WHERE table_schema = %s AND table_name = ANY(%s) "
        "ORDER BY table_name, ordinal_position",
        (schema, list(ACCOUNTING_TABLE_COLUMNS)),
    )
    accounting_columns: dict[str, list[str]] = {
        table: [] for table in ACCOUNTING_TABLE_COLUMNS
    }
    for row in cursor.fetchall():
        if not isinstance(row, (tuple, list)) or len(row) != 2:
            raise PostgresStorageError("accounting_table_columns_invalid")
        table = str(row[0])
        if table in accounting_columns:
            accounting_columns[table].append(str(row[1]))
    if any(
        tuple(accounting_columns[table]) != expected
        for table, expected in ACCOUNTING_TABLE_COLUMNS.items()
    ):
        raise PostgresStorageError("accounting_table_columns_invalid")

    cursor.execute(
        "SELECT tablename, policyname, qual, with_check FROM pg_policies "
        "WHERE schemaname = %s",
        (schema,),
    )
    policies = {
        str(row[0]): row
        for row in cursor.fetchall()
        if len(row) == 4 and str(row[1]) == "tenant_isolation"
    }
    if set(policies) != set(TENANT_TABLES):
        raise PostgresStorageError("tenant_policy_set_invalid")
    for row in policies.values():
        if row[2] is None or row[3] is None:
            raise PostgresStorageError("tenant_policy_not_bidirectional")
        normalized_qual = re.sub(r"\s+", "", str(row[2]))
        normalized_check = re.sub(r"\s+", "", str(row[3]))
        if (
            normalized_qual != _EXPECTED_POLICY_EXPRESSION
            or normalized_check != _EXPECTED_POLICY_EXPRESSION
        ):
            raise PostgresStorageError("tenant_policy_definition_invalid")

    cursor.execute(
        "SELECT p.prosrc, p.prosecdef, p.proconfig, pg_get_userbyid(p.proowner), p.oid "
        "FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace "
        "WHERE n.nspname = %s AND p.proname = 'reject_tenant_id_change'",
        (schema,),
    )
    function_rows = cursor.fetchall()
    if len(function_rows) != 1:
        raise PostgresStorageError("tenant_immutability_function_invalid")
    function = function_rows[0]
    if (
        re.sub(r"\s+", " ", str(function[0]).strip()) != _EXPECTED_TRIGGER_SOURCE
        or bool(function[1])
        or list(function[2] or ()) != ["search_path=pg_catalog"]
        or function[3] != migration_role
    ):
        raise PostgresStorageError("tenant_immutability_function_invalid")
    function_oid = int(function[4])

    cursor.execute(
        "SELECT c.relname, t.tgfoid FROM pg_trigger t "
        "JOIN pg_class c ON c.oid = t.tgrelid "
        "JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname = %s AND t.tgname = 'tenant_id_immutable' "
        "AND NOT t.tgisinternal AND t.tgenabled <> 'D'",
        (schema,),
    )
    trigger_rows = cursor.fetchall()
    triggered = {str(row[0]) for row in trigger_rows}
    if triggered != set(TENANT_TABLES) or any(int(row[1]) != function_oid for row in trigger_rows):
        raise PostgresStorageError("tenant_immutability_trigger_invalid")

    cursor.execute(
        "SELECT has_schema_privilege(%s, %s, 'USAGE'), "
        "has_schema_privilege(%s, %s, 'CREATE')",
        (runtime_role, schema, runtime_role, schema),
    )
    schema_privileges = cursor.fetchone()
    if schema_privileges != (True, False):
        raise PostgresStorageError("runtime_schema_privileges_invalid")

    cursor.execute(
        "SELECT c.relname, "
        "has_table_privilege(%s, c.oid, 'SELECT'), "
        "has_table_privilege(%s, c.oid, 'INSERT'), "
        "has_table_privilege(%s, c.oid, 'UPDATE'), "
        "has_table_privilege(%s, c.oid, 'DELETE'), "
        "has_table_privilege(%s, c.oid, 'TRUNCATE'), "
        "has_table_privilege(%s, c.oid, 'REFERENCES'), "
        "has_table_privilege(%s, c.oid, 'TRIGGER') "
        "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname = %s AND c.relname = ANY(%s)",
        (
            runtime_role,
            runtime_role,
            runtime_role,
            runtime_role,
            runtime_role,
            runtime_role,
            runtime_role,
            schema,
            list(TENANT_TABLES),
        ),
    )
    privileges = {str(row[0]): tuple(bool(value) for value in row[1:]) for row in cursor.fetchall()}
    if set(privileges) != set(TENANT_TABLES):
        raise PostgresStorageError("runtime_table_privileges_invalid")
    for table, values in privileges.items():
        expected_dml = (
            (True, False, False, False)
            if table == "tenants"
            else (True, True, True, True)
        )
        if values[:4] != expected_dml or values[4:] != (False, False, False):
            raise PostgresStorageError("runtime_table_privileges_invalid")

    cursor.execute(
        f"SELECT has_table_privilege(%s, '{quoted_schema}.schema_migrations', 'SELECT')",
        (runtime_role,),
    )
    if cursor.fetchone() != (False,):
        raise PostgresStorageError("runtime_migration_ledger_visible")


def migrate_postgres(
    dsn: str,
    *,
    schema: str = DEFAULT_POSTGRES_SCHEMA,
    runtime_role: str = DEFAULT_POSTGRES_RUNTIME_ROLE,
    connect: Connect | None = None,
) -> PostgresFoundationStatus:
    schema = validate_postgres_identifier(schema, "postgres_schema")
    runtime_role = validate_postgres_identifier(runtime_role, "runtime_role")
    migrations = load_postgres_migrations()
    connection = _open_connection(dsn, connect)
    try:
        with connection.transaction():
            with connection.cursor() as cursor:
                cursor.execute("SET LOCAL lock_timeout = '10s'")
                cursor.execute("SET LOCAL statement_timeout = '60s'")
                cursor.execute("SET LOCAL idle_in_transaction_session_timeout = '60s'")
                cursor.execute(
                    "SELECT pg_try_advisory_xact_lock(hashtextextended(%s, 0))",
                    ("hormuz:migrations:" + schema,),
                )
                if cursor.fetchone() != (True,):
                    raise PostgresStorageError("migration_lock_unavailable")
                _validate_runtime_role(cursor, runtime_role)
                _bootstrap_migration_ledger(cursor, schema)
                applied = _applied_migrations(cursor, schema)
                _verify_migration_prefix(applied, migrations)
                quoted_schema = _quote_identifier(schema)
                cursor.execute(f"SET LOCAL search_path TO {quoted_schema}, pg_catalog")
                for migration in migrations:
                    if migration.version in applied:
                        continue
                    cursor.execute(migration.sql)
                    cursor.execute(
                        f"INSERT INTO {quoted_schema}.schema_migrations "
                        "(version, name, sha256) VALUES (%s, %s, %s)",
                        (migration.version, migration.name, migration.sha256),
                    )
                    applied[migration.version] = (migration.name, migration.sha256)
                _grant_runtime_access(cursor, schema, runtime_role)
                _verify_foundation(cursor, schema, runtime_role)
        return PostgresFoundationStatus(
            schema=schema,
            runtime_role=runtime_role,
            target_version=POSTGRES_SCHEMA_VERSION,
            applied_versions=tuple(sorted(applied)),
            verified=True,
        )
    except PostgresStorageError:
        raise
    except Exception:
        raise PostgresStorageError("postgres_migration_failed") from None
    finally:
        connection.close()


def verify_postgres(
    dsn: str,
    *,
    schema: str = DEFAULT_POSTGRES_SCHEMA,
    runtime_role: str = DEFAULT_POSTGRES_RUNTIME_ROLE,
    connect: Connect | None = None,
) -> PostgresFoundationStatus:
    schema = validate_postgres_identifier(schema, "postgres_schema")
    runtime_role = validate_postgres_identifier(runtime_role, "runtime_role")
    migrations = load_postgres_migrations()
    connection = _open_connection(dsn, connect)
    try:
        with connection.transaction():
            with connection.cursor() as cursor:
                cursor.execute("SET LOCAL statement_timeout = '60s'")
                cursor.execute("SET LOCAL idle_in_transaction_session_timeout = '60s'")
                _validate_runtime_role(cursor, runtime_role)
                cursor.execute(
                    "SELECT pg_get_userbyid(nspowner), current_user "
                    "FROM pg_namespace WHERE nspname = %s",
                    (schema,),
                )
                row = cursor.fetchone()
                if not isinstance(row, (tuple, list)) or len(row) != 2 or row[0] != row[1]:
                    raise PostgresStorageError("migration_role_not_schema_owner")
                applied = _applied_migrations(cursor, schema)
                _verify_migration_prefix(applied, migrations)
                if len(applied) != len(migrations):
                    raise PostgresStorageError("schema_behind_binary")
                _verify_foundation(cursor, schema, runtime_role)
        return PostgresFoundationStatus(
            schema=schema,
            runtime_role=runtime_role,
            target_version=POSTGRES_SCHEMA_VERSION,
            applied_versions=tuple(sorted(applied)),
            verified=True,
        )
    except PostgresStorageError:
        raise
    except Exception:
        raise PostgresStorageError("postgres_verification_failed") from None
    finally:
        connection.close()


@contextmanager
def tenant_transaction(
    connection: _Connection,
    context: TenantContext,
    *,
    runtime_role: str = DEFAULT_POSTGRES_RUNTIME_ROLE,
) -> Iterator[_Connection]:
    """Bind a verified tenant only for the lifetime of one database transaction."""

    if not isinstance(context, TenantContext):
        raise PostgresStorageError("invalid_tenant_context")
    runtime_role = validate_postgres_identifier(runtime_role, "runtime_role")
    try:
        with connection.transaction():
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT "
                    "set_config('hormuz.tenant_id', %s, true), "
                    "set_config('hormuz.principal_id', %s, true), "
                    "set_config('hormuz.client_id', %s, true), "
                    "set_config('hormuz.authorization_version', %s, true), "
                    "current_user, rolsuper, rolbypassrls "
                    "FROM pg_roles WHERE rolname = current_user",
                    (
                        context.tenant_id,
                        context.principal_id,
                        context.client_id,
                        str(context.authorization_version),
                    ),
                )
                if cursor.fetchone() != (
                    context.tenant_id,
                    context.principal_id,
                    context.client_id,
                    str(context.authorization_version),
                    runtime_role,
                    False,
                    False,
                ):
                    raise PostgresStorageError("runtime_connection_role_invalid")
            yield connection
    except PostgresStorageError:
        raise
    except Exception as error:
        code = {
            "42501": "tenant_policy_denied",
            "23503": "tenant_foreign_key_denied",
            "23514": "tenant_immutability_denied",
        }.get(getattr(error, "sqlstate", None), "tenant_transaction_failed")
        raise PostgresStorageError(code) from None


def migrate_postgres_from_env(
    *,
    environ: dict[str, str] | None = None,
    dsn_env: str = DEFAULT_POSTGRES_DSN_ENV,
    schema: str = DEFAULT_POSTGRES_SCHEMA,
    runtime_role: str = DEFAULT_POSTGRES_RUNTIME_ROLE,
) -> PostgresFoundationStatus:
    return migrate_postgres(
        postgres_dsn_from_env(environ, dsn_env=dsn_env),
        schema=schema,
        runtime_role=runtime_role,
    )


def verify_postgres_from_env(
    *,
    environ: dict[str, str] | None = None,
    dsn_env: str = DEFAULT_POSTGRES_DSN_ENV,
    schema: str = DEFAULT_POSTGRES_SCHEMA,
    runtime_role: str = DEFAULT_POSTGRES_RUNTIME_ROLE,
) -> PostgresFoundationStatus:
    return verify_postgres(
        postgres_dsn_from_env(environ, dsn_env=dsn_env),
        schema=schema,
        runtime_role=runtime_role,
    )
