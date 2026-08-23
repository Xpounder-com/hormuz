-- Commit-time, per-organization audit-chain evidence. Runtime code may append
-- entries and advance a tenant head, but it has no direct UPDATE or DELETE
-- authority over historical entries or checkpoints.

CREATE TABLE IF NOT EXISTS {schema}.gateway_audit_chain_epochs (
    organization_id TEXT NOT NULL,
    chain_version INTEGER NOT NULL CHECK (chain_version = 1),
    chain_epoch INTEGER NOT NULL CHECK (chain_epoch >= 1),
    created_at TIMESTAMPTZ NOT NULL,
    reason_code TEXT NOT NULL CHECK (reason_code IN ('initial_adoption', 'restore', 'migration')),
    predecessor_chain_epoch INTEGER,
    predecessor_sequence INTEGER,
    predecessor_head_digest TEXT,
    PRIMARY KEY (organization_id, chain_epoch),
    CHECK (
        (predecessor_chain_epoch IS NULL AND predecessor_sequence IS NULL AND predecessor_head_digest IS NULL)
        OR (
            predecessor_chain_epoch >= 1
            AND predecessor_sequence >= 1
            AND predecessor_head_digest IS NOT NULL
        )
    )
);

CREATE TABLE IF NOT EXISTS {schema}.gateway_audit_chain_heads (
    organization_id TEXT PRIMARY KEY,
    chain_version INTEGER NOT NULL CHECK (chain_version = 1),
    chain_epoch INTEGER NOT NULL CHECK (chain_epoch >= 1),
    sequence BIGINT NOT NULL CHECK (sequence >= 0),
    head_digest TEXT,
    FOREIGN KEY (organization_id, chain_epoch)
        REFERENCES {schema}.gateway_audit_chain_epochs (organization_id, chain_epoch),
    CHECK (sequence = 0 OR head_digest IS NOT NULL)
);

CREATE TABLE IF NOT EXISTS {schema}.gateway_audit_chain_entries (
    organization_id TEXT NOT NULL,
    chain_version INTEGER NOT NULL CHECK (chain_version = 1),
    chain_epoch INTEGER NOT NULL CHECK (chain_epoch >= 1),
    sequence BIGINT NOT NULL CHECK (sequence >= 1),
    entry_schema_id TEXT NOT NULL CHECK (entry_schema_id = 'hormuz.commit-audit-chain-entry'),
    entry_schema_version INTEGER NOT NULL CHECK (entry_schema_version = 1),
    event_id TEXT NOT NULL,
    previous_digest TEXT,
    event_digest TEXT NOT NULL,
    event_json TEXT NOT NULL,
    appended_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (organization_id, chain_epoch, sequence),
    UNIQUE (organization_id, event_id),
    FOREIGN KEY (organization_id, chain_epoch)
        REFERENCES {schema}.gateway_audit_chain_epochs (organization_id, chain_epoch)
);

CREATE INDEX IF NOT EXISTS idx_gateway_audit_chain_entries_event
    ON {schema}.gateway_audit_chain_entries (organization_id, event_id);

CREATE TABLE IF NOT EXISTS {schema}.gateway_audit_chain_checkpoints (
    checkpoint_id TEXT PRIMARY KEY,
    organization_id TEXT NOT NULL,
    chain_version INTEGER NOT NULL CHECK (chain_version = 1),
    chain_epoch INTEGER NOT NULL CHECK (chain_epoch >= 1),
    sequence BIGINT NOT NULL CHECK (sequence >= 1),
    head_digest TEXT NOT NULL,
    artifact_sha256 TEXT NOT NULL,
    anchor_backend TEXT NOT NULL,
    object_version TEXT,
    anchored_at TIMESTAMPTZ NOT NULL,
    FOREIGN KEY (organization_id, chain_epoch)
        REFERENCES {schema}.gateway_audit_chain_epochs (organization_id, chain_epoch)
);

CREATE INDEX IF NOT EXISTS idx_gateway_audit_chain_checkpoint_latest
    ON {schema}.gateway_audit_chain_checkpoints (organization_id, anchored_at DESC);

ALTER TABLE {schema}.gateway_audit_chain_epochs ENABLE ROW LEVEL SECURITY;
ALTER TABLE {schema}.gateway_audit_chain_epochs FORCE ROW LEVEL SECURITY;
ALTER TABLE {schema}.gateway_audit_chain_heads ENABLE ROW LEVEL SECURITY;
ALTER TABLE {schema}.gateway_audit_chain_heads FORCE ROW LEVEL SECURITY;
ALTER TABLE {schema}.gateway_audit_chain_entries ENABLE ROW LEVEL SECURITY;
ALTER TABLE {schema}.gateway_audit_chain_entries FORCE ROW LEVEL SECURITY;
ALTER TABLE {schema}.gateway_audit_chain_checkpoints ENABLE ROW LEVEL SECURITY;
ALTER TABLE {schema}.gateway_audit_chain_checkpoints FORCE ROW LEVEL SECURITY;

CREATE POLICY gateway_audit_chain_epochs_organization_isolation
    ON {schema}.gateway_audit_chain_epochs
    USING (organization_id = current_setting('hormuz.organization_id', true))
    WITH CHECK (organization_id = current_setting('hormuz.organization_id', true));
CREATE POLICY gateway_audit_chain_heads_organization_isolation
    ON {schema}.gateway_audit_chain_heads
    USING (organization_id = current_setting('hormuz.organization_id', true))
    WITH CHECK (organization_id = current_setting('hormuz.organization_id', true));
CREATE POLICY gateway_audit_chain_entries_organization_isolation
    ON {schema}.gateway_audit_chain_entries
    USING (organization_id = current_setting('hormuz.organization_id', true))
    WITH CHECK (organization_id = current_setting('hormuz.organization_id', true));
CREATE POLICY gateway_audit_chain_checkpoints_organization_isolation
    ON {schema}.gateway_audit_chain_checkpoints
    USING (organization_id = current_setting('hormuz.organization_id', true))
    WITH CHECK (organization_id = current_setting('hormuz.organization_id', true));

-- Clear any deployment-local broad grant before assigning the minimal runtime
-- capability. The head is deliberately mutable; epochs, entries, and
-- checkpoint receipts are append-only through this role.
REVOKE ALL PRIVILEGES ON {schema}.gateway_audit_chain_epochs FROM {runtime_role};
REVOKE ALL PRIVILEGES ON {schema}.gateway_audit_chain_heads FROM {runtime_role};
REVOKE ALL PRIVILEGES ON {schema}.gateway_audit_chain_entries FROM {runtime_role};
REVOKE ALL PRIVILEGES ON {schema}.gateway_audit_chain_checkpoints FROM {runtime_role};
GRANT SELECT, INSERT ON {schema}.gateway_audit_chain_epochs TO {runtime_role};
GRANT SELECT, INSERT, UPDATE ON {schema}.gateway_audit_chain_heads TO {runtime_role};
GRANT SELECT, INSERT ON {schema}.gateway_audit_chain_entries TO {runtime_role};
GRANT SELECT, INSERT ON {schema}.gateway_audit_chain_checkpoints TO {runtime_role};
