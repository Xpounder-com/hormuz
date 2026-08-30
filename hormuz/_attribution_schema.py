"""Attribution-owned additive tables; never modify v1 or registry evidence."""

from __future__ import annotations


TABLE_DDL = {
    "portfolio_attribution_audit_events": """
        organization_id TEXT NOT NULL,
        event_id TEXT NOT NULL,
        sequence BIGINT NOT NULL CHECK (sequence >= 1),
        actor_id TEXT,
        operation TEXT NOT NULL CHECK (operation IN ('admit','reject_admission','correct','list_attributions','read_facts')),
        entity_id TEXT,
        reason_code TEXT NOT NULL CHECK (reason_code IN ('bound','corrected','voided','observed','missing_evidence','ambiguous','invalid_reference','stale_version','unsupported','unauthorized_scope','dependency_unavailable')),
        occurred_at TEXT NOT NULL,
        PRIMARY KEY (organization_id, event_id),
        UNIQUE (organization_id, sequence)
    """,
    "portfolio_attribution_events": """
        organization_id TEXT NOT NULL,
        attribution_event_id TEXT NOT NULL,
        request_attempt_id TEXT NOT NULL,
        work_scope_id TEXT,
        work_scope_version INTEGER,
        confidence TEXT NOT NULL CHECK (confidence IN ('explicit_authorized','server_side_default','authorized_post_run','unattributed','ambiguous')),
        state TEXT NOT NULL CHECK (state IN ('active','voided')),
        supersedes_event_id TEXT,
        actor_id TEXT,
        reason_code TEXT NOT NULL CHECK (reason_code IN ('bound','corrected','voided','missing_evidence','ambiguous')),
        event_at TEXT NOT NULL,
        observed_at TEXT NOT NULL,
        ingested_at TEXT NOT NULL,
        sequence BIGINT NOT NULL,
        PRIMARY KEY (organization_id, attribution_event_id),
        UNIQUE (organization_id, sequence),
        UNIQUE (organization_id, request_attempt_id, attribution_event_id),
        UNIQUE (organization_id, request_attempt_id, supersedes_event_id),
        FOREIGN KEY (organization_id, request_attempt_id, supersedes_event_id)
            REFERENCES {prefix}portfolio_attribution_events (organization_id, request_attempt_id, attribution_event_id),
        FOREIGN KEY (organization_id, work_scope_id, work_scope_version)
            REFERENCES {prefix}portfolio_work_scope_versions (organization_id, work_scope_id, version),
        FOREIGN KEY (organization_id, sequence)
            REFERENCES {prefix}portfolio_attribution_audit_events (organization_id, sequence),
        CHECK ((work_scope_id IS NULL AND work_scope_version IS NULL) OR (work_scope_id IS NOT NULL AND work_scope_version IS NOT NULL)),
        CHECK ((confidence IN ('unattributed','ambiguous') AND work_scope_id IS NULL AND actor_id IS NULL AND supersedes_event_id IS NULL AND state='active') OR (confidence IN ('explicit_authorized','server_side_default') AND work_scope_id IS NOT NULL AND actor_id IS NULL AND supersedes_event_id IS NULL AND state='active') OR (confidence='authorized_post_run' AND actor_id IS NOT NULL)),
        CHECK ((state='voided' AND work_scope_id IS NULL AND confidence='authorized_post_run' AND reason_code='voided') OR (state='active' AND reason_code<>'voided'))
    """,
    "portfolio_attribution_rejections": """
        organization_id TEXT NOT NULL,
        receipt_id TEXT NOT NULL,
        actor_id TEXT NOT NULL,
        client TEXT NOT NULL CHECK (client IN ('codex','claude-code')),
        protocol TEXT NOT NULL CHECK (protocol IN ('openai','anthropic')),
        result_status TEXT NOT NULL CHECK (result_status IN ('rejected','unavailable')),
        reason_code TEXT NOT NULL CHECK (reason_code IN ('missing_evidence','ambiguous','invalid_reference','stale_version','unsupported','unauthorized_scope','dependency_unavailable')),
        occurred_at TEXT NOT NULL,
        sequence BIGINT NOT NULL,
        PRIMARY KEY (organization_id, receipt_id),
        UNIQUE (organization_id, sequence),
        FOREIGN KEY (organization_id, sequence)
            REFERENCES {prefix}portfolio_attribution_audit_events (organization_id, sequence)
    """,
    "portfolio_attribution_idempotency": """
        organization_id TEXT NOT NULL,
        actor_id TEXT NOT NULL,
        idempotency_key TEXT NOT NULL CHECK (length(idempotency_key) BETWEEN 1 AND 128),
        request_mac TEXT NOT NULL,
        attribution_event_id TEXT NOT NULL,
        PRIMARY KEY (organization_id, actor_id, idempotency_key),
        FOREIGN KEY (organization_id, attribution_event_id)
            REFERENCES {prefix}portfolio_attribution_events (organization_id, attribution_event_id)
    """,
    "portfolio_attribution_cursors": """
        organization_id TEXT NOT NULL,
        cursor_id TEXT NOT NULL,
        actor_id TEXT NOT NULL,
        authority_json TEXT NOT NULL,
        as_of TEXT NOT NULL,
        snapshot_sequence BIGINT NOT NULL CHECK (snapshot_sequence >= 0),
        after_at TEXT NOT NULL,
        after_id TEXT NOT NULL,
        filters_json TEXT NOT NULL,
        PRIMARY KEY (organization_id, cursor_id)
    """,
}

INDEX_DDL = {
    "portfolio_attribution_root": "UNIQUE INDEX {prefix}portfolio_attribution_root ON {prefix}portfolio_attribution_events (organization_id, request_attempt_id) WHERE supersedes_event_id IS NULL",
    "portfolio_attribution_attempt": "INDEX {prefix}portfolio_attribution_attempt ON {prefix}portfolio_attribution_events (organization_id, request_attempt_id, sequence)",
    "portfolio_attribution_window": "INDEX {prefix}portfolio_attribution_window ON {prefix}portfolio_attribution_events (organization_id, event_at, attribution_event_id, sequence)",
    "portfolio_attribution_scope": "INDEX {prefix}portfolio_attribution_scope ON {prefix}portfolio_attribution_events (organization_id, work_scope_id, sequence)",
    "portfolio_attribution_rejection_window": "INDEX {prefix}portfolio_attribution_rejection_window ON {prefix}portfolio_attribution_rejections (organization_id, occurred_at, sequence)",
}


def sqlite_statements() -> tuple[str, ...]:
    result = [f"CREATE TABLE {name} ({ddl.format(prefix='')})" for name, ddl in TABLE_DDL.items()]
    result.extend("CREATE " + ddl.format(prefix="") for ddl in INDEX_DDL.values())
    for table in TABLE_DDL:
        for operation in ("UPDATE", "DELETE"):
            result.append(f"CREATE TRIGGER {table}_no_{operation.lower()} BEFORE {operation} ON {table} "
                          "BEGIN SELECT RAISE(ABORT, 'portfolio_append_only'); END")
    result.append("""CREATE TRIGGER portfolio_attribution_attempt_exists BEFORE INSERT ON portfolio_attribution_events
        WHEN NOT EXISTS (SELECT 1 FROM gateway_request_attempts a WHERE a.organization_id=NEW.organization_id AND a.attempt_id=NEW.request_attempt_id)
        BEGIN SELECT RAISE(ABORT, 'attribution_attempt_invalid'); END""")
    return tuple(result)


def verify_sqlite_attribution(connection, error_factory) -> None:
    observed = {" ".join(row[0].split()) for row in connection.execute(
        "SELECT sql FROM sqlite_master WHERE sql IS NOT NULL AND name LIKE 'portfolio_attribution_%'"
    ).fetchall()}
    if any(" ".join(statement.split()) not in observed for statement in sqlite_statements()):
        raise error_factory("storage_schema_partial_upgrade")


def postgres_statements(schema: str, runtime_role: str) -> str:
    """Source generator for the immutable migration, not a runtime migrator."""
    prefix = schema + "."
    result = [f"CREATE TABLE {prefix}{name} ({ddl.format(prefix=prefix)});" for name, ddl in TABLE_DDL.items()]
    # PostgreSQL indexes inherit the table schema; index names are unqualified.
    for name, ddl in INDEX_DDL.items():
        result.append("CREATE " + ddl.format(prefix=prefix).replace(prefix + name + " ON", name + " ON") + ";")
    result.append(f"ALTER TABLE {prefix}portfolio_attribution_events ADD CONSTRAINT portfolio_attribution_attempt_fk "
                  f"FOREIGN KEY (organization_id, request_attempt_id) REFERENCES {prefix}gateway_request_attempts (organization_id, attempt_id);")
    for table in TABLE_DDL:
        result.extend((
            f"ALTER TABLE {prefix}{table} ENABLE ROW LEVEL SECURITY;",
            f"ALTER TABLE {prefix}{table} FORCE ROW LEVEL SECURITY;",
            f"CREATE POLICY {table}_tenant ON {prefix}{table} "
            "USING (organization_id = current_setting('hormuz.organization_id', true)) "
            "WITH CHECK (organization_id = current_setting('hormuz.organization_id', true));",
            f"CREATE TRIGGER {table}_immutable BEFORE UPDATE OR DELETE OR TRUNCATE ON {prefix}{table} "
            f"FOR EACH STATEMENT EXECUTE FUNCTION {prefix}portfolio_reject_mutation();",
            f"REVOKE ALL ON {prefix}{table} FROM PUBLIC;",
            f"GRANT SELECT, INSERT ON {prefix}{table} TO {runtime_role};",
        ))
    return "\n\n".join(result) + "\n"


def verify_postgres_attribution(cursor, schema: str, error_factory) -> None:
    from ._portfolio_schema import verify_postgres_owned_tables

    verify_postgres_owned_tables(cursor, schema, error_factory, TABLE_DDL, INDEX_DDL, {
        "portfolio_attribution_audit_events": {"p": 1, "u": 1, "c": 3},
        "portfolio_attribution_events": {"p": 1, "u": 3, "f": 4, "c": 6},
        "portfolio_attribution_rejections": {"p": 1, "u": 1, "f": 1, "c": 4},
        "portfolio_attribution_idempotency": {"p": 1, "f": 1, "c": 1},
        "portfolio_attribution_cursors": {"p": 1, "c": 1},
    }, trigger_type=58)
