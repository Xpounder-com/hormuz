"""SQLite 16/PostgreSQL 21 run-to-outcome association schema."""

from __future__ import annotations

from collections.abc import Mapping


AUDIT_TABLE = "portfolio_association_audit_events"
LINK_TABLE = "portfolio_run_work_link_events"
IDEMPOTENCY_TABLE = "portfolio_run_work_link_idempotency"
ASSOCIATION_TABLE = "portfolio_run_outcome_association_events"
CURSOR_TABLE = "portfolio_run_outcome_association_cursors"


TABLE_DDL = {
    AUDIT_TABLE: """
        organization_id TEXT NOT NULL CHECK (length(organization_id) BETWEEN 1 AND 128),
        event_id TEXT NOT NULL CHECK (length(event_id) = 36),
        sequence BIGINT NOT NULL CHECK (sequence >= 1),
        actor_id TEXT,
        operation TEXT NOT NULL CHECK (operation IN ('link','correct','tombstone','evaluate','list_links','list_associations','read_metrics')),
        entity_id TEXT,
        reason_code TEXT NOT NULL CHECK (reason_code IN ('explicit_link','corrected','tombstoned','associated','unmatched','ambiguous','excluded','observed')),
        occurred_at TEXT NOT NULL,
        PRIMARY KEY (organization_id, event_id),
        UNIQUE (organization_id, sequence)
    """,
    LINK_TABLE: """
        organization_id TEXT NOT NULL CHECK (length(organization_id) BETWEEN 1 AND 128),
        link_event_id TEXT NOT NULL CHECK (length(link_event_id) = 36),
        request_attempt_id TEXT NOT NULL CHECK (length(request_attempt_id) BETWEEN 1 AND 128),
        attribution_event_id TEXT NOT NULL CHECK (length(attribution_event_id) BETWEEN 1 AND 128),
        connector_id TEXT NOT NULL CHECK (length(connector_id) BETWEEN 1 AND 128),
        source_event_id TEXT NOT NULL CHECK (length(source_event_id) BETWEEN 1 AND 256),
        external_object_id TEXT NOT NULL CHECK (length(external_object_id) BETWEEN 1 AND 256),
        source_revision TEXT CHECK (source_revision IS NULL OR length(source_revision) BETWEEN 1 AND 256),
        work_scope_id TEXT NOT NULL CHECK (length(work_scope_id) BETWEEN 1 AND 128),
        work_scope_version INTEGER NOT NULL CHECK (work_scope_version BETWEEN 1 AND 2147483647),
        binding_event_id TEXT NOT NULL CHECK (length(binding_event_id) BETWEEN 1 AND 128),
        state TEXT NOT NULL CHECK (state IN ('active','tombstoned')),
        evidence_level TEXT NOT NULL CHECK (evidence_level = 'associated'),
        supersedes_link_event_id TEXT,
        actor_id TEXT NOT NULL CHECK (length(actor_id) BETWEEN 1 AND 128),
        reason_code TEXT NOT NULL CHECK (reason_code IN ('explicit_link','corrected','tombstoned')),
        event_at TEXT NOT NULL,
        observed_at TEXT NOT NULL,
        ingested_at TEXT NOT NULL,
        sequence BIGINT NOT NULL CHECK (sequence >= 1),
        evidence_json TEXT NOT NULL CHECK (length(evidence_json) BETWEEN 2 AND 65536),
        PRIMARY KEY (organization_id, link_event_id),
        UNIQUE (organization_id, request_attempt_id, connector_id, source_event_id, supersedes_link_event_id),
        FOREIGN KEY (organization_id, request_attempt_id)
            REFERENCES {prefix}gateway_request_attempts (organization_id, attempt_id),
        FOREIGN KEY (organization_id, attribution_event_id)
            REFERENCES {prefix}portfolio_attribution_events (organization_id, attribution_event_id),
        FOREIGN KEY (organization_id, connector_id, source_event_id)
            REFERENCES {prefix}portfolio_outcome_events (organization_id, connector_id, source_event_id),
        FOREIGN KEY (organization_id, work_scope_id, work_scope_version)
            REFERENCES {prefix}portfolio_work_scope_versions (organization_id, work_scope_id, version),
        FOREIGN KEY (organization_id, binding_event_id)
            REFERENCES {prefix}portfolio_binding_events (organization_id, binding_event_id),
        FOREIGN KEY (organization_id, supersedes_link_event_id)
            REFERENCES {prefix}portfolio_run_work_link_events (organization_id, link_event_id),
        FOREIGN KEY (organization_id, sequence)
            REFERENCES {prefix}portfolio_association_audit_events (organization_id, sequence),
        CHECK (
            (state='active' AND reason_code IN ('explicit_link','corrected'))
            OR (state='tombstoned' AND reason_code='tombstoned')
        )
    """,
    IDEMPOTENCY_TABLE: """
        organization_id TEXT NOT NULL CHECK (length(organization_id) BETWEEN 1 AND 128),
        actor_id TEXT NOT NULL CHECK (length(actor_id) BETWEEN 1 AND 128),
        idempotency_key TEXT NOT NULL CHECK (length(idempotency_key) BETWEEN 1 AND 128),
        request_mac TEXT NOT NULL CHECK (length(request_mac) = 64),
        key_version INTEGER NOT NULL CHECK (key_version BETWEEN 1 AND 2147483647),
        link_event_id TEXT NOT NULL CHECK (length(link_event_id) = 36),
        PRIMARY KEY (organization_id, actor_id, idempotency_key),
        FOREIGN KEY (organization_id, link_event_id)
            REFERENCES {prefix}portfolio_run_work_link_events (organization_id, link_event_id)
    """,
    ASSOCIATION_TABLE: """
        organization_id TEXT NOT NULL CHECK (length(organization_id) BETWEEN 1 AND 128),
        association_event_id TEXT NOT NULL CHECK (length(association_event_id) = 36),
        connector_id TEXT NOT NULL CHECK (length(connector_id) BETWEEN 1 AND 128),
        source_event_id TEXT NOT NULL CHECK (length(source_event_id) BETWEEN 1 AND 256),
        external_object_id TEXT NOT NULL CHECK (length(external_object_id) BETWEEN 1 AND 256),
        source_revision TEXT CHECK (source_revision IS NULL OR length(source_revision) BETWEEN 1 AND 256),
        request_attempt_id TEXT,
        work_scope_id TEXT,
        work_scope_version INTEGER,
        rule_id TEXT NOT NULL CHECK (length(rule_id) BETWEEN 1 AND 128),
        rule_version INTEGER NOT NULL CHECK (rule_version BETWEEN 1 AND 2147483647),
        rule_digest TEXT NOT NULL CHECK (length(rule_digest) = 64),
        window_id TEXT NOT NULL CHECK (length(window_id) = 64),
        window_start_at TEXT NOT NULL,
        window_end_at TEXT NOT NULL,
        evaluation_as_of TEXT NOT NULL,
        snapshot_sequence BIGINT NOT NULL CHECK (snapshot_sequence >= 0),
        candidate_count INTEGER NOT NULL CHECK (candidate_count BETWEEN 0 AND 2147483647),
        state TEXT NOT NULL CHECK (state IN ('unmatched','ambiguous','associated','excluded')),
        evidence_level TEXT NOT NULL CHECK (evidence_level IN ('descriptive','associated')),
        link_event_id TEXT,
        supersedes_event_id TEXT,
        reason_code TEXT NOT NULL CHECK (reason_code IN ('explicit_link','missing_evidence','multiple_eligible_links','source_conflict','source_excluded','source_superseded','link_tombstoned','unsupported')),
        event_at TEXT NOT NULL,
        observed_at TEXT NOT NULL,
        ingested_at TEXT NOT NULL,
        sequence BIGINT NOT NULL CHECK (sequence >= 1),
        evidence_json TEXT NOT NULL CHECK (length(evidence_json) BETWEEN 2 AND 65536),
        PRIMARY KEY (organization_id, association_event_id),
        UNIQUE (organization_id, sequence),
        FOREIGN KEY (organization_id, connector_id, source_event_id)
            REFERENCES {prefix}portfolio_outcome_events (organization_id, connector_id, source_event_id),
        FOREIGN KEY (organization_id, request_attempt_id)
            REFERENCES {prefix}gateway_request_attempts (organization_id, attempt_id),
        FOREIGN KEY (organization_id, work_scope_id, work_scope_version)
            REFERENCES {prefix}portfolio_work_scope_versions (organization_id, work_scope_id, version),
        FOREIGN KEY (organization_id, link_event_id)
            REFERENCES {prefix}portfolio_run_work_link_events (organization_id, link_event_id),
        FOREIGN KEY (organization_id, supersedes_event_id)
            REFERENCES {prefix}portfolio_run_outcome_association_events (organization_id, association_event_id),
        FOREIGN KEY (organization_id, sequence)
            REFERENCES {prefix}portfolio_association_audit_events (organization_id, sequence),
        CHECK ((work_scope_id IS NULL AND work_scope_version IS NULL) OR (work_scope_id IS NOT NULL AND work_scope_version IS NOT NULL)),
        CHECK (window_start_at < window_end_at),
        CHECK ((state='associated' AND evidence_level='associated' AND request_attempt_id IS NOT NULL AND work_scope_id IS NOT NULL AND link_event_id IS NOT NULL AND candidate_count=1) OR (state<>'associated' AND evidence_level='descriptive'))
    """,
    CURSOR_TABLE: """
        organization_id TEXT NOT NULL CHECK (length(organization_id) BETWEEN 1 AND 128),
        cursor_id TEXT NOT NULL CHECK (length(cursor_id) = 64),
        actor_id TEXT NOT NULL CHECK (length(actor_id) BETWEEN 1 AND 128),
        authority_digest TEXT NOT NULL CHECK (length(authority_digest) = 64),
        as_of TEXT NOT NULL,
        snapshot_sequence BIGINT NOT NULL CHECK (snapshot_sequence >= 0),
        after_at TEXT NOT NULL,
        after_id TEXT NOT NULL CHECK (length(after_id) = 36),
        filters_digest TEXT NOT NULL CHECK (length(filters_digest) = 64),
        PRIMARY KEY (organization_id, cursor_id)
    """,
}


INDEX_DDL = {
    "portfolio_run_work_link_attempt": (
        f"{LINK_TABLE} (organization_id, request_attempt_id, sequence)"
    ),
    "portfolio_run_work_link_outcome": (
        f"{LINK_TABLE} (organization_id, connector_id, source_event_id, sequence)"
    ),
    "portfolio_run_work_link_root": (
        f"{LINK_TABLE} (organization_id, request_attempt_id, connector_id, source_event_id) "
        "WHERE supersedes_link_event_id IS NULL"
    ),
    "portfolio_run_work_link_lineage": (
        f"{LINK_TABLE} (organization_id, supersedes_link_event_id) "
        "WHERE supersedes_link_event_id IS NOT NULL"
    ),
    "portfolio_run_outcome_association_window": (
        f"{ASSOCIATION_TABLE} (organization_id, event_at, association_event_id, sequence)"
    ),
    "portfolio_run_outcome_association_scope": (
        f"{ASSOCIATION_TABLE} (organization_id, work_scope_id, work_scope_version, sequence)"
    ),
    "portfolio_run_outcome_association_root": (
        f"{ASSOCIATION_TABLE} (organization_id, connector_id, source_event_id, rule_id, rule_version, window_id) "
        "WHERE supersedes_event_id IS NULL"
    ),
    "portfolio_run_outcome_association_lineage": (
        f"{ASSOCIATION_TABLE} (organization_id, supersedes_event_id) "
        "WHERE supersedes_event_id IS NOT NULL"
    ),
}


UNIQUE_INDEXES = frozenset({
    "portfolio_run_work_link_root",
    "portfolio_run_work_link_lineage",
    "portfolio_run_outcome_association_root",
    "portfolio_run_outcome_association_lineage",
})


SQLITE_AUDIT_SOURCE_TRIGGER = """CREATE TRIGGER gateway_association_audit_source_required
BEFORE INSERT ON gateway_audit_chain_entries
WHEN NEW.entry_schema_version=2
  AND NEW.source_schema_id IN (
      'hormuz.run-work-link-event',
      'hormuz.run-outcome-association-event'
  )
  AND NOT (
      NEW.event_id=NEW.source_event_id AND (
          (NEW.source_schema_id='hormuz.run-work-link-event' AND EXISTS (
              SELECT 1 FROM portfolio_run_work_link_events source
              WHERE source.organization_id=NEW.organization_id
                AND source.link_event_id=NEW.source_event_id
                AND source.evidence_json=NEW.event_json
          )) OR
          (NEW.source_schema_id='hormuz.run-outcome-association-event' AND EXISTS (
              SELECT 1 FROM portfolio_run_outcome_association_events source
              WHERE source.organization_id=NEW.organization_id
                AND source.association_event_id=NEW.source_event_id
                AND source.evidence_json=NEW.event_json
          ))
      )
  )
BEGIN SELECT RAISE(ABORT, 'association_audit_source_missing'); END"""


def sqlite_statements() -> tuple[str, ...]:
    statements = [
        f"CREATE TABLE {table} ({ddl.format(prefix='')}) WITHOUT ROWID"
        for table, ddl in TABLE_DDL.items()
    ]
    for name, ddl in INDEX_DDL.items():
        unique = "UNIQUE " if name in UNIQUE_INDEXES else ""
        statements.append(f"CREATE {unique}INDEX {name} ON {ddl}")
    for table in TABLE_DDL:
        for operation in ("UPDATE", "DELETE"):
            statements.append(
                f"CREATE TRIGGER {table}_no_{operation.lower()} BEFORE {operation} ON {table} "
                "BEGIN SELECT RAISE(ABORT, 'association_metadata_append_only'); END"
            )
    return tuple(statements)


def _audit_table_v7() -> str:
    from ._linear_snapshot_schema import _audit_table_v6

    predecessor = _audit_table_v6()
    marker = "                OR (source_schema_id = 'hormuz.linear-snapshot-receipt' AND source_schema_version = 1)"
    addition = (
        marker
        + "\n                OR (source_schema_id = 'hormuz.run-work-link-event' AND source_schema_version = 1)"
        + "\n                OR (source_schema_id = 'hormuz.run-outcome-association-event' AND source_schema_version = 1)"
    )
    if predecessor.count(marker) != 1:
        raise RuntimeError("association_audit_schema_predecessor_changed")
    return predecessor.replace(marker, addition).replace(
        "gateway_audit_chain_entries_v6",
        "gateway_audit_chain_entries_v7",
    )


def apply_sqlite_association_migration(connection) -> None:
    """Apply schema 16 atomically inside the caller-owned transaction."""

    from ._finance_account_binding_schema import _AUDIT_SOURCE_TRIGGER
    from ._finance_attempt_schema import SQLITE_FINANCE_ATTEMPT_TRIGGERS
    from ._finance_collection_schema import _COLLECTION_AUDIT_TRIGGER
    from ._linear_snapshot_schema import _SQLITE_LINEAR_AUDIT_SOURCE_TRIGGER

    for statement in sqlite_statements():
        connection.execute(statement)
    for statement in (
        "DROP TRIGGER gateway_audit_chain_entries_no_update",
        "DROP TRIGGER gateway_audit_chain_entries_no_delete",
        "DROP TRIGGER gateway_finance_attempt_audit_source_required",
        "DROP TRIGGER gateway_finance_collection_audit_source_required",
        "DROP TRIGGER gateway_finance_account_audit_source_required",
        "DROP TRIGGER gateway_linear_audit_source_required",
        "DROP INDEX idx_gateway_audit_chain_entries_event",
        "DROP INDEX idx_gateway_audit_chain_entries_source_identity",
    ):
        connection.execute(statement)
    connection.execute(_audit_table_v7())
    connection.execute(
        """
        INSERT INTO gateway_audit_chain_entries_v7 (
            organization_id, chain_version, chain_epoch, sequence,
            entry_schema_id, entry_schema_version, event_id, previous_digest,
            event_digest, event_json, appended_at, source_schema_id,
            source_schema_version, source_event_id
        )
        SELECT organization_id, chain_version, chain_epoch, sequence,
               entry_schema_id, entry_schema_version, event_id, previous_digest,
               event_digest, event_json, appended_at, source_schema_id,
               source_schema_version, source_event_id
        FROM gateway_audit_chain_entries
        """
    )
    connection.execute("DROP TABLE gateway_audit_chain_entries")
    connection.execute(
        "ALTER TABLE gateway_audit_chain_entries_v7 RENAME TO gateway_audit_chain_entries"
    )
    for statement in (
        "CREATE INDEX idx_gateway_audit_chain_entries_event ON gateway_audit_chain_entries (organization_id, event_id)",
        "CREATE UNIQUE INDEX idx_gateway_audit_chain_entries_source_identity ON gateway_audit_chain_entries (organization_id, source_schema_id, source_schema_version, source_event_id) WHERE entry_schema_version = 2",
        "CREATE TRIGGER gateway_audit_chain_entries_no_update BEFORE UPDATE ON gateway_audit_chain_entries BEGIN SELECT RAISE(ABORT, 'audit_chain_entry_immutable'); END",
        "CREATE TRIGGER gateway_audit_chain_entries_no_delete BEFORE DELETE ON gateway_audit_chain_entries BEGIN SELECT RAISE(ABORT, 'audit_chain_entry_immutable'); END",
        SQLITE_FINANCE_ATTEMPT_TRIGGERS["gateway_finance_attempt_audit_source_required"],
        _COLLECTION_AUDIT_TRIGGER,
        _AUDIT_SOURCE_TRIGGER,
        _SQLITE_LINEAR_AUDIT_SOURCE_TRIGGER,
        SQLITE_AUDIT_SOURCE_TRIGGER,
    ):
        connection.execute(statement)


def _statement_name(statement: str) -> str:
    words = statement.split()
    if len(words) >= 3 and words[:2] in (
        ["CREATE", "TABLE"],
        ["CREATE", "INDEX"],
        ["CREATE", "TRIGGER"],
    ):
        return words[2]
    if len(words) >= 4 and words[:2] == ["CREATE", "UNIQUE"] and words[2] == "INDEX":
        return words[3]
    return ""


def verify_sqlite_association(connection, error_factory) -> None:
    observed = {
        str(row["name"]): " ".join(str(row["sql"]).split())
        for row in connection.execute(
            "SELECT name, sql FROM sqlite_master WHERE sql IS NOT NULL"
        ).fetchall()
    }
    expected = [*sqlite_statements(), SQLITE_AUDIT_SOURCE_TRIGGER]
    if any(
        observed.get(_statement_name(statement)) != " ".join(statement.split())
        for statement in expected
    ):
        raise error_factory("storage_schema_partial_upgrade")
    audit_sql = observed.get("gateway_audit_chain_entries", "")
    if any(
        schema_id not in audit_sql
        for schema_id in (
            "hormuz.run-work-link-event",
            "hormuz.run-outcome-association-event",
        )
    ):
        raise error_factory("storage_schema_partial_upgrade")


def verify_postgres_association(cursor, schema: str, error_factory) -> None:
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
    cursor.execute(
        "SELECT pg_get_constraintdef(con.oid) AS definition FROM pg_constraint con "
        "JOIN pg_class rel ON rel.oid=con.conrelid "
        "JOIN pg_namespace n ON n.oid=rel.relnamespace "
        "WHERE n.nspname=%s AND rel.relname='gateway_audit_chain_entries' "
        "AND con.conname='gateway_audit_chain_entries_source_identity_check'",
        (schema,),
    )
    row = cursor.fetchone()
    definition = None if row is None else (
        row["definition"] if isinstance(row, Mapping) else row[0]
    )
    if not isinstance(definition, str) or any(
        schema_id not in definition
        for schema_id in (
            "hormuz.run-work-link-event",
            "hormuz.run-outcome-association-event",
        )
    ):
        raise error_factory("storage_schema_partial_upgrade")
    for function in (
        "enforce_custody_audit_chain_entry_insert",
        "custody_audit_chain_source_event_json",
    ):
        cursor.execute(
            "SELECT p.prosrc,p.prosecdef,p.proconfig FROM pg_proc p "
            "JOIN pg_namespace n ON n.oid=p.pronamespace "
            "WHERE n.nspname=%s AND p.proname=%s",
            (schema, function),
        )
        row = cursor.fetchone()
        values = tuple(row.values()) if isinstance(row, Mapping) else tuple(row or ())
        body = "" if not values else str(values[0])
        if (
            len(values) != 3
            or any(
                item not in body
                for item in (
                    "hormuz.run-work-link-event",
                    "hormuz.run-outcome-association-event",
                    LINK_TABLE,
                    ASSOCIATION_TABLE,
                )
            )
            or values[1] is not True
            or values[2] != ["search_path=pg_catalog"]
        ):
            raise error_factory("storage_schema_partial_upgrade")
