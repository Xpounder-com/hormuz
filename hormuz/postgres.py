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
from typing import Any, Iterator, Mapping

from .config import PostgresPoolConfig


POSTGRES_SCHEMA_VERSION = 14
_IDENTIFIER_PATTERN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
_POOL_RECONNECT_TIMEOUT_SECONDS = 15


class PostgresStorageError(RuntimeError):
    """A stable, content-free PostgreSQL storage failure."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class PostgresSchemaStatus:
    version: int
    complete: bool


@dataclass(frozen=True)
class PostgresDeploymentBootstrapStatus:
    """Content-free result for a least-privilege deployment bootstrap."""

    schema_version: int
    schema_complete: bool
    restricted_roles: int
    runtime_login_restricted: bool
    runtime_membership_verified: bool


def _rearm_pool_after_reconnect_failure(pool: Any) -> None:
    """Start a fresh bounded reconnect cycle without making the pool usable.

    Psycopg deliberately stops a single failed connection attempt after its
    reconnect horizon. The gateway remains fail-closed, but its long-running
    pool must keep trying in later bounded cycles so a returning database does
    not require a Hormuz process restart.
    """

    try:
        pool.check()
    except Exception:
        # The callback runs on Psycopg's maintenance worker. A later checkout
        # remains fail-closed and may request another recovery check.
        pass


class PostgresConnectionPool:
    """A bounded, checked Psycopg pool for one restricted runtime credential.

    The pool intentionally has no tenant or role state of its own. Every
    checkout is wrapped in :func:`postgres_transaction`, which applies the
    role, search path, and organization context with ``SET LOCAL`` inside a
    fresh transaction. That makes a returned connection safe to reuse for a
    different tenant after the transaction finishes.
    """

    def __init__(self, dsn: str, *, settings: PostgresPoolConfig):
        if not isinstance(dsn, str) or not dsn:
            raise PostgresStorageError("postgres_dsn_unavailable")
        _validate_pool_settings(settings)
        psycopg, _ = _driver()
        pool_module = _pool_driver()
        self._settings = settings
        self._psycopg = psycopg
        self._pool_module = pool_module
        self._closed = False
        self._pool = None
        try:
            self._pool = pool_module.ConnectionPool(
                dsn,
                kwargs={
                    "row_factory": psycopg.rows.dict_row,
                    "connect_timeout": settings.acquire_timeout_seconds,
                },
                min_size=settings.min_connections,
                max_size=settings.max_connections,
                timeout=settings.acquire_timeout_seconds,
                max_waiting=settings.max_waiting,
                max_lifetime=settings.max_lifetime_seconds,
                max_idle=settings.max_idle_seconds,
                # Foreground acquisition stays short, while this separate
                # bounded horizon gives Psycopg time to reconnect after an
                # abrupt dependency interruption. The callback re-arms a
                # later bounded cycle if PostgreSQL is still unavailable.
                reconnect_timeout=_POOL_RECONNECT_TIMEOUT_SECONDS,
                reconnect_failed=_rearm_pool_after_reconnect_failure,
                check=pool_module.ConnectionPool.check_connection,
                name="hormuz-runtime",
                num_workers=1,
                open=False,
            )
            # Startup must prove the configured minimum is available. Do not
            # leave a process nominally healthy while its durable authority is
            # absent and background retries continue indefinitely.
            self._pool.open(wait=True, timeout=settings.acquire_timeout_seconds)
        except Exception as error:
            self._close_quietly()
            raise self._storage_error(error) from None

    @property
    def settings(self) -> PostgresPoolConfig:
        return self._settings

    @property
    def closed(self) -> bool:
        return self._closed

    def operational_stats(self) -> dict[str, int | bool]:
        """Return a stable, content-free projection of bounded pool pressure.

        Psycopg's counters contain no SQL, DSN, tenant, or connection details.
        Keep the public projection explicit so a dependency upgrade cannot add
        an unexpected field to Hormuz operational evidence.
        """

        if self._closed or self._pool is None:
            return {
                "configured": True,
                "closed": True,
                "min_connections": self._settings.min_connections,
                "max_connections": self._settings.max_connections,
                "pool_size": 0,
                "available_connections": 0,
                "requests_waiting": 0,
                "requests_total": 0,
                "requests_queued_total": 0,
                "requests_error_total": 0,
                "requests_wait_milliseconds_total": 0,
            }
        try:
            raw = self._pool.get_stats()
        except Exception as error:
            raise self._storage_error(error) from None

        def counter(name: str) -> int:
            value = raw.get(name, 0)
            return value if type(value) is int and value >= 0 else 0

        return {
            "configured": True,
            "closed": False,
            "min_connections": self._settings.min_connections,
            "max_connections": self._settings.max_connections,
            "pool_size": counter("pool_size"),
            "available_connections": counter("pool_available"),
            "requests_waiting": counter("requests_waiting"),
            "requests_total": counter("requests_num"),
            "requests_queued_total": counter("requests_queued"),
            "requests_error_total": counter("requests_errors"),
            "requests_wait_milliseconds_total": counter("requests_wait_ms"),
        }

    @contextmanager
    def connection(self) -> Iterator[Any]:
        """Lease one verified connection, mapping pool failures content-free."""

        if self._closed:
            raise PostgresStorageError("storage_pool_closed")
        assert self._pool is not None
        body_error: BaseException | None = None
        try:
            with self._pool.connection(timeout=self._settings.acquire_timeout_seconds) as connection:
                try:
                    yield connection
                except BaseException as error:
                    # The transaction body owns policy and domain failures.
                    # A legitimate denial must not become a storage outage
                    # merely because it propagates through the pool context.
                    body_error = error
                    raise
        except Exception as error:
            if error is body_error or isinstance(error, PostgresStorageError):
                raise
            raise self._storage_error(error) from None

    def close(self) -> None:
        """Stop new checkouts and release pool workers without exposing DSNs."""

        if self._closed:
            return
        self._closed = True
        if self._pool is None:
            return
        try:
            self._pool.close(timeout=self._settings.acquire_timeout_seconds)
        except Exception as error:
            raise self._storage_error(error) from None

    def _close_quietly(self) -> None:
        self._closed = True
        if self._pool is None:
            return
        try:
            self._pool.close(timeout=self._settings.acquire_timeout_seconds)
        except Exception:
            pass

    def _storage_error(self, error: BaseException) -> PostgresStorageError:
        if isinstance(error, (self._pool_module.PoolTimeout, self._pool_module.TooManyRequests)):
            return PostgresStorageError("storage_pool_exhausted")
        if isinstance(error, self._pool_module.PoolClosed):
            return PostgresStorageError("storage_pool_closed")
        if isinstance(error, self._psycopg.Error):
            return _storage_error(error)
        return PostgresStorageError("storage_unavailable")


def validate_postgres_identifier(value: str, field: str) -> str:
    if not isinstance(value, str) or _IDENTIFIER_PATTERN.fullmatch(value) is None:
        raise PostgresStorageError(f"{field}_invalid")
    return value


def bootstrap_postgres_deployment(
    migration_dsn: str,
    runtime_dsn: str,
    *,
    schema: str = "hormuz",
    runtime_role: str = "hormuz_runtime",
    policy_control_role: str = "hormuz_policy_control",
    custody_control_role: str = "hormuz_custody_control",
    custody_executor_role: str = "hormuz_custody_executor",
) -> PostgresDeploymentBootstrapStatus:
    """Prepare and verify a separate owner/login boundary for one deployment.

    The migration credential must own schema objects.  The runtime DSN must
    identify a different, pre-existing managed login.  Hormuz never creates or
    receives that login's password: it creates stable NOLOGIN authorization
    roles, removes inheritance from the managed login, grants only the runtime
    role, applies the schema as the owner, and finally proves that the runtime
    credential can use the schema only by explicitly assuming that role.

    Safe, already-complete work is accepted so an interrupted maintenance run
    can be repeated.  Unexpected roles, memberships, ownership, or elevated
    login attributes fail closed instead of being repaired silently.
    """

    schema = validate_postgres_identifier(schema, "postgres_schema")
    role_names = (
        validate_postgres_identifier(runtime_role, "postgres_runtime_role"),
        validate_postgres_identifier(
            policy_control_role, "postgres_policy_control_role"
        ),
        validate_postgres_identifier(
            custody_control_role, "postgres_custody_control_role"
        ),
        validate_postgres_identifier(
            custody_executor_role, "postgres_custody_executor_role"
        ),
    )
    if len(set(role_names)) != len(role_names):
        raise PostgresStorageError("postgres_bootstrap_roles_not_distinct")
    if (
        not isinstance(migration_dsn, str)
        or not migration_dsn
        or not isinstance(runtime_dsn, str)
        or not runtime_dsn
        or migration_dsn == runtime_dsn
    ):
        raise PostgresStorageError("postgres_bootstrap_credentials_invalid")

    psycopg, sql = _driver()
    runtime_connection = None
    runtime_identity = None
    try:
        runtime_connection = psycopg.connect(runtime_dsn, connect_timeout=10)
        with runtime_connection.cursor() as cursor:
            cursor.execute(
                "SELECT session_user, current_user, current_database()"
            )
            runtime_identity = cursor.fetchone()
    except psycopg.Error as error:
        raise _storage_error(error) from None
    finally:
        if runtime_connection is not None:
            runtime_connection.close()
    if not runtime_identity or len(runtime_identity) != 3:
        raise PostgresStorageError("postgres_bootstrap_runtime_identity_invalid")
    runtime_login, runtime_effective_role, runtime_database = map(
        str, runtime_identity
    )
    if (
        runtime_login != runtime_effective_role
        or _IDENTIFIER_PATTERN.fullmatch(runtime_login) is None
        or runtime_login in role_names
        or not runtime_database
    ):
        raise PostgresStorageError("postgres_bootstrap_runtime_identity_invalid")

    migration_connection = None
    try:
        migration_connection = psycopg.connect(migration_dsn, connect_timeout=10)
        with migration_connection.transaction():
            with migration_connection.cursor() as cursor:
                cursor.execute(
                    "SELECT session_user, current_user, current_database()"
                )
                migration_identity = cursor.fetchone()
                if (
                    not migration_identity
                    or len(migration_identity) != 3
                    or str(migration_identity[0])
                    != str(migration_identity[1])
                    or str(migration_identity[0]) == runtime_login
                    or str(migration_identity[2]) != runtime_database
                ):
                    raise PostgresStorageError(
                        "postgres_bootstrap_credential_boundary_invalid"
                    )

                runtime_attributes = _postgres_role_attributes(cursor, runtime_login)
                if runtime_attributes is None or not runtime_attributes[0] or any(
                    runtime_attributes[index] for index in (1, 2, 3, 5, 6)
                ):
                    raise PostgresStorageError(
                        "postgres_bootstrap_runtime_role_unsafe"
                    )
                unexpected_memberships = _postgres_role_memberships(
                    cursor, runtime_login
                ) - {runtime_role}
                if unexpected_memberships or _postgres_role_member_grants(
                    cursor, runtime_login
                ):
                    raise PostgresStorageError(
                        "postgres_bootstrap_runtime_membership_unsafe"
                    )
                cursor.execute(
                    sql.SQL(
                        "ALTER ROLE {} NOSUPERUSER NOCREATEDB NOCREATEROLE "
                        "NOINHERIT NOREPLICATION NOBYPASSRLS"
                    ).format(sql.Identifier(runtime_login))
                )

                for role in role_names:
                    attributes = _postgres_role_attributes(cursor, role)
                    if attributes is None:
                        cursor.execute(
                            sql.SQL(
                                "CREATE ROLE {} NOLOGIN NOSUPERUSER NOCREATEDB "
                                "NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS"
                            ).format(sql.Identifier(role))
                        )
                    elif attributes != (False, False, False, False, False, False, False):
                        raise PostgresStorageError(
                            "postgres_bootstrap_authorization_role_unsafe"
                        )
                    if _postgres_role_memberships(cursor, role):
                        raise PostgresStorageError(
                            "postgres_bootstrap_authorization_membership_unsafe"
                        )
                    expected_members = (
                        {(runtime_login, False)} if role == runtime_role else set()
                    )
                    if _postgres_role_member_grants(cursor, role) - expected_members:
                        raise PostgresStorageError(
                            "postgres_bootstrap_authorization_membership_unsafe"
                        )
                cursor.execute(
                    sql.SQL("GRANT {} TO {}").format(
                        sql.Identifier(runtime_role), sql.Identifier(runtime_login)
                    )
                )
                cursor.execute(
                    sql.SQL("REVOKE ALL PRIVILEGES ON SCHEMA public FROM {}").format(
                        sql.Identifier(runtime_login)
                    )
                )
    except PostgresStorageError:
        raise
    except psycopg.Error as error:
        raise _storage_error(error) from None
    finally:
        if migration_connection is not None:
            migration_connection.close()

    migration = migrate_postgres(
        migration_dsn,
        schema=schema,
        runtime_role=runtime_role,
        policy_control_role=policy_control_role,
        custody_control_role=custody_control_role,
        custody_executor_role=custody_executor_role,
    )

    migration_connection = None
    try:
        migration_connection = psycopg.connect(migration_dsn, connect_timeout=10)
        with migration_connection.transaction():
            with migration_connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL("REVOKE ALL PRIVILEGES ON SCHEMA {} FROM {}").format(
                        sql.Identifier(schema), sql.Identifier(runtime_login)
                    )
                )
                cursor.execute(
                    sql.SQL(
                        "REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA {} FROM {}"
                    ).format(sql.Identifier(schema), sql.Identifier(runtime_login))
                )
                cursor.execute(
                    sql.SQL(
                        "REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA {} FROM {}"
                    ).format(sql.Identifier(schema), sql.Identifier(runtime_login))
                )
                cursor.execute(
                    sql.SQL(
                        "REVOKE ALL PRIVILEGES ON ALL FUNCTIONS IN SCHEMA {} FROM {}"
                    ).format(sql.Identifier(schema), sql.Identifier(runtime_login))
                )
                _verify_postgres_deployment_roles(
                    cursor,
                    schema=schema,
                    database=runtime_database,
                    runtime_login=runtime_login,
                    role_names=role_names,
                )
    except PostgresStorageError:
        raise
    except psycopg.Error as error:
        raise _storage_error(error) from None
    finally:
        if migration_connection is not None:
            migration_connection.close()

    verified = verify_postgres_schema(
        runtime_dsn,
        schema=schema,
        runtime_role=runtime_role,
    )
    if verified != migration:
        raise PostgresStorageError("postgres_bootstrap_schema_verification_failed")
    return PostgresDeploymentBootstrapStatus(
        schema_version=verified.version,
        schema_complete=verified.complete,
        restricted_roles=len(role_names),
        runtime_login_restricted=True,
        runtime_membership_verified=True,
    )


def _postgres_role_attributes(cursor: Any, role: str) -> tuple[bool, ...] | None:
    cursor.execute(
        """
        SELECT rolcanlogin, rolsuper, rolcreatedb, rolcreaterole, rolinherit,
               rolreplication, rolbypassrls
        FROM pg_roles
        WHERE rolname = %s
        """,
        (role,),
    )
    row = cursor.fetchone()
    return None if row is None else tuple(bool(value) for value in row)


def _postgres_role_memberships(cursor: Any, role: str) -> set[str]:
    cursor.execute(
        """
        SELECT granted.rolname
        FROM pg_auth_members AS membership
        JOIN pg_roles AS granted ON granted.oid = membership.roleid
        JOIN pg_roles AS member ON member.oid = membership.member
        WHERE member.rolname = %s
        """,
        (role,),
    )
    return {str(row[0]) for row in cursor.fetchall()}


def _postgres_role_member_grants(cursor: Any, role: str) -> set[tuple[str, bool]]:
    """Return principals that can assume a role and their admin option."""

    cursor.execute(
        """
        SELECT member.rolname, membership.admin_option
        FROM pg_auth_members AS membership
        JOIN pg_roles AS granted ON granted.oid = membership.roleid
        JOIN pg_roles AS member ON member.oid = membership.member
        WHERE granted.rolname = %s
        """,
        (role,),
    )
    return {(str(row[0]), bool(row[1])) for row in cursor.fetchall()}


def _verify_postgres_deployment_roles(
    cursor: Any,
    *,
    schema: str,
    database: str,
    runtime_login: str,
    role_names: tuple[str, ...],
) -> None:
    runtime_role = role_names[0]
    attributes = _postgres_role_attributes(cursor, runtime_login)
    if attributes != (True, False, False, False, False, False, False):
        raise PostgresStorageError("postgres_bootstrap_runtime_role_unsafe")
    if _postgres_role_memberships(cursor, runtime_login) != {runtime_role}:
        raise PostgresStorageError("postgres_bootstrap_runtime_membership_unsafe")
    if _postgres_role_member_grants(cursor, runtime_login):
        raise PostgresStorageError("postgres_bootstrap_runtime_membership_unsafe")
    for role in role_names:
        expected_members = (
            {(runtime_login, False)} if role == runtime_role else set()
        )
        if _postgres_role_attributes(cursor, role) != (
            False,
            False,
            False,
            False,
            False,
            False,
            False,
        ) or _postgres_role_memberships(cursor, role) or _postgres_role_member_grants(
            cursor, role
        ) != expected_members:
            raise PostgresStorageError(
                "postgres_bootstrap_authorization_role_unsafe"
            )
    cursor.execute(
        """
        SELECT
            pg_get_userbyid(database_owner) = %s,
            pg_get_userbyid(schema_owner) = %s,
            has_schema_privilege(%s, %s, 'CREATE'),
            EXISTS (
                SELECT 1
                FROM pg_class AS relation
                JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace
                WHERE namespace.nspname = %s
                  AND pg_get_userbyid(relation.relowner) = %s
            ),
            EXISTS (
                SELECT 1
                FROM information_schema.role_table_grants
                WHERE table_schema = %s AND grantee = %s
            )
        FROM
            (SELECT datdba AS database_owner FROM pg_database WHERE datname = %s) AS database,
            (SELECT nspowner AS schema_owner FROM pg_namespace WHERE nspname = %s) AS namespace
        """,
        (
            runtime_login,
            runtime_login,
            runtime_login,
            schema,
            schema,
            runtime_login,
            schema,
            runtime_login,
            database,
            schema,
        ),
    )
    boundary = cursor.fetchone()
    if not boundary or any(bool(value) for value in boundary):
        raise PostgresStorageError("postgres_bootstrap_ownership_boundary_invalid")


def migrate_postgres(
    dsn: str,
    *,
    schema: str = "hormuz",
    runtime_role: str = "hormuz_runtime",
    policy_control_role: str = "hormuz_policy_control",
    custody_control_role: str = "hormuz_custody_control",
    custody_executor_role: str = "hormuz_custody_executor",
) -> PostgresSchemaStatus:
    """Apply all bundled PostgreSQL migrations atomically and idempotently."""

    schema = validate_postgres_identifier(schema, "postgres_schema")
    runtime_role = validate_postgres_identifier(runtime_role, "postgres_runtime_role")
    policy_control_role = validate_postgres_identifier(policy_control_role, "postgres_policy_control_role")
    custody_control_role = validate_postgres_identifier(custody_control_role, "postgres_custody_control_role")
    custody_executor_role = validate_postgres_identifier(custody_executor_role, "postgres_custody_executor_role")
    quoted_schema = _quote_identifier(schema)
    quoted_role = _quote_identifier(runtime_role)
    quoted_policy_control_role = _quote_identifier(policy_control_role)
    quoted_custody_control_role = _quote_identifier(custody_control_role)
    quoted_custody_executor_role = _quote_identifier(custody_executor_role)
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
                if states and set(states) != set(range(1, max(states) + 1)):
                    raise PostgresStorageError("storage_schema_partial_upgrade")
                if states:
                    _verify_applied_schema_shape(cursor, schema=schema, version=max(states))
                    _verify_custody_schema_shape(cursor, schema=schema, version=max(states))
                    if max(states) >= 9:
                        from ._portfolio_schema import verify_postgres_registry
                        verify_postgres_registry(cursor, schema, PostgresStorageError)
                    if max(states) >= 10:
                        from ._attribution_schema import verify_postgres_attribution
                        verify_postgres_attribution(cursor, schema, PostgresStorageError)
                    if max(states) >= 11:
                        from ._outcome_schema import verify_postgres_outcomes
                        verify_postgres_outcomes(cursor, schema, PostgresStorageError)
                    if max(states) >= 12:
                        from ._finance_schema import verify_postgres_finance
                        verify_postgres_finance(cursor, schema, PostgresStorageError)
                    if max(states) >= 13:
                        from ._budget_schema import verify_postgres_budget
                        verify_postgres_budget(cursor, schema, PostgresStorageError)
                    if max(states) >= 14:
                        from ._provider_reliability_schema import verify_postgres_provider_reliability
                        verify_postgres_provider_reliability(cursor, schema, PostgresStorageError)
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
                            quoted_custody_control_role,
                            quoted_custody_executor_role,
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
                _verify_applied_schema_shape(cursor, schema=schema, version=POSTGRES_SCHEMA_VERSION)
                _verify_custody_schema_shape(cursor, schema=schema, version=POSTGRES_SCHEMA_VERSION)
                if POSTGRES_SCHEMA_VERSION >= 9:
                    from ._portfolio_schema import verify_postgres_registry
                    verify_postgres_registry(cursor, schema, PostgresStorageError)
                if POSTGRES_SCHEMA_VERSION >= 10:
                    from ._attribution_schema import verify_postgres_attribution
                    verify_postgres_attribution(cursor, schema, PostgresStorageError)
                if POSTGRES_SCHEMA_VERSION >= 11:
                    from ._outcome_schema import verify_postgres_outcomes
                    verify_postgres_outcomes(cursor, schema, PostgresStorageError)
                if POSTGRES_SCHEMA_VERSION >= 12:
                    from ._finance_schema import verify_postgres_finance
                    verify_postgres_finance(cursor, schema, PostgresStorageError)
                if POSTGRES_SCHEMA_VERSION >= 13:
                    from ._budget_schema import verify_postgres_budget
                    verify_postgres_budget(cursor, schema, PostgresStorageError)
                if POSTGRES_SCHEMA_VERSION >= 14:
                    from ._provider_reliability_schema import verify_postgres_provider_reliability
                    verify_postgres_provider_reliability(cursor, schema, PostgresStorageError)
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
    connection_pool: PostgresConnectionPool | None = None,
    verify_runtime_schema: bool = True,
    verify_custody_schema: bool = False,
    verify_custody_executor_schema: bool = False,
) -> PostgresSchemaStatus:
    """Verify a credential sees the complete supported migration ledger.

    A long-running gateway supplies its bounded runtime pool so startup schema
    verification does not create an out-of-band runtime connection. One-shot
    operator and compatibility callers continue to use a direct connection.

    By default, this also proves the restricted runtime credential can read
    every durable evidence object required by the applied schema.  The
    policy-control role is intentionally not authorized to read request
    evidence, so its startup check verifies the shared migration ledger only.
    """

    schema = validate_postgres_identifier(schema, "postgres_schema")
    runtime_role = validate_postgres_identifier(runtime_role, "postgres_runtime_role")
    if sum((verify_runtime_schema, verify_custody_schema, verify_custody_executor_schema)) > 1:
        raise PostgresStorageError("storage_schema_verification_scope_invalid")
    psycopg, sql = _driver()
    if connection_pool is not None:
        try:
            with connection_pool.connection() as connection:
                rows = _schema_migration_rows(
                    connection,
                    sql=sql,
                    schema=schema,
                    runtime_role=runtime_role,
                    verify_runtime_schema=verify_runtime_schema,
                    verify_custody_schema=verify_custody_schema,
                    verify_custody_executor_schema=verify_custody_executor_schema,
                )
        except PostgresStorageError:
            raise
        except psycopg.Error as error:
            raise _storage_error(error) from None
        return _verified_schema_status(rows)
    try:
        connection = psycopg.connect(dsn)
    except psycopg.Error as error:
        raise _storage_error(error) from None
    try:
        rows = _schema_migration_rows(
            connection,
            sql=sql,
            schema=schema,
            runtime_role=runtime_role,
            verify_runtime_schema=verify_runtime_schema,
            verify_custody_schema=verify_custody_schema,
            verify_custody_executor_schema=verify_custody_executor_schema,
        )
        return _verified_schema_status(rows)
    except PostgresStorageError:
        raise
    except psycopg.Error as error:
        raise _storage_error(error) from None
    finally:
        connection.close()


def _schema_migration_rows(
    connection: Any,
    *,
    sql: Any,
    schema: str,
    runtime_role: str,
    verify_runtime_schema: bool = True,
    verify_custody_schema: bool = False,
    verify_custody_executor_schema: bool = False,
) -> list[Any]:
    """Read the migration ledger under a least-privileged product role."""

    with connection.transaction():
        with connection.cursor() as cursor:
            cursor.execute(sql.SQL("SET LOCAL ROLE {}").format(sql.Identifier(runtime_role)))
            cursor.execute(
                sql.SQL(
                    "SELECT version, state FROM {}.hormuz_schema_migrations ORDER BY version"
                ).format(sql.Identifier(schema))
            )
            rows = cursor.fetchall()
            states = _schema_states(rows)
            if (
                verify_runtime_schema
                and states
                and all(state == "applied" for state in states.values())
                and max(states) <= POSTGRES_SCHEMA_VERSION
            ):
                _verify_applied_schema_shape(cursor, schema=schema, version=max(states))
                if max(states) >= 9:
                    from ._portfolio_schema import verify_postgres_registry
                    verify_postgres_registry(cursor, schema, PostgresStorageError)
                if max(states) >= 10:
                    from ._attribution_schema import verify_postgres_attribution
                    verify_postgres_attribution(cursor, schema, PostgresStorageError)
                if max(states) >= 11:
                    from ._outcome_schema import verify_postgres_outcomes
                    verify_postgres_outcomes(cursor, schema, PostgresStorageError)
                if max(states) >= 12:
                    from ._finance_schema import verify_postgres_finance
                    verify_postgres_finance(cursor, schema, PostgresStorageError)
                if max(states) >= 13:
                    from ._budget_schema import verify_postgres_budget
                    verify_postgres_budget(cursor, schema, PostgresStorageError)
                if max(states) >= 14:
                    from ._provider_reliability_schema import verify_postgres_provider_reliability
                    verify_postgres_provider_reliability(cursor, schema, PostgresStorageError)
            if (
                verify_custody_schema
                and states
                and all(state == "applied" for state in states.values())
                and max(states) <= POSTGRES_SCHEMA_VERSION
            ):
                _verify_custody_schema_shape(cursor, schema=schema, version=max(states))
            if (
                verify_custody_executor_schema
                and states
                and all(state == "applied" for state in states.values())
                and max(states) <= POSTGRES_SCHEMA_VERSION
            ):
                _verify_custody_executor_schema_shape(cursor, schema=schema, version=max(states))
            return rows


def _verified_schema_status(rows: list[Any]) -> PostgresSchemaStatus:
    states = _schema_states(rows)
    if any(state != "applied" for state in states.values()):
        raise PostgresStorageError("storage_schema_partial_upgrade")
    if not states:
        raise PostgresStorageError("storage_schema_unavailable")
    maximum = max(states)
    if maximum > POSTGRES_SCHEMA_VERSION:
        raise PostgresStorageError("storage_schema_newer_than_binary")
    if set(states) != set(range(1, maximum + 1)):
        raise PostgresStorageError("storage_schema_partial_upgrade")
    if maximum != POSTGRES_SCHEMA_VERSION:
        raise PostgresStorageError("storage_schema_unavailable")
    return PostgresSchemaStatus(version=maximum, complete=True)


def _schema_states(rows: list[Any]) -> dict[int, str]:
    states: dict[int, str] = {}
    for row in rows:
        if isinstance(row, Mapping):
            version, state = row["version"], row["state"]
        else:
            version, state = row
        states[int(version)] = str(state)
    return states


def _verify_applied_schema_shape(cursor: Any, *, schema: str, version: int) -> None:
    """Reject an applied ledger whose required durable objects are missing."""

    required = {
        "gateway_usage_events": {
            "id",
            "occurred_at",
            "evidence_schema_id",
            "evidence_schema_version",
            "organization_id",
            "actor_id",
            "actor_name",
            "team_id",
            "team_name",
            "identity_type",
            "authentication_source",
            "client",
            "protocol",
            "requested_model",
            "policy_version",
            "policy_action",
            "status",
            "input_tokens",
            "output_tokens",
            "cache_read_tokens",
            "cache_write_tokens",
            "reasoning_tokens",
            "cost_microusd",
            "cost_basis",
            "allocation_basis",
            "coverage",
            "redaction_count",
            "redaction_rules",
        },
        "gateway_secret_events": {
            "id",
            "occurred_at",
            "evidence_schema_id",
            "evidence_schema_version",
            "organization_id",
            "actor_id",
            "actor_name",
            "team_id",
            "team_name",
            "identity_type",
            "authentication_source",
            "client",
            "protocol",
            "requested_model",
            "policy_version",
            "coverage",
            "action",
            "detection_count",
            "rules",
        },
        "gateway_budget_reservations": {
            "id",
            "created_at",
            "expires_at",
            "organization_id",
            "actor_id",
            "team_id",
            "reserved_tokens",
            "reserved_cost_microusd",
        },
    }
    if version >= 3:
        required["gateway_budget_reservations"].add("attempt_id")
        required["gateway_request_attempts"] = {
            "attempt_id",
            "created_at",
            "evidence_schema_id",
            "evidence_schema_version",
            "organization_id",
            "actor_id",
            "actor_name",
            "team_id",
            "team_name",
            "identity_type",
            "authentication_source",
            "client",
            "protocol",
            "requested_model",
            "policy_version",
            "policy_action",
            "redaction_count",
            "redaction_rules",
            "reserved_tokens",
            "reserved_cost_microusd",
        }
        required["gateway_request_attempt_events"] = {
            "id",
            "attempt_id",
            "organization_id",
            "occurred_at",
            "event_schema_id",
            "event_schema_version",
            "sequence",
            "state",
            "reason_code",
            "usage_event_id",
        }
    if version >= 4:
        required["gateway_audit_chain_epochs"] = {
            "organization_id",
            "chain_version",
            "chain_epoch",
            "created_at",
            "reason_code",
            "predecessor_chain_epoch",
            "predecessor_sequence",
            "predecessor_head_digest",
        }
        required["gateway_audit_chain_heads"] = {
            "organization_id",
            "chain_version",
            "chain_epoch",
            "sequence",
            "head_digest",
        }
        required["gateway_audit_chain_entries"] = {
            "organization_id",
            "chain_version",
            "chain_epoch",
            "sequence",
            "entry_schema_id",
            "entry_schema_version",
            "event_id",
            "previous_digest",
            "event_digest",
            "event_json",
            "appended_at",
        }
        required["gateway_audit_chain_checkpoints"] = {
            "checkpoint_id",
            "organization_id",
            "chain_version",
            "chain_epoch",
            "sequence",
            "head_digest",
            "artifact_sha256",
            "anchor_backend",
            "object_version",
            "anchored_at",
        }
    if version >= 7:
        required["custody_lifecycle_asset_identities"] = {
            "organization_id",
            "asset_type",
            "asset_id",
            "generation",
            "binding_fingerprint",
            "envelope_key_asset_id",
            "envelope_key_generation",
            "envelope_key_binding_fingerprint",
            "registered_at",
        }
        required["custody_runtime_projection_heads"] = {
            "organization_id",
            "projection_schema_id",
            "projection_schema_version",
            "version",
            "committed_at",
        }
        required["custody_runtime_projection_restrictions"] = {
            "organization_id",
            "asset_type",
            "asset_id",
            "generation",
            "binding_fingerprint",
            "restriction_kind",
            "lifecycle_event_id",
            "committed_at",
        }
        required["custody_runtime_replicas"] = {
            "organization_id",
            "replica_id",
            "heartbeat_at",
            "lease_expires_at",
            "observed_projection_version",
            "retired_at",
        }
        required["custody_runtime_projection_barriers"] = {
            "organization_id",
            "barrier_id",
            "execution_id",
            "proposed_version",
            "asset_type",
            "asset_id",
            "asset_generation",
            "restriction_kind",
            "prepared_at",
            "activated_at",
            "resolved_at",
        }
        required["custody_runtime_projection_acks"] = {
            "organization_id",
            "barrier_id",
            "replica_id",
            "acknowledged_at",
        }
    if version >= 8:
        required["gateway_audit_chain_entries"].update(
            {"source_schema_id", "source_schema_version", "source_event_id"}
        )
    for table, columns in required.items():
        cursor.execute(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = %s AND table_name = %s
            """,
            (schema, table),
        )
        observed = {
            str(row["column_name"] if isinstance(row, Mapping) else row[0])
            for row in cursor.fetchall()
        }
        if not columns.issubset(observed):
            raise PostgresStorageError("storage_schema_partial_upgrade")
    if version >= 8:
        _verify_custody_evidence_schema_objects(cursor, schema=schema)


def _verify_custody_schema_shape(cursor: Any, *, schema: str, version: int) -> None:
    """Reject a current custody ledger whose dedicated objects are missing."""

    if version < 5:
        return
    required = {
        "custody_tenants": {
            "organization_id",
            "initialized_at",
            "initialized_by_kind",
            "initialized_by_identity_key",
        },
        "custody_administrators": {
            "organization_id",
            "identity_key",
            "authentication_kind",
            "actor_id",
            "issuer",
            "subject",
            "active",
            "created_at",
            "created_by_kind",
            "created_by_identity_key",
            "revoked_at",
            "revoked_by_kind",
            "revoked_by_identity_key",
        },
        "custody_operation_intents": {
            "organization_id",
            "operation_id",
            "intent_schema_id",
            "intent_schema_version",
            "operation_type",
            "risk_level",
            "target_kind",
            "target_sha256",
            "parameters_sha256",
            "protected_input_ref_sha256",
            "state",
            "required_approvals",
            "created_at",
            "expires_at",
            "authorized_at",
            "requested_by_kind",
            "requested_by_identity_key",
        },
        "custody_operation_approvals": {
            "organization_id",
            "operation_id",
            "approval_schema_id",
            "approval_schema_version",
            "approver_kind",
            "approver_identity_key",
            "approved_at",
        },
        "custody_control_events": {
            "event_id",
            "event_schema_id",
            "event_schema_version",
            "organization_id",
            "occurred_at",
            "event_type",
            "actor_kind",
            "actor_identity_key",
            "target_identity_key",
            "operation_id",
            "operation_type",
            "risk_level",
            "target_kind",
            "target_sha256",
            "parameters_sha256",
            "protected_input_ref_sha256",
            "required_approvals",
            "approval_count",
            "expires_at",
        },
    }
    if version >= 6:
        required.update(
            {
                "custody_execution_attempts": {
                    "organization_id",
                    "execution_id",
                    "execution_schema_id",
                    "execution_schema_version",
                    "operation_id",
                    "operation_type",
                    "target_kind",
                    "target_sha256",
                    "parameters_sha256",
                    "protected_input_ref_sha256",
                    "claimed_at",
                },
                "custody_execution_events": {
                    "organization_id",
                    "execution_id",
                    "sequence",
                    "event_schema_id",
                    "event_schema_version",
                    "operation_id",
                    "occurred_at",
                    "state",
                    "reason_code",
                },
            }
        )
    if version >= 7:
        required.update(
            {
                "custody_lifecycle_asset_identities": {
                    "organization_id",
                    "asset_type",
                    "asset_id",
                    "generation",
                    "binding_fingerprint",
                    "envelope_key_asset_id",
                    "envelope_key_generation",
                    "envelope_key_binding_fingerprint",
                    "registered_at",
                },
                "custody_lifecycle_chain_heads": {
                    "organization_id",
                    "chain_version",
                    "sequence",
                    "head_digest",
                    "committed_at",
                },
                "custody_lifecycle_events": {
                    "organization_id",
                    "lifecycle_event_id",
                    "lifecycle_schema_id",
                    "lifecycle_schema_version",
                    "execution_id",
                    "operation_id",
                    "occurred_at",
                    "operation_type",
                    "target_sha256",
                    "parameters_sha256",
                    "event_digest",
                },
                "custody_envelope_attestations": {
                    "organization_id",
                    "execution_id",
                    "attestation_kind",
                    "envelope_asset_id",
                    "destination_key_asset_id",
                    "occurred_at",
                },
                "custody_runtime_projection_heads": {
                    "organization_id",
                    "projection_schema_id",
                    "projection_schema_version",
                    "version",
                    "committed_at",
                },
                "custody_runtime_projection_restrictions": {
                    "organization_id",
                    "asset_type",
                    "asset_id",
                    "generation",
                    "binding_fingerprint",
                    "restriction_kind",
                    "lifecycle_event_id",
                    "committed_at",
                },
                "custody_runtime_replicas": {
                    "organization_id",
                    "replica_id",
                    "registered_at",
                    "heartbeat_at",
                    "lease_expires_at",
                    "observed_projection_version",
                    "retired_at",
                },
                "custody_runtime_projection_barriers": {
                    "organization_id",
                    "barrier_id",
                    "execution_id",
                    "proposed_version",
                    "operation_type",
                    "asset_type",
                    "asset_id",
                    "asset_generation",
                    "restriction_kind",
                    "prepared_at",
                    "activated_at",
                    "resolved_at",
                    "resolution_lifecycle_event_id",
                },
                "custody_runtime_projection_acks": {
                    "organization_id",
                    "barrier_id",
                    "replica_id",
                    "acknowledged_at",
                },
            }
        )
    if version >= 8:
        required["custody_tenants"].update({"retention_days", "retention_legal_hold"})
        required["custody_control_events"].update({"evidence_json", "retain_until", "legal_hold"})
        required["custody_execution_attempts"].update({"evidence_json", "retain_until", "legal_hold"})
        required["custody_execution_events"].update({"evidence_json", "retain_until", "legal_hold"})
        required["custody_lifecycle_events"].update({"evidence_json", "retain_until", "legal_hold"})
        required["custody_envelope_attestations"].update({"evidence_json", "retain_until", "legal_hold"})
        required["custody_deletion_events"] = {
            "organization_id",
            "deletion_event_id",
            "deletion_schema_id",
            "deletion_schema_version",
            "occurred_at",
            "source_schema_id",
            "source_schema_version",
            "source_event_id",
            "source_retain_until",
            "source_legal_hold",
            "decision",
            "reason_code",
            "evidence_json",
            "retain_until",
            "legal_hold",
        }
    for table, columns in required.items():
        cursor.execute(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = %s AND table_name = %s
            """,
            (schema, table),
        )
        observed = {
            str(row["column_name"] if isinstance(row, Mapping) else row[0])
            for row in cursor.fetchall()
        }
        if not columns.issubset(observed):
            raise PostgresStorageError("storage_schema_partial_upgrade")
    if version >= 8:
        _verify_custody_evidence_schema_objects(cursor, schema=schema)


def _verify_custody_executor_schema_shape(cursor: Any, *, schema: str, version: int) -> None:
    """Reject an executor ledger whose routine-attempt objects are missing."""

    if version < 6:
        return
    required = {
        "custody_execution_attempts": {
            "organization_id",
            "execution_id",
            "execution_schema_id",
            "execution_schema_version",
            "operation_id",
            "operation_type",
            "target_kind",
            "target_sha256",
            "parameters_sha256",
            "protected_input_ref_sha256",
            "claimed_at",
        },
        "custody_execution_events": {
            "organization_id",
            "execution_id",
            "sequence",
            "event_schema_id",
            "event_schema_version",
            "operation_id",
            "occurred_at",
            "state",
            "reason_code",
        },
    }
    if version >= 7:
        required.update(
            {
                "custody_lifecycle_asset_identities": {
                    "organization_id",
                    "asset_type",
                    "asset_id",
                    "generation",
                    "binding_fingerprint",
                    "envelope_key_asset_id",
                    "envelope_key_generation",
                    "envelope_key_binding_fingerprint",
                    "registered_at",
                },
                "custody_lifecycle_chain_heads": {
                    "organization_id",
                    "chain_version",
                    "sequence",
                    "head_digest",
                    "committed_at",
                },
                "custody_lifecycle_events": {
                    "organization_id",
                    "lifecycle_event_id",
                    "lifecycle_schema_id",
                    "lifecycle_schema_version",
                    "execution_id",
                    "operation_id",
                    "occurred_at",
                    "operation_type",
                    "target_sha256",
                    "parameters_sha256",
                    "event_digest",
                },
                "custody_envelope_attestations": {
                    "organization_id",
                    "execution_id",
                    "attestation_kind",
                    "envelope_asset_id",
                    "destination_key_asset_id",
                    "occurred_at",
                },
                "custody_runtime_projection_heads": {
                    "organization_id",
                    "projection_schema_id",
                    "projection_schema_version",
                    "version",
                    "committed_at",
                },
                "custody_runtime_projection_restrictions": {
                    "organization_id",
                    "asset_type",
                    "asset_id",
                    "generation",
                    "binding_fingerprint",
                    "restriction_kind",
                    "lifecycle_event_id",
                    "committed_at",
                },
                "custody_runtime_replicas": {
                    "organization_id",
                    "replica_id",
                    "registered_at",
                    "heartbeat_at",
                    "lease_expires_at",
                    "observed_projection_version",
                    "retired_at",
                },
                "custody_runtime_projection_barriers": {
                    "organization_id",
                    "barrier_id",
                    "execution_id",
                    "proposed_version",
                    "operation_type",
                    "asset_type",
                    "asset_id",
                    "asset_generation",
                    "restriction_kind",
                    "prepared_at",
                    "activated_at",
                    "resolved_at",
                    "resolution_lifecycle_event_id",
                },
                "custody_runtime_projection_acks": {
                    "organization_id",
                    "barrier_id",
                    "replica_id",
                    "acknowledged_at",
                },
            }
        )
    if version >= 8:
        required["custody_execution_attempts"].update({"evidence_json", "retain_until", "legal_hold"})
        required["custody_execution_events"].update({"evidence_json", "retain_until", "legal_hold"})
        required["custody_lifecycle_events"].update({"evidence_json", "retain_until", "legal_hold"})
        required["custody_envelope_attestations"].update({"evidence_json", "retain_until", "legal_hold"})
    for table, columns in required.items():
        cursor.execute(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = %s AND table_name = %s
            """,
            (schema, table),
        )
        observed = {
            str(row["column_name"] if isinstance(row, Mapping) else row[0])
            for row in cursor.fetchall()
        }
        if not columns.issubset(observed):
            raise PostgresStorageError("storage_schema_partial_upgrade")
    if version >= 7:
        cursor.execute(
            """
            SELECT procedure.proname
            FROM pg_catalog.pg_proc AS procedure
            JOIN pg_catalog.pg_namespace AS namespace
              ON namespace.oid = procedure.pronamespace
            WHERE namespace.nspname = %s
              AND procedure.proname IN (
                  'custody_execution_has_two_active_approvers',
                  'enforce_custody_lifecycle_asset_identity',
                  'custody_lifecycle_next_chain_head'
              )
            """,
            (schema,),
        )
        observed_functions = {
            str(row["proname"] if isinstance(row, Mapping) else row[0])
            for row in cursor.fetchall()
        }
        if observed_functions != {
            "custody_execution_has_two_active_approvers",
            "enforce_custody_lifecycle_asset_identity",
            "custody_lifecycle_next_chain_head",
        }:
            raise PostgresStorageError("storage_schema_partial_upgrade")
    if version >= 8:
        _verify_custody_evidence_schema_objects(cursor, schema=schema)


def _verify_custody_evidence_schema_objects(cursor: Any, *, schema: str) -> None:
    """Require v8 source-binding and retention guards, not just their columns."""

    required_functions = {
        "custody_audit_chain_append_entry",
        "custody_audit_chain_export_entries",
        "custody_audit_chain_next_position",
        "custody_audit_chain_source_event_json",
        "custody_audit_chain_source_retention",
        "custody_evidence_exact_keys",
        "custody_evidence_timestamp_matches",
        "deny_custody_evidence_mutation",
        "enforce_custody_audit_chain_entry",
        "enforce_custody_audit_chain_entry_insert",
        "enforce_custody_evidence_contract",
        "enforce_custody_evidence_retention",
    }
    cursor.execute(
        """
        SELECT procedure.proname
        FROM pg_catalog.pg_proc AS procedure
        JOIN pg_catalog.pg_namespace AS namespace
          ON namespace.oid = procedure.pronamespace
        WHERE namespace.nspname = %s
          AND procedure.proname IN (
              'custody_audit_chain_append_entry',
              'custody_audit_chain_export_entries',
              'custody_audit_chain_next_position',
              'custody_audit_chain_source_event_json',
              'custody_audit_chain_source_retention',
              'custody_evidence_exact_keys',
              'custody_evidence_timestamp_matches',
              'deny_custody_evidence_mutation',
              'enforce_custody_audit_chain_entry',
              'enforce_custody_audit_chain_entry_insert',
              'enforce_custody_evidence_contract',
              'enforce_custody_evidence_retention'
          )
        """,
        (schema,),
    )
    observed_functions = {
        str(row["proname"] if isinstance(row, Mapping) else row[0])
        for row in cursor.fetchall()
    }
    if observed_functions != required_functions:
        raise PostgresStorageError("storage_schema_partial_upgrade")

    required_triggers = {
        "custody_control_events_chain_required",
        "custody_control_events_contract_required",
        "custody_control_events_immutable",
        "custody_control_events_retention_required",
        "custody_deletion_events_chain_required",
        "custody_deletion_events_contract_required",
        "custody_deletion_events_immutable",
        "custody_deletion_events_retention_required",
        "custody_envelope_attestations_chain_required",
        "custody_envelope_attestations_contract_required",
        "custody_envelope_attestations_immutable",
        "custody_envelope_attestations_retention_required",
        "custody_execution_attempts_chain_required",
        "custody_execution_attempts_contract_required",
        "custody_execution_attempts_immutable",
        "custody_execution_attempts_retention_required",
        "custody_execution_events_chain_required",
        "custody_execution_events_contract_required",
        "custody_execution_events_immutable",
        "custody_execution_events_retention_required",
        "gateway_audit_chain_entries_v2_source_required",
        "custody_lifecycle_events_chain_required",
        "custody_lifecycle_events_contract_required",
        "custody_lifecycle_events_immutable",
        "custody_lifecycle_events_retention_required",
    }
    cursor.execute(
        """
        SELECT trigger.tgname
        FROM pg_catalog.pg_trigger AS trigger
        JOIN pg_catalog.pg_class AS relation
          ON relation.oid = trigger.tgrelid
        JOIN pg_catalog.pg_namespace AS namespace
          ON namespace.oid = relation.relnamespace
        WHERE namespace.nspname = %s
          AND NOT trigger.tgisinternal
          AND trigger.tgname IN (
              'custody_control_events_chain_required',
              'custody_control_events_contract_required',
              'custody_control_events_immutable',
              'custody_control_events_retention_required',
              'custody_deletion_events_chain_required',
              'custody_deletion_events_contract_required',
              'custody_deletion_events_immutable',
              'custody_deletion_events_retention_required',
              'custody_envelope_attestations_chain_required',
              'custody_envelope_attestations_contract_required',
              'custody_envelope_attestations_immutable',
              'custody_envelope_attestations_retention_required',
              'custody_execution_attempts_chain_required',
              'custody_execution_attempts_contract_required',
              'custody_execution_attempts_immutable',
              'custody_execution_attempts_retention_required',
              'custody_execution_events_chain_required',
              'custody_execution_events_contract_required',
              'custody_execution_events_immutable',
              'custody_execution_events_retention_required',
              'gateway_audit_chain_entries_v2_source_required',
              'custody_lifecycle_events_chain_required',
              'custody_lifecycle_events_contract_required',
              'custody_lifecycle_events_immutable',
              'custody_lifecycle_events_retention_required'
          )
        """,
        (schema,),
    )
    observed_triggers = {
        str(row["tgname"] if isinstance(row, Mapping) else row[0])
        for row in cursor.fetchall()
    }
    if observed_triggers != required_triggers:
        raise PostgresStorageError("storage_schema_partial_upgrade")


@contextmanager
def postgres_transaction(
    dsn: str,
    *,
    schema: str,
    runtime_role: str,
    organization_id: str,
    connection_pool: PostgresConnectionPool | None = None,
) -> Iterator[Any]:
    """Open a transaction with a restricted role and transaction-local tenant key."""

    schema = validate_postgres_identifier(schema, "postgres_schema")
    runtime_role = validate_postgres_identifier(runtime_role, "postgres_runtime_role")
    if not isinstance(organization_id, str) or not organization_id:
        raise PostgresStorageError("storage_organization_invalid")
    psycopg, sql = _driver()
    if connection_pool is not None:
        try:
            with connection_pool.connection() as connection:
                with _tenant_transaction(
                    connection,
                    sql=sql,
                    schema=schema,
                    runtime_role=runtime_role,
                    organization_id=organization_id,
                ):
                    yield connection
            return
        except PostgresStorageError:
            raise
        except psycopg.Error as error:
            raise _storage_error(error) from None
    try:
        connection = psycopg.connect(dsn, row_factory=psycopg.rows.dict_row)
    except psycopg.Error as error:
        raise _storage_error(error) from None
    try:
        with _tenant_transaction(
            connection,
            sql=sql,
            schema=schema,
            runtime_role=runtime_role,
            organization_id=organization_id,
        ):
            yield connection
    except PostgresStorageError:
        raise
    except psycopg.Error as error:
        raise _storage_error(error) from None
    finally:
        connection.close()


@contextmanager
def _tenant_transaction(
    connection: Any,
    *,
    sql: Any,
    schema: str,
    runtime_role: str,
    organization_id: str,
) -> Iterator[None]:
    """Set all security-critical state transaction-locally before database work."""

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
        yield


def _migration_sql(
    version: int,
    quoted_schema: str,
    quoted_runtime_role: str,
    quoted_policy_control_role: str,
    quoted_custody_control_role: str | None = None,
    quoted_custody_executor_role: str | None = None,
) -> str:
    filenames = {
        1: "0001_usage_evidence.sql",
        2: "0002_policy_control.sql",
        3: "0003_request_attempts.sql",
        4: "0004_commit_audit_chain.sql",
        5: "0005_custody_control.sql",
        6: "0006_custody_executor.sql",
        7: "0007_custody_lifecycle.sql",
        8: "0008_custody_evidence_retention.sql",
        9: "0009_portfolio_registry.sql",
        10: "0010_governed_run_attribution.sql",
        11: "0011_work_outcomes.sql",
        12: "0012_finance_rate_cards.sql",
        13: "0013_work_budgets.sql",
        14: "0014_provider_reliability.sql",
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
        custody_control_role=(
            _quote_identifier("hormuz_custody_control")
            if quoted_custody_control_role is None
            else quoted_custody_control_role
        ),
        custody_executor_role=(
            _quote_identifier("hormuz_custody_executor")
            if quoted_custody_executor_role is None
            else quoted_custody_executor_role
        ),
    )


def _driver() -> tuple[Any, Any]:
    try:
        import psycopg
        from psycopg import sql
    except ImportError:
        raise PostgresStorageError("postgres_driver_unavailable") from None
    return psycopg, sql


def _pool_driver() -> Any:
    try:
        import psycopg_pool
    except ImportError:
        raise PostgresStorageError("postgres_pool_driver_unavailable") from None
    return psycopg_pool


def _validate_pool_settings(settings: PostgresPoolConfig) -> None:
    if not isinstance(settings, PostgresPoolConfig):
        raise PostgresStorageError("postgres_pool_configuration_invalid")
    values = (
        settings.min_connections,
        settings.max_connections,
        settings.acquire_timeout_seconds,
        settings.max_waiting,
        settings.max_lifetime_seconds,
        settings.max_idle_seconds,
    )
    if any(isinstance(value, bool) or not isinstance(value, int) for value in values):
        raise PostgresStorageError("postgres_pool_configuration_invalid")
    if not (1 <= settings.min_connections <= settings.max_connections <= 1000):
        raise PostgresStorageError("postgres_pool_configuration_invalid")
    if not 1 <= settings.acquire_timeout_seconds <= 120:
        raise PostgresStorageError("postgres_pool_configuration_invalid")
    if not 1 <= settings.max_waiting <= 10000:
        raise PostgresStorageError("postgres_pool_configuration_invalid")
    if not 60 <= settings.max_lifetime_seconds <= 7 * 24 * 60 * 60:
        raise PostgresStorageError("postgres_pool_configuration_invalid")
    if not 1 <= settings.max_idle_seconds <= settings.max_lifetime_seconds:
        raise PostgresStorageError("postgres_pool_configuration_invalid")


def _quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _storage_error(error: BaseException) -> PostgresStorageError:
    sqlstate = getattr(error, "sqlstate", None)
    if sqlstate in {"3F000", "42P01"}:
        return PostgresStorageError("storage_schema_unavailable")
    if sqlstate in {"42501", "0LP01"}:
        return PostgresStorageError("storage_access_denied")
    return PostgresStorageError("storage_unavailable")
