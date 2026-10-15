"""SQLite 13/PostgreSQL 18 finance account and privileged-read schema."""

from __future__ import annotations

from collections.abc import Mapping


REGISTRATION_TABLE = "portfolio_finance_account_binding_versions"
ATTEMPT_BINDING_TABLE = "gateway_finance_attempt_account_bindings"
QUERY_AUDIT_TABLE = "portfolio_finance_query_audit_events"

TABLE_DDL = {
    REGISTRATION_TABLE: """
        organization_id TEXT NOT NULL CHECK (length(organization_id) BETWEEN 1 AND 128),
        binding_id TEXT NOT NULL CHECK (length(binding_id) BETWEEN 1 AND 128),
        version INTEGER NOT NULL CHECK (version BETWEEN 1 AND 2147483647),
        binding_event_id TEXT NOT NULL CHECK (length(binding_event_id) = 36),
        previous_version INTEGER,
        binding_state TEXT NOT NULL CHECK (binding_state IN ('active','revoked')),
        reason_code TEXT NOT NULL CHECK (reason_code IN ('created','replaced','revoked')),
        upstream_reference_id TEXT NOT NULL CHECK (length(upstream_reference_id) BETWEEN 1 AND 128),
        upstream_reference_version INTEGER NOT NULL CHECK (upstream_reference_version BETWEEN 1 AND 2147483647),
        transport_profile TEXT NOT NULL CHECK (transport_profile IN ('openai.first-party.v1','anthropic.first-party.v1')),
        inference_credential_reference_id TEXT NOT NULL CHECK (length(inference_credential_reference_id) BETWEEN 1 AND 128),
        inference_credential_reference_version INTEGER NOT NULL CHECK (inference_credential_reference_version BETWEEN 1 AND 2147483647),
        source_binding_id TEXT NOT NULL CHECK (length(source_binding_id) BETWEEN 1 AND 128),
        source_binding_version INTEGER NOT NULL CHECK (source_binding_version BETWEEN 1 AND 2147483647),
        source_binding_digest TEXT NOT NULL CHECK (length(source_binding_digest) = 64),
        provider TEXT NOT NULL CHECK (provider IN ('openai','anthropic')),
        provider_account_fingerprint TEXT NOT NULL CHECK (length(provider_account_fingerprint) = 64),
        scope_kind TEXT NOT NULL CHECK (scope_kind IN ('organization','projects','workspaces')),
        scope_fingerprints_json TEXT NOT NULL CHECK (length(scope_fingerprints_json) BETWEEN 2 AND 65536),
        fingerprint_key_version INTEGER NOT NULL CHECK (fingerprint_key_version BETWEEN 1 AND 2147483647),
        content_digest TEXT NOT NULL CHECK (length(content_digest) = 64),
        request_digest TEXT NOT NULL CHECK (length(request_digest) = 64),
        registered_by TEXT NOT NULL CHECK (length(registered_by) BETWEEN 1 AND 128),
        registered_at TEXT NOT NULL,
        evidence_json TEXT NOT NULL CHECK (length(evidence_json) BETWEEN 2 AND 65536),
        PRIMARY KEY (organization_id, binding_id, version),
        UNIQUE (organization_id, binding_event_id),
        UNIQUE (organization_id, binding_id, request_digest),
        FOREIGN KEY (organization_id, source_binding_id, source_binding_version)
            REFERENCES {prefix}portfolio_finance_source_binding_versions
                (organization_id, binding_id, version),
        CHECK (
            (version=1 AND previous_version IS NULL AND reason_code='created')
            OR (
                version>1 AND previous_version=version-1 AND (
                    (binding_state='active' AND reason_code='replaced')
                    OR (binding_state='revoked' AND reason_code='revoked')
                )
            )
        ),
        CHECK (
            (provider='openai' AND transport_profile='openai.first-party.v1')
            OR (provider='anthropic' AND transport_profile='anthropic.first-party.v1')
        )
    """,
    ATTEMPT_BINDING_TABLE: """
        organization_id TEXT NOT NULL CHECK (length(organization_id) BETWEEN 1 AND 128),
        request_attempt_id TEXT NOT NULL CHECK (length(request_attempt_id) = 36),
        event_id TEXT NOT NULL CHECK (length(event_id) = 36),
        captured_at TEXT NOT NULL,
        state TEXT NOT NULL CHECK (state IN ('bound','unbound')),
        reason_code TEXT CHECK (reason_code IS NULL OR reason_code IN (
            'not_configured','binding_missing','binding_invalid','binding_revoked',
            'binding_version_stale','binding_ambiguous','source_binding_missing',
            'source_binding_revoked','source_binding_version_stale','tenant_mismatch',
            'upstream_reference_mismatch','inference_reference_mismatch',
            'unsupported_transport','fingerprint_key_version_mismatch'
        )),
        binding_id TEXT,
        binding_version INTEGER,
        binding_digest TEXT,
        upstream_reference_id TEXT,
        upstream_reference_version INTEGER,
        transport_profile TEXT,
        inference_credential_reference_id TEXT,
        inference_credential_reference_version INTEGER,
        source_binding_id TEXT,
        source_binding_version INTEGER,
        source_binding_digest TEXT,
        provider TEXT,
        provider_account_fingerprint TEXT,
        scope_kind TEXT,
        scope_fingerprints_json TEXT,
        fingerprint_key_version INTEGER,
        evidence_json TEXT NOT NULL CHECK (length(evidence_json) BETWEEN 2 AND 65536),
        PRIMARY KEY (organization_id, request_attempt_id),
        UNIQUE (organization_id, event_id),
        FOREIGN KEY (organization_id, request_attempt_id)
            REFERENCES {prefix}gateway_request_attempts (organization_id, attempt_id),
        FOREIGN KEY (organization_id, binding_id, binding_version)
            REFERENCES {prefix}portfolio_finance_account_binding_versions
                (organization_id, binding_id, version),
        CHECK (
            (
                state='unbound' AND reason_code IS NOT NULL
                AND binding_id IS NULL AND binding_version IS NULL AND binding_digest IS NULL
                AND upstream_reference_id IS NULL AND upstream_reference_version IS NULL
                AND transport_profile IS NULL AND inference_credential_reference_id IS NULL
                AND inference_credential_reference_version IS NULL AND source_binding_id IS NULL
                AND source_binding_version IS NULL AND source_binding_digest IS NULL
                AND provider IS NULL AND provider_account_fingerprint IS NULL
                AND scope_kind IS NULL AND scope_fingerprints_json IS NULL
                AND fingerprint_key_version IS NULL
            ) OR (
                state='bound' AND reason_code IS NULL
                AND binding_id IS NOT NULL AND length(binding_id) BETWEEN 1 AND 128
                AND binding_version BETWEEN 1 AND 2147483647 AND length(binding_digest)=64
                AND upstream_reference_id IS NOT NULL AND length(upstream_reference_id) BETWEEN 1 AND 128
                AND upstream_reference_version BETWEEN 1 AND 2147483647
                AND transport_profile IN ('openai.first-party.v1','anthropic.first-party.v1')
                AND inference_credential_reference_id IS NOT NULL
                AND length(inference_credential_reference_id) BETWEEN 1 AND 128
                AND inference_credential_reference_version BETWEEN 1 AND 2147483647
                AND source_binding_id IS NOT NULL AND length(source_binding_id) BETWEEN 1 AND 128
                AND source_binding_version BETWEEN 1 AND 2147483647
                AND length(source_binding_digest)=64 AND provider IN ('openai','anthropic')
                AND length(provider_account_fingerprint)=64
                AND scope_kind IN ('organization','projects','workspaces')
                AND length(scope_fingerprints_json) BETWEEN 2 AND 65536
                AND fingerprint_key_version BETWEEN 1 AND 2147483647
                AND (
                    (provider='openai' AND transport_profile='openai.first-party.v1')
                    OR (provider='anthropic' AND transport_profile='anthropic.first-party.v1')
                )
            )
        )
    """,
    QUERY_AUDIT_TABLE: """
        organization_id TEXT NOT NULL CHECK (length(organization_id) BETWEEN 1 AND 128),
        query_event_id TEXT NOT NULL CHECK (length(query_event_id) = 36),
        actor_id TEXT NOT NULL CHECK (length(actor_id) BETWEEN 1 AND 128),
        query_class TEXT NOT NULL CHECK (query_class='finance_coverage_report_v1'),
        binding_id TEXT NOT NULL CHECK (length(binding_id) BETWEEN 1 AND 128),
        binding_version INTEGER NOT NULL CHECK (binding_version BETWEEN 1 AND 2147483647),
        collection_profile TEXT NOT NULL CHECK (length(collection_profile) BETWEEN 1 AND 128),
        query_start_at TEXT NOT NULL,
        query_end_at TEXT NOT NULL,
        as_of_commit_sequence BIGINT NOT NULL CHECK (as_of_commit_sequence >= 0),
        currency TEXT NOT NULL CHECK (length(currency)=3),
        selected_snapshot_count BIGINT NOT NULL CHECK (selected_snapshot_count >= 0),
        coverage_bucket_count BIGINT NOT NULL CHECK (coverage_bucket_count >= 0),
        provider_observation_count BIGINT NOT NULL CHECK (provider_observation_count >= 0),
        terminal_attempt_count BIGINT NOT NULL CHECK (terminal_attempt_count >= 0),
        terminal_attempts_missing_sidecar_count BIGINT NOT NULL CHECK (
            terminal_attempts_missing_sidecar_count >= 0
            AND terminal_attempts_missing_sidecar_count <= terminal_attempt_count
        ),
        occurred_at TEXT NOT NULL,
        evidence_json TEXT NOT NULL CHECK (length(evidence_json) BETWEEN 2 AND 65536),
        PRIMARY KEY (organization_id, query_event_id),
        FOREIGN KEY (organization_id, binding_id, binding_version)
            REFERENCES {prefix}portfolio_finance_source_binding_versions
                (organization_id, binding_id, version)
    """,
}

INDEX_DDL = {
    "portfolio_finance_account_binding_current": (
        f"{REGISTRATION_TABLE} (organization_id, binding_id, version DESC)"
    ),
    "portfolio_finance_query_audit_time": (
        f"{QUERY_AUDIT_TABLE} (organization_id, occurred_at, query_event_id)"
    ),
    "portfolio_finance_attempt_account_binding_event": (
        f"{ATTEMPT_BINDING_TABLE} (organization_id, event_id)"
    ),
}

_CONFLICT_KEYS = {
    REGISTRATION_TABLE: (
        ("organization_id", "binding_id", "version"),
        ("organization_id", "binding_event_id"),
        ("organization_id", "binding_id", "request_digest"),
    ),
    ATTEMPT_BINDING_TABLE: (
        ("organization_id", "request_attempt_id"),
        ("organization_id", "event_id"),
    ),
    QUERY_AUDIT_TABLE: (("organization_id", "query_event_id"),),
}

_REGISTRATION_CONSISTENCY_TRIGGER = """CREATE TRIGGER portfolio_finance_account_binding_source_consistency
BEFORE INSERT ON portfolio_finance_account_binding_versions
WHEN NOT EXISTS (
    SELECT 1 FROM portfolio_finance_source_binding_versions source
    WHERE source.organization_id=NEW.organization_id
      AND source.binding_id=NEW.source_binding_id
      AND source.version=NEW.source_binding_version
      AND source.content_digest=NEW.source_binding_digest
      AND source.provider=NEW.provider
      AND source.provider_account_fingerprint=NEW.provider_account_fingerprint
      AND source.scope_kind=NEW.scope_kind
      AND source.scope_fingerprints_json=NEW.scope_fingerprints_json
      AND source.fingerprint_key_version=NEW.fingerprint_key_version
)
BEGIN SELECT RAISE(ABORT, 'finance_account_binding_source_mismatch'); END"""

_ATTEMPT_CONSISTENCY_TRIGGER = """CREATE TRIGGER gateway_finance_attempt_account_binding_consistency
BEFORE INSERT ON gateway_finance_attempt_account_bindings
WHEN NOT EXISTS (
    SELECT 1 FROM gateway_request_attempts root
    WHERE root.organization_id=NEW.organization_id
      AND root.attempt_id=NEW.request_attempt_id
      AND root.created_at=NEW.captured_at
) OR (
    NEW.state='bound' AND NOT EXISTS (
        SELECT 1 FROM portfolio_finance_account_binding_versions binding
        WHERE binding.organization_id=NEW.organization_id
          AND binding.binding_id=NEW.binding_id
          AND binding.version=NEW.binding_version
          AND binding.binding_state='active'
          AND binding.content_digest=NEW.binding_digest
          AND binding.upstream_reference_id=NEW.upstream_reference_id
          AND binding.upstream_reference_version=NEW.upstream_reference_version
          AND binding.transport_profile=NEW.transport_profile
          AND binding.inference_credential_reference_id=NEW.inference_credential_reference_id
          AND binding.inference_credential_reference_version=NEW.inference_credential_reference_version
          AND binding.source_binding_id=NEW.source_binding_id
          AND binding.source_binding_version=NEW.source_binding_version
          AND binding.source_binding_digest=NEW.source_binding_digest
          AND binding.provider=NEW.provider
          AND binding.provider_account_fingerprint=NEW.provider_account_fingerprint
          AND binding.scope_kind=NEW.scope_kind
          AND binding.scope_fingerprints_json=NEW.scope_fingerprints_json
          AND binding.fingerprint_key_version=NEW.fingerprint_key_version
    )
)
BEGIN SELECT RAISE(ABORT, 'finance_attempt_account_binding_mismatch'); END"""

_AUDIT_SOURCE_TRIGGER = """CREATE TRIGGER gateway_finance_account_audit_source_required
BEFORE INSERT ON gateway_audit_chain_entries
WHEN NEW.entry_schema_version=2
  AND NEW.source_schema_id IN (
      'hormuz.finance-account-binding-version',
      'hormuz.finance-attempt-account-binding',
      'hormuz.finance-query-audit-event'
  )
  AND NOT (
      NEW.event_id=NEW.source_event_id AND (
          (
              NEW.source_schema_id='hormuz.finance-account-binding-version'
              AND EXISTS (
                  SELECT 1 FROM portfolio_finance_account_binding_versions source
                  WHERE source.organization_id=NEW.organization_id
                    AND source.binding_event_id=NEW.source_event_id
                    AND source.evidence_json=NEW.event_json
              )
          ) OR (
              NEW.source_schema_id='hormuz.finance-attempt-account-binding'
              AND EXISTS (
                  SELECT 1 FROM gateway_finance_attempt_account_bindings source
                  WHERE source.organization_id=NEW.organization_id
                    AND source.event_id=NEW.source_event_id
                    AND source.evidence_json=NEW.event_json
              )
          ) OR (
              NEW.source_schema_id='hormuz.finance-query-audit-event'
              AND EXISTS (
                  SELECT 1 FROM portfolio_finance_query_audit_events source
                  WHERE source.organization_id=NEW.organization_id
                    AND source.query_event_id=NEW.source_event_id
                    AND source.evidence_json=NEW.event_json
              )
          )
      )
  )
BEGIN SELECT RAISE(ABORT, 'finance_account_audit_source_missing'); END"""

_AUDIT_TABLE_V4 = """
CREATE TABLE gateway_audit_chain_entries_v4 (
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
                OR (source_schema_id = 'hormuz.finance-account-binding-version' AND source_schema_version = 1)
                OR (source_schema_id = 'hormuz.finance-attempt-account-binding' AND source_schema_version = 1)
                OR (source_schema_id = 'hormuz.finance-query-audit-event' AND source_schema_version = 1)
            )
        )
    )
)
"""


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
                "BEGIN SELECT RAISE(ABORT, 'finance_account_append_only'); END"
            )
        conflicts = " OR ".join(
            "(" + " AND ".join(f"existing.{field}=NEW.{field}" for field in key) + ")"
            for key in keys
        )
        statements.append(
            f"CREATE TRIGGER {table}_no_replace BEFORE INSERT ON {table} "
            f"WHEN EXISTS (SELECT 1 FROM {table} existing WHERE {conflicts}) "
            "BEGIN SELECT RAISE(ABORT, 'finance_account_replace_refused'); END"
        )
    statements.extend((_REGISTRATION_CONSISTENCY_TRIGGER, _ATTEMPT_CONSISTENCY_TRIGGER))
    return tuple(statements)


def apply_sqlite_finance_account_binding_migration(connection) -> None:
    """Apply schema 13 atomically inside the caller-owned transaction."""

    from ._finance_attempt_schema import SQLITE_FINANCE_ATTEMPT_TRIGGERS
    from ._finance_collection_schema import _COLLECTION_AUDIT_TRIGGER

    for statement in sqlite_statements():
        connection.execute(statement)
    for statement in (
        "DROP TRIGGER gateway_audit_chain_entries_no_update",
        "DROP TRIGGER gateway_audit_chain_entries_no_delete",
        "DROP TRIGGER gateway_finance_attempt_audit_source_required",
        "DROP TRIGGER gateway_finance_collection_audit_source_required",
        "DROP INDEX idx_gateway_audit_chain_entries_event",
        "DROP INDEX idx_gateway_audit_chain_entries_source_identity",
    ):
        connection.execute(statement)
    connection.execute(_AUDIT_TABLE_V4)
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
    ):
        connection.execute(statement)


def verify_sqlite_finance_account_binding(connection, error_factory) -> None:
    observed = {
        str(row["name"]): " ".join(str(row["sql"]).split())
        for row in connection.execute(
            "SELECT name, sql FROM sqlite_master WHERE sql IS NOT NULL"
        ).fetchall()
    }
    expected = list(sqlite_statements()) + [_AUDIT_SOURCE_TRIGGER]
    if any(
        observed.get(_statement_name(statement)) != " ".join(statement.split())
        for statement in expected
    ):
        raise error_factory("storage_schema_partial_upgrade")
    audit_sql = observed.get("gateway_audit_chain_entries", "")
    if any(source not in audit_sql for source in (
        "hormuz.finance-account-binding-version",
        "hormuz.finance-attempt-account-binding",
        "hormuz.finance-query-audit-event",
    )):
        raise error_factory("storage_schema_partial_upgrade")


def _statement_name(statement: str) -> str:
    words = statement.split()
    if len(words) < 3:
        return ""
    if words[0:2] in (["CREATE", "TABLE"], ["CREATE", "TRIGGER"], ["CREATE", "INDEX"]):
        return words[2]
    return ""
