"""SQLite 14/PostgreSQL 19 metadata-only Linear connector schema."""

from __future__ import annotations

from collections.abc import Mapping


BINDING_TABLE = "portfolio_linear_source_binding_versions"
RECEIPT_TABLE = "gateway_linear_delivery_receipts"
CONTEXT_TABLE = "portfolio_linear_context_events"
RETENTION_TABLE = "portfolio_linear_context_retention_events"
POSTGRES_CLAIM_TABLE = "gateway_linear_route_claims"

TABLE_DDL = {
    BINDING_TABLE: """
        organization_id TEXT NOT NULL CHECK (length(organization_id) BETWEEN 1 AND 128),
        connector_id TEXT NOT NULL CHECK (length(connector_id) BETWEEN 1 AND 128),
        version INTEGER NOT NULL CHECK (version BETWEEN 1 AND 2147483647),
        binding_event_id TEXT NOT NULL CHECK (length(binding_event_id) = 36),
        previous_version INTEGER,
        binding_state TEXT NOT NULL CHECK (binding_state IN ('active','revoked')),
        source_workspace_id TEXT NOT NULL CHECK (length(source_workspace_id) = 36),
        source_webhook_id TEXT NOT NULL CHECK (length(source_webhook_id) = 36),
        source_team_ids_json TEXT NOT NULL CHECK (length(source_team_ids_json) BETWEEN 2 AND 65536),
        typed_enrollment_json TEXT NOT NULL CHECK (length(typed_enrollment_json) BETWEEN 2 AND 1048576),
        credential_version TEXT NOT NULL CHECK (length(credential_version) BETWEEN 1 AND 128),
        fingerprint_key_version INTEGER NOT NULL CHECK (fingerprint_key_version BETWEEN 1 AND 2147483647),
        content_digest TEXT NOT NULL CHECK (length(content_digest) = 64),
        request_digest TEXT NOT NULL CHECK (length(request_digest) = 64),
        registered_by TEXT NOT NULL CHECK (length(registered_by) BETWEEN 1 AND 128),
        registered_at TEXT NOT NULL,
        evidence_json TEXT NOT NULL CHECK (length(evidence_json) BETWEEN 2 AND 1048576),
        PRIMARY KEY (organization_id, connector_id, version),
        UNIQUE (organization_id, binding_event_id),
        UNIQUE (organization_id, connector_id, request_digest),
        CHECK (
            (version=1 AND previous_version IS NULL AND binding_state='active')
            OR (version>1 AND previous_version=version-1)
        )
    """,
    RECEIPT_TABLE: """
        organization_id TEXT NOT NULL CHECK (length(organization_id) BETWEEN 1 AND 128),
        connector_id TEXT NOT NULL CHECK (length(connector_id) BETWEEN 1 AND 128),
        receipt_id TEXT NOT NULL CHECK (length(receipt_id) = 36),
        binding_version INTEGER NOT NULL CHECK (binding_version BETWEEN 1 AND 2147483647),
        source_delivery_id TEXT NOT NULL CHECK (length(source_delivery_id) BETWEEN 1 AND 128),
        credential_version TEXT NOT NULL CHECK (length(credential_version) BETWEEN 1 AND 128),
        body_fingerprint TEXT NOT NULL CHECK (length(body_fingerprint) = 64),
        fingerprint_key_version INTEGER NOT NULL CHECK (fingerprint_key_version BETWEEN 1 AND 2147483647),
        source_fact_fingerprint TEXT NOT NULL CHECK (length(source_fact_fingerprint) = 64),
        source_fact_key_version INTEGER NOT NULL CHECK (source_fact_key_version BETWEEN 1 AND 2147483647),
        received_at TEXT NOT NULL,
        committed_at TEXT NOT NULL,
        response_digest TEXT NOT NULL CHECK (length(response_digest) = 64),
        evidence_json TEXT NOT NULL CHECK (length(evidence_json) BETWEEN 2 AND 65536),
        PRIMARY KEY (organization_id, connector_id, receipt_id),
        UNIQUE (organization_id, connector_id, source_delivery_id),
        UNIQUE (organization_id, connector_id, fingerprint_key_version, body_fingerprint),
        UNIQUE (organization_id, connector_id, source_fact_key_version, source_fact_fingerprint),
        FOREIGN KEY (organization_id, connector_id, binding_version)
            REFERENCES {prefix}portfolio_linear_source_binding_versions
                (organization_id, connector_id, version)
    """,
    CONTEXT_TABLE: """
        organization_id TEXT NOT NULL CHECK (length(organization_id) BETWEEN 1 AND 128),
        connector_id TEXT NOT NULL CHECK (length(connector_id) BETWEEN 1 AND 128),
        context_event_id TEXT NOT NULL CHECK (length(context_event_id) = 36),
        receipt_id TEXT NOT NULL CHECK (length(receipt_id) = 36),
        object_kind TEXT NOT NULL CHECK (object_kind IN ('initiative','project','cycle','issue')),
        object_id TEXT NOT NULL CHECK (length(object_id) = 36),
        lifecycle TEXT NOT NULL CHECK (lifecycle IN ('created','updated','archived','restored','deleted')),
        normalized_state TEXT NOT NULL CHECK (normalized_state IN ('not_started','in_progress','completed','canceled','unknown')),
        relationship_coverage TEXT NOT NULL CHECK (relationship_coverage IN ('complete','partial','unknown','not_applicable')),
        revision_kind TEXT NOT NULL CHECK (revision_kind IN ('source_updated_at_v1','source_revision_counter_v1','unknown')),
        revision_value TEXT CHECK (revision_value IS NULL OR length(revision_value) BETWEEN 1 AND 64),
        ordering_state TEXT NOT NULL CHECK (ordering_state IN ('current','late','incomparable','unknown')),
        scope_state TEXT NOT NULL CHECK (scope_state IN ('matched','unmatched','excluded')),
        event_at TEXT,
        observed_at TEXT NOT NULL,
        ingested_at TEXT NOT NULL,
        provenance_digest TEXT NOT NULL CHECK (length(provenance_digest) = 64),
        commit_sequence BIGINT NOT NULL CHECK (commit_sequence >= 1),
        evidence_json TEXT NOT NULL CHECK (length(evidence_json) BETWEEN 2 AND 1048576),
        PRIMARY KEY (organization_id, connector_id, context_event_id),
        UNIQUE (organization_id, commit_sequence),
        FOREIGN KEY (organization_id, connector_id, receipt_id)
            REFERENCES {prefix}gateway_linear_delivery_receipts
                (organization_id, connector_id, receipt_id)
    """,
    RETENTION_TABLE: """
        organization_id TEXT NOT NULL CHECK (length(organization_id) BETWEEN 1 AND 128),
        connector_id TEXT NOT NULL CHECK (length(connector_id) BETWEEN 1 AND 128),
        retention_event_id TEXT NOT NULL CHECK (length(retention_event_id) = 36),
        target_context_event_id TEXT NOT NULL CHECK (length(target_context_event_id) = 36),
        actor_id TEXT NOT NULL CHECK (length(actor_id) BETWEEN 1 AND 128),
        reason_code TEXT NOT NULL CHECK (reason_code = 'tombstoned'),
        observed_at TEXT NOT NULL,
        ingested_at TEXT NOT NULL,
        provenance_digest TEXT NOT NULL CHECK (length(provenance_digest) = 64),
        evidence_json TEXT NOT NULL CHECK (length(evidence_json) BETWEEN 2 AND 65536),
        PRIMARY KEY (organization_id, connector_id, retention_event_id),
        FOREIGN KEY (organization_id, connector_id, target_context_event_id)
            REFERENCES {prefix}portfolio_linear_context_events
                (organization_id, connector_id, context_event_id)
    """,
}

INDEX_DDL = {
    "portfolio_linear_source_binding_current": (
        f"{BINDING_TABLE} (organization_id, connector_id, version DESC)"
    ),
    "gateway_linear_delivery_receipt_time": (
        f"{RECEIPT_TABLE} (organization_id, connector_id, committed_at, receipt_id)"
    ),
    "portfolio_linear_context_object_time": (
        f"{CONTEXT_TABLE} (organization_id, connector_id, object_kind, object_id, commit_sequence DESC)"
    ),
    "portfolio_linear_context_retention_target": (
        f"{RETENTION_TABLE} (organization_id, connector_id, target_context_event_id, ingested_at)"
    ),
}

_CONFLICT_KEYS = {
    BINDING_TABLE: (
        ("organization_id", "connector_id", "version"),
        ("organization_id", "binding_event_id"),
        ("organization_id", "connector_id", "request_digest"),
    ),
    RECEIPT_TABLE: (
        ("organization_id", "connector_id", "receipt_id"),
        ("organization_id", "connector_id", "source_delivery_id"),
        ("organization_id", "connector_id", "fingerprint_key_version", "body_fingerprint"),
        ("organization_id", "connector_id", "source_fact_key_version", "source_fact_fingerprint"),
    ),
    CONTEXT_TABLE: (
        ("organization_id", "connector_id", "context_event_id"),
        ("organization_id", "commit_sequence"),
    ),
    RETENTION_TABLE: (("organization_id", "connector_id", "retention_event_id"),),
}

LINEAR_AUDIT_SOURCES = {
    "hormuz.linear-source-binding-version": (BINDING_TABLE, "binding_event_id"),
    "hormuz.linear-delivery-receipt": (RECEIPT_TABLE, "receipt_id"),
    "hormuz.linear-context-event": (CONTEXT_TABLE, "context_event_id"),
    "hormuz.linear-context-retention": (RETENTION_TABLE, "retention_event_id"),
}

_LINEAR_AUDIT_SOURCE_TRIGGER = """CREATE TRIGGER gateway_linear_audit_source_required
BEFORE INSERT ON gateway_audit_chain_entries
WHEN NEW.entry_schema_version=2
  AND NEW.source_schema_id IN (
      'hormuz.linear-source-binding-version',
      'hormuz.linear-delivery-receipt',
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
          (NEW.source_schema_id='hormuz.linear-context-event' AND EXISTS (
              SELECT 1 FROM portfolio_linear_context_events source
              WHERE source.organization_id=NEW.organization_id
                AND source.context_event_id=NEW.source_event_id
                AND source.evidence_json=NEW.event_json
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

_SQLITE_BINDING_CARDINALITY_TRIGGER = """CREATE TRIGGER portfolio_linear_binding_cardinality
BEFORE INSERT ON portfolio_linear_source_binding_versions
WHEN EXISTS (
    SELECT 1 FROM portfolio_linear_source_binding_versions existing
    WHERE (
        existing.source_workspace_id=NEW.source_workspace_id
        AND existing.organization_id<>NEW.organization_id
    ) OR (
        existing.source_webhook_id=NEW.source_webhook_id
        AND (
            existing.organization_id<>NEW.organization_id
            OR existing.connector_id<>NEW.connector_id
        )
    )
)
BEGIN SELECT RAISE(ABORT, 'linear_binding_cardinality_conflict'); END"""


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
                "BEGIN SELECT RAISE(ABORT, 'linear_metadata_append_only'); END"
            )
        conflicts = " OR ".join(
            "(" + " AND ".join(f"existing.{field}=NEW.{field}" for field in key) + ")"
            for key in keys
        )
        statements.append(
            f"CREATE TRIGGER {table}_no_replace BEFORE INSERT ON {table} "
            f"WHEN EXISTS (SELECT 1 FROM {table} existing WHERE {conflicts}) "
            "BEGIN SELECT RAISE(ABORT, 'linear_metadata_replace_refused'); END"
        )
    statements.append(_SQLITE_BINDING_CARDINALITY_TRIGGER)
    return tuple(statements)


def _audit_table_v5() -> str:
    from ._finance_account_binding_schema import _AUDIT_TABLE_V4

    marker = "                OR (source_schema_id = 'hormuz.finance-query-audit-event' AND source_schema_version = 1)"
    addition = marker + "\n" + "\n".join(
        f"                OR (source_schema_id = '{source}' AND source_schema_version = 1)"
        for source in LINEAR_AUDIT_SOURCES
    )
    if _AUDIT_TABLE_V4.count(marker) != 1:
        raise RuntimeError("linear_audit_schema_predecessor_changed")
    return _AUDIT_TABLE_V4.replace(marker, addition)


def apply_sqlite_linear_migration(connection) -> None:
    """Apply schema 14 atomically inside the caller-owned transaction."""

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
        "DROP INDEX idx_gateway_audit_chain_entries_event",
        "DROP INDEX idx_gateway_audit_chain_entries_source_identity",
    ):
        connection.execute(statement)
    connection.execute(_audit_table_v5())
    connection.execute(
        """
        INSERT INTO gateway_audit_chain_entries_v4 (
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
        "ALTER TABLE gateway_audit_chain_entries_v4 RENAME TO gateway_audit_chain_entries"
    )
    for statement in (
        "CREATE INDEX idx_gateway_audit_chain_entries_event ON gateway_audit_chain_entries (organization_id, event_id)",
        "CREATE UNIQUE INDEX idx_gateway_audit_chain_entries_source_identity ON gateway_audit_chain_entries (organization_id, source_schema_id, source_schema_version, source_event_id) WHERE entry_schema_version = 2",
        "CREATE TRIGGER gateway_audit_chain_entries_no_update BEFORE UPDATE ON gateway_audit_chain_entries BEGIN SELECT RAISE(ABORT, 'audit_chain_entry_immutable'); END",
        "CREATE TRIGGER gateway_audit_chain_entries_no_delete BEFORE DELETE ON gateway_audit_chain_entries BEGIN SELECT RAISE(ABORT, 'audit_chain_entry_immutable'); END",
        SQLITE_FINANCE_ATTEMPT_TRIGGERS["gateway_finance_attempt_audit_source_required"],
        _COLLECTION_AUDIT_TRIGGER,
        _AUDIT_SOURCE_TRIGGER,
        _LINEAR_AUDIT_SOURCE_TRIGGER,
    ):
        connection.execute(statement)


def _statement_name(statement: str) -> str:
    words = statement.split()
    if len(words) >= 3 and words[:2] in (
        ["CREATE", "TABLE"], ["CREATE", "INDEX"], ["CREATE", "TRIGGER"],
    ):
        return words[2]
    return ""


def verify_sqlite_linear(connection, error_factory, *, audit_trigger=None) -> None:
    observed = {
        str(row["name"]): " ".join(str(row["sql"]).split())
        for row in connection.execute(
            "SELECT name, sql FROM sqlite_master WHERE sql IS NOT NULL"
        ).fetchall()
    }
    expected = [
        *sqlite_statements(),
        _LINEAR_AUDIT_SOURCE_TRIGGER if audit_trigger is None else audit_trigger,
    ]
    if any(
        observed.get(_statement_name(statement)) != " ".join(statement.split())
        for statement in expected
    ):
        raise error_factory("storage_schema_partial_upgrade")
    audit_sql = observed.get("gateway_audit_chain_entries", "")
    if any(source not in audit_sql for source in LINEAR_AUDIT_SOURCES):
        raise error_factory("storage_schema_partial_upgrade")


def verify_postgres_linear(cursor, schema: str, error_factory) -> None:
    from ._portfolio_schema import verify_postgres_owned_tables

    expected = {
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
        {name: ddl.format(prefix=schema + ".") for name, ddl in TABLE_DDL.items()},
        INDEX_DDL,
        expected,
        trigger_type=58,
    )
    cursor.execute(
        "SELECT c.relkind,c.relrowsecurity,c.relforcerowsecurity,"
        "pg_get_userbyid(c.relowner)=pg_get_userbyid(n.nspowner) AS owner_matches,"
        "(SELECT count(*) FROM pg_policy p WHERE p.polrelid=c.oid) AS policy_count,"
        "(SELECT count(*) FROM aclexplode(COALESCE(c.relacl,acldefault('r',c.relowner))) a "
        "WHERE a.grantor<>c.relowner OR a.grantee<>c.relowner) AS unexpected_acl "
        "FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
        "WHERE n.nspname=%s AND c.relname=%s",
        (schema, POSTGRES_CLAIM_TABLE),
    )
    row = cursor.fetchone()
    values = tuple(row.values()) if isinstance(row, Mapping) else tuple(row or ())
    if values != ("r", False, False, True, 0, 0):
        raise error_factory("storage_schema_partial_upgrade")
    cursor.execute(
        "SELECT a.attname,format_type(a.atttypid,a.atttypmod),a.attnotnull "
        "FROM pg_attribute a JOIN pg_class c ON c.oid=a.attrelid "
        "JOIN pg_namespace n ON n.oid=c.relnamespace "
        "WHERE n.nspname=%s AND c.relname=%s AND a.attnum>0 "
        "AND NOT a.attisdropped ORDER BY a.attnum",
        (schema, POSTGRES_CLAIM_TABLE),
    )
    columns = [
        tuple(row.values()) if isinstance(row, Mapping) else tuple(row)
        for row in cursor.fetchall()
    ]
    if columns != [
        ("claim_kind", "text", True),
        ("source_identifier", "text", True),
        ("organization_id", "text", True),
        ("connector_id", "text", False),
        ("claimed_at", "text", True),
    ]:
        raise error_factory("storage_schema_partial_upgrade")
    cursor.execute(
        "SELECT con.contype,pg_get_constraintdef(con.oid) AS definition "
        "FROM pg_constraint con JOIN pg_class c ON c.oid=con.conrelid "
        "JOIN pg_namespace n ON n.oid=c.relnamespace "
        "WHERE n.nspname=%s AND c.relname=%s",
        (schema, POSTGRES_CLAIM_TABLE),
    )
    constraints = [
        tuple(row.values()) if isinstance(row, Mapping) else tuple(row)
        for row in cursor.fetchall()
    ]
    definitions = " ".join(str(value[1]) for value in constraints)
    if (
        sum(value[0] == "p" for value in constraints) != 1
        or sum(value[0] == "c" for value in constraints) != 5
        or any(
            marker not in definitions
            for marker in (
                "claim_kind",
                "source_identifier",
                "organization_id",
                "connector_id",
                "workspace",
                "webhook",
            )
        )
    ):
        raise error_factory("storage_schema_partial_upgrade")
    cursor.execute(
        "SELECT t.tgenabled,t.tgtype,p.proname FROM pg_trigger t "
        "JOIN pg_class c ON c.oid=t.tgrelid "
        "JOIN pg_namespace n ON n.oid=c.relnamespace "
        "JOIN pg_proc p ON p.oid=t.tgfoid "
        "WHERE n.nspname=%s AND c.relname=%s AND t.tgname=%s "
        "AND NOT t.tgisinternal",
        (
            schema,
            POSTGRES_CLAIM_TABLE,
            "gateway_linear_route_claims_immutable",
        ),
    )
    row = cursor.fetchone()
    values = tuple(row.values()) if isinstance(row, Mapping) else tuple(row or ())
    if values != ("O", 58, "portfolio_reject_mutation"):
        raise error_factory("storage_schema_partial_upgrade")
    cursor.execute(
        "SELECT t.tgenabled,t.tgtype,p.proname,p.prosrc,p.prosecdef,p.proconfig "
        "FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid "
        "JOIN pg_namespace n ON n.oid=c.relnamespace "
        "JOIN pg_proc p ON p.oid=t.tgfoid "
        "WHERE n.nspname=%s AND c.relname=%s AND t.tgname=%s "
        "AND NOT t.tgisinternal",
        (
            schema,
            BINDING_TABLE,
            "portfolio_linear_binding_cardinality",
        ),
    )
    row = cursor.fetchone()
    values = tuple(row.values()) if isinstance(row, Mapping) else tuple(row or ())
    body = "" if len(values) < 4 else str(values[3])
    if (
        len(values) != 6
        or values[:3] != ("O", 7, "enforce_linear_binding_cardinality")
        or "source_workspace_id" not in body
        or "source_webhook_id" not in body
        or POSTGRES_CLAIM_TABLE not in body
        or "ON CONFLICT" not in body
        or "claim_kind" not in body
        or values[4] is not True
        or values[5] != ["search_path=pg_catalog"]
    ):
        raise error_factory("storage_schema_partial_upgrade")
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
        source not in definition for source in LINEAR_AUDIT_SOURCES
    ):
        raise error_factory("storage_schema_partial_upgrade")
    for function in (
        "enforce_custody_audit_chain_entry_insert",
        "custody_audit_chain_source_event_json",
    ):
        cursor.execute(
            "SELECT p.prosrc, p.prosecdef, p.proconfig FROM pg_proc p "
            "JOIN pg_namespace n ON n.oid=p.pronamespace "
            "WHERE n.nspname=%s AND p.proname=%s",
            (schema, function),
        )
        row = cursor.fetchone()
        values = tuple(row.values()) if isinstance(row, Mapping) else tuple(row or ())
        body = "" if not values else str(values[0])
        if (
            len(values) != 3
            or any(source not in body for source in LINEAR_AUDIT_SOURCES)
            or values[1] is not True
            or values[2] != ["search_path=pg_catalog"]
        ):
            raise error_factory("storage_schema_partial_upgrade")
