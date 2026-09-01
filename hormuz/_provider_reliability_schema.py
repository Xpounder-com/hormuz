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


def postgres_statements(schema: str, runtime_role: str) -> str:
    prefix = schema + "."
    statements = [
        "-- Content-free per-attempt latency metrics and one-hop failover linkage.\n"
        "-- Both tables are append-only, tenant-isolated, and reference the immutable\n"
        "-- request-attempt roots that were committed before provider egress.",
        *(
        f"CREATE TABLE {prefix}{name} ({ddl.format(prefix=prefix)});"
        for name, ddl in TABLE_DDL.items()
        ),
    ]
    for table in TABLE_DDL:
        statements.extend(
            (
                f"ALTER TABLE {prefix}{table} ENABLE ROW LEVEL SECURITY;",
                f"ALTER TABLE {prefix}{table} FORCE ROW LEVEL SECURITY;",
                f"CREATE POLICY {table}_tenant ON {prefix}{table} "
                "USING (organization_id = current_setting('hormuz.organization_id', true)) "
                "WITH CHECK (organization_id = current_setting('hormuz.organization_id', true));",
                f"CREATE TRIGGER {table}_immutable BEFORE UPDATE OR DELETE OR TRUNCATE ON {prefix}{table} "
                f"FOR EACH STATEMENT EXECUTE FUNCTION {prefix}portfolio_reject_mutation();",
                f"REVOKE ALL ON {prefix}{table} FROM PUBLIC;",
                f"GRANT SELECT, INSERT ON {prefix}{table} TO {runtime_role};",
            )
        )
    return "\n\n".join(statements) + "\n"


def verify_postgres_provider_reliability(cursor, schema: str, error_factory) -> None:
    from ._portfolio_schema import verify_postgres_owned_tables

    expected = {
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
        {},
        expected,
        trigger_type=58,
    )
