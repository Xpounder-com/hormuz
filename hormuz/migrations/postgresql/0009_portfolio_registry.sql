-- #215 additive registry; all references remain tenant-qualified.
-- No v1 rows, triggers, grants, or contracts are rewritten.

CREATE TABLE {schema}.portfolio_audit_events (
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
    );

CREATE TABLE {schema}.portfolio_work_scope_versions (
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
            REFERENCES {schema}.portfolio_work_scope_versions (organization_id, work_scope_id, version),
        FOREIGN KEY (organization_id, work_scope_id, supersedes_version)
            REFERENCES {schema}.portfolio_work_scope_versions (organization_id, work_scope_id, version),
        FOREIGN KEY (organization_id, sequence)
            REFERENCES {schema}.portfolio_audit_events (organization_id, sequence),
        CHECK ((parent_work_scope_id IS NULL AND parent_version IS NULL) OR (parent_work_scope_id IS NOT NULL AND parent_version IS NOT NULL)),
        CHECK ((version = 1 AND supersedes_version IS NULL) OR (version > 1 AND supersedes_version = version - 1)),
        CHECK ((state = 'tombstoned' AND display_name IS NULL) OR (state <> 'tombstoned' AND display_name IS NOT NULL))
    );

CREATE TABLE {schema}.portfolio_binding_events (
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
            REFERENCES {schema}.portfolio_binding_events (organization_id, connector_id, external_object_id, binding_event_id),
        FOREIGN KEY (organization_id, work_scope_id, work_scope_version)
            REFERENCES {schema}.portfolio_work_scope_versions (organization_id, work_scope_id, version),
        FOREIGN KEY (organization_id, sequence)
            REFERENCES {schema}.portfolio_audit_events (organization_id, sequence)
    );

CREATE TABLE {schema}.portfolio_idempotency (
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
            REFERENCES {schema}.portfolio_work_scope_versions (organization_id, work_scope_id, version),
        FOREIGN KEY (organization_id, binding_event_id)
            REFERENCES {schema}.portfolio_binding_events (organization_id, binding_event_id),
        CHECK ((work_scope_id IS NOT NULL AND work_scope_version IS NOT NULL AND binding_event_id IS NULL)
            OR (work_scope_id IS NULL AND work_scope_version IS NULL AND binding_event_id IS NOT NULL)),
        FOREIGN KEY (organization_id, sequence)
            REFERENCES {schema}.portfolio_audit_events (organization_id, sequence)
    );

CREATE TABLE {schema}.portfolio_cursors (
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
    );

CREATE INDEX portfolio_scope_window ON {schema}.portfolio_work_scope_versions (organization_id, event_at, work_scope_id, sequence);
CREATE INDEX portfolio_binding_current ON {schema}.portfolio_binding_events (organization_id, connector_id, external_object_id, sequence);
CREATE INDEX portfolio_binding_window ON {schema}.portfolio_binding_events (organization_id, event_at, binding_event_id, sequence);
CREATE INDEX portfolio_binding_scope ON {schema}.portfolio_binding_events (organization_id, work_scope_id, sequence);

CREATE FUNCTION {schema}.portfolio_reject_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'portfolio_append_only' USING ERRCODE = '23514';
END;
$$;
REVOKE ALL ON FUNCTION {schema}.portfolio_reject_mutation() FROM PUBLIC;

ALTER TABLE {schema}.portfolio_audit_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE {schema}.portfolio_audit_events FORCE ROW LEVEL SECURITY;
CREATE POLICY portfolio_audit_events_organization_isolation ON {schema}.portfolio_audit_events
    USING (organization_id = current_setting('hormuz.organization_id', true))
    WITH CHECK (organization_id = current_setting('hormuz.organization_id', true));
CREATE TRIGGER portfolio_audit_events_immutable BEFORE UPDATE OR DELETE ON {schema}.portfolio_audit_events
    FOR EACH ROW EXECUTE FUNCTION {schema}.portfolio_reject_mutation();
REVOKE ALL ON {schema}.portfolio_audit_events FROM PUBLIC;
GRANT SELECT, INSERT ON {schema}.portfolio_audit_events TO {runtime_role};

ALTER TABLE {schema}.portfolio_work_scope_versions ENABLE ROW LEVEL SECURITY;
ALTER TABLE {schema}.portfolio_work_scope_versions FORCE ROW LEVEL SECURITY;
CREATE POLICY portfolio_work_scope_versions_organization_isolation ON {schema}.portfolio_work_scope_versions
    USING (organization_id = current_setting('hormuz.organization_id', true))
    WITH CHECK (organization_id = current_setting('hormuz.organization_id', true));
CREATE TRIGGER portfolio_work_scope_versions_immutable BEFORE UPDATE OR DELETE ON {schema}.portfolio_work_scope_versions
    FOR EACH ROW EXECUTE FUNCTION {schema}.portfolio_reject_mutation();
REVOKE ALL ON {schema}.portfolio_work_scope_versions FROM PUBLIC;
GRANT SELECT, INSERT ON {schema}.portfolio_work_scope_versions TO {runtime_role};

ALTER TABLE {schema}.portfolio_binding_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE {schema}.portfolio_binding_events FORCE ROW LEVEL SECURITY;
CREATE POLICY portfolio_binding_events_organization_isolation ON {schema}.portfolio_binding_events
    USING (organization_id = current_setting('hormuz.organization_id', true))
    WITH CHECK (organization_id = current_setting('hormuz.organization_id', true));
CREATE TRIGGER portfolio_binding_events_immutable BEFORE UPDATE OR DELETE ON {schema}.portfolio_binding_events
    FOR EACH ROW EXECUTE FUNCTION {schema}.portfolio_reject_mutation();
REVOKE ALL ON {schema}.portfolio_binding_events FROM PUBLIC;
GRANT SELECT, INSERT ON {schema}.portfolio_binding_events TO {runtime_role};

ALTER TABLE {schema}.portfolio_idempotency ENABLE ROW LEVEL SECURITY;
ALTER TABLE {schema}.portfolio_idempotency FORCE ROW LEVEL SECURITY;
CREATE POLICY portfolio_idempotency_organization_isolation ON {schema}.portfolio_idempotency
    USING (organization_id = current_setting('hormuz.organization_id', true))
    WITH CHECK (organization_id = current_setting('hormuz.organization_id', true));
CREATE TRIGGER portfolio_idempotency_immutable BEFORE UPDATE OR DELETE ON {schema}.portfolio_idempotency
    FOR EACH ROW EXECUTE FUNCTION {schema}.portfolio_reject_mutation();
REVOKE ALL ON {schema}.portfolio_idempotency FROM PUBLIC;
GRANT SELECT, INSERT ON {schema}.portfolio_idempotency TO {runtime_role};

ALTER TABLE {schema}.portfolio_cursors ENABLE ROW LEVEL SECURITY;
ALTER TABLE {schema}.portfolio_cursors FORCE ROW LEVEL SECURITY;
CREATE POLICY portfolio_cursors_organization_isolation ON {schema}.portfolio_cursors
    USING (organization_id = current_setting('hormuz.organization_id', true))
    WITH CHECK (organization_id = current_setting('hormuz.organization_id', true));
CREATE TRIGGER portfolio_cursors_immutable BEFORE UPDATE OR DELETE ON {schema}.portfolio_cursors
    FOR EACH ROW EXECUTE FUNCTION {schema}.portfolio_reject_mutation();
REVOKE ALL ON {schema}.portfolio_cursors FROM PUBLIC;
GRANT SELECT, INSERT ON {schema}.portfolio_cursors TO {runtime_role};
