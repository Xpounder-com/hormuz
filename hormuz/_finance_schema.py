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
    # No hidden rowid identity may bypass the declared conflict-key guards.
    statements = [f"CREATE TABLE {name} ({ddl.format(prefix='')}) WITHOUT ROWID" for name, ddl in TABLE_DDL.items()]
    conflict_keys = {
        "portfolio_finance_audit_events": (("organization_id", "event_id"), ("organization_id", "sequence")),
        "portfolio_finance_rate_cards": (("organization_id", "rate_card_id", "version"), ("organization_id", "receipt_id")),
    }
    for table in TABLE_DDL:
        for operation in ("UPDATE", "DELETE"):
            statements.append(f"CREATE TRIGGER {table}_no_{operation.lower()} BEFORE {operation} ON {table} "
                              "BEGIN SELECT RAISE(ABORT, 'portfolio_append_only'); END")
        # REPLACE's implicit deletes need not fire DELETE triggers. Refuse a
        # collision on every declared key before insertion, independently of
        # recursive_triggers. ABORT also undoes earlier rows of this statement.
        conflicts = " OR ".join("(" + " AND ".join(f"{field}=NEW.{field}" for field in key) + ")"
                                for key in conflict_keys[table])
        statements.append(f"CREATE TRIGGER {table}_no_replace BEFORE INSERT ON {table} "
                          f"WHEN EXISTS (SELECT 1 FROM {table} WHERE {conflicts}) "
                          "BEGIN SELECT RAISE(ABORT, 'portfolio_append_only'); END")
    return tuple(statements)


def verify_sqlite_finance(connection, error_factory) -> None:
    observed = {" ".join(row[0].split()) for row in connection.execute(
        "SELECT sql FROM sqlite_master WHERE sql IS NOT NULL AND name LIKE 'portfolio_finance_%'"
    ).fetchall()}
    if any(" ".join(statement.split()) not in observed for statement in sqlite_statements()):
        raise error_factory("storage_schema_partial_upgrade")
