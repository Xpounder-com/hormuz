"""Additive registry DDL and shape checks, owned outside the v1 usage adapter."""

from __future__ import annotations


# SQL types deliberately shared by SQLite and PostgreSQL. UTC timestamps are
# normalized fixed-width text, so ordering and JSON representations agree.
TABLE_DDL = {
    "portfolio_audit_events": """
        organization_id TEXT NOT NULL,
        event_id TEXT NOT NULL,
        sequence BIGINT NOT NULL CHECK (sequence >= 1),
        actor_id TEXT NOT NULL,
        operation TEXT NOT NULL CHECK (operation IN ('create_scope','version_scope','bind','show_scope','list_scopes','list_bindings')),
        entity_id TEXT,
        entity_version INTEGER,
        reason_code TEXT NOT NULL CHECK (reason_code IN ('created','corrected','reparented','archived','reactivated','tombstoned','bound','observed')),
        occurred_at TEXT NOT NULL,
        PRIMARY KEY (organization_id, event_id),
        UNIQUE (organization_id, sequence)
    """,
    "portfolio_work_scope_versions": """
        organization_id TEXT NOT NULL,
        work_scope_id TEXT NOT NULL,
        version INTEGER NOT NULL CHECK (version BETWEEN 1 AND 2147483647),
        kind TEXT NOT NULL CHECK (kind IN ('portfolio','initiative','use_case')),
        parent_work_scope_id TEXT,
        parent_version INTEGER,
        owner_team_id TEXT,
        display_name TEXT CHECK (display_name IS NULL OR length(display_name) BETWEEN 1 AND 120),
        state TEXT NOT NULL CHECK (state IN ('active','archived','tombstoned')),
        supersedes_version INTEGER,
        actor_id TEXT NOT NULL,
        reason_code TEXT NOT NULL CHECK (reason_code IN ('created','corrected','reparented','archived','reactivated','tombstoned')),
        event_at TEXT NOT NULL,
        observed_at TEXT NOT NULL,
        ingested_at TEXT NOT NULL,
        sequence BIGINT NOT NULL,
        PRIMARY KEY (organization_id, work_scope_id, version),
        UNIQUE (organization_id, sequence),
        FOREIGN KEY (organization_id, parent_work_scope_id, parent_version)
            REFERENCES {prefix}portfolio_work_scope_versions (organization_id, work_scope_id, version),
        FOREIGN KEY (organization_id, work_scope_id, supersedes_version)
            REFERENCES {prefix}portfolio_work_scope_versions (organization_id, work_scope_id, version),
        FOREIGN KEY (organization_id, sequence)
            REFERENCES {prefix}portfolio_audit_events (organization_id, sequence),
        CHECK ((parent_work_scope_id IS NULL AND parent_version IS NULL) OR (parent_work_scope_id IS NOT NULL AND parent_version IS NOT NULL)),
        CHECK ((version = 1 AND supersedes_version IS NULL) OR (version > 1 AND supersedes_version = version - 1)),
        CHECK ((state = 'tombstoned' AND display_name IS NULL) OR (state <> 'tombstoned' AND display_name IS NOT NULL))
    """,
    "portfolio_binding_events": """
        organization_id TEXT NOT NULL,
        binding_event_id TEXT NOT NULL,
        connector_id TEXT NOT NULL,
        external_object_id TEXT NOT NULL,
        work_scope_id TEXT NOT NULL,
        work_scope_version INTEGER NOT NULL,
        state TEXT NOT NULL CHECK (state IN ('active','superseded','tombstoned')),
        supersedes_event_id TEXT,
        actor_id TEXT NOT NULL,
        reason_code TEXT NOT NULL CHECK (reason_code IN ('bound','corrected','tombstoned')),
        event_at TEXT NOT NULL,
        observed_at TEXT NOT NULL,
        ingested_at TEXT NOT NULL,
        sequence BIGINT NOT NULL,
        PRIMARY KEY (organization_id, binding_event_id),
        UNIQUE (organization_id, sequence),
        UNIQUE (organization_id, connector_id, external_object_id, binding_event_id),
        FOREIGN KEY (organization_id, connector_id, external_object_id, supersedes_event_id)
            REFERENCES {prefix}portfolio_binding_events (organization_id, connector_id, external_object_id, binding_event_id),
        FOREIGN KEY (organization_id, work_scope_id, work_scope_version)
            REFERENCES {prefix}portfolio_work_scope_versions (organization_id, work_scope_id, version),
        FOREIGN KEY (organization_id, sequence)
            REFERENCES {prefix}portfolio_audit_events (organization_id, sequence)
    """,
    "portfolio_idempotency": """
        organization_id TEXT NOT NULL,
        actor_id TEXT NOT NULL,
        method TEXT NOT NULL CHECK (method = 'POST'),
        route TEXT NOT NULL,
        idempotency_key TEXT NOT NULL CHECK (length(idempotency_key) BETWEEN 1 AND 128),
        request_mac TEXT NOT NULL,
        work_scope_id TEXT,
        work_scope_version INTEGER,
        binding_event_id TEXT,
        sequence BIGINT NOT NULL,
        PRIMARY KEY (organization_id, actor_id, method, route, idempotency_key),
        FOREIGN KEY (organization_id, work_scope_id, work_scope_version)
            REFERENCES {prefix}portfolio_work_scope_versions (organization_id, work_scope_id, version),
        FOREIGN KEY (organization_id, binding_event_id)
            REFERENCES {prefix}portfolio_binding_events (organization_id, binding_event_id),
        CHECK ((work_scope_id IS NOT NULL AND work_scope_version IS NOT NULL AND binding_event_id IS NULL)
            OR (work_scope_id IS NULL AND work_scope_version IS NULL AND binding_event_id IS NOT NULL)),
        FOREIGN KEY (organization_id, sequence)
            REFERENCES {prefix}portfolio_audit_events (organization_id, sequence)
    """,
    "portfolio_cursors": """
        organization_id TEXT NOT NULL,
        cursor_id TEXT NOT NULL,
        actor_id TEXT NOT NULL,
        authority_json TEXT NOT NULL,
        operation TEXT NOT NULL CHECK (operation IN ('list_scopes','list_bindings')),
        as_of TEXT NOT NULL,
        snapshot_sequence BIGINT NOT NULL CHECK (snapshot_sequence >= 0),
        after_at TEXT NOT NULL,
        after_id TEXT NOT NULL,
        filters_json TEXT NOT NULL,
        PRIMARY KEY (organization_id, cursor_id)
    """,
}

INDEX_DDL = {
    "portfolio_scope_window": "portfolio_work_scope_versions (organization_id, event_at, work_scope_id, sequence)",
    "portfolio_binding_current": "portfolio_binding_events (organization_id, connector_id, external_object_id, sequence)",
    "portfolio_binding_window": "portfolio_binding_events (organization_id, event_at, binding_event_id, sequence)",
    "portfolio_binding_scope": "portfolio_binding_events (organization_id, work_scope_id, sequence)",
}


def sqlite_statements() -> tuple[str, ...]:
    result = [f"CREATE TABLE {name} ({ddl.format(prefix='')})" for name, ddl in TABLE_DDL.items()]
    result.extend(f"CREATE INDEX {name} ON {ddl}" for name, ddl in INDEX_DDL.items())
    for table in TABLE_DDL:
        for operation in ("UPDATE", "DELETE"):
            result.append(
                f"CREATE TRIGGER {table}_no_{operation.lower()} BEFORE {operation} ON {table} "
                "BEGIN SELECT RAISE(ABORT, 'portfolio_append_only'); END"
            )
    return tuple(result)


def verify_sqlite_registry(connection, error_factory) -> None:
    # Unlike a column-only probe, this also detects lost constraints/indexes and
    # disabled append-only triggers. SQLite preserves these owned SQL strings.
    expected = sqlite_statements()
    observed = {" ".join(row[0].split()) for row in connection.execute(
        "SELECT sql FROM sqlite_master WHERE sql IS NOT NULL AND name LIKE 'portfolio_%'"
    ).fetchall()}
    if any(" ".join(statement.split()) not in observed for statement in expected):
        raise error_factory("storage_schema_partial_upgrade")


def verify_postgres_registry(cursor, schema: str, error_factory) -> None:
    for table in TABLE_DDL:
        cursor.execute(
            "SELECT c.relrowsecurity, c.relforcerowsecurity FROM pg_class c "
            "JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=%s AND c.relname=%s AND c.relkind='r'",
            (schema, table),
        )
        row = cursor.fetchone()
        values = tuple(row.values()) if isinstance(row, dict) else row
        if values != (True, True):
            raise error_factory("storage_schema_partial_upgrade")
        cursor.execute(
            "SELECT qual, with_check, roles, cmd, permissive FROM pg_policies WHERE schemaname=%s AND tablename=%s",
            (schema, table),
        )
        policies = cursor.fetchall()
        if len(policies) != 1:
            raise error_factory("storage_schema_partial_upgrade")
        policy = tuple(policies[0].values()) if isinstance(policies[0], dict) else policies[0]
        expected = "(organization_id = current_setting('hormuz.organization_id'::text, true))"
        if policy[:2] != (expected, expected) or policy[2:] != (["public"], "ALL", "PERMISSIVE"):
            raise error_factory("storage_schema_partial_upgrade")
        cursor.execute(
            "SELECT t.tgname, t.tgenabled, t.tgtype, p.proname, p.prosrc, p.prosecdef, pn.nspname "
            "FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid "
            "JOIN pg_proc p ON p.oid=t.tgfoid JOIN pg_namespace pn ON pn.oid=p.pronamespace "
            "JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=%s AND c.relname=%s "
            "AND NOT t.tgisinternal", (schema, table),
        )
        triggers = [tuple(row.values()) if isinstance(row, dict) else row for row in cursor.fetchall()]
        body = "BEGIN RAISE EXCEPTION 'portfolio_append_only' USING ERRCODE = '23514'; END;"
        if not any(row[:4] == (table + "_immutable", "O", 27, "portfolio_reject_mutation")
                   and " ".join(row[4].split()) == body and row[5:] == (False, schema) for row in triggers):
            raise error_factory("storage_schema_partial_upgrade")
        cursor.execute(
            "SELECT con.contype, count(*) FROM pg_constraint con JOIN pg_class c ON c.oid=con.conrelid "
            "JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=%s AND c.relname=%s "
            "AND con.convalidated GROUP BY con.contype", (schema, table),
        )
        constraints = dict(tuple(row.values()) if isinstance(row, dict) else row for row in cursor.fetchall())
        required = {
            "portfolio_audit_events": {"p": 1, "u": 1, "c": 3},
            "portfolio_work_scope_versions": {"p": 1, "u": 1, "f": 3, "c": 8},
            "portfolio_binding_events": {"p": 1, "u": 2, "f": 3, "c": 2},
            "portfolio_idempotency": {"p": 1, "f": 3, "c": 3},
            "portfolio_cursors": {"p": 1, "c": 2},
        }[table]
        if any(constraints.get(kind) != count for kind, count in required.items()):
            raise error_factory("storage_schema_partial_upgrade")
        # Read all owned columns under the checking role without enumerating data.
        columns = [line.strip().split()[0] for line in TABLE_DDL[table].splitlines()
                   if line.strip() and line.strip().split()[0] not in {"PRIMARY", "UNIQUE", "FOREIGN", "REFERENCES", "CHECK", "OR"}]
        quoted = '"' + schema.replace('"', '""') + '"'
        cursor.execute(f"SELECT {', '.join(columns)} FROM {quoted}.{table} WHERE false")
    cursor.execute("SELECT indexname FROM pg_indexes WHERE schemaname=%s AND indexname LIKE 'portfolio_%%'", (schema,))
    indexes = {next(iter(row.values())) if isinstance(row, dict) else row[0] for row in cursor.fetchall()}
    if not set(INDEX_DDL).issubset(indexes):
        raise error_factory("storage_schema_partial_upgrade")
