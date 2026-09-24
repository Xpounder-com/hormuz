"""SQLite 17/PostgreSQL 22 immutable model-scorecard snapshots."""

from __future__ import annotations


AUDIT_TABLE = "portfolio_scorecard_audit_events"
SNAPSHOT_TABLE = "portfolio_model_scorecard_snapshots"


TABLE_DDL = {
    AUDIT_TABLE: """
        organization_id TEXT NOT NULL CHECK (length(organization_id) BETWEEN 1 AND 128),
        event_id TEXT NOT NULL CHECK (length(event_id) = 36),
        sequence BIGINT NOT NULL CHECK (sequence >= 1),
        actor_id TEXT NOT NULL CHECK (length(actor_id) BETWEEN 1 AND 128),
        operation TEXT NOT NULL CHECK (operation = 'build'),
        scorecard_id TEXT NOT NULL CHECK (length(scorecard_id) BETWEEN 1 AND 128),
        scorecard_version INTEGER NOT NULL CHECK (scorecard_version BETWEEN 1 AND 2147483647),
        reason_code TEXT NOT NULL CHECK (reason_code IN ('eligible','missing_evidence')),
        occurred_at TEXT NOT NULL,
        PRIMARY KEY (organization_id, event_id),
        UNIQUE (organization_id, sequence)
    """,
    SNAPSHOT_TABLE: """
        organization_id TEXT NOT NULL CHECK (length(organization_id) BETWEEN 1 AND 128),
        scorecard_id TEXT NOT NULL CHECK (length(scorecard_id) BETWEEN 1 AND 128),
        version INTEGER NOT NULL CHECK (version BETWEEN 1 AND 2147483647),
        work_scope_id TEXT NOT NULL CHECK (length(work_scope_id) BETWEEN 1 AND 128),
        work_scope_version INTEGER NOT NULL CHECK (work_scope_version BETWEEN 1 AND 2147483647),
        window_start_at TEXT NOT NULL,
        window_end_at TEXT NOT NULL,
        evaluated_at TEXT NOT NULL,
        generated_at TEXT NOT NULL,
        expires_at TEXT NOT NULL,
        review_after TEXT NOT NULL,
        state TEXT NOT NULL CHECK (state IN ('eligible','inconclusive')),
        evidence_level TEXT NOT NULL CHECK (evidence_level = 'associated'),
        decision_owner_id TEXT NOT NULL CHECK (length(decision_owner_id) BETWEEN 1 AND 128),
        supersedes_version INTEGER,
        input_digest TEXT NOT NULL CHECK (length(input_digest) = 64),
        evaluation_digest TEXT NOT NULL CHECK (length(evaluation_digest) = 64),
        source_set_digest TEXT NOT NULL CHECK (length(source_set_digest) = 64),
        input_json TEXT NOT NULL CHECK (length(input_json) BETWEEN 2 AND 16777216),
        evaluation_json TEXT NOT NULL CHECK (length(evaluation_json) BETWEEN 2 AND 16777216),
        sequence BIGINT NOT NULL CHECK (sequence >= 1),
        PRIMARY KEY (organization_id, scorecard_id, version),
        UNIQUE (organization_id, sequence),
        UNIQUE (organization_id, scorecard_id, input_digest),
        FOREIGN KEY (organization_id, work_scope_id, work_scope_version)
            REFERENCES {prefix}portfolio_work_scope_versions (organization_id, work_scope_id, version),
        FOREIGN KEY (organization_id, scorecard_id, supersedes_version)
            REFERENCES {prefix}portfolio_model_scorecard_snapshots (organization_id, scorecard_id, version),
        FOREIGN KEY (organization_id, sequence)
            REFERENCES {prefix}portfolio_scorecard_audit_events (organization_id, sequence),
        CHECK (window_start_at < window_end_at),
        CHECK (window_end_at <= evaluated_at AND evaluated_at <= generated_at),
        CHECK (generated_at < expires_at AND generated_at < review_after),
        CHECK ((version = 1 AND supersedes_version IS NULL) OR (version > 1 AND supersedes_version = version - 1))
    """,
}


INDEX_DDL = {
    "portfolio_model_scorecard_scope_window": (
        f"{SNAPSHOT_TABLE} (organization_id, work_scope_id, work_scope_version, "
        "window_start_at, window_end_at, generated_at, scorecard_id, version)"
    ),
    "portfolio_model_scorecard_latest": (
        f"{SNAPSHOT_TABLE} (organization_id, scorecard_id, version DESC)"
    ),
}


def sqlite_statements() -> tuple[str, ...]:
    statements = [
        f"CREATE TABLE {table} ({ddl.format(prefix='')}) WITHOUT ROWID"
        for table, ddl in TABLE_DDL.items()
    ]
    statements.extend(
        f"CREATE INDEX {name} ON {ddl}" for name, ddl in INDEX_DDL.items()
    )
    for table in TABLE_DDL:
        for operation in ("UPDATE", "DELETE"):
            statements.append(
                f"CREATE TRIGGER {table}_no_{operation.lower()} BEFORE {operation} ON {table} "
                "BEGIN SELECT RAISE(ABORT, 'scorecard_snapshot_append_only'); END"
            )
    return tuple(statements)


def apply_sqlite_scorecard_migration(connection) -> None:
    """Apply schema 17 atomically inside the caller-owned transaction."""

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


def verify_sqlite_scorecards(connection, error_factory) -> None:
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


def verify_postgres_scorecards(cursor, schema: str, error_factory) -> None:
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
