CREATE TABLE {schema}.portfolio_attribution_audit_events (
        organization_id TEXT NOT NULL,
        event_id TEXT NOT NULL,
        sequence BIGINT NOT NULL CHECK (sequence >= 1),
        actor_id TEXT,
        operation TEXT NOT NULL CHECK (operation IN ('admit','reject_admission','correct','list_attributions','read_facts')),
        entity_id TEXT,
        reason_code TEXT NOT NULL CHECK (reason_code IN ('bound','corrected','voided','observed','missing_evidence','ambiguous','invalid_reference','stale_version','unsupported','unauthorized_scope','dependency_unavailable')),
        occurred_at TEXT NOT NULL,
        PRIMARY KEY (organization_id, event_id),
        UNIQUE (organization_id, sequence)
    );

CREATE TABLE {schema}.portfolio_attribution_events (
        organization_id TEXT NOT NULL,
        attribution_event_id TEXT NOT NULL,
        request_attempt_id TEXT NOT NULL,
        work_scope_id TEXT,
        work_scope_version INTEGER,
        confidence TEXT NOT NULL CHECK (confidence IN ('explicit_authorized','server_side_default','authorized_post_run','unattributed','ambiguous')),
        state TEXT NOT NULL CHECK (state IN ('active','voided')),
        supersedes_event_id TEXT,
        actor_id TEXT,
        reason_code TEXT NOT NULL CHECK (reason_code IN ('bound','corrected','voided','missing_evidence','ambiguous')),
        event_at TEXT NOT NULL,
        observed_at TEXT NOT NULL,
        ingested_at TEXT NOT NULL,
        sequence BIGINT NOT NULL,
        PRIMARY KEY (organization_id, attribution_event_id),
        UNIQUE (organization_id, sequence),
        UNIQUE (organization_id, request_attempt_id, attribution_event_id),
        UNIQUE (organization_id, request_attempt_id, supersedes_event_id),
        FOREIGN KEY (organization_id, request_attempt_id, supersedes_event_id)
            REFERENCES {schema}.portfolio_attribution_events (organization_id, request_attempt_id, attribution_event_id),
        FOREIGN KEY (organization_id, work_scope_id, work_scope_version)
            REFERENCES {schema}.portfolio_work_scope_versions (organization_id, work_scope_id, version),
        FOREIGN KEY (organization_id, sequence)
            REFERENCES {schema}.portfolio_attribution_audit_events (organization_id, sequence),
        CHECK ((work_scope_id IS NULL AND work_scope_version IS NULL) OR (work_scope_id IS NOT NULL AND work_scope_version IS NOT NULL)),
        CHECK ((confidence IN ('unattributed','ambiguous') AND work_scope_id IS NULL AND actor_id IS NULL AND supersedes_event_id IS NULL AND state='active') OR (confidence IN ('explicit_authorized','server_side_default') AND work_scope_id IS NOT NULL AND actor_id IS NULL AND supersedes_event_id IS NULL AND state='active') OR (confidence='authorized_post_run' AND actor_id IS NOT NULL)),
        CHECK ((state='voided' AND work_scope_id IS NULL AND confidence='authorized_post_run' AND reason_code='voided') OR (state='active' AND reason_code<>'voided'))
    );

CREATE TABLE {schema}.portfolio_attribution_rejections (
        organization_id TEXT NOT NULL,
        receipt_id TEXT NOT NULL,
        actor_id TEXT NOT NULL,
        client TEXT NOT NULL CHECK (client IN ('codex','claude-code')),
        protocol TEXT NOT NULL CHECK (protocol IN ('openai','anthropic')),
        result_status TEXT NOT NULL CHECK (result_status IN ('rejected','unavailable')),
        reason_code TEXT NOT NULL CHECK (reason_code IN ('missing_evidence','ambiguous','invalid_reference','stale_version','unsupported','unauthorized_scope','dependency_unavailable')),
        occurred_at TEXT NOT NULL,
        sequence BIGINT NOT NULL,
        PRIMARY KEY (organization_id, receipt_id),
        UNIQUE (organization_id, sequence),
        FOREIGN KEY (organization_id, sequence)
            REFERENCES {schema}.portfolio_attribution_audit_events (organization_id, sequence)
    );

CREATE TABLE {schema}.portfolio_attribution_idempotency (
        organization_id TEXT NOT NULL,
        actor_id TEXT NOT NULL,
        idempotency_key TEXT NOT NULL CHECK (length(idempotency_key) BETWEEN 1 AND 128),
        request_mac TEXT NOT NULL,
        attribution_event_id TEXT NOT NULL,
        PRIMARY KEY (organization_id, actor_id, idempotency_key),
        FOREIGN KEY (organization_id, attribution_event_id)
            REFERENCES {schema}.portfolio_attribution_events (organization_id, attribution_event_id)
    );

CREATE TABLE {schema}.portfolio_attribution_cursors (
        organization_id TEXT NOT NULL,
        cursor_id TEXT NOT NULL,
        actor_id TEXT NOT NULL,
        authority_json TEXT NOT NULL,
        as_of TEXT NOT NULL,
        snapshot_sequence BIGINT NOT NULL CHECK (snapshot_sequence >= 0),
        after_at TEXT NOT NULL,
        after_id TEXT NOT NULL,
        filters_json TEXT NOT NULL,
        PRIMARY KEY (organization_id, cursor_id)
    );

CREATE UNIQUE INDEX portfolio_attribution_root ON {schema}.portfolio_attribution_events (organization_id, request_attempt_id) WHERE supersedes_event_id IS NULL;

CREATE INDEX portfolio_attribution_attempt ON {schema}.portfolio_attribution_events (organization_id, request_attempt_id, sequence);

CREATE INDEX portfolio_attribution_window ON {schema}.portfolio_attribution_events (organization_id, event_at, attribution_event_id, sequence);

CREATE INDEX portfolio_attribution_scope ON {schema}.portfolio_attribution_events (organization_id, work_scope_id, sequence);

CREATE INDEX portfolio_attribution_rejection_window ON {schema}.portfolio_attribution_rejections (organization_id, occurred_at, sequence);

ALTER TABLE {schema}.portfolio_attribution_events ADD CONSTRAINT portfolio_attribution_attempt_fk FOREIGN KEY (organization_id, request_attempt_id) REFERENCES {schema}.gateway_request_attempts (organization_id, attempt_id);

ALTER TABLE {schema}.portfolio_attribution_audit_events ENABLE ROW LEVEL SECURITY;

ALTER TABLE {schema}.portfolio_attribution_audit_events FORCE ROW LEVEL SECURITY;

CREATE POLICY portfolio_attribution_audit_events_tenant ON {schema}.portfolio_attribution_audit_events USING (organization_id = current_setting('hormuz.organization_id', true)) WITH CHECK (organization_id = current_setting('hormuz.organization_id', true));

CREATE TRIGGER portfolio_attribution_audit_events_immutable BEFORE UPDATE OR DELETE OR TRUNCATE ON {schema}.portfolio_attribution_audit_events FOR EACH STATEMENT EXECUTE FUNCTION {schema}.portfolio_reject_mutation();

REVOKE ALL ON {schema}.portfolio_attribution_audit_events FROM PUBLIC;

GRANT SELECT, INSERT ON {schema}.portfolio_attribution_audit_events TO {runtime_role};

ALTER TABLE {schema}.portfolio_attribution_events ENABLE ROW LEVEL SECURITY;

ALTER TABLE {schema}.portfolio_attribution_events FORCE ROW LEVEL SECURITY;

CREATE POLICY portfolio_attribution_events_tenant ON {schema}.portfolio_attribution_events USING (organization_id = current_setting('hormuz.organization_id', true)) WITH CHECK (organization_id = current_setting('hormuz.organization_id', true));

CREATE TRIGGER portfolio_attribution_events_immutable BEFORE UPDATE OR DELETE OR TRUNCATE ON {schema}.portfolio_attribution_events FOR EACH STATEMENT EXECUTE FUNCTION {schema}.portfolio_reject_mutation();

REVOKE ALL ON {schema}.portfolio_attribution_events FROM PUBLIC;

GRANT SELECT, INSERT ON {schema}.portfolio_attribution_events TO {runtime_role};

ALTER TABLE {schema}.portfolio_attribution_rejections ENABLE ROW LEVEL SECURITY;

ALTER TABLE {schema}.portfolio_attribution_rejections FORCE ROW LEVEL SECURITY;

CREATE POLICY portfolio_attribution_rejections_tenant ON {schema}.portfolio_attribution_rejections USING (organization_id = current_setting('hormuz.organization_id', true)) WITH CHECK (organization_id = current_setting('hormuz.organization_id', true));

CREATE TRIGGER portfolio_attribution_rejections_immutable BEFORE UPDATE OR DELETE OR TRUNCATE ON {schema}.portfolio_attribution_rejections FOR EACH STATEMENT EXECUTE FUNCTION {schema}.portfolio_reject_mutation();

REVOKE ALL ON {schema}.portfolio_attribution_rejections FROM PUBLIC;

GRANT SELECT, INSERT ON {schema}.portfolio_attribution_rejections TO {runtime_role};

ALTER TABLE {schema}.portfolio_attribution_idempotency ENABLE ROW LEVEL SECURITY;

ALTER TABLE {schema}.portfolio_attribution_idempotency FORCE ROW LEVEL SECURITY;

CREATE POLICY portfolio_attribution_idempotency_tenant ON {schema}.portfolio_attribution_idempotency USING (organization_id = current_setting('hormuz.organization_id', true)) WITH CHECK (organization_id = current_setting('hormuz.organization_id', true));

CREATE TRIGGER portfolio_attribution_idempotency_immutable BEFORE UPDATE OR DELETE OR TRUNCATE ON {schema}.portfolio_attribution_idempotency FOR EACH STATEMENT EXECUTE FUNCTION {schema}.portfolio_reject_mutation();

REVOKE ALL ON {schema}.portfolio_attribution_idempotency FROM PUBLIC;

GRANT SELECT, INSERT ON {schema}.portfolio_attribution_idempotency TO {runtime_role};

ALTER TABLE {schema}.portfolio_attribution_cursors ENABLE ROW LEVEL SECURITY;

ALTER TABLE {schema}.portfolio_attribution_cursors FORCE ROW LEVEL SECURITY;

CREATE POLICY portfolio_attribution_cursors_tenant ON {schema}.portfolio_attribution_cursors USING (organization_id = current_setting('hormuz.organization_id', true)) WITH CHECK (organization_id = current_setting('hormuz.organization_id', true));

CREATE TRIGGER portfolio_attribution_cursors_immutable BEFORE UPDATE OR DELETE OR TRUNCATE ON {schema}.portfolio_attribution_cursors FOR EACH STATEMENT EXECUTE FUNCTION {schema}.portfolio_reject_mutation();

REVOKE ALL ON {schema}.portfolio_attribution_cursors FROM PUBLIC;

GRANT SELECT, INSERT ON {schema}.portfolio_attribution_cursors TO {runtime_role};
