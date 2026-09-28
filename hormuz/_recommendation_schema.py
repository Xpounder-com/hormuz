"""SQLite 19/PostgreSQL 24 immutable recommendation snapshots and events."""

from __future__ import annotations


SNAPSHOT_TABLE = "portfolio_policy_recommendation_snapshots"
EVENT_TABLE = "portfolio_policy_recommendation_events"
AUDIT_TABLE = "portfolio_policy_recommendation_read_audit"
CURSOR_TABLE = "portfolio_policy_recommendation_cursors"


TABLE_DDL = {
    SNAPSHOT_TABLE: """
        organization_id TEXT NOT NULL CHECK (length(organization_id) BETWEEN 1 AND 128),
        recommendation_id TEXT NOT NULL CHECK (length(recommendation_id) BETWEEN 1 AND 128),
        version INTEGER NOT NULL CHECK (version BETWEEN 1 AND 2147483647),
        work_scope_id TEXT NOT NULL CHECK (length(work_scope_id) BETWEEN 1 AND 128),
        work_scope_version INTEGER NOT NULL CHECK (work_scope_version BETWEEN 1 AND 2147483647),
        scorecard_id TEXT NOT NULL CHECK (length(scorecard_id) BETWEEN 1 AND 128),
        scorecard_version INTEGER NOT NULL CHECK (scorecard_version BETWEEN 1 AND 2147483647),
        scorecard_evaluation_digest TEXT NOT NULL CHECK (length(scorecard_evaluation_digest) = 64),
        policy_id TEXT NOT NULL CHECK (length(policy_id) BETWEEN 1 AND 128),
        policy_version INTEGER NOT NULL CHECK (policy_version BETWEEN 1 AND 2147483647),
        policy_digest TEXT NOT NULL CHECK (length(policy_digest) = 64),
        change_type TEXT NOT NULL CHECK (change_type IN ('budget_plan_change','model_allowlist_change','model_fallback_change','output_or_cost_cap_change','routing_policy_change')),
        candidate_policy_digest TEXT CHECK (candidate_policy_digest IS NULL OR length(candidate_policy_digest) = 64),
        candidate_budget_plan_id TEXT CHECK (candidate_budget_plan_id IS NULL OR length(candidate_budget_plan_id) BETWEEN 1 AND 128),
        candidate_budget_plan_version INTEGER CHECK (candidate_budget_plan_version IS NULL OR candidate_budget_plan_version BETWEEN 1 AND 2147483647),
        evidence_level TEXT NOT NULL CHECK (evidence_level IN ('descriptive','associated','controlled')),
        window_start_at TEXT NOT NULL,
        window_end_at TEXT NOT NULL,
        created_at TEXT NOT NULL,
        expires_at TEXT NOT NULL,
        supersedes_version INTEGER,
        generation_digest TEXT NOT NULL CHECK (length(generation_digest) = 64),
        recommendation_json TEXT NOT NULL CHECK (length(recommendation_json) BETWEEN 2 AND 1048576),
        evaluation_json TEXT NOT NULL CHECK (length(evaluation_json) BETWEEN 2 AND 1048576),
        expected_pre_apply_json TEXT NOT NULL CHECK (length(expected_pre_apply_json) BETWEEN 2 AND 8192),
        bindings_json TEXT NOT NULL CHECK (length(bindings_json) BETWEEN 2 AND 65536),
        sequence BIGINT NOT NULL CHECK (sequence BETWEEN 1 AND 9223372036854775807),
        PRIMARY KEY (organization_id, recommendation_id, version),
        UNIQUE (organization_id, sequence),
        UNIQUE (organization_id, recommendation_id, generation_digest),
        FOREIGN KEY (organization_id, work_scope_id, work_scope_version)
            REFERENCES {prefix}portfolio_work_scope_versions (organization_id, work_scope_id, version),
        FOREIGN KEY (organization_id, scorecard_id, scorecard_version)
            REFERENCES {prefix}portfolio_model_scorecard_snapshots (organization_id, scorecard_id, version),
        FOREIGN KEY (organization_id, recommendation_id, supersedes_version)
            REFERENCES {prefix}portfolio_policy_recommendation_snapshots (organization_id, recommendation_id, version),
        FOREIGN KEY (organization_id, candidate_budget_plan_id, candidate_budget_plan_version)
            REFERENCES {prefix}portfolio_work_budget_plan_versions (organization_id, budget_plan_id, version),
        CHECK (length(window_start_at) = 27 AND substr(window_start_at, 20, 1) = '.' AND substr(window_start_at, 27, 1) = 'Z'),
        CHECK (length(window_end_at) = 27 AND substr(window_end_at, 20, 1) = '.' AND substr(window_end_at, 27, 1) = 'Z'),
        CHECK (length(created_at) = 27 AND substr(created_at, 20, 1) = '.' AND substr(created_at, 27, 1) = 'Z'),
        CHECK (length(expires_at) = 27 AND substr(expires_at, 20, 1) = '.' AND substr(expires_at, 27, 1) = 'Z'),
        CHECK (window_start_at < window_end_at AND created_at < expires_at),
        CHECK ((version = 1 AND supersedes_version IS NULL) OR (version > 1 AND supersedes_version = version - 1)),
        CHECK ((change_type = 'budget_plan_change' AND candidate_policy_digest IS NULL AND candidate_budget_plan_id IS NOT NULL AND candidate_budget_plan_version IS NOT NULL)
            OR (change_type <> 'budget_plan_change' AND candidate_policy_digest IS NOT NULL AND candidate_budget_plan_id IS NULL AND candidate_budget_plan_version IS NULL))
    """,
    EVENT_TABLE: """
        organization_id TEXT NOT NULL CHECK (length(organization_id) BETWEEN 1 AND 128),
        event_id TEXT NOT NULL CHECK (length(event_id) = 36),
        sequence BIGINT NOT NULL CHECK (sequence BETWEEN 1 AND 9223372036854775807),
        actor_id TEXT NOT NULL CHECK (length(actor_id) BETWEEN 1 AND 128),
        recommendation_id TEXT NOT NULL CHECK (length(recommendation_id) BETWEEN 1 AND 128),
        recommendation_version INTEGER NOT NULL CHECK (recommendation_version BETWEEN 1 AND 2147483647),
        event_type TEXT NOT NULL CHECK (event_type IN ('generated','accepted','rejected','expired','invalidated','superseded','applied')),
        public_state TEXT NOT NULL CHECK (public_state IN ('pending','accepted','rejected','expired','invalidated','superseded')),
        reason_code TEXT NOT NULL CHECK (reason_code IN ('eligible','accepted','rejected','expired','policy_drift','scorecard_drift','superseded','not_applicable')),
        occurred_at TEXT NOT NULL,
        idempotency_key TEXT CHECK (idempotency_key IS NULL OR length(idempotency_key) BETWEEN 1 AND 128),
        request_digest TEXT CHECK (request_digest IS NULL OR length(request_digest) = 64),
        evidence_json TEXT NOT NULL CHECK (length(evidence_json) BETWEEN 2 AND 8192),
        PRIMARY KEY (organization_id, event_id),
        UNIQUE (organization_id, sequence),
        UNIQUE (organization_id, actor_id, idempotency_key),
        FOREIGN KEY (organization_id, recommendation_id, recommendation_version)
            REFERENCES {prefix}portfolio_policy_recommendation_snapshots (organization_id, recommendation_id, version),
        CHECK (length(occurred_at) = 27 AND substr(occurred_at, 20, 1) = '.' AND substr(occurred_at, 27, 1) = 'Z'),
        CHECK ((idempotency_key IS NULL AND request_digest IS NULL) OR (idempotency_key IS NOT NULL AND request_digest IS NOT NULL)),
        CHECK ((event_type = 'generated' AND public_state = 'pending' AND reason_code = 'eligible')
            OR (event_type = 'accepted' AND public_state = 'accepted' AND reason_code = 'accepted')
            OR (event_type = 'rejected' AND public_state = 'rejected' AND reason_code = 'rejected')
            OR (event_type = 'expired' AND public_state = 'expired' AND reason_code = 'expired')
            OR (event_type = 'invalidated' AND public_state = 'invalidated' AND reason_code IN ('policy_drift','scorecard_drift'))
            OR (event_type = 'superseded' AND public_state = 'superseded' AND reason_code = 'superseded')
            OR (event_type = 'applied' AND public_state = 'accepted' AND reason_code = 'not_applicable'))
    """,
    AUDIT_TABLE: """
        organization_id TEXT NOT NULL CHECK (length(organization_id) BETWEEN 1 AND 128),
        event_id TEXT NOT NULL CHECK (length(event_id) = 36),
        sequence BIGINT NOT NULL CHECK (sequence BETWEEN 1 AND 9223372036854775807),
        actor_id TEXT NOT NULL CHECK (length(actor_id) BETWEEN 1 AND 128),
        operation TEXT NOT NULL CHECK (operation IN ('list','show')),
        recommendation_id TEXT CHECK (recommendation_id IS NULL OR length(recommendation_id) BETWEEN 1 AND 128),
        recommendation_version INTEGER CHECK (recommendation_version IS NULL OR recommendation_version BETWEEN 1 AND 2147483647),
        filter_digest TEXT NOT NULL CHECK (length(filter_digest) = 64),
        snapshot_sequence BIGINT NOT NULL CHECK (snapshot_sequence >= 0),
        result_count INTEGER NOT NULL CHECK (result_count BETWEEN 0 AND 100),
        occurred_at TEXT NOT NULL,
        PRIMARY KEY (organization_id, event_id),
        UNIQUE (organization_id, sequence),
        CHECK (length(occurred_at) = 27 AND substr(occurred_at, 20, 1) = '.' AND substr(occurred_at, 27, 1) = 'Z'),
        CHECK ((operation = 'list' AND recommendation_id IS NULL AND recommendation_version IS NULL)
            OR (operation = 'show' AND recommendation_id IS NOT NULL AND recommendation_version IS NOT NULL))
    """,
    CURSOR_TABLE: """
        organization_id TEXT NOT NULL CHECK (length(organization_id) BETWEEN 1 AND 128),
        cursor_id TEXT NOT NULL CHECK (length(cursor_id) = 64),
        actor_id TEXT NOT NULL CHECK (length(actor_id) BETWEEN 1 AND 128),
        authority_digest TEXT NOT NULL CHECK (length(authority_digest) = 64),
        schema_id TEXT NOT NULL CHECK (schema_id = 'hormuz.policy-recommendation-page'),
        schema_version INTEGER NOT NULL CHECK (schema_version = 1),
        as_of TEXT NOT NULL,
        expires_at TEXT NOT NULL,
        snapshot_sequence BIGINT NOT NULL CHECK (snapshot_sequence >= 0),
        page_limit INTEGER NOT NULL CHECK (page_limit BETWEEN 1 AND 100),
        after_at TEXT NOT NULL,
        after_id TEXT NOT NULL CHECK (length(after_id) BETWEEN 1 AND 128),
        filters_json TEXT NOT NULL CHECK (length(filters_json) BETWEEN 2 AND 4096),
        PRIMARY KEY (organization_id, cursor_id),
        CHECK (length(as_of) = 27 AND substr(as_of, 20, 1) = '.' AND substr(as_of, 27, 1) = 'Z'),
        CHECK (length(expires_at) = 27 AND substr(expires_at, 20, 1) = '.' AND substr(expires_at, 27, 1) = 'Z'),
        CHECK (length(after_at) = 27 AND substr(after_at, 20, 1) = '.' AND substr(after_at, 27, 1) = 'Z'),
        CHECK (as_of < expires_at)
    """,
}


INDEX_DDL = {
    "portfolio_recommendation_scope_created": (
        f"{SNAPSHOT_TABLE} (organization_id, work_scope_id, work_scope_version, created_at, recommendation_id, version)"
    ),
    "portfolio_recommendation_latest": (
        f"{SNAPSHOT_TABLE} (organization_id, recommendation_id, version DESC)"
    ),
    "portfolio_recommendation_event_latest": (
        f"{EVENT_TABLE} (organization_id, recommendation_id, recommendation_version, sequence DESC)"
    ),
    "portfolio_recommendation_audit_window": (
        f"{AUDIT_TABLE} (organization_id, occurred_at, event_id)"
    ),
    "portfolio_recommendation_cursor_expiry": (
        f"{CURSOR_TABLE} (organization_id, expires_at, cursor_id)"
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
                "BEGIN SELECT RAISE(ABORT, 'recommendation_append_only'); END"
            )
    return tuple(statements)


def apply_sqlite_recommendation_migration(connection) -> None:
    """Apply schema 19 atomically inside the caller-owned transaction."""

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


def verify_sqlite_recommendations(connection, error_factory) -> None:
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


def verify_postgres_recommendations(cursor, schema: str, error_factory) -> None:
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
