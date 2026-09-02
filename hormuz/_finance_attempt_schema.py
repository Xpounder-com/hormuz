"""Schema 11/15 for immutable provider-attempt finance evidence."""

from __future__ import annotations

from collections.abc import Mapping
from importlib import resources


ATTEMPT_BINDING_COLUMNS = {
    "configured_rate_card_state",
    "configured_rate_card_id",
    "configured_rate_card_version",
    "configured_rate_card_digest",
    "configured_rate_card_currency",
}

FINANCE_ATTEMPT_TABLE = "gateway_finance_attempt_evidence"

SQLITE_FINANCE_ATTEMPT_INDEXES = {
    "idx_gateway_usage_organization_id": (
        "CREATE UNIQUE INDEX idx_gateway_usage_organization_id "
        "ON gateway_usage_events (organization_id, id)"
    ),
    "idx_gateway_attempt_organization_id": (
        "CREATE UNIQUE INDEX idx_gateway_attempt_organization_id "
        "ON gateway_request_attempts (organization_id, attempt_id)"
    ),
    "idx_gateway_attempt_event_organization_id": (
        "CREATE UNIQUE INDEX idx_gateway_attempt_event_organization_id "
        "ON gateway_request_attempt_events (organization_id, id)"
    ),
    "gateway_finance_attempt_time": (
        "CREATE INDEX gateway_finance_attempt_time ON "
        "gateway_finance_attempt_evidence (organization_id, occurred_at, request_attempt_id)"
    ),
    "gateway_finance_attempt_rate_card": (
        "CREATE INDEX gateway_finance_attempt_rate_card ON "
        "gateway_finance_attempt_evidence (organization_id, configured_rate_card_id, "
        "configured_rate_card_version, occurred_at)"
    ),
    "gateway_finance_attempt_provider": (
        "CREATE INDEX gateway_finance_attempt_provider ON "
        "gateway_finance_attempt_evidence (organization_id, provider_schema_id, "
        "provider_service_tier, occurred_at)"
    ),
}

SQLITE_FINANCE_ATTEMPT_TRIGGERS = {
    "gateway_request_attempt_finance_binding_required": (
        "CREATE TRIGGER gateway_request_attempt_finance_binding_required BEFORE INSERT ON gateway_request_attempts "
        "WHEN NEW.configured_rate_card_state <> 'configured' "
        "OR NEW.configured_rate_card_id IS NULL "
        "OR length(NEW.configured_rate_card_id) NOT BETWEEN 1 AND 128 "
        "OR NOT (substr(NEW.configured_rate_card_id, 1, 1) GLOB '[A-Za-z0-9]') "
        "OR NEW.configured_rate_card_id GLOB '*[^A-Za-z0-9._:-]*' "
        "OR NEW.configured_rate_card_version IS NULL "
        "OR typeof(NEW.configured_rate_card_version) <> 'integer' "
        "OR NEW.configured_rate_card_version NOT BETWEEN 1 AND 2147483647 "
        "OR NEW.configured_rate_card_digest IS NULL "
        "OR length(NEW.configured_rate_card_digest) <> 64 "
        "OR NEW.configured_rate_card_digest GLOB '*[^0-9a-f]*' "
        "OR NEW.configured_rate_card_currency IS NULL "
        "OR NOT (NEW.configured_rate_card_currency GLOB '[A-Z][A-Z][A-Z]') "
        "BEGIN SELECT RAISE(ABORT, 'finance_attempt_binding_required'); END"
    ),
    "gateway_request_attempt_finance_binding_immutable": (
        "CREATE TRIGGER gateway_request_attempt_finance_binding_immutable BEFORE UPDATE OF "
        "configured_rate_card_state, configured_rate_card_id, configured_rate_card_version, "
        "configured_rate_card_digest, configured_rate_card_currency ON gateway_request_attempts "
        "BEGIN SELECT RAISE(ABORT, 'finance_attempt_binding_immutable'); END"
    ),
    "gateway_finance_attempt_evidence_consistency": (
        "CREATE TRIGGER gateway_finance_attempt_evidence_consistency BEFORE INSERT ON gateway_finance_attempt_evidence "
        "WHEN NOT EXISTS (SELECT 1 FROM gateway_request_attempts r JOIN gateway_request_attempt_events e "
        "ON e.organization_id=r.organization_id AND e.attempt_id=r.attempt_id "
        "WHERE r.organization_id=NEW.organization_id AND r.attempt_id=NEW.request_attempt_id "
        "AND r.configured_rate_card_state='configured' "
        "AND r.configured_rate_card_id=NEW.configured_rate_card_id "
        "AND r.configured_rate_card_version=NEW.configured_rate_card_version "
        "AND r.configured_rate_card_digest=NEW.configured_rate_card_digest "
        "AND r.configured_rate_card_currency=NEW.configured_estimate_currency "
        "AND ((r.protocol='openai' AND NEW.provider_schema_id='openai.responses.usage.v1') "
        "OR (r.protocol='anthropic' AND NEW.provider_schema_id='anthropic.messages.usage.v1')) "
        "AND e.organization_id=NEW.organization_id AND e.id=NEW.terminal_attempt_event_id "
        "AND e.state=NEW.terminal_state AND e.occurred_at=NEW.occurred_at "
        "AND e.usage_event_id IS NEW.usage_event_id) "
        "BEGIN SELECT RAISE(ABORT, 'finance_attempt_evidence_inconsistent'); END"
    ),
    "gateway_finance_attempt_evidence_no_update": (
        "CREATE TRIGGER gateway_finance_attempt_evidence_no_update BEFORE UPDATE ON "
        "gateway_finance_attempt_evidence BEGIN SELECT RAISE(ABORT, "
        "'finance_attempt_evidence_append_only'); END"
    ),
    "gateway_finance_attempt_evidence_no_delete": (
        "CREATE TRIGGER gateway_finance_attempt_evidence_no_delete BEFORE DELETE ON "
        "gateway_finance_attempt_evidence BEGIN SELECT RAISE(ABORT, "
        "'finance_attempt_evidence_append_only'); END"
    ),
    "gateway_finance_attempt_evidence_no_replace": (
        "CREATE TRIGGER gateway_finance_attempt_evidence_no_replace BEFORE INSERT ON "
        "gateway_finance_attempt_evidence WHEN EXISTS (SELECT 1 FROM gateway_finance_attempt_evidence "
        "WHERE (organization_id=NEW.organization_id AND evidence_event_id=NEW.evidence_event_id) "
        "OR (organization_id=NEW.organization_id AND request_attempt_id=NEW.request_attempt_id)) "
        "BEGIN SELECT RAISE(ABORT, 'finance_attempt_evidence_append_only'); END"
    ),
    "gateway_finance_attempt_audit_source_required": (
        "CREATE TRIGGER gateway_finance_attempt_audit_source_required BEFORE INSERT ON "
        "gateway_audit_chain_entries WHEN NEW.entry_schema_version=2 "
        "AND NEW.source_schema_id='hormuz.finance-attempt-evidence' "
        "AND NOT EXISTS (SELECT 1 FROM gateway_finance_attempt_evidence f "
        "WHERE f.organization_id=NEW.organization_id AND f.evidence_event_id=NEW.source_event_id "
        "AND f.evidence_json=NEW.event_json AND NEW.event_id=NEW.source_event_id) "
        "BEGIN SELECT RAISE(ABORT, 'finance_attempt_audit_source_missing'); END"
    ),
}

POSTGRES_FINANCE_ATTEMPT_INDEXES = {
    "gateway_request_attempt_event_organization_id": (
        True,
        ("organization_id", "id"),
    ),
    "gateway_finance_attempt_time": (
        False,
        ("organization_id", "occurred_at", "request_attempt_id"),
    ),
    "gateway_finance_attempt_rate_card": (
        False,
        (
            "organization_id",
            "configured_rate_card_id",
            "configured_rate_card_version",
            "occurred_at",
        ),
    ),
    "gateway_finance_attempt_provider": (
        False,
        ("organization_id", "provider_schema_id", "provider_service_tier", "occurred_at"),
    ),
}

FINANCE_ATTEMPT_COLUMNS = {
    "evidence_event_id", "event_schema_id", "event_schema_version", "organization_id",
    "request_attempt_id", "terminal_attempt_event_id", "usage_event_id", "terminal_state",
    "occurred_at", "provider_schema_id", "provider_schema_version", "observation_state",
    "observation_reason_code", "native_payload_json", "native_payload_digest",
    "provider_input_tokens", "provider_output_tokens", "cache_read_input_tokens",
    "cache_write_input_tokens", "cache_write_5m_input_tokens", "cache_write_1h_input_tokens",
    "reasoning_output_tokens", "total_tokens", "billable_input_tokens", "billable_output_tokens",
    "server_tool_request_count", "provider_service_tier", "provider_inference_geo",
    "configured_estimate_availability", "configured_estimate_amount",
    "configured_estimate_microusd", "configured_estimate_currency", "configured_estimate_basis",
    "configured_estimate_reason_code", "configured_rate_card_id", "configured_rate_card_version",
    "configured_rate_card_digest", "provider_final", "evidence_json",
}


SQLITE_FINANCE_TABLE = """
CREATE TABLE gateway_finance_attempt_evidence (
    evidence_event_id TEXT NOT NULL CHECK (length(evidence_event_id) = 36),
    event_schema_id TEXT NOT NULL CHECK (event_schema_id = 'hormuz.finance-attempt-evidence'),
    event_schema_version INTEGER NOT NULL CHECK (event_schema_version = 1),
    organization_id TEXT NOT NULL CHECK (length(organization_id) BETWEEN 1 AND 128),
    request_attempt_id TEXT NOT NULL CHECK (length(request_attempt_id) BETWEEN 1 AND 128),
    terminal_attempt_event_id TEXT NOT NULL CHECK (length(terminal_attempt_event_id) = 36),
    usage_event_id TEXT,
    terminal_state TEXT NOT NULL CHECK (terminal_state IN ('succeeded','failed','rate_limited','outcome_unknown')),
    occurred_at TEXT NOT NULL,
    provider_schema_id TEXT NOT NULL CHECK (provider_schema_id IN ('openai.responses.usage.v1','anthropic.messages.usage.v1')),
    provider_schema_version INTEGER NOT NULL CHECK (provider_schema_version = 1),
    observation_state TEXT NOT NULL CHECK (observation_state IN ('complete','partial','absent')),
    observation_reason_code TEXT CHECK (observation_reason_code IS NULL OR length(observation_reason_code) BETWEEN 1 AND 128),
    native_payload_json TEXT,
    native_payload_digest TEXT,
    provider_input_tokens INTEGER CHECK (provider_input_tokens >= 0),
    provider_output_tokens INTEGER CHECK (provider_output_tokens >= 0),
    cache_read_input_tokens INTEGER CHECK (cache_read_input_tokens >= 0),
    cache_write_input_tokens INTEGER CHECK (cache_write_input_tokens >= 0),
    cache_write_5m_input_tokens INTEGER CHECK (cache_write_5m_input_tokens >= 0),
    cache_write_1h_input_tokens INTEGER CHECK (cache_write_1h_input_tokens >= 0),
    reasoning_output_tokens INTEGER CHECK (reasoning_output_tokens >= 0),
    total_tokens INTEGER CHECK (total_tokens >= 0),
    billable_input_tokens INTEGER CHECK (billable_input_tokens >= 0),
    billable_output_tokens INTEGER CHECK (billable_output_tokens >= 0),
    server_tool_request_count INTEGER CHECK (server_tool_request_count >= 0),
    provider_service_tier TEXT CHECK (provider_service_tier IS NULL OR length(provider_service_tier) BETWEEN 1 AND 128),
    provider_inference_geo TEXT CHECK (provider_inference_geo IS NULL OR length(provider_inference_geo) BETWEEN 1 AND 128),
    configured_estimate_availability TEXT NOT NULL CHECK (configured_estimate_availability IN ('available','unavailable')),
    configured_estimate_amount TEXT,
    configured_estimate_microusd INTEGER CHECK (configured_estimate_microusd >= 0),
    configured_estimate_currency TEXT NOT NULL CHECK (length(configured_estimate_currency) = 3),
    configured_estimate_basis TEXT NOT NULL CHECK (configured_estimate_basis = 'configured_rate_card_estimate'),
    configured_estimate_reason_code TEXT NOT NULL CHECK (length(configured_estimate_reason_code) BETWEEN 1 AND 128),
    configured_rate_card_id TEXT NOT NULL CHECK (length(configured_rate_card_id) BETWEEN 1 AND 128),
    configured_rate_card_version INTEGER NOT NULL CHECK (configured_rate_card_version BETWEEN 1 AND 2147483647),
    configured_rate_card_digest TEXT NOT NULL CHECK (length(configured_rate_card_digest) = 64),
    provider_final INTEGER NOT NULL CHECK (provider_final = 0),
    evidence_json TEXT NOT NULL CHECK (length(evidence_json) BETWEEN 2 AND 65536),
    PRIMARY KEY (organization_id, evidence_event_id),
    UNIQUE (organization_id, request_attempt_id),
    FOREIGN KEY (organization_id, request_attempt_id)
        REFERENCES gateway_request_attempts (organization_id, attempt_id),
    FOREIGN KEY (organization_id, terminal_attempt_event_id)
        REFERENCES gateway_request_attempt_events (organization_id, id),
    FOREIGN KEY (organization_id, usage_event_id)
        REFERENCES gateway_usage_events (organization_id, id),
    CHECK ((terminal_state = 'outcome_unknown') = (usage_event_id IS NULL)),
    CHECK (
        (observation_state = 'complete' AND observation_reason_code IS NULL AND native_payload_json IS NOT NULL AND native_payload_digest IS NOT NULL)
        OR (observation_state = 'partial' AND observation_reason_code IS NOT NULL AND native_payload_json IS NOT NULL AND native_payload_digest IS NOT NULL)
        OR (observation_state = 'absent' AND observation_reason_code IS NOT NULL AND native_payload_json IS NULL AND native_payload_digest IS NULL)
    ),
    CHECK (native_payload_json IS NULL OR length(CAST(native_payload_json AS BLOB)) <= 16384),
    CHECK (native_payload_digest IS NULL OR length(native_payload_digest) = 64),
    CHECK (
        (configured_estimate_availability = 'available' AND configured_estimate_amount IS NOT NULL AND configured_estimate_microusd IS NOT NULL AND configured_estimate_reason_code = 'estimated')
        OR (configured_estimate_availability = 'unavailable' AND configured_estimate_amount IS NULL AND configured_estimate_microusd IS NULL AND configured_estimate_reason_code <> 'estimated')
    ),
    CHECK (
        configured_estimate_availability <> 'available'
        OR (
            provider_input_tokens IS NOT NULL
            AND provider_output_tokens IS NOT NULL
            AND cache_read_input_tokens IS NOT NULL
            AND cache_write_input_tokens IS NOT NULL
            AND (
                provider_schema_id <> 'openai.responses.usage.v1'
                OR (
                    cache_write_input_tokens <= provider_input_tokens
                    AND cache_read_input_tokens <= provider_input_tokens - cache_write_input_tokens
                )
            )
        )
    ),
    CHECK (
        provider_schema_id <> 'anthropic.messages.usage.v1'
        OR observation_state <> 'complete'
        OR provider_input_tokens IS NULL
        OR provider_output_tokens IS NULL
        OR cache_read_input_tokens IS NULL
        OR cache_write_input_tokens IS NULL
        OR total_tokens IS NOT NULL
    ),
    CHECK (
        (terminal_state = 'outcome_unknown' AND configured_estimate_availability = 'unavailable' AND configured_estimate_reason_code = 'attempt_outcome_unknown')
        OR (terminal_state <> 'outcome_unknown' AND configured_estimate_reason_code <> 'attempt_outcome_unknown')
    ),
    CHECK (terminal_state <> 'outcome_unknown' OR observation_state <> 'complete')
) WITHOUT ROWID
"""


POSTGRES_FINANCE_TABLE_DDL = """
    evidence_event_id TEXT NOT NULL CHECK (length(evidence_event_id) = 36),
    event_schema_id TEXT NOT NULL CHECK (event_schema_id = 'hormuz.finance-attempt-evidence'),
    event_schema_version INTEGER NOT NULL CHECK (event_schema_version = 1),
    organization_id TEXT NOT NULL CHECK (length(organization_id) BETWEEN 1 AND 128),
    request_attempt_id TEXT NOT NULL CHECK (length(request_attempt_id) BETWEEN 1 AND 128),
    terminal_attempt_event_id TEXT NOT NULL CHECK (length(terminal_attempt_event_id) = 36),
    usage_event_id TEXT,
    terminal_state TEXT NOT NULL CHECK (terminal_state IN ('succeeded','failed','rate_limited','outcome_unknown')),
    occurred_at TIMESTAMPTZ NOT NULL,
    provider_schema_id TEXT NOT NULL CHECK (provider_schema_id IN ('openai.responses.usage.v1','anthropic.messages.usage.v1')),
    provider_schema_version INTEGER NOT NULL CHECK (provider_schema_version = 1),
    observation_state TEXT NOT NULL CHECK (observation_state IN ('complete','partial','absent')),
    observation_reason_code TEXT CHECK (observation_reason_code IS NULL OR length(observation_reason_code) BETWEEN 1 AND 128),
    native_payload_json TEXT,
    native_payload_digest TEXT,
    provider_input_tokens BIGINT CHECK (provider_input_tokens >= 0),
    provider_output_tokens BIGINT CHECK (provider_output_tokens >= 0),
    cache_read_input_tokens BIGINT CHECK (cache_read_input_tokens >= 0),
    cache_write_input_tokens BIGINT CHECK (cache_write_input_tokens >= 0),
    cache_write_5m_input_tokens BIGINT CHECK (cache_write_5m_input_tokens >= 0),
    cache_write_1h_input_tokens BIGINT CHECK (cache_write_1h_input_tokens >= 0),
    reasoning_output_tokens BIGINT CHECK (reasoning_output_tokens >= 0),
    total_tokens BIGINT CHECK (total_tokens >= 0),
    billable_input_tokens BIGINT CHECK (billable_input_tokens >= 0),
    billable_output_tokens BIGINT CHECK (billable_output_tokens >= 0),
    server_tool_request_count BIGINT CHECK (server_tool_request_count >= 0),
    provider_service_tier TEXT CHECK (provider_service_tier IS NULL OR length(provider_service_tier) BETWEEN 1 AND 128),
    provider_inference_geo TEXT CHECK (provider_inference_geo IS NULL OR length(provider_inference_geo) BETWEEN 1 AND 128),
    configured_estimate_availability TEXT NOT NULL CHECK (configured_estimate_availability IN ('available','unavailable')),
    configured_estimate_amount TEXT,
    configured_estimate_microusd BIGINT CHECK (configured_estimate_microusd >= 0),
    configured_estimate_currency TEXT NOT NULL CHECK (configured_estimate_currency ~ '^[A-Z]{{3}}$'),
    configured_estimate_basis TEXT NOT NULL CHECK (configured_estimate_basis = 'configured_rate_card_estimate'),
    configured_estimate_reason_code TEXT NOT NULL CHECK (length(configured_estimate_reason_code) BETWEEN 1 AND 128),
    configured_rate_card_id TEXT NOT NULL CHECK (length(configured_rate_card_id) BETWEEN 1 AND 128),
    configured_rate_card_version INTEGER NOT NULL CHECK (configured_rate_card_version BETWEEN 1 AND 2147483647),
    configured_rate_card_digest TEXT NOT NULL CHECK (configured_rate_card_digest ~ '^[0-9a-f]{{64}}$'),
    provider_final BOOLEAN NOT NULL CHECK (provider_final = FALSE),
    evidence_json TEXT NOT NULL CHECK (octet_length(evidence_json) BETWEEN 2 AND 65536),
    PRIMARY KEY (organization_id, evidence_event_id),
    UNIQUE (organization_id, request_attempt_id),
    FOREIGN KEY (organization_id, request_attempt_id)
        REFERENCES {prefix}gateway_request_attempts (organization_id, attempt_id),
    FOREIGN KEY (organization_id, terminal_attempt_event_id)
        REFERENCES {prefix}gateway_request_attempt_events (organization_id, id),
    FOREIGN KEY (organization_id, usage_event_id)
        REFERENCES {prefix}gateway_usage_events (organization_id, id),
    CHECK ((terminal_state = 'outcome_unknown') = (usage_event_id IS NULL)),
    CHECK (
        (observation_state = 'complete' AND observation_reason_code IS NULL AND native_payload_json IS NOT NULL AND native_payload_digest IS NOT NULL)
        OR (observation_state = 'partial' AND observation_reason_code IS NOT NULL AND native_payload_json IS NOT NULL AND native_payload_digest IS NOT NULL)
        OR (observation_state = 'absent' AND observation_reason_code IS NOT NULL AND native_payload_json IS NULL AND native_payload_digest IS NULL)
    ),
    CHECK (native_payload_json IS NULL OR octet_length(native_payload_json) <= 16384),
    CHECK (native_payload_digest IS NULL OR native_payload_digest ~ '^[0-9a-f]{{64}}$'),
    CHECK (
        (configured_estimate_availability = 'available' AND configured_estimate_amount IS NOT NULL AND configured_estimate_microusd IS NOT NULL AND configured_estimate_reason_code = 'estimated')
        OR (configured_estimate_availability = 'unavailable' AND configured_estimate_amount IS NULL AND configured_estimate_microusd IS NULL AND configured_estimate_reason_code <> 'estimated')
    ),
    CHECK (
        configured_estimate_availability <> 'available'
        OR (
            provider_input_tokens IS NOT NULL
            AND provider_output_tokens IS NOT NULL
            AND cache_read_input_tokens IS NOT NULL
            AND cache_write_input_tokens IS NOT NULL
            AND (
                provider_schema_id <> 'openai.responses.usage.v1'
                OR (
                    cache_write_input_tokens <= provider_input_tokens
                    AND cache_read_input_tokens <= provider_input_tokens - cache_write_input_tokens
                )
            )
        )
    ),
    CHECK (
        provider_schema_id <> 'anthropic.messages.usage.v1'
        OR observation_state <> 'complete'
        OR provider_input_tokens IS NULL
        OR provider_output_tokens IS NULL
        OR cache_read_input_tokens IS NULL
        OR cache_write_input_tokens IS NULL
        OR total_tokens IS NOT NULL
    ),
    CHECK (
        (terminal_state = 'outcome_unknown' AND configured_estimate_availability = 'unavailable' AND configured_estimate_reason_code = 'attempt_outcome_unknown')
        OR (terminal_state <> 'outcome_unknown' AND configured_estimate_reason_code <> 'attempt_outcome_unknown')
    ),
    CHECK (terminal_state <> 'outcome_unknown' OR observation_state <> 'complete')
"""


def apply_sqlite_finance_attempt_migration(connection) -> None:
    """Apply the real migration inside the caller's schema transaction."""

    for statement in (
        "ALTER TABLE gateway_request_attempts ADD COLUMN configured_rate_card_state TEXT NOT NULL DEFAULT 'legacy_unavailable' CHECK (configured_rate_card_state IN ('configured','legacy_unavailable'))",
        "ALTER TABLE gateway_request_attempts ADD COLUMN configured_rate_card_id TEXT",
        "ALTER TABLE gateway_request_attempts ADD COLUMN configured_rate_card_version INTEGER",
        "ALTER TABLE gateway_request_attempts ADD COLUMN configured_rate_card_digest TEXT",
        "ALTER TABLE gateway_request_attempts ADD COLUMN configured_rate_card_currency TEXT",
    ):
        connection.execute(statement)

    # SQLite cannot relax the historical entry_schema_version=1 CHECK in
    # place. Rebuild the table transactionally and copy every existing byte.
    connection.execute("DROP TRIGGER gateway_audit_chain_entries_no_update")
    connection.execute("DROP TRIGGER gateway_audit_chain_entries_no_delete")
    connection.execute("DROP INDEX idx_gateway_audit_chain_entries_event")
    connection.execute(
        """
        CREATE TABLE gateway_audit_chain_entries_v2 (
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
                    )
                )
            )
        )
        """
    )
    connection.execute(
        """
        INSERT INTO gateway_audit_chain_entries_v2 (
            organization_id, chain_version, chain_epoch, sequence, entry_schema_id,
            entry_schema_version, event_id, previous_digest, event_digest, event_json,
            appended_at, source_schema_id, source_schema_version, source_event_id
        )
        SELECT organization_id, chain_version, chain_epoch, sequence, entry_schema_id,
               entry_schema_version, event_id, previous_digest, event_digest, event_json,
               appended_at, NULL, NULL, NULL
        FROM gateway_audit_chain_entries
        """
    )
    connection.execute("DROP TABLE gateway_audit_chain_entries")
    connection.execute("ALTER TABLE gateway_audit_chain_entries_v2 RENAME TO gateway_audit_chain_entries")

    for statement in sqlite_finance_attempt_statements():
        connection.execute(statement)


def sqlite_finance_attempt_statements() -> tuple[str, ...]:
    return (
        SQLITE_FINANCE_ATTEMPT_INDEXES["idx_gateway_usage_organization_id"],
        SQLITE_FINANCE_ATTEMPT_INDEXES["idx_gateway_attempt_organization_id"],
        SQLITE_FINANCE_ATTEMPT_INDEXES["idx_gateway_attempt_event_organization_id"],
        "CREATE INDEX idx_gateway_audit_chain_entries_event ON gateway_audit_chain_entries (organization_id, event_id)",
        "CREATE UNIQUE INDEX idx_gateway_audit_chain_entries_source_identity ON gateway_audit_chain_entries (organization_id, source_schema_id, source_schema_version, source_event_id) WHERE entry_schema_version = 2",
        "CREATE TRIGGER gateway_audit_chain_entries_no_update BEFORE UPDATE ON gateway_audit_chain_entries BEGIN SELECT RAISE(ABORT, 'audit_chain_entry_immutable'); END",
        "CREATE TRIGGER gateway_audit_chain_entries_no_delete BEFORE DELETE ON gateway_audit_chain_entries BEGIN SELECT RAISE(ABORT, 'audit_chain_entry_immutable'); END",
        SQLITE_FINANCE_ATTEMPT_TRIGGERS["gateway_request_attempt_finance_binding_required"],
        SQLITE_FINANCE_ATTEMPT_TRIGGERS["gateway_request_attempt_finance_binding_immutable"],
        SQLITE_FINANCE_TABLE,
        SQLITE_FINANCE_ATTEMPT_INDEXES["gateway_finance_attempt_time"],
        SQLITE_FINANCE_ATTEMPT_INDEXES["gateway_finance_attempt_rate_card"],
        SQLITE_FINANCE_ATTEMPT_INDEXES["gateway_finance_attempt_provider"],
        SQLITE_FINANCE_ATTEMPT_TRIGGERS["gateway_finance_attempt_evidence_consistency"],
        SQLITE_FINANCE_ATTEMPT_TRIGGERS["gateway_finance_attempt_evidence_no_update"],
        SQLITE_FINANCE_ATTEMPT_TRIGGERS["gateway_finance_attempt_evidence_no_delete"],
        SQLITE_FINANCE_ATTEMPT_TRIGGERS["gateway_finance_attempt_evidence_no_replace"],
        SQLITE_FINANCE_ATTEMPT_TRIGGERS["gateway_finance_attempt_audit_source_required"],
    )


def verify_sqlite_finance_attempt(connection, error_factory) -> None:
    for table, required in (
        ("gateway_request_attempts", ATTEMPT_BINDING_COLUMNS),
        ("gateway_audit_chain_entries", {"source_schema_id", "source_schema_version", "source_event_id"}),
        (FINANCE_ATTEMPT_TABLE, FINANCE_ATTEMPT_COLUMNS),
    ):
        observed = {
            str(row["name"])
            for row in connection.execute(f"PRAGMA table_info({table})").fetchall()
        }
        if not required.issubset(observed):
            raise error_factory("storage_schema_partial_upgrade")
    observed_triggers = {
        str(row["name"]): " ".join(str(row["sql"]).split())
        for row in connection.execute(
            "SELECT name, sql FROM sqlite_master WHERE type='trigger' AND sql IS NOT NULL"
        ).fetchall()
    }
    if any(
        observed_triggers.get(name) != " ".join(statement.split())
        for name, statement in SQLITE_FINANCE_ATTEMPT_TRIGGERS.items()
    ):
        raise error_factory("storage_schema_partial_upgrade")
    observed_indexes = {
        str(row["name"]): " ".join(str(row["sql"]).split())
        for row in connection.execute(
            "SELECT name, sql FROM sqlite_master WHERE type='index' AND sql IS NOT NULL"
        ).fetchall()
    }
    if any(
        observed_indexes.get(name) != " ".join(statement.split())
        for name, statement in SQLITE_FINANCE_ATTEMPT_INDEXES.items()
    ):
        raise error_factory("storage_schema_partial_upgrade")
    finance_table_row = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
        (FINANCE_ATTEMPT_TABLE,),
    ).fetchone()
    finance_table_sql = (
        "" if finance_table_row is None
        else " ".join(str(finance_table_row["sql"]).split())
    )
    if finance_table_sql != " ".join(SQLITE_FINANCE_TABLE.split()):
        raise error_factory("storage_schema_partial_upgrade")
    row = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='gateway_audit_chain_entries'"
    ).fetchone()
    normalized = "" if row is None else " ".join(str(row["sql"]).split())
    if "hormuz.finance-attempt-evidence" not in normalized or "entry_schema_version IN (1,2)" not in normalized:
        raise error_factory("storage_schema_partial_upgrade")


def verify_postgres_finance_attempt(cursor, schema: str, error_factory) -> None:
    from ._portfolio_schema import verify_postgres_owned_tables

    ddl = POSTGRES_FINANCE_TABLE_DDL.format(prefix=schema + ".")
    constraints = {
        kind: ddl.count(marker)
        for kind, marker in (
            ("p", "PRIMARY KEY"), ("u", "UNIQUE ("), ("f", "FOREIGN KEY"), ("c", "CHECK ("),
        )
        if marker in ddl
    }
    verify_postgres_owned_tables(
        cursor,
        schema,
        error_factory,
        {FINANCE_ATTEMPT_TABLE: ddl},
        {},
        {FINANCE_ATTEMPT_TABLE: constraints},
        trigger_type=58,
    )
    for table, required in (
        ("gateway_request_attempts", ATTEMPT_BINDING_COLUMNS),
        (FINANCE_ATTEMPT_TABLE, FINANCE_ATTEMPT_COLUMNS),
    ):
        cursor.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_schema=%s AND table_name=%s",
            (schema, table),
        )
        observed = {
            str(row["column_name"] if isinstance(row, Mapping) else row[0])
            for row in cursor.fetchall()
        }
        if not required.issubset(observed):
            raise error_factory("storage_schema_partial_upgrade")
    cursor.execute(
        "SELECT pg_get_constraintdef(con.oid) AS definition FROM pg_constraint con "
        "JOIN pg_class rel ON rel.oid=con.conrelid JOIN pg_namespace n ON n.oid=rel.relnamespace "
        "WHERE n.nspname=%s AND rel.relname='gateway_audit_chain_entries' "
        "AND con.conname='gateway_audit_chain_entries_source_identity_check'",
        (schema,),
    )
    row = cursor.fetchone()
    definition = None if row is None else (
        row["definition"] if isinstance(row, Mapping) else row[0]
    )
    if not isinstance(definition, str) or "hormuz.finance-attempt-evidence" not in definition:
        raise error_factory("storage_schema_partial_upgrade")
    cursor.execute(
        "SELECT t.tgname, t.tgtype, t.tgenabled, p.proname, pn.nspname AS function_schema, "
        "pg_get_triggerdef(t.oid, true) AS definition FROM pg_trigger t "
        "JOIN pg_class c ON c.oid=t.tgrelid "
        "JOIN pg_namespace n ON n.oid=c.relnamespace "
        "JOIN pg_proc p ON p.oid=t.tgfoid "
        "JOIN pg_namespace pn ON pn.oid=p.pronamespace WHERE n.nspname=%s "
        "AND c.relname IN ('gateway_request_attempts','gateway_finance_attempt_evidence') "
        "AND NOT t.tgisinternal",
        (schema,),
    )
    observed_triggers = {
        str(row["tgname"] if isinstance(row, Mapping) else row[0]): (
            int(row["tgtype"] if isinstance(row, Mapping) else row[1]),
            str(row["tgenabled"] if isinstance(row, Mapping) else row[2]),
            str(row["proname"] if isinstance(row, Mapping) else row[3]),
            str(row["function_schema"] if isinstance(row, Mapping) else row[4]),
            str(row["definition"] if isinstance(row, Mapping) else row[5]),
        )
        for row in cursor.fetchall()
    }
    expected_triggers = {
        "gateway_request_attempt_finance_binding_required": (
            7,
            "require_request_attempt_finance_binding",
        ),
        "gateway_request_attempt_finance_binding_immutable": (
            18,
            "portfolio_reject_mutation",
        ),
        "gateway_finance_attempt_evidence_consistency": (
            7,
            "enforce_finance_attempt_evidence_consistency",
        ),
        "gateway_finance_attempt_evidence_immutable": (
            58,
            "portfolio_reject_mutation",
        ),
    }
    for name, (trigger_type, function_name) in expected_triggers.items():
        observed = observed_triggers.get(name)
        if (
            observed is None
            or observed[:4] != (trigger_type, "O", function_name, schema)
        ):
            raise error_factory("storage_schema_partial_upgrade")
    binding_immutable_definition = observed_triggers[
        "gateway_request_attempt_finance_binding_immutable"
    ][4]
    if any(
        column not in binding_immutable_definition
        for column in ATTEMPT_BINDING_COLUMNS
    ):
        raise error_factory("storage_schema_partial_upgrade")
    cursor.execute(
        "SELECT p.prosrc, p.prosecdef, p.proconfig FROM pg_proc p "
        "JOIN pg_namespace n ON n.oid=p.pronamespace "
        "WHERE n.nspname=%s AND p.proname=%s",
        (schema, "enforce_finance_attempt_evidence_consistency"),
    )
    consistency_row = cursor.fetchone()
    quoted_schema = '"' + schema.replace('"', '""') + '"'
    try:
        template = (
            resources.files("hormuz.migrations.postgresql")
            .joinpath("0015_finance_attempt_evidence.sql")
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
            "enforce_finance_attempt_evidence_consistency()"
        )
        function_sql = rendered.split(marker, 1)[1]
        expected_consistency_body = " ".join(
            function_sql.split("AS $$", 1)[1].split("$$;", 1)[0].split()
        )
    except (FileNotFoundError, IndexError, KeyError, ModuleNotFoundError, ValueError):
        raise error_factory("storage_schema_partial_upgrade") from None
    if consistency_row is None:
        raise error_factory("storage_schema_partial_upgrade")
    consistency_values = (
        tuple(consistency_row.values())
        if isinstance(consistency_row, Mapping)
        else tuple(consistency_row)
    )
    if (
        " ".join(str(consistency_values[0]).split()) != expected_consistency_body
        or consistency_values[1] is not False
        or consistency_values[2] != ["search_path=pg_catalog"]
    ):
        raise error_factory("storage_schema_partial_upgrade")
    cursor.execute(
        "SELECT p.prosrc, p.prosecdef, p.proconfig FROM pg_proc p "
        "JOIN pg_namespace n ON n.oid=p.pronamespace "
        "WHERE n.nspname=%s AND p.proname=%s",
        (schema, "enforce_custody_audit_chain_entry_insert"),
    )
    audit_guard_row = cursor.fetchone()
    if audit_guard_row is None:
        raise error_factory("storage_schema_partial_upgrade")
    audit_guard_values = (
        tuple(audit_guard_row.values())
        if isinstance(audit_guard_row, Mapping)
        else tuple(audit_guard_row)
    )
    audit_guard_body = " ".join(str(audit_guard_values[0]).split())
    if (
        audit_guard_values[1] is not True
        or audit_guard_values[2] != ["search_path=pg_catalog"]
        or audit_guard_body.count("hormuz.finance-attempt-evidence") != 2
        or f"FROM {quoted_schema}.gateway_finance_attempt_evidence" not in audit_guard_body
    ):
        raise error_factory("storage_schema_partial_upgrade")
    cursor.execute(
        "SELECT pg_get_constraintdef(con.oid) AS definition FROM pg_constraint con "
        "JOIN pg_class rel ON rel.oid=con.conrelid "
        "JOIN pg_namespace n ON n.oid=rel.relnamespace "
        "WHERE n.nspname=%s AND rel.relname=%s AND con.contype='c'",
        (schema, FINANCE_ATTEMPT_TABLE),
    )
    constraint_definitions = [
        str(row["definition"] if isinstance(row, Mapping) else row[0])
        for row in cursor.fetchall()
    ]
    if not any(
        "terminal_state" in definition
        and "configured_estimate_availability" in definition
        and "attempt_outcome_unknown" in definition
        for definition in constraint_definitions
    ):
        raise error_factory("storage_schema_partial_upgrade")
    cursor.execute(
        "SELECT index_relation.relname AS index_name, index_data.indisunique, "
        "array_agg(column_data.attname ORDER BY key_data.ordinality) AS columns "
        "FROM pg_index index_data "
        "JOIN pg_class table_relation ON table_relation.oid=index_data.indrelid "
        "JOIN pg_namespace namespace_data ON namespace_data.oid=table_relation.relnamespace "
        "JOIN pg_class index_relation ON index_relation.oid=index_data.indexrelid "
        "JOIN LATERAL unnest(index_data.indkey) WITH ORDINALITY "
        "AS key_data(attnum, ordinality) ON true "
        "JOIN pg_attribute column_data ON column_data.attrelid=table_relation.oid "
        "AND column_data.attnum=key_data.attnum "
        "WHERE namespace_data.nspname=%s AND index_relation.relname=ANY(%s) "
        "GROUP BY index_relation.relname, index_data.indisunique",
        (schema, list(POSTGRES_FINANCE_ATTEMPT_INDEXES)),
    )
    observed_indexes = {
        str(row["index_name"] if isinstance(row, Mapping) else row[0]): (
            bool(row["indisunique"] if isinstance(row, Mapping) else row[1]),
            tuple(row["columns"] if isinstance(row, Mapping) else row[2]),
        )
        for row in cursor.fetchall()
    }
    if observed_indexes != POSTGRES_FINANCE_ATTEMPT_INDEXES:
        raise error_factory("storage_schema_partial_upgrade")
