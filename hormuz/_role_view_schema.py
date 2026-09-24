"""SQLite 18/PostgreSQL 23 metadata-only portfolio role-view reads."""

from __future__ import annotations


AUDIT_TABLE = "portfolio_role_view_audit_events"
CURSOR_TABLE = "portfolio_role_view_cursors"
QUERY_CLASSES = (
    "finance_budget",
    "platform_scorecard",
    "team_budget",
    "team_scorecard",
)


TABLE_DDL = {
    AUDIT_TABLE: """
        organization_id TEXT NOT NULL CHECK (length(organization_id) BETWEEN 1 AND 128),
        event_id TEXT NOT NULL CHECK (length(event_id) = 36),
        sequence BIGINT NOT NULL CHECK (sequence BETWEEN 1 AND 9223372036854775807),
        actor_id TEXT NOT NULL CHECK (length(actor_id) BETWEEN 1 AND 128),
        reader_role TEXT NOT NULL CHECK (reader_role IN ('finance_viewer','platform_viewer','team_lead')),
        query_class TEXT NOT NULL CHECK (query_class IN ('finance_budget','platform_scorecard','team_budget','team_scorecard')),
        scope_kind TEXT NOT NULL CHECK (scope_kind IN ('organization','team')),
        scope_id TEXT CHECK (scope_id IS NULL OR length(scope_id) BETWEEN 1 AND 128),
        filter_digest TEXT NOT NULL CHECK (length(filter_digest) = 64),
        snapshot_sequence BIGINT NOT NULL CHECK (snapshot_sequence >= 0),
        companion_snapshot_sequence BIGINT NOT NULL CHECK (companion_snapshot_sequence >= 0),
        result_count INTEGER NOT NULL CHECK (result_count BETWEEN 0 AND 100),
        partial_count INTEGER NOT NULL CHECK (partial_count BETWEEN 0 AND result_count),
        occurred_at TEXT NOT NULL,
        PRIMARY KEY (organization_id, event_id),
        UNIQUE (organization_id, sequence),
        CHECK ((scope_kind = 'team' AND reader_role = 'team_lead' AND scope_id IS NOT NULL)
            OR (scope_kind = 'organization' AND reader_role <> 'team_lead' AND scope_id IS NULL)),
        CHECK (length(occurred_at) = 27 AND substr(occurred_at, 20, 1) = '.' AND substr(occurred_at, 27, 1) = 'Z')
    """,
    CURSOR_TABLE: """
        organization_id TEXT NOT NULL CHECK (length(organization_id) BETWEEN 1 AND 128),
        cursor_id TEXT NOT NULL CHECK (length(cursor_id) = 64),
        actor_id TEXT NOT NULL CHECK (length(actor_id) BETWEEN 1 AND 128),
        authority_digest TEXT NOT NULL CHECK (length(authority_digest) = 64),
        reader_role TEXT NOT NULL CHECK (reader_role IN ('finance_viewer','platform_viewer','team_lead')),
        query_class TEXT NOT NULL CHECK (query_class IN ('finance_budget','platform_scorecard','team_budget','team_scorecard')),
        scope_id TEXT CHECK (scope_id IS NULL OR length(scope_id) BETWEEN 1 AND 128),
        schema_id TEXT NOT NULL CHECK (schema_id = 'hormuz.portfolio-role-view-page'),
        schema_version INTEGER NOT NULL CHECK (schema_version = 1),
        as_of TEXT NOT NULL,
        expires_at TEXT NOT NULL,
        snapshot_sequence BIGINT NOT NULL CHECK (snapshot_sequence >= 0),
        companion_snapshot_sequence BIGINT NOT NULL CHECK (companion_snapshot_sequence >= 0),
        page_limit INTEGER NOT NULL CHECK (page_limit BETWEEN 1 AND 100),
        after_at TEXT NOT NULL,
        after_id TEXT NOT NULL CHECK (length(after_id) BETWEEN 1 AND 128),
        filters_json TEXT NOT NULL CHECK (length(filters_json) BETWEEN 2 AND 4096),
        PRIMARY KEY (organization_id, cursor_id),
        CHECK ((reader_role = 'team_lead' AND scope_id IS NOT NULL)
            OR (reader_role <> 'team_lead' AND scope_id IS NULL)),
        CHECK (length(as_of) = 27 AND substr(as_of, 20, 1) = '.' AND substr(as_of, 27, 1) = 'Z'),
        CHECK (length(expires_at) = 27 AND substr(expires_at, 20, 1) = '.' AND substr(expires_at, 27, 1) = 'Z'),
        CHECK (length(after_at) = 27 AND substr(after_at, 20, 1) = '.' AND substr(after_at, 27, 1) = 'Z'),
        CHECK (as_of < expires_at)
    """,
}


INDEX_DDL = {
    "portfolio_role_view_audit_window": (
        f"{AUDIT_TABLE} (organization_id, occurred_at, event_id)"
    ),
    "portfolio_role_view_cursor_expiry": (
        f"{CURSOR_TABLE} (organization_id, expires_at, cursor_id)"
    ),
}


def sqlite_statements() -> tuple[str, ...]:
    statements = [
        f"CREATE TABLE {table} ({ddl}) WITHOUT ROWID"
        for table, ddl in TABLE_DDL.items()
    ]
    statements.extend(
        f"CREATE INDEX {name} ON {ddl}" for name, ddl in INDEX_DDL.items()
    )
    for table in TABLE_DDL:
        for operation in ("UPDATE", "DELETE"):
            statements.append(
                f"CREATE TRIGGER {table}_no_{operation.lower()} BEFORE {operation} ON {table} "
                "BEGIN SELECT RAISE(ABORT, 'role_view_append_only'); END"
            )
    return tuple(statements)


def apply_sqlite_role_view_migration(connection) -> None:
    """Apply schema 18 atomically inside the caller-owned transaction."""

    for statement in sqlite_statements():
        connection.execute(statement)


def _statement_name(statement: str) -> str:
    words = statement.split()
    if len(words) >= 3 and words[:2] in (
        ["CREATE", "TABLE"],
        ["CREATE", "INDEX"],
        ["CREATE", "TRIGGER"],
    ):
        return words[2]
    return ""


def verify_sqlite_role_views(connection, error_factory) -> None:
    observed = {
        str(row["name"]): " ".join(str(row["sql"]).split())
        for row in connection.execute(
            "SELECT name, sql FROM sqlite_master WHERE sql IS NOT NULL"
        ).fetchall()
    }
    if any(
        observed.get(_statement_name(statement)) != " ".join(statement.split())
        for statement in sqlite_statements()
    ):
        raise error_factory("storage_schema_partial_upgrade")


def verify_postgres_role_views(cursor, schema: str, error_factory) -> None:
    from ._portfolio_schema import verify_postgres_owned_tables

    expected_constraints = {
        table: {
            kind: ddl.count(marker)
            for kind, marker in (
                ("p", "PRIMARY KEY"),
                ("u", "UNIQUE ("),
                ("f", "FOREIGN KEY"),
                ("c", "CHECK ("),
            )
            if marker in ddl
        }
        for table, ddl in TABLE_DDL.items()
    }
    verify_postgres_owned_tables(
        cursor,
        schema,
        error_factory,
        TABLE_DDL,
        INDEX_DDL,
        expected_constraints,
        trigger_type=58,
    )
