"""SQLite 15/PostgreSQL 20 Linear reconciliation snapshot schema."""

from __future__ import annotations

from collections.abc import Mapping


SNAPSHOT_RECEIPT_TABLE = "gateway_linear_snapshot_receipts"
SNAPSHOT_CONTEXT_TABLE = "portfolio_linear_snapshot_context_events"

TABLE_DDL = {
    SNAPSHOT_RECEIPT_TABLE: """
        organization_id TEXT NOT NULL CHECK (length(organization_id) BETWEEN 1 AND 128),
        connector_id TEXT NOT NULL CHECK (length(connector_id) BETWEEN 1 AND 128),
        receipt_id TEXT NOT NULL CHECK (length(receipt_id) = 36),
        binding_version INTEGER NOT NULL CHECK (binding_version BETWEEN 1 AND 2147483647),
        reconciliation_id TEXT NOT NULL CHECK (length(reconciliation_id) = 36),
        snapshot_id TEXT NOT NULL CHECK (length(snapshot_id) = 36),
        page_id TEXT NOT NULL CHECK (length(page_id) = 36),
        page_number INTEGER NOT NULL CHECK (page_number BETWEEN 1 AND 100),
        page_count INTEGER NOT NULL CHECK (page_count BETWEEN 1 AND 100),
        credential_version TEXT NOT NULL CHECK (length(credential_version) BETWEEN 1 AND 128),
        body_fingerprint TEXT NOT NULL CHECK (length(body_fingerprint) = 64),
        fingerprint_key_version INTEGER NOT NULL CHECK (fingerprint_key_version BETWEEN 1 AND 2147483647),
        received_at TEXT NOT NULL,
        captured_at TEXT NOT NULL,
        committed_at TEXT NOT NULL,
        accepted_context_count INTEGER NOT NULL CHECK (accepted_context_count BETWEEN 0 AND 100),
        response_digest TEXT NOT NULL CHECK (length(response_digest) = 64),
        evidence_json TEXT NOT NULL CHECK (length(evidence_json) BETWEEN 2 AND 1048576),
        PRIMARY KEY (organization_id, connector_id, receipt_id),
        UNIQUE (organization_id, connector_id, page_id),
        UNIQUE (organization_id, connector_id, snapshot_id, page_number),
        UNIQUE (organization_id, connector_id, fingerprint_key_version, body_fingerprint),
        FOREIGN KEY (organization_id, connector_id, binding_version)
            REFERENCES {prefix}portfolio_linear_source_binding_versions
                (organization_id, connector_id, version),
        CHECK (page_number <= page_count)
    """,
    SNAPSHOT_CONTEXT_TABLE: """
        organization_id TEXT NOT NULL CHECK (length(organization_id) BETWEEN 1 AND 128),
        connector_id TEXT NOT NULL CHECK (length(connector_id) BETWEEN 1 AND 128),
        context_event_id TEXT NOT NULL CHECK (length(context_event_id) = 36),
        snapshot_receipt_id TEXT NOT NULL CHECK (length(snapshot_receipt_id) = 36),
        object_kind TEXT NOT NULL CHECK (object_kind IN ('initiative','project','cycle','issue')),
        object_id TEXT NOT NULL CHECK (length(object_id) = 36),
        lifecycle TEXT NOT NULL CHECK (lifecycle IN ('updated','archived')),
        normalized_state TEXT NOT NULL CHECK (normalized_state IN ('not_started','in_progress','completed','canceled','unknown')),
        relationship_coverage TEXT NOT NULL CHECK (relationship_coverage IN ('complete','not_applicable')),
        revision_kind TEXT NOT NULL CHECK (revision_kind = 'source_updated_at_v1'),
        revision_value TEXT NOT NULL CHECK (length(revision_value) BETWEEN 1 AND 64),
        ordering_state TEXT NOT NULL CHECK (ordering_state IN ('current','late','incomparable')),
        scope_state TEXT NOT NULL CHECK (scope_state IN ('matched','unmatched','excluded')),
        event_at TEXT NOT NULL,
        observed_at TEXT NOT NULL,
        ingested_at TEXT NOT NULL,
        source_fact_fingerprint TEXT NOT NULL CHECK (length(source_fact_fingerprint) = 64),
        source_fact_key_version INTEGER NOT NULL CHECK (source_fact_key_version BETWEEN 1 AND 2147483647),
        provenance_digest TEXT NOT NULL CHECK (length(provenance_digest) = 64),
        commit_sequence BIGINT NOT NULL CHECK (commit_sequence >= 1),
        evidence_json TEXT NOT NULL CHECK (length(evidence_json) BETWEEN 2 AND 1048576),
        PRIMARY KEY (organization_id, connector_id, context_event_id),
        UNIQUE (organization_id, commit_sequence),
        UNIQUE (organization_id, connector_id, source_fact_key_version, source_fact_fingerprint),
        FOREIGN KEY (organization_id, connector_id, snapshot_receipt_id)
            REFERENCES {prefix}gateway_linear_snapshot_receipts
                (organization_id, connector_id, receipt_id)
    """,
}

INDEX_DDL = {
    "gateway_linear_snapshot_receipt_time": (
        f"{SNAPSHOT_RECEIPT_TABLE} (organization_id, connector_id, committed_at, receipt_id)"
    ),
    "portfolio_linear_snapshot_context_object_time": (
        f"{SNAPSHOT_CONTEXT_TABLE} (organization_id, connector_id, object_kind, object_id, commit_sequence DESC)"
    ),
}

_CONFLICT_KEYS = {
    SNAPSHOT_RECEIPT_TABLE: (
        ("organization_id", "connector_id", "receipt_id"),
        ("organization_id", "connector_id", "page_id"),
        ("organization_id", "connector_id", "snapshot_id", "page_number"),
        ("organization_id", "connector_id", "fingerprint_key_version", "body_fingerprint"),
    ),
    SNAPSHOT_CONTEXT_TABLE: (
        ("organization_id", "connector_id", "context_event_id"),
        ("organization_id", "commit_sequence"),
        ("organization_id", "connector_id", "source_fact_key_version", "source_fact_fingerprint"),
    ),
}

SNAPSHOT_AUDIT_SOURCES = {
    "hormuz.linear-snapshot-receipt": (SNAPSHOT_RECEIPT_TABLE, "receipt_id"),
}

_SQLITE_LINEAR_AUDIT_SOURCE_TRIGGER = """CREATE TRIGGER gateway_linear_audit_source_required
BEFORE INSERT ON gateway_audit_chain_entries
WHEN NEW.entry_schema_version=2
  AND NEW.source_schema_id IN (
      'hormuz.linear-source-binding-version',
      'hormuz.linear-delivery-receipt',
      'hormuz.linear-snapshot-receipt',
      'hormuz.linear-context-event',
      'hormuz.linear-context-retention'
  )
  AND NOT (
      NEW.event_id=NEW.source_event_id AND (
          (NEW.source_schema_id='hormuz.linear-source-binding-version' AND EXISTS (
              SELECT 1 FROM portfolio_linear_source_binding_versions source
              WHERE source.organization_id=NEW.organization_id
                AND source.binding_event_id=NEW.source_event_id
                AND source.evidence_json=NEW.event_json
          )) OR
          (NEW.source_schema_id='hormuz.linear-delivery-receipt' AND EXISTS (
              SELECT 1 FROM gateway_linear_delivery_receipts source
              WHERE source.organization_id=NEW.organization_id
                AND source.receipt_id=NEW.source_event_id
                AND source.evidence_json=NEW.event_json
          )) OR
          (NEW.source_schema_id='hormuz.linear-snapshot-receipt' AND EXISTS (
              SELECT 1 FROM gateway_linear_snapshot_receipts source
              WHERE source.organization_id=NEW.organization_id
                AND source.receipt_id=NEW.source_event_id
                AND source.evidence_json=NEW.event_json
          )) OR
          (NEW.source_schema_id='hormuz.linear-context-event' AND (
              EXISTS (
                  SELECT 1 FROM portfolio_linear_context_events source
                  WHERE source.organization_id=NEW.organization_id
                    AND source.context_event_id=NEW.source_event_id
                    AND source.evidence_json=NEW.event_json
              ) OR EXISTS (
                  SELECT 1 FROM portfolio_linear_snapshot_context_events source
                  WHERE source.organization_id=NEW.organization_id
                    AND source.context_event_id=NEW.source_event_id
                    AND source.evidence_json=NEW.event_json
              )
          )) OR
          (NEW.source_schema_id='hormuz.linear-context-retention' AND EXISTS (
              SELECT 1 FROM portfolio_linear_context_retention_events source
              WHERE source.organization_id=NEW.organization_id
                AND source.retention_event_id=NEW.source_event_id
                AND source.evidence_json=NEW.event_json
          ))
      )
  )
BEGIN SELECT RAISE(ABORT, 'linear_audit_source_missing'); END"""

_SQLITE_CROSS_SEQUENCE_TRIGGERS = (
    """CREATE TRIGGER portfolio_linear_context_webhook_sequence_unique
BEFORE INSERT ON portfolio_linear_context_events
WHEN EXISTS (
    SELECT 1 FROM portfolio_linear_snapshot_context_events other
    WHERE other.organization_id=NEW.organization_id
      AND (
          other.context_event_id=NEW.context_event_id
          OR other.commit_sequence=NEW.commit_sequence
      )
)
BEGIN SELECT RAISE(ABORT, 'linear_context_cross_table_conflict'); END""",
    """CREATE TRIGGER portfolio_linear_context_snapshot_sequence_unique
BEFORE INSERT ON portfolio_linear_snapshot_context_events
WHEN EXISTS (
    SELECT 1 FROM portfolio_linear_context_events other
    WHERE other.organization_id=NEW.organization_id
      AND (
          other.context_event_id=NEW.context_event_id
          OR other.commit_sequence=NEW.commit_sequence
      )
)
BEGIN SELECT RAISE(ABORT, 'linear_context_cross_table_conflict'); END""",
)

_SQLITE_SNAPSHOT_SET_TRIGGER = """CREATE TRIGGER gateway_linear_snapshot_page_set_consistent
BEFORE INSERT ON gateway_linear_snapshot_receipts
WHEN EXISTS (
    SELECT 1 FROM gateway_linear_snapshot_receipts existing
    WHERE existing.organization_id=NEW.organization_id
      AND existing.connector_id=NEW.connector_id
      AND existing.snapshot_id=NEW.snapshot_id
      AND (
          existing.binding_version<>NEW.binding_version
          OR existing.reconciliation_id<>NEW.reconciliation_id
          OR existing.page_count<>NEW.page_count
          OR existing.captured_at<>NEW.captured_at
      )
)
BEGIN SELECT RAISE(ABORT, 'linear_snapshot_page_set_conflict'); END"""


def sqlite_statements() -> tuple[str, ...]:
    statements = [
        f"CREATE TABLE {table} ({ddl.format(prefix='')}) WITHOUT ROWID"
        for table, ddl in TABLE_DDL.items()
    ]
    statements.extend(f"CREATE INDEX {name} ON {ddl}" for name, ddl in INDEX_DDL.items())
    for table, keys in _CONFLICT_KEYS.items():
        for operation in ("UPDATE", "DELETE"):
            statements.append(
                f"CREATE TRIGGER {table}_no_{operation.lower()} BEFORE {operation} ON {table} "
                "BEGIN SELECT RAISE(ABORT, 'linear_snapshot_append_only'); END"
            )
        conflicts = " OR ".join(
            "(" + " AND ".join(f"existing.{field}=NEW.{field}" for field in key) + ")"
            for key in keys
        )
        statements.append(
            f"CREATE TRIGGER {table}_no_replace BEFORE INSERT ON {table} "
            f"WHEN EXISTS (SELECT 1 FROM {table} existing WHERE {conflicts}) "
            "BEGIN SELECT RAISE(ABORT, 'linear_snapshot_replace_refused'); END"
        )
    statements.extend(_SQLITE_CROSS_SEQUENCE_TRIGGERS)
    statements.append(_SQLITE_SNAPSHOT_SET_TRIGGER)
    return tuple(statements)


def _audit_table_v6() -> str:
    from ._linear_schema import _audit_table_v5

    predecessor = _audit_table_v5()
    marker = "                OR (source_schema_id = 'hormuz.linear-context-retention' AND source_schema_version = 1)"
    addition = marker + "\n                OR (source_schema_id = 'hormuz.linear-snapshot-receipt' AND source_schema_version = 1)"
    if predecessor.count(marker) != 1:
        raise RuntimeError("linear_snapshot_audit_schema_predecessor_changed")
    return predecessor.replace(marker, addition).replace(
        "gateway_audit_chain_entries_v4",
        "gateway_audit_chain_entries_v6",
    )


def apply_sqlite_linear_snapshot_migration(connection) -> None:
    """Apply schema 15 atomically inside the caller-owned transaction."""

    from ._finance_account_binding_schema import _AUDIT_SOURCE_TRIGGER
    from ._finance_attempt_schema import SQLITE_FINANCE_ATTEMPT_TRIGGERS
    from ._finance_collection_schema import _COLLECTION_AUDIT_TRIGGER

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
    connection.execute(_audit_table_v6())
    connection.execute(
        """
        INSERT INTO gateway_audit_chain_entries_v6 (
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
        "ALTER TABLE gateway_audit_chain_entries_v6 RENAME TO gateway_audit_chain_entries"
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
    ):
        connection.execute(statement)


def _statement_name(statement: str) -> str:
    words = statement.split()
    if len(words) >= 3 and words[:2] in (
        ["CREATE", "TABLE"], ["CREATE", "INDEX"], ["CREATE", "TRIGGER"],
    ):
        return words[2]
    return ""


def verify_sqlite_linear_snapshot(connection, error_factory) -> None:
    observed = {
        str(row["name"]): " ".join(str(row["sql"]).split())
        for row in connection.execute(
            "SELECT name, sql FROM sqlite_master WHERE sql IS NOT NULL"
        ).fetchall()
    }
    expected = [*sqlite_statements(), _SQLITE_LINEAR_AUDIT_SOURCE_TRIGGER]
    if any(
        observed.get(_statement_name(statement)) != " ".join(statement.split())
        for statement in expected
    ):
        raise error_factory("storage_schema_partial_upgrade")
    audit_sql = observed.get("gateway_audit_chain_entries", "")
    if "hormuz.linear-snapshot-receipt" not in audit_sql:
        raise error_factory("storage_schema_partial_upgrade")


def verify_postgres_linear_snapshot(cursor, schema: str, error_factory) -> None:
    from ._portfolio_schema import verify_postgres_owned_tables

    expected_constraints = {
        table: {
            kind: ddl.count(marker)
            for kind, marker in (
                ("p", "PRIMARY KEY"), ("u", "UNIQUE ("),
                ("f", "FOREIGN KEY"), ("c", "CHECK ("),
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
    if not isinstance(definition, str) or "hormuz.linear-snapshot-receipt" not in definition:
        raise error_factory("storage_schema_partial_upgrade")
    for function in (
        "enforce_custody_audit_chain_entry_insert",
        "custody_audit_chain_source_event_json",
        "enforce_linear_snapshot_page_set",
        "enforce_linear_context_cross_capture",
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
        if function == "enforce_linear_snapshot_page_set":
            required = (
                SNAPSHOT_RECEIPT_TABLE,
                "pg_advisory_xact_lock",
                "binding_version",
                "reconciliation_id",
                "page_count",
                "captured_at",
            )
        elif function == "enforce_linear_context_cross_capture":
            required = (
                "pg_advisory_xact_lock",
                "portfolio_linear_context_events",
                SNAPSHOT_CONTEXT_TABLE,
                "context_event_id",
                "commit_sequence",
            )
        else:
            required = (
                "hormuz.linear-snapshot-receipt",
                SNAPSHOT_RECEIPT_TABLE,
                SNAPSHOT_CONTEXT_TABLE,
            )
        if (
            len(values) != 3
            or any(item not in body for item in required)
            or values[1] is not True
            or values[2] != ["search_path=pg_catalog"]
        ):
            raise error_factory("storage_schema_partial_upgrade")
