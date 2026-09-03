"""Schema 12/16 for provider aggregate finance collection evidence.

The seven tables in this module are append-only.  Provider payloads, opaque
cursors, credentials, and free-form provider text are deliberately absent from
the durable shape; only normalized observations and keyed fingerprints fit.
"""

from __future__ import annotations

from collections.abc import Mapping
from importlib import resources


SOURCE_BINDING_TABLE = "portfolio_finance_source_binding_versions"
COLLECTION_ATTEMPT_TABLE = "portfolio_finance_collection_attempts"
COLLECTION_EVENT_TABLE = "portfolio_finance_collection_events"
SNAPSHOT_TABLE = "portfolio_finance_snapshots"
COVERAGE_TABLE = "portfolio_finance_snapshot_bucket_coverage"
USAGE_TABLE = "portfolio_finance_usage_observations"
COST_TABLE = "portfolio_finance_cost_observations"

TABLE_DDL = {
    SOURCE_BINDING_TABLE: """
        organization_id TEXT NOT NULL CHECK (length(organization_id) BETWEEN 1 AND 128),
        binding_id TEXT NOT NULL CHECK (length(binding_id) BETWEEN 1 AND 128),
        version INTEGER NOT NULL CHECK (version BETWEEN 1 AND 2147483647),
        binding_event_id TEXT NOT NULL CHECK (length(binding_event_id) = 36),
        provider TEXT NOT NULL CHECK (provider IN ('openai','anthropic')),
        provider_account_fingerprint TEXT NOT NULL CHECK (length(provider_account_fingerprint) = 64),
        scope_kind TEXT NOT NULL CHECK (scope_kind IN ('organization','projects','workspaces')),
        scope_fingerprints_json TEXT NOT NULL CHECK (length(scope_fingerprints_json) BETWEEN 2 AND 65536),
        credential_reference_id TEXT NOT NULL CHECK (length(credential_reference_id) BETWEEN 1 AND 128),
        credential_reference_version INTEGER NOT NULL CHECK (credential_reference_version BETWEEN 1 AND 2147483647),
        fingerprint_key_version INTEGER NOT NULL CHECK (fingerprint_key_version BETWEEN 1 AND 2147483647),
        binding_state TEXT NOT NULL CHECK (binding_state IN ('active','revoked')),
        previous_version INTEGER,
        content_digest TEXT NOT NULL CHECK (length(content_digest) = 64),
        bound_by TEXT NOT NULL CHECK (length(bound_by) BETWEEN 1 AND 128),
        bound_at TEXT NOT NULL,
        evidence_json TEXT NOT NULL CHECK (length(evidence_json) BETWEEN 2 AND 65536),
        PRIMARY KEY (organization_id, binding_id, version),
        UNIQUE (organization_id, binding_event_id),
        UNIQUE (organization_id, binding_id, content_digest),
        CHECK (
            (version = 1 AND previous_version IS NULL)
            OR (version > 1 AND previous_version = version - 1)
        )
    """,
    COLLECTION_ATTEMPT_TABLE: """
        organization_id TEXT NOT NULL CHECK (length(organization_id) BETWEEN 1 AND 128),
        attempt_id TEXT NOT NULL CHECK (length(attempt_id) = 36),
        binding_id TEXT NOT NULL CHECK (length(binding_id) BETWEEN 1 AND 128),
        binding_version INTEGER NOT NULL CHECK (binding_version BETWEEN 1 AND 2147483647),
        provider TEXT NOT NULL CHECK (provider IN ('openai','anthropic')),
        collection_profile TEXT NOT NULL CHECK (length(collection_profile) BETWEEN 1 AND 128),
        source_kind TEXT NOT NULL CHECK (source_kind IN ('usage','cost')),
        query_start_at TEXT NOT NULL,
        query_end_at TEXT NOT NULL,
        bucket_width TEXT NOT NULL CHECK (bucket_width IN ('1m','1h','1d')),
        requested_page_size INTEGER NOT NULL CHECK (requested_page_size BETWEEN 1 AND 1440),
        evidence_origin TEXT NOT NULL CHECK (evidence_origin IN ('authenticated_api','customer_file')),
        idempotency_digest TEXT NOT NULL CHECK (length(idempotency_digest) = 64),
        request_digest TEXT NOT NULL CHECK (length(request_digest) = 64),
        credential_reference_id TEXT NOT NULL CHECK (length(credential_reference_id) BETWEEN 1 AND 128),
        credential_reference_version INTEGER NOT NULL CHECK (credential_reference_version BETWEEN 1 AND 2147483647),
        fingerprint_key_version INTEGER NOT NULL CHECK (fingerprint_key_version BETWEEN 1 AND 2147483647),
        prepared_by TEXT NOT NULL CHECK (length(prepared_by) BETWEEN 1 AND 128),
        prepared_at TEXT NOT NULL,
        PRIMARY KEY (organization_id, attempt_id),
        UNIQUE (
            organization_id, binding_id, binding_version, collection_profile,
            query_start_at, query_end_at, idempotency_digest
        ),
        FOREIGN KEY (organization_id, binding_id, binding_version)
            REFERENCES {prefix}portfolio_finance_source_binding_versions
                (organization_id, binding_id, version)
    """,
    COLLECTION_EVENT_TABLE: """
        organization_id TEXT NOT NULL CHECK (length(organization_id) BETWEEN 1 AND 128),
        event_id TEXT NOT NULL CHECK (length(event_id) = 36),
        attempt_id TEXT NOT NULL CHECK (length(attempt_id) = 36),
        state TEXT NOT NULL CHECK (state IN ('succeeded','failed','abandoned')),
        reason_code TEXT NOT NULL CHECK (length(reason_code) BETWEEN 1 AND 128),
        receipt_id TEXT,
        snapshot_id TEXT,
        actor_id TEXT NOT NULL CHECK (length(actor_id) BETWEEN 1 AND 128),
        occurred_at TEXT NOT NULL,
        evidence_json TEXT NOT NULL CHECK (length(evidence_json) BETWEEN 2 AND 65536),
        PRIMARY KEY (organization_id, event_id),
        UNIQUE (organization_id, attempt_id),
        UNIQUE (organization_id, receipt_id),
        FOREIGN KEY (organization_id, attempt_id)
            REFERENCES {prefix}portfolio_finance_collection_attempts
                (organization_id, attempt_id),
        CHECK (
            (state='succeeded' AND reason_code='completed' AND receipt_id IS NOT NULL AND length(receipt_id)=32 AND snapshot_id IS NOT NULL AND length(snapshot_id)=36)
            OR (state IN ('failed','abandoned') AND receipt_id IS NULL AND snapshot_id IS NULL)
        )
    """,
    SNAPSHOT_TABLE: """
        organization_id TEXT NOT NULL CHECK (length(organization_id) BETWEEN 1 AND 128),
        snapshot_id TEXT NOT NULL CHECK (length(snapshot_id) = 36),
        attempt_id TEXT NOT NULL CHECK (length(attempt_id) = 36),
        binding_id TEXT NOT NULL CHECK (length(binding_id) BETWEEN 1 AND 128),
        binding_version INTEGER NOT NULL CHECK (binding_version BETWEEN 1 AND 2147483647),
        collection_profile TEXT NOT NULL CHECK (length(collection_profile) BETWEEN 1 AND 128),
        source_kind TEXT NOT NULL CHECK (source_kind IN ('usage','cost')),
        query_start_at TEXT NOT NULL,
        query_end_at TEXT NOT NULL,
        evidence_origin TEXT NOT NULL CHECK (evidence_origin IN ('authenticated_api','customer_file')),
        scope_provenance TEXT NOT NULL CHECK (scope_provenance IN ('authenticated_query_scope_unverified','customer_supplied_scope_unverified')),
        parser_version INTEGER NOT NULL CHECK (parser_version = 1),
        page_count INTEGER NOT NULL CHECK (page_count BETWEEN 1 AND 32),
        record_count INTEGER NOT NULL CHECK (record_count BETWEEN 0 AND 4096),
        requested_page_size INTEGER NOT NULL CHECK (requested_page_size BETWEEN 1 AND 1440),
        page_chain_digest TEXT NOT NULL CHECK (length(page_chain_digest) = 64),
        content_digest TEXT NOT NULL CHECK (length(content_digest) = 64),
        supersedes_snapshot_id TEXT,
        commit_sequence BIGINT NOT NULL CHECK (commit_sequence >= 1),
        published_by TEXT NOT NULL CHECK (length(published_by) BETWEEN 1 AND 128),
        published_at TEXT NOT NULL,
        provider_final INTEGER NOT NULL CHECK (provider_final = 0),
        invoice_final INTEGER NOT NULL CHECK (invoice_final = 0),
        evidence_json TEXT NOT NULL CHECK (length(evidence_json) BETWEEN 2 AND 65536),
        PRIMARY KEY (organization_id, snapshot_id),
        UNIQUE (organization_id, attempt_id),
        UNIQUE (organization_id, commit_sequence),
        FOREIGN KEY (organization_id, attempt_id)
            REFERENCES {prefix}portfolio_finance_collection_attempts
                (organization_id, attempt_id),
        FOREIGN KEY (organization_id, binding_id, binding_version)
            REFERENCES {prefix}portfolio_finance_source_binding_versions
                (organization_id, binding_id, version),
        FOREIGN KEY (organization_id, supersedes_snapshot_id)
            REFERENCES {prefix}portfolio_finance_snapshots
                (organization_id, snapshot_id)
    """,
    COVERAGE_TABLE: """
        organization_id TEXT NOT NULL CHECK (length(organization_id) BETWEEN 1 AND 128),
        coverage_id TEXT NOT NULL CHECK (length(coverage_id) = 36),
        snapshot_id TEXT NOT NULL CHECK (length(snapshot_id) = 36),
        bucket_start_at TEXT NOT NULL,
        bucket_end_at TEXT NOT NULL,
        coverage_state TEXT NOT NULL CHECK (coverage_state IN ('observed','no_observation')),
        observation_count INTEGER NOT NULL CHECK (observation_count BETWEEN 0 AND 4096),
        PRIMARY KEY (organization_id, coverage_id),
        UNIQUE (organization_id, snapshot_id, bucket_start_at, bucket_end_at),
        FOREIGN KEY (organization_id, snapshot_id)
            REFERENCES {prefix}portfolio_finance_snapshots
                (organization_id, snapshot_id),
        CHECK (
            (coverage_state='no_observation' AND observation_count=0)
            OR (coverage_state='observed' AND observation_count>=1)
        )
    """,
    USAGE_TABLE: """
        organization_id TEXT NOT NULL CHECK (length(organization_id) BETWEEN 1 AND 128),
        observation_id TEXT NOT NULL CHECK (length(observation_id) = 36),
        snapshot_id TEXT NOT NULL CHECK (length(snapshot_id) = 36),
        bucket_start_at TEXT NOT NULL,
        bucket_end_at TEXT NOT NULL,
        observation_digest TEXT NOT NULL CHECK (length(observation_digest) = 64),
        provider_project_fingerprint TEXT,
        provider_workspace_fingerprint TEXT,
        api_key_fingerprint TEXT,
        model TEXT,
        batch INTEGER CHECK (batch IS NULL OR batch IN (0,1)),
        service_tier TEXT,
        context_window TEXT,
        inference_geo TEXT,
        input_tokens BIGINT CHECK (input_tokens IS NULL OR input_tokens >= 0),
        output_tokens BIGINT CHECK (output_tokens IS NULL OR output_tokens >= 0),
        num_model_requests BIGINT CHECK (num_model_requests IS NULL OR num_model_requests >= 0),
        input_cached_tokens BIGINT CHECK (input_cached_tokens IS NULL OR input_cached_tokens >= 0),
        input_cache_write_tokens BIGINT CHECK (input_cache_write_tokens IS NULL OR input_cache_write_tokens >= 0),
        input_uncached_tokens BIGINT CHECK (input_uncached_tokens IS NULL OR input_uncached_tokens >= 0),
        input_text_tokens BIGINT CHECK (input_text_tokens IS NULL OR input_text_tokens >= 0),
        input_image_tokens BIGINT CHECK (input_image_tokens IS NULL OR input_image_tokens >= 0),
        input_audio_tokens BIGINT CHECK (input_audio_tokens IS NULL OR input_audio_tokens >= 0),
        input_cached_text_tokens BIGINT CHECK (input_cached_text_tokens IS NULL OR input_cached_text_tokens >= 0),
        input_cached_image_tokens BIGINT CHECK (input_cached_image_tokens IS NULL OR input_cached_image_tokens >= 0),
        input_cached_audio_tokens BIGINT CHECK (input_cached_audio_tokens IS NULL OR input_cached_audio_tokens >= 0),
        output_text_tokens BIGINT CHECK (output_text_tokens IS NULL OR output_text_tokens >= 0),
        output_image_tokens BIGINT CHECK (output_image_tokens IS NULL OR output_image_tokens >= 0),
        output_audio_tokens BIGINT CHECK (output_audio_tokens IS NULL OR output_audio_tokens >= 0),
        uncached_input_tokens BIGINT CHECK (uncached_input_tokens IS NULL OR uncached_input_tokens >= 0),
        cache_read_input_tokens BIGINT CHECK (cache_read_input_tokens IS NULL OR cache_read_input_tokens >= 0),
        cache_creation_5m_input_tokens BIGINT CHECK (cache_creation_5m_input_tokens IS NULL OR cache_creation_5m_input_tokens >= 0),
        cache_creation_1h_input_tokens BIGINT CHECK (cache_creation_1h_input_tokens IS NULL OR cache_creation_1h_input_tokens >= 0),
        server_tool_web_search_requests BIGINT CHECK (server_tool_web_search_requests IS NULL OR server_tool_web_search_requests >= 0),
        usage_basis TEXT NOT NULL CHECK (usage_basis = 'provider_native_aggregate_observation'),
        provider_final INTEGER NOT NULL CHECK (provider_final = 0),
        PRIMARY KEY (organization_id, observation_id),
        UNIQUE (organization_id, snapshot_id, observation_digest),
        FOREIGN KEY (organization_id, snapshot_id)
            REFERENCES {prefix}portfolio_finance_snapshots
                (organization_id, snapshot_id)
    """,
    COST_TABLE: """
        organization_id TEXT NOT NULL CHECK (length(organization_id) BETWEEN 1 AND 128),
        observation_id TEXT NOT NULL CHECK (length(observation_id) = 36),
        snapshot_id TEXT NOT NULL CHECK (length(snapshot_id) = 36),
        bucket_start_at TEXT NOT NULL,
        bucket_end_at TEXT NOT NULL,
        observation_digest TEXT NOT NULL CHECK (length(observation_digest) = 64),
        provider_project_fingerprint TEXT,
        provider_workspace_fingerprint TEXT,
        api_key_fingerprint TEXT,
        free_text_classification TEXT NOT NULL CHECK (length(free_text_classification) BETWEEN 1 AND 128),
        free_text_fingerprint TEXT,
        model TEXT,
        cost_type TEXT,
        token_type TEXT,
        service_tier TEXT,
        context_window TEXT,
        inference_geo TEXT,
        native_amount TEXT NOT NULL CHECK (length(native_amount) BETWEEN 1 AND 128),
        canonical_amount TEXT NOT NULL CHECK (length(canonical_amount) BETWEEN 1 AND 128),
        currency TEXT NOT NULL CHECK (length(currency) = 3),
        native_quantity TEXT,
        quantity_unit TEXT,
        cost_basis TEXT NOT NULL CHECK (cost_basis = 'provider_reported_aggregate'),
        provider_final INTEGER NOT NULL CHECK (provider_final = 0),
        invoice_final INTEGER NOT NULL CHECK (invoice_final = 0),
        PRIMARY KEY (organization_id, observation_id),
        UNIQUE (organization_id, snapshot_id, observation_digest),
        FOREIGN KEY (organization_id, snapshot_id)
            REFERENCES {prefix}portfolio_finance_snapshots
                (organization_id, snapshot_id)
    """,
}


INDEX_DDL = {
    "portfolio_finance_binding_current": (
        "CREATE INDEX portfolio_finance_binding_current ON "
        "portfolio_finance_source_binding_versions (organization_id, binding_id, version DESC)"
    ),
    "portfolio_finance_snapshot_current": (
        "CREATE INDEX portfolio_finance_snapshot_current ON portfolio_finance_snapshots "
        "(organization_id, binding_id, binding_version, collection_profile, commit_sequence DESC)"
    ),
    "portfolio_finance_coverage_current": (
        "CREATE INDEX portfolio_finance_coverage_current ON portfolio_finance_snapshot_bucket_coverage "
        "(organization_id, bucket_start_at, bucket_end_at, snapshot_id)"
    ),
    "portfolio_finance_usage_bucket": (
        "CREATE INDEX portfolio_finance_usage_bucket ON portfolio_finance_usage_observations "
        "(organization_id, snapshot_id, bucket_start_at, bucket_end_at)"
    ),
    "portfolio_finance_cost_bucket": (
        "CREATE INDEX portfolio_finance_cost_bucket ON portfolio_finance_cost_observations "
        "(organization_id, snapshot_id, bucket_start_at, bucket_end_at)"
    ),
}


_CONFLICT_KEYS = {
    SOURCE_BINDING_TABLE: (
        ("organization_id", "binding_id", "version"),
        ("organization_id", "binding_event_id"),
        ("organization_id", "binding_id", "content_digest"),
    ),
    COLLECTION_ATTEMPT_TABLE: (
        ("organization_id", "attempt_id"),
        (
            "organization_id", "binding_id", "binding_version",
            "collection_profile", "query_start_at", "query_end_at",
            "idempotency_digest",
        ),
    ),
    COLLECTION_EVENT_TABLE: (
        ("organization_id", "event_id"),
        ("organization_id", "attempt_id"),
        ("organization_id", "receipt_id"),
    ),
    SNAPSHOT_TABLE: (
        ("organization_id", "snapshot_id"),
        ("organization_id", "attempt_id"),
        ("organization_id", "commit_sequence"),
    ),
    COVERAGE_TABLE: (
        ("organization_id", "coverage_id"),
        ("organization_id", "snapshot_id", "bucket_start_at", "bucket_end_at"),
    ),
    USAGE_TABLE: (
        ("organization_id", "observation_id"),
        ("organization_id", "snapshot_id", "observation_digest"),
    ),
    COST_TABLE: (
        ("organization_id", "observation_id"),
        ("organization_id", "snapshot_id", "observation_digest"),
    ),
}


_AUDIT_TABLE_V3 = """
CREATE TABLE gateway_audit_chain_entries_v3 (
    organization_id TEXT NOT NULL,
    chain_version INTEGER NOT NULL,
    chain_epoch INTEGER NOT NULL,
    sequence INTEGER NOT NULL,
    entry_schema_id TEXT NOT NULL,
    entry_schema_version INTEGER NOT NULL,
    event_id TEXT NOT NULL,
    previous_digest TEXT,
    event_digest TEXT NOT NULL,
    event_json TEXT NOT NULL,
    appended_at TEXT NOT NULL,
    source_schema_id TEXT,
    source_schema_version INTEGER,
    source_event_id TEXT,
    PRIMARY KEY (organization_id, chain_epoch, sequence),
    UNIQUE (organization_id, event_id),
    FOREIGN KEY (organization_id, chain_epoch)
        REFERENCES gateway_audit_chain_epochs (organization_id, chain_epoch),
    CHECK (chain_version = 1),
    CHECK (chain_epoch >= 1),
    CHECK (sequence >= 1),
    CHECK (entry_schema_id = 'hormuz.commit-audit-chain-entry'),
    CHECK (entry_schema_version IN (1,2)),
    CHECK (
        (entry_schema_version = 1 AND source_schema_id IS NULL AND source_schema_version IS NULL AND source_event_id IS NULL)
        OR (
            entry_schema_version = 2 AND source_event_id IS NOT NULL AND (
                (source_schema_id = 'hormuz.custody-control-event' AND source_schema_version = 1)
                OR (source_schema_id = 'hormuz.custody-execution-attempt' AND source_schema_version = 2)
                OR (source_schema_id = 'hormuz.custody-execution-event' AND source_schema_version = 1)
                OR (source_schema_id = 'hormuz.custody-lifecycle-event' AND source_schema_version = 1)
                OR (source_schema_id = 'hormuz.custody-envelope-attestation' AND source_schema_version = 1)
                OR (source_schema_id = 'hormuz.custody-deletion-event' AND source_schema_version = 1)
                OR (source_schema_id = 'hormuz.finance-attempt-evidence' AND source_schema_version = 1)
                OR (source_schema_id = 'hormuz.finance-source-binding-version' AND source_schema_version = 1)
                OR (source_schema_id = 'hormuz.finance-collection-event' AND source_schema_version = 1)
                OR (source_schema_id = 'hormuz.finance-snapshot' AND source_schema_version = 1)
            )
        )
    )
)
"""


_COLLECTION_AUDIT_TRIGGER = """CREATE TRIGGER gateway_finance_collection_audit_source_required
BEFORE INSERT ON gateway_audit_chain_entries
WHEN NEW.entry_schema_version=2
  AND NEW.source_schema_id IN (
      'hormuz.finance-source-binding-version',
      'hormuz.finance-collection-event',
      'hormuz.finance-snapshot'
  )
  AND NOT (
      NEW.event_id=NEW.source_event_id AND (
          (
              NEW.source_schema_id='hormuz.finance-source-binding-version'
              AND EXISTS (
                  SELECT 1 FROM portfolio_finance_source_binding_versions source
                  WHERE source.organization_id=NEW.organization_id
                    AND source.binding_event_id=NEW.source_event_id
                    AND source.evidence_json=NEW.event_json
              )
          ) OR (
              NEW.source_schema_id='hormuz.finance-collection-event'
              AND EXISTS (
                  SELECT 1 FROM portfolio_finance_collection_events source
                  WHERE source.organization_id=NEW.organization_id
                    AND source.event_id=NEW.source_event_id
                    AND source.evidence_json=NEW.event_json
              )
          ) OR (
              NEW.source_schema_id='hormuz.finance-snapshot'
              AND EXISTS (
                  SELECT 1 FROM portfolio_finance_snapshots source
                  WHERE source.organization_id=NEW.organization_id
                    AND source.snapshot_id=NEW.source_event_id
                    AND source.evidence_json=NEW.event_json
              )
          )
      )
  )
BEGIN SELECT RAISE(ABORT, 'finance_collection_audit_source_missing'); END"""


def sqlite_statements() -> tuple[str, ...]:
    statements: list[str] = []
    for table, ddl in TABLE_DDL.items():
        statements.append(
            f"CREATE TABLE {table} ({ddl.format(prefix='')}) WITHOUT ROWID"
        )
    statements.extend(INDEX_DDL.values())
    for table, keys in _CONFLICT_KEYS.items():
        for operation in ("UPDATE", "DELETE"):
            statements.append(
                f"CREATE TRIGGER {table}_no_{operation.lower()} BEFORE {operation} ON {table} "
                "BEGIN SELECT RAISE(ABORT, 'finance_collection_append_only'); END"
            )
        conflicts = " OR ".join(
            "(" + " AND ".join(
                # Match SQLite UNIQUE semantics: NULL does not conflict with
                # another NULL.  This matters for failed collection events,
                # whose nullable receipt IDs must not collide with each other.
                f"NEW.{field} IS NOT NULL AND existing.{field}=NEW.{field}"
                for field in key
            ) + ")"
            for key in keys
        )
        statements.append(
            f"CREATE TRIGGER {table}_no_replace BEFORE INSERT ON {table} "
            f"WHEN EXISTS (SELECT 1 FROM {table} existing WHERE {conflicts}) "
            "BEGIN SELECT RAISE(ABORT, 'finance_collection_replace_refused'); END"
        )
    return tuple(statements)


def apply_sqlite_finance_collection_migration(connection) -> None:
    """Apply schema 12 atomically inside the caller-owned transaction."""

    from ._finance_attempt_schema import SQLITE_FINANCE_ATTEMPT_TRIGGERS

    for statement in sqlite_statements():
        connection.execute(statement)

    for statement in (
        "DROP TRIGGER gateway_audit_chain_entries_no_update",
        "DROP TRIGGER gateway_audit_chain_entries_no_delete",
        "DROP TRIGGER gateway_finance_attempt_audit_source_required",
        "DROP INDEX idx_gateway_audit_chain_entries_event",
        "DROP INDEX idx_gateway_audit_chain_entries_source_identity",
    ):
        connection.execute(statement)
    connection.execute(_AUDIT_TABLE_V3)
    connection.execute(
        """
        INSERT INTO gateway_audit_chain_entries_v3 (
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
        "ALTER TABLE gateway_audit_chain_entries_v3 "
        "RENAME TO gateway_audit_chain_entries"
    )
    for statement in (
        "CREATE INDEX idx_gateway_audit_chain_entries_event ON gateway_audit_chain_entries (organization_id, event_id)",
        "CREATE UNIQUE INDEX idx_gateway_audit_chain_entries_source_identity ON gateway_audit_chain_entries (organization_id, source_schema_id, source_schema_version, source_event_id) WHERE entry_schema_version = 2",
        "CREATE TRIGGER gateway_audit_chain_entries_no_update BEFORE UPDATE ON gateway_audit_chain_entries BEGIN SELECT RAISE(ABORT, 'audit_chain_entry_immutable'); END",
        "CREATE TRIGGER gateway_audit_chain_entries_no_delete BEFORE DELETE ON gateway_audit_chain_entries BEGIN SELECT RAISE(ABORT, 'audit_chain_entry_immutable'); END",
        SQLITE_FINANCE_ATTEMPT_TRIGGERS[
            "gateway_finance_attempt_audit_source_required"
        ],
        _COLLECTION_AUDIT_TRIGGER,
    ):
        connection.execute(statement)


def verify_sqlite_finance_collection(connection, error_factory) -> None:
    observed = {
        str(row["name"]): " ".join(str(row["sql"]).split())
        for row in connection.execute(
            "SELECT name, sql FROM sqlite_master WHERE sql IS NOT NULL"
        ).fetchall()
    }
    expected = list(sqlite_statements()) + [_COLLECTION_AUDIT_TRIGGER]
    if any(
        observed.get(_statement_name(statement)) != " ".join(statement.split())
        for statement in expected
    ):
        raise error_factory("storage_schema_partial_upgrade")
    audit_sql = observed.get("gateway_audit_chain_entries", "")
    if any(
        source not in audit_sql
        for source in (
            "hormuz.finance-attempt-evidence",
            "hormuz.finance-source-binding-version",
            "hormuz.finance-collection-event",
            "hormuz.finance-snapshot",
        )
    ):
        raise error_factory("storage_schema_partial_upgrade")


def _statement_name(statement: str) -> str:
    words = statement.split()
    if len(words) < 3:
        return ""
    if words[0:2] == ["CREATE", "TABLE"]:
        return words[2]
    if words[0:2] == ["CREATE", "TRIGGER"]:
        return words[2]
    if words[0:2] == ["CREATE", "INDEX"]:
        return words[2]
    return ""


def verify_postgres_finance_collection(cursor, schema: str, error_factory) -> None:
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
        {name: ddl.format(prefix=schema + ".") for name, ddl in TABLE_DDL.items()},
        INDEX_DDL,
        expected,
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
        source not in definition
        for source in (
            "hormuz.finance-source-binding-version",
            "hormuz.finance-collection-event",
            "hormuz.finance-snapshot",
        )
    ):
        raise error_factory("storage_schema_partial_upgrade")
    cursor.execute(
        "SELECT p.prosrc, p.prosecdef, p.proconfig FROM pg_proc p "
        "JOIN pg_namespace n ON n.oid=p.pronamespace "
        "WHERE n.nspname=%s AND p.proname=%s",
        (schema, "enforce_custody_audit_chain_entry_insert"),
    )
    row = cursor.fetchone()
    values = tuple(row.values()) if isinstance(row, Mapping) else tuple(row or ())
    body = "" if not values else " ".join(str(values[0]).split())
    if (
        len(values) != 3
        or values[1] is not True
        or values[2] != ["search_path=pg_catalog"]
        or any(body.count(source) != 2 for source in (
            "hormuz.finance-source-binding-version",
            "hormuz.finance-collection-event",
            "hormuz.finance-snapshot",
        ))
    ):
        raise error_factory("storage_schema_partial_upgrade")

    # The ordinary runtime has no direct SELECT privilege on the custody or
    # collection source tables.  It verifies those entries through this
    # SECURITY DEFINER reader, so a schema that merely has the right function
    # name but an old or weakened body must fail closed at startup.
    cursor.execute(
        "SELECT p.prosrc, p.prosecdef, p.proconfig FROM pg_proc p "
        "JOIN pg_namespace n ON n.oid=p.pronamespace "
        "WHERE n.nspname=%s AND p.proname=%s",
        (schema, "custody_audit_chain_source_event_json"),
    )
    source_reader_row = cursor.fetchone()
    quoted_schema = '"' + schema.replace('"', '""') + '"'
    try:
        template = (
            resources.files("hormuz.migrations.postgresql")
            .joinpath("0016_finance_collection.sql")
            .read_text(encoding="utf-8")
        )
        rendered = template.format(
            schema=quoted_schema,
            runtime_role='"runtime_role"',
            policy_control_role='"policy_control_role"',
            custody_control_role='"custody_control_role"',
            custody_executor_role='"custody_executor_role"',
        )
        marker = (
            f"CREATE OR REPLACE FUNCTION {quoted_schema}."
            "custody_audit_chain_source_event_json("
        )
        source_reader_sql = rendered.split(marker, 1)[1]
        expected_source_reader_body = " ".join(
            source_reader_sql.split("AS $$", 1)[1]
            .split("$$;", 1)[0]
            .split()
        )
    except (FileNotFoundError, IndexError, KeyError, ModuleNotFoundError, ValueError):
        raise error_factory("storage_schema_partial_upgrade") from None
    source_reader_values = (
        tuple(source_reader_row.values())
        if isinstance(source_reader_row, Mapping)
        else tuple(source_reader_row or ())
    )
    if (
        len(source_reader_values) != 3
        or " ".join(str(source_reader_values[0]).split())
        != expected_source_reader_body
        or source_reader_values[1] is not True
        or source_reader_values[2] != ["search_path=pg_catalog"]
    ):
        raise error_factory("storage_schema_partial_upgrade")
