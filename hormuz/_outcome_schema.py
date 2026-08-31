"""Outcome-owned additive storage; prior ledgers and migration bytes are intact."""

from __future__ import annotations


TABLE_DDL = {
    "portfolio_outcome_audit_events": """
        organization_id TEXT NOT NULL,
        event_id TEXT NOT NULL,
        sequence BIGINT NOT NULL CHECK (sequence >= 1),
        actor_id TEXT,
        connector_id TEXT,
        operation TEXT NOT NULL CHECK (operation IN ('ingest','reject','list_outcomes','read_context','retention')),
        entity_id TEXT,
        reason_code TEXT NOT NULL CHECK (reason_code IN ('observed','unsupported','tombstoned','invalid_shape','unauthorized_scope','conflicting_identity','dependency_unavailable')),
        occurred_at TEXT NOT NULL,
        PRIMARY KEY (organization_id, event_id),
        UNIQUE (organization_id, sequence)
    """,
    "portfolio_outcome_receipts": """
        organization_id TEXT NOT NULL,
        connector_id TEXT NOT NULL,
        source_delivery_id TEXT NOT NULL,
        receipt_id TEXT NOT NULL,
        schema_version INTEGER NOT NULL CHECK (schema_version = 1),
        fingerprint TEXT NOT NULL CHECK (length(fingerprint) = 64),
        authority_digest TEXT NOT NULL CHECK (length(authority_digest) = 64),
        key_version TEXT NOT NULL,
        disposition TEXT NOT NULL CHECK (disposition IN ('accepted','unsupported')),
        accepted_event_count INTEGER NOT NULL CHECK (accepted_event_count BETWEEN 0 AND 100),
        observed_at TEXT NOT NULL,
        ingested_at TEXT NOT NULL,
        sequence BIGINT NOT NULL,
        PRIMARY KEY (organization_id, connector_id, source_delivery_id),
        UNIQUE (organization_id, receipt_id),
        FOREIGN KEY (organization_id, sequence)
            REFERENCES {prefix}portfolio_outcome_audit_events (organization_id, sequence)
    """,
    "portfolio_outcome_observations": """
        organization_id TEXT NOT NULL,
        connector_id TEXT NOT NULL,
        source_event_id TEXT NOT NULL,
        source_delivery_id TEXT NOT NULL,
        metadata_json TEXT NOT NULL CHECK (length(metadata_json) <= 8192),
        PRIMARY KEY (organization_id, connector_id, source_event_id),
        FOREIGN KEY (organization_id, connector_id, source_delivery_id)
            REFERENCES {prefix}portfolio_outcome_receipts (organization_id, connector_id, source_delivery_id)
    """,
    "portfolio_outcome_events": """
        organization_id TEXT NOT NULL,
        connector_id TEXT NOT NULL,
        source_event_id TEXT NOT NULL,
        source_delivery_id TEXT NOT NULL,
        schema_version INTEGER NOT NULL CHECK (schema_version = 1),
        external_object_id TEXT NOT NULL,
        source_revision TEXT,
        object_type TEXT NOT NULL CHECK (object_type IN ('issue','pull_request')),
        event_type TEXT NOT NULL CHECK (event_type IN ('created','started','completed','reopened','accepted','reverted','defect_reported','canceled','deleted','unsupported')),
        quality_state TEXT NOT NULL CHECK (quality_state IN ('accepted','rejected','reverted','defect','unknown','not_applicable')),
        duration_ms TEXT,
        state TEXT NOT NULL CHECK (state IN ('observed','superseded','tombstoned')),
        evidence_level TEXT NOT NULL CHECK (evidence_level = 'descriptive'),
        supersedes_source_event_id TEXT,
        provenance_digest TEXT NOT NULL CHECK (length(provenance_digest) = 64),
        reason_code TEXT NOT NULL CHECK (reason_code IN ('observed','corrected','superseded','tombstoned','unsupported','missing_evidence')),
        event_at TEXT NOT NULL,
        observed_at TEXT NOT NULL,
        ingested_at TEXT NOT NULL,
        sequence BIGINT NOT NULL,
        PRIMARY KEY (organization_id, connector_id, source_event_id),
        FOREIGN KEY (organization_id, connector_id, source_event_id)
            REFERENCES {prefix}portfolio_outcome_observations (organization_id, connector_id, source_event_id),
        FOREIGN KEY (organization_id, connector_id, source_delivery_id)
            REFERENCES {prefix}portfolio_outcome_receipts (organization_id, connector_id, source_delivery_id),
        FOREIGN KEY (organization_id, connector_id, supersedes_source_event_id)
            REFERENCES {prefix}portfolio_outcome_events (organization_id, connector_id, source_event_id),
        FOREIGN KEY (organization_id, sequence)
            REFERENCES {prefix}portfolio_outcome_audit_events (organization_id, sequence)
    """,
    "portfolio_outcome_contexts": """
        organization_id TEXT NOT NULL,
        connector_id TEXT NOT NULL,
        source_event_id TEXT NOT NULL,
        schema_version INTEGER NOT NULL CHECK (schema_version = 1),
        provider TEXT NOT NULL CHECK (provider IN ('github','linear')),
        authority_id TEXT NOT NULL,
        source_container_id TEXT NOT NULL,
        actor_id TEXT,
        authentication_kind TEXT NOT NULL CHECK (authentication_kind IN ('verified_connector','authorized_retention')),
        work_scope_id TEXT,
        work_scope_version INTEGER,
        binding_event_id TEXT,
        registry_sequence BIGINT NOT NULL CHECK (registry_sequence >= 0),
        key_version TEXT NOT NULL,
        credential_version TEXT NOT NULL,
        source_time_known INTEGER NOT NULL CHECK (source_time_known IN (0,1)),
        ordering_domain TEXT CHECK (ordering_domain IN ('source_revision_counter_v1','source_updated_at_v1')),
        revision_order BIGINT CHECK (revision_order >= 0),
        ordering_state TEXT NOT NULL CHECK (ordering_state IN ('authoritative','late','uncertain','superseded','tombstoned')),
        scope_state TEXT NOT NULL CHECK (scope_state IN ('matched','unmatched','ambiguous','excluded')),
        PRIMARY KEY (organization_id, connector_id, source_event_id),
        FOREIGN KEY (organization_id, connector_id, source_event_id)
            REFERENCES {prefix}portfolio_outcome_events (organization_id, connector_id, source_event_id),
        FOREIGN KEY (organization_id, work_scope_id, work_scope_version)
            REFERENCES {prefix}portfolio_work_scope_versions (organization_id, work_scope_id, version),
        FOREIGN KEY (organization_id, binding_event_id)
            REFERENCES {prefix}portfolio_binding_events (organization_id, binding_event_id),
        CHECK ((work_scope_id IS NULL AND work_scope_version IS NULL) OR (work_scope_id IS NOT NULL AND work_scope_version IS NOT NULL)),
        CHECK ((ordering_domain IS NULL AND revision_order IS NULL) OR (ordering_domain IS NOT NULL AND revision_order IS NOT NULL)),
        CHECK ((authentication_kind='verified_connector' AND actor_id IS NULL) OR (authentication_kind='authorized_retention' AND actor_id IS NOT NULL))
    """,
    "portfolio_outcome_coverage_events": """
        organization_id TEXT NOT NULL,
        coverage_event_id TEXT NOT NULL,
        connector_id TEXT NOT NULL,
        source_delivery_id TEXT NOT NULL,
        source_event_id TEXT,
        schema_version INTEGER NOT NULL CHECK (schema_version = 1),
        state TEXT NOT NULL CHECK (state IN ('observed','unmatched','ambiguous','late','excluded','superseded','unsupported','failed')),
        reason_code TEXT NOT NULL CHECK (reason_code IN ('observed','unmatched','ambiguous','excluded','superseded','unsupported','missing_evidence','tombstoned','invalid_shape','unauthorized_scope','conflicting_identity','dependency_unavailable')),
        eligibility_state TEXT NOT NULL CHECK (eligibility_state = 'inconclusive'),
        rule_id TEXT,
        rule_version INTEGER,
        member_count INTEGER NOT NULL CHECK (member_count BETWEEN 1 AND 100),
        member_unit TEXT NOT NULL CHECK (member_unit IN ('source_event','delivery')),
        ingested_at TEXT NOT NULL,
        sequence BIGINT NOT NULL,
        PRIMARY KEY (organization_id, coverage_event_id),
        FOREIGN KEY (organization_id, connector_id, source_event_id)
            REFERENCES {prefix}portfolio_outcome_events (organization_id, connector_id, source_event_id),
        FOREIGN KEY (organization_id, sequence)
            REFERENCES {prefix}portfolio_outcome_audit_events (organization_id, sequence),
        CHECK (rule_id IS NULL AND rule_version IS NULL)
    """,
    "portfolio_outcome_dead_letters": """
        organization_id TEXT NOT NULL,
        connector_id TEXT NOT NULL,
        source_delivery_id TEXT NOT NULL,
        fingerprint TEXT NOT NULL CHECK (length(fingerprint) = 64),
        authority_digest TEXT NOT NULL CHECK (length(authority_digest) = 64),
        key_version TEXT NOT NULL,
        reason_code TEXT NOT NULL CHECK (reason_code IN ('invalid_shape','unauthorized_scope','conflicting_identity','dependency_unavailable')),
        metadata_json TEXT NOT NULL CHECK (length(metadata_json) <= 2048),
        occurred_at TEXT NOT NULL,
        sequence BIGINT NOT NULL,
        PRIMARY KEY (organization_id, connector_id, source_delivery_id),
        FOREIGN KEY (organization_id, sequence)
            REFERENCES {prefix}portfolio_outcome_audit_events (organization_id, sequence)
    """,
    "portfolio_outcome_retention_events": """
        organization_id TEXT NOT NULL,
        retention_event_id TEXT NOT NULL,
        connector_id TEXT NOT NULL,
        source_event_id TEXT NOT NULL,
        schema_version INTEGER NOT NULL CHECK (schema_version = 1),
        actor_id TEXT NOT NULL,
        idempotency_key TEXT NOT NULL CHECK (length(idempotency_key) BETWEEN 1 AND 128),
        request_mac TEXT NOT NULL CHECK (length(request_mac) = 64),
        key_version TEXT NOT NULL,
        reason_code TEXT NOT NULL CHECK (reason_code = 'tombstoned'),
        event_at TEXT NOT NULL,
        observed_at TEXT NOT NULL,
        ingested_at TEXT NOT NULL,
        sequence BIGINT NOT NULL,
        PRIMARY KEY (organization_id, retention_event_id),
        UNIQUE (organization_id, actor_id, idempotency_key),
        FOREIGN KEY (organization_id, connector_id, source_event_id)
            REFERENCES {prefix}portfolio_outcome_events (organization_id, connector_id, source_event_id),
        FOREIGN KEY (organization_id, sequence)
            REFERENCES {prefix}portfolio_outcome_audit_events (organization_id, sequence)
    """,
    "portfolio_outcome_cursors": """
        organization_id TEXT NOT NULL,
        cursor_id TEXT NOT NULL,
        actor_id TEXT NOT NULL,
        authority_json TEXT NOT NULL,
        as_of TEXT NOT NULL,
        snapshot_sequence BIGINT NOT NULL CHECK (snapshot_sequence >= 0),
        after_at TEXT NOT NULL,
        after_connector TEXT NOT NULL,
        after_id TEXT NOT NULL,
        filters_json TEXT NOT NULL,
        PRIMARY KEY (organization_id, cursor_id)
    """,
}

INDEX_DDL = {
    "portfolio_outcome_window": "INDEX {prefix}portfolio_outcome_window ON {prefix}portfolio_outcome_events (organization_id, event_at, connector_id, source_event_id, sequence)",
    "portfolio_outcome_object": "INDEX {prefix}portfolio_outcome_object ON {prefix}portfolio_outcome_events (organization_id, connector_id, external_object_id, sequence)",
    "portfolio_outcome_lineage": "UNIQUE INDEX {prefix}portfolio_outcome_lineage ON {prefix}portfolio_outcome_events (organization_id, connector_id, supersedes_source_event_id) WHERE supersedes_source_event_id IS NOT NULL",
    "portfolio_outcome_scope": "INDEX {prefix}portfolio_outcome_scope ON {prefix}portfolio_outcome_contexts (organization_id, work_scope_id, connector_id, source_event_id)",
    "portfolio_outcome_coverage_window": "INDEX {prefix}portfolio_outcome_coverage_window ON {prefix}portfolio_outcome_coverage_events (organization_id, connector_id, ingested_at, sequence)",
    "portfolio_outcome_retention_target": "INDEX {prefix}portfolio_outcome_retention_target ON {prefix}portfolio_outcome_retention_events (organization_id, connector_id, source_event_id)",
}


def sqlite_statements() -> tuple[str, ...]:
    statements = [f"CREATE TABLE {name} ({ddl.format(prefix='')})" for name, ddl in TABLE_DDL.items()]
    statements.extend("CREATE " + ddl.format(prefix="") for ddl in INDEX_DDL.values())
    for table in TABLE_DDL:
        for operation in ("UPDATE", "DELETE"):
            statements.append(f"CREATE TRIGGER {table}_no_{operation.lower()} BEFORE {operation} ON {table} "
                              "BEGIN SELECT RAISE(ABORT, 'portfolio_append_only'); END")
    return tuple(statements)


def verify_sqlite_outcomes(connection, error_factory) -> None:
    observed = {" ".join(row[0].split()) for row in connection.execute(
        "SELECT sql FROM sqlite_master WHERE sql IS NOT NULL AND name LIKE 'portfolio_outcome_%'"
    ).fetchall()}
    if any(" ".join(statement.split()) not in observed for statement in sqlite_statements()):
        raise error_factory("storage_schema_partial_upgrade")


def postgres_statements(schema: str, runtime_role: str) -> str:
    """Generate the checked-in migration; never run a schema repair at runtime."""
    prefix = schema + "."
    statements = [f"CREATE TABLE {prefix}{name} ({ddl.format(prefix=prefix)});" for name, ddl in TABLE_DDL.items()]
    for name, ddl in INDEX_DDL.items():
        statements.append("CREATE " + ddl.format(prefix=prefix).replace(prefix + name + " ON", name + " ON") + ";")
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


def verify_postgres_outcomes(cursor, schema: str, error_factory) -> None:
    from ._portfolio_schema import verify_postgres_owned_tables

    expected = {
        table: {kind: ddl.count(marker) for kind, marker in (
            ("p", "PRIMARY KEY"), ("u", "UNIQUE ("), ("f", "FOREIGN KEY"), ("c", "CHECK ("),
        ) if marker in ddl}
        for table, ddl in TABLE_DDL.items()
    }
    verify_postgres_owned_tables(cursor, schema, error_factory, TABLE_DDL, INDEX_DDL, expected, trigger_type=58)
