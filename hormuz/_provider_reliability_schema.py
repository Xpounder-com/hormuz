"""Additive provider latency metrics and one-hop failover linkage."""

from __future__ import annotations


TABLE_DDL = {
    "gateway_provider_attempt_metrics": """
        event_id TEXT NOT NULL CHECK (length(event_id) = 36),
        event_schema_id TEXT NOT NULL CHECK (event_schema_id = 'hormuz.provider-attempt-metrics'),
        event_schema_version INTEGER NOT NULL CHECK (event_schema_version = 1),
        organization_id TEXT NOT NULL CHECK (length(organization_id) BETWEEN 1 AND 128),
        attempt_id TEXT NOT NULL CHECK (length(attempt_id) BETWEEN 1 AND 128),
        recorded_at TEXT NOT NULL,
        provider_status INTEGER CHECK (provider_status BETWEEN 100 AND 599),
        response_headers_us BIGINT CHECK (response_headers_us >= 0),
        first_body_byte_us BIGINT CHECK (first_body_byte_us >= 0),
        total_us BIGINT NOT NULL CHECK (total_us >= 0),
        provider_bytes_read BIGINT NOT NULL CHECK (provider_bytes_read >= 0),
        downstream_bytes_sent BIGINT NOT NULL CHECK (downstream_bytes_sent >= 0),
        PRIMARY KEY (organization_id, event_id),
        UNIQUE (organization_id, attempt_id),
        FOREIGN KEY (organization_id, attempt_id)
            REFERENCES {prefix}gateway_request_attempts (organization_id, attempt_id),
        CHECK (response_headers_us IS NULL OR response_headers_us <= total_us),
        CHECK (
            first_body_byte_us IS NULL
            OR (
                response_headers_us IS NOT NULL
                AND first_body_byte_us >= response_headers_us
                AND first_body_byte_us <= total_us
            )
        ),
        CHECK (downstream_bytes_sent <= provider_bytes_read)
    """,
    "gateway_provider_failover_events": """
        event_id TEXT NOT NULL CHECK (length(event_id) = 36),
        event_schema_id TEXT NOT NULL CHECK (event_schema_id = 'hormuz.provider-failover'),
        event_schema_version INTEGER NOT NULL CHECK (event_schema_version = 1),
        organization_id TEXT NOT NULL CHECK (length(organization_id) BETWEEN 1 AND 128),
        original_attempt_id TEXT NOT NULL CHECK (length(original_attempt_id) BETWEEN 1 AND 128),
        failover_attempt_id TEXT NOT NULL CHECK (length(failover_attempt_id) BETWEEN 1 AND 128),
        trigger_status INTEGER NOT NULL CHECK (trigger_status IN (429, 529)),
        reason_code TEXT NOT NULL CHECK (reason_code IN ('provider_rate_limited', 'provider_overloaded')),
        recorded_at TEXT NOT NULL,
        PRIMARY KEY (organization_id, event_id),
        UNIQUE (organization_id, original_attempt_id),
        UNIQUE (organization_id, failover_attempt_id),
        FOREIGN KEY (organization_id, original_attempt_id)
            REFERENCES {prefix}gateway_request_attempts (organization_id, attempt_id),
        FOREIGN KEY (organization_id, failover_attempt_id)
            REFERENCES {prefix}gateway_request_attempts (organization_id, attempt_id),
        CHECK (original_attempt_id <> failover_attempt_id),
        CHECK (
            (trigger_status = 429 AND reason_code = 'provider_rate_limited')
            OR (trigger_status = 529 AND reason_code = 'provider_overloaded')
        )
    """,
}


def sqlite_statements() -> tuple[str, ...]:
    statements = [
        "CREATE UNIQUE INDEX gateway_provider_attempt_parent "
        "ON gateway_request_attempts (organization_id, attempt_id)",
        *(
        f"CREATE TABLE {name} ({ddl.format(prefix='')}) WITHOUT ROWID"
        for name, ddl in TABLE_DDL.items()
        ),
    ]
    conflict_keys = {
        "gateway_provider_attempt_metrics": (
            ("organization_id", "event_id"),
            ("organization_id", "attempt_id"),
        ),
        "gateway_provider_failover_events": (
            ("organization_id", "event_id"),
            ("organization_id", "original_attempt_id"),
            ("organization_id", "failover_attempt_id"),
        ),
    }
    for table in TABLE_DDL:
        for operation in ("UPDATE", "DELETE"):
            statements.append(
                f"CREATE TRIGGER {table}_no_{operation.lower()} BEFORE {operation} ON {table} "
                "BEGIN SELECT RAISE(ABORT, 'provider_reliability_append_only'); END"
            )
        conflicts = " OR ".join(
            "(" + " AND ".join(f"{field}=NEW.{field}" for field in key) + ")"
            for key in conflict_keys[table]
        )
        statements.append(
            f"CREATE TRIGGER {table}_no_replace BEFORE INSERT ON {table} "
            f"WHEN EXISTS (SELECT 1 FROM {table} WHERE {conflicts}) "
            "BEGIN SELECT RAISE(ABORT, 'provider_reliability_append_only'); END"
        )
    return tuple(statements)


def verify_sqlite_provider_reliability(connection, error_factory) -> None:
    observed = {
        " ".join(row[0].split())
        for row in connection.execute(
            "SELECT sql FROM sqlite_master WHERE sql IS NOT NULL "
            "AND name LIKE 'gateway_provider_%'"
        ).fetchall()
    }
    if any(" ".join(statement.split()) not in observed for statement in sqlite_statements()):
        raise error_factory("storage_schema_partial_upgrade")


def sqlite_translated_metrics_statements() -> tuple[str, ...]:
    """Add v2 byte observations without changing legacy append-only evidence."""
    table = "gateway_provider_attempt_metrics_v2"
    ddl = TABLE_DDL["gateway_provider_attempt_metrics"].replace(
        "event_schema_version = 1", "event_schema_version = 2").replace(
        ",\n        CHECK (downstream_bytes_sent <= provider_bytes_read)", "")
    return (
        f"CREATE TABLE {table} ({ddl.format(prefix='')}) WITHOUT ROWID",
        *(f"CREATE TRIGGER {table}_no_{operation.lower()} BEFORE {operation} ON {table} "
          "BEGIN SELECT RAISE(ABORT, 'provider_reliability_append_only'); END"
          for operation in ("UPDATE", "DELETE")),
        f"CREATE TRIGGER {table}_no_replace BEFORE INSERT ON {table} "
        f"WHEN EXISTS (SELECT 1 FROM {table} WHERE organization_id=NEW.organization_id "
        "AND (event_id=NEW.event_id OR attempt_id=NEW.attempt_id)) "
        "OR EXISTS (SELECT 1 FROM gateway_provider_attempt_metrics WHERE organization_id=NEW.organization_id "
        "AND attempt_id=NEW.attempt_id) "
        "BEGIN SELECT RAISE(ABORT, 'provider_reliability_append_only'); END",
        "CREATE TRIGGER gateway_provider_attempt_metrics_no_v2 BEFORE INSERT ON gateway_provider_attempt_metrics "
        f"WHEN EXISTS (SELECT 1 FROM {table} WHERE organization_id=NEW.organization_id AND attempt_id=NEW.attempt_id) "
        "BEGIN SELECT RAISE(ABORT, 'provider_reliability_append_only'); END",
    )


def verify_sqlite_translated_metrics(connection, error_factory) -> None:
    observed = {" ".join(row[0].split()) for row in connection.execute(
        "SELECT sql FROM sqlite_master WHERE sql IS NOT NULL AND name LIKE 'gateway_provider_%'").fetchall()}
    if any(" ".join(statement.split()) not in observed for statement in sqlite_translated_metrics_statements()):
        raise error_factory("storage_schema_partial_upgrade")
