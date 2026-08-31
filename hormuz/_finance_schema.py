"""Additive, append-only rate-card history. No usage backfill or repricing."""

from __future__ import annotations


TABLE_DDL = {
    "portfolio_finance_audit_events": """
        organization_id TEXT NOT NULL CHECK (length(organization_id) BETWEEN 1 AND 128),
        event_id TEXT NOT NULL CHECK (length(event_id) = 32),
        sequence BIGINT NOT NULL CHECK (sequence >= 1),
        actor_id TEXT NOT NULL CHECK (length(actor_id) BETWEEN 1 AND 128),
        operation TEXT NOT NULL CHECK (operation IN ('register','read')),
        rate_card_id TEXT NOT NULL CHECK (length(rate_card_id) BETWEEN 1 AND 128),
        version INTEGER NOT NULL CHECK (version BETWEEN 1 AND 2147483647),
        content_digest TEXT NOT NULL CHECK (length(content_digest) = 64),
        occurred_at TEXT NOT NULL,
        PRIMARY KEY (organization_id, event_id),
        UNIQUE (organization_id, sequence)
    """,
    "portfolio_finance_rate_cards": """
        organization_id TEXT NOT NULL CHECK (length(organization_id) BETWEEN 1 AND 128),
        rate_card_id TEXT NOT NULL CHECK (length(rate_card_id) BETWEEN 1 AND 128),
        version INTEGER NOT NULL CHECK (version BETWEEN 1 AND 2147483647),
        card_json TEXT NOT NULL CHECK (length(card_json) BETWEEN 1 AND 8192),
        content_digest TEXT NOT NULL CHECK (length(content_digest) = 64),
        receipt_id TEXT NOT NULL CHECK (length(receipt_id) = 32),
        registered_by TEXT NOT NULL CHECK (length(registered_by) BETWEEN 1 AND 128),
        registered_at TEXT NOT NULL,
        sequence BIGINT NOT NULL CHECK (sequence >= 1),
        PRIMARY KEY (organization_id, rate_card_id, version),
        UNIQUE (organization_id, receipt_id),
        FOREIGN KEY (organization_id, sequence)
            REFERENCES {prefix}portfolio_finance_audit_events (organization_id, sequence)
    """,
}


def sqlite_statements() -> tuple[str, ...]:
    statements = [f"CREATE TABLE {name} ({ddl.format(prefix='')})" for name, ddl in TABLE_DDL.items()]
    for table in TABLE_DDL:
        for operation in ("UPDATE", "DELETE"):
            statements.append(f"CREATE TRIGGER {table}_no_{operation.lower()} BEFORE {operation} ON {table} "
                              "BEGIN SELECT RAISE(ABORT, 'portfolio_append_only'); END")
    return tuple(statements)


def verify_sqlite_finance(connection, error_factory) -> None:
    observed = {" ".join(row[0].split()) for row in connection.execute(
        "SELECT sql FROM sqlite_master WHERE sql IS NOT NULL AND name LIKE 'portfolio_finance_%'"
    ).fetchall()}
    if any(" ".join(statement.split()) not in observed for statement in sqlite_statements()):
        raise error_factory("storage_schema_partial_upgrade")


def postgres_statements(schema: str, runtime_role: str) -> str:
    """Generate the checked-in migration; runtime inspection never repairs it."""
    prefix = schema + "."
    statements = [f"CREATE TABLE {prefix}{name} ({ddl.format(prefix=prefix)});" for name, ddl in TABLE_DDL.items()]
    for table in TABLE_DDL:
        statements.extend((
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
    return "\n\n".join(statements) + "\n"


def verify_postgres_finance(cursor, schema: str, error_factory) -> None:
    from ._portfolio_schema import verify_postgres_owned_tables

    expected = {
        table: {kind: ddl.count(marker) for kind, marker in (
            ("p", "PRIMARY KEY"), ("u", "UNIQUE ("), ("f", "FOREIGN KEY"), ("c", "CHECK ("),
        ) if marker in ddl}
        for table, ddl in TABLE_DDL.items()
    }
    verify_postgres_owned_tables(cursor, schema, error_factory, TABLE_DDL, {}, expected, trigger_type=58)
