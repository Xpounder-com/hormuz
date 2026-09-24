-- SQLite 19 / PostgreSQL 24 immutable, reviewable policy recommendations.

CREATE TABLE {schema}.portfolio_policy_recommendation_snapshots (
        organization_id TEXT NOT NULL CHECK (length(organization_id) BETWEEN 1 AND 128),
        recommendation_id TEXT NOT NULL CHECK (length(recommendation_id) BETWEEN 1 AND 128),
        version INTEGER NOT NULL CHECK (version BETWEEN 1 AND 2147483647),
        work_scope_id TEXT NOT NULL CHECK (length(work_scope_id) BETWEEN 1 AND 128),
        work_scope_version INTEGER NOT NULL CHECK (work_scope_version BETWEEN 1 AND 2147483647),
        scorecard_id TEXT NOT NULL CHECK (length(scorecard_id) BETWEEN 1 AND 128),
        scorecard_version INTEGER NOT NULL CHECK (scorecard_version BETWEEN 1 AND 2147483647),
        scorecard_evaluation_digest TEXT NOT NULL CHECK (length(scorecard_evaluation_digest) = 64),
        policy_id TEXT NOT NULL CHECK (length(policy_id) BETWEEN 1 AND 128),
        policy_version INTEGER NOT NULL CHECK (policy_version BETWEEN 1 AND 2147483647),
        policy_digest TEXT NOT NULL CHECK (length(policy_digest) = 64),
        change_type TEXT NOT NULL CHECK (change_type IN ('budget_plan_change','model_allowlist_change','model_fallback_change','output_or_cost_cap_change','routing_policy_change')),
        candidate_policy_digest TEXT CHECK (candidate_policy_digest IS NULL OR length(candidate_policy_digest) = 64),
        candidate_budget_plan_id TEXT CHECK (candidate_budget_plan_id IS NULL OR length(candidate_budget_plan_id) BETWEEN 1 AND 128),
        candidate_budget_plan_version INTEGER CHECK (candidate_budget_plan_version IS NULL OR candidate_budget_plan_version BETWEEN 1 AND 2147483647),
        evidence_level TEXT NOT NULL CHECK (evidence_level IN ('descriptive','associated','controlled')),
        window_start_at TEXT NOT NULL,
        window_end_at TEXT NOT NULL,
        created_at TEXT NOT NULL,
        expires_at TEXT NOT NULL,
        supersedes_version INTEGER,
        generation_digest TEXT NOT NULL CHECK (length(generation_digest) = 64),
        recommendation_json TEXT NOT NULL CHECK (length(recommendation_json) BETWEEN 2 AND 1048576),
        evaluation_json TEXT NOT NULL CHECK (length(evaluation_json) BETWEEN 2 AND 1048576),
        expected_pre_apply_json TEXT NOT NULL CHECK (length(expected_pre_apply_json) BETWEEN 2 AND 8192),
        bindings_json TEXT NOT NULL CHECK (length(bindings_json) BETWEEN 2 AND 65536),
        sequence BIGINT NOT NULL CHECK (sequence BETWEEN 1 AND 9223372036854775807),
        PRIMARY KEY (organization_id, recommendation_id, version),
        UNIQUE (organization_id, sequence),
        UNIQUE (organization_id, recommendation_id, generation_digest),
        FOREIGN KEY (organization_id, work_scope_id, work_scope_version)
            REFERENCES {schema}.portfolio_work_scope_versions (organization_id, work_scope_id, version),
        FOREIGN KEY (organization_id, scorecard_id, scorecard_version)
            REFERENCES {schema}.portfolio_model_scorecard_snapshots (organization_id, scorecard_id, version),
        FOREIGN KEY (organization_id, recommendation_id, supersedes_version)
            REFERENCES {schema}.portfolio_policy_recommendation_snapshots (organization_id, recommendation_id, version),
        FOREIGN KEY (organization_id, candidate_budget_plan_id, candidate_budget_plan_version)
            REFERENCES {schema}.portfolio_work_budget_plan_versions (organization_id, budget_plan_id, version),
        CHECK (length(window_start_at) = 27 AND substr(window_start_at, 20, 1) = '.' AND substr(window_start_at, 27, 1) = 'Z'),
        CHECK (length(window_end_at) = 27 AND substr(window_end_at, 20, 1) = '.' AND substr(window_end_at, 27, 1) = 'Z'),
        CHECK (length(created_at) = 27 AND substr(created_at, 20, 1) = '.' AND substr(created_at, 27, 1) = 'Z'),
        CHECK (length(expires_at) = 27 AND substr(expires_at, 20, 1) = '.' AND substr(expires_at, 27, 1) = 'Z'),
        CHECK (window_start_at < window_end_at AND created_at < expires_at),
        CHECK ((version = 1 AND supersedes_version IS NULL) OR (version > 1 AND supersedes_version = version - 1)),
        CHECK ((change_type = 'budget_plan_change' AND candidate_policy_digest IS NULL AND candidate_budget_plan_id IS NOT NULL AND candidate_budget_plan_version IS NOT NULL)
            OR (change_type <> 'budget_plan_change' AND candidate_policy_digest IS NOT NULL AND candidate_budget_plan_id IS NULL AND candidate_budget_plan_version IS NULL))
    );

CREATE TABLE {schema}.portfolio_policy_recommendation_events (
        organization_id TEXT NOT NULL CHECK (length(organization_id) BETWEEN 1 AND 128),
        event_id TEXT NOT NULL CHECK (length(event_id) = 36),
        sequence BIGINT NOT NULL CHECK (sequence BETWEEN 1 AND 9223372036854775807),
        actor_id TEXT NOT NULL CHECK (length(actor_id) BETWEEN 1 AND 128),
        recommendation_id TEXT NOT NULL CHECK (length(recommendation_id) BETWEEN 1 AND 128),
        recommendation_version INTEGER NOT NULL CHECK (recommendation_version BETWEEN 1 AND 2147483647),
        event_type TEXT NOT NULL CHECK (event_type IN ('generated','accepted','rejected','expired','invalidated','superseded','applied')),
        public_state TEXT NOT NULL CHECK (public_state IN ('pending','accepted','rejected','expired','invalidated','superseded')),
        reason_code TEXT NOT NULL CHECK (reason_code IN ('eligible','accepted','rejected','expired','policy_drift','scorecard_drift','superseded','not_applicable')),
        occurred_at TEXT NOT NULL,
        idempotency_key TEXT CHECK (idempotency_key IS NULL OR length(idempotency_key) BETWEEN 1 AND 128),
        request_digest TEXT CHECK (request_digest IS NULL OR length(request_digest) = 64),
        evidence_json TEXT NOT NULL CHECK (length(evidence_json) BETWEEN 2 AND 8192),
        PRIMARY KEY (organization_id, event_id),
        UNIQUE (organization_id, sequence),
        UNIQUE (organization_id, actor_id, idempotency_key),
        FOREIGN KEY (organization_id, recommendation_id, recommendation_version)
            REFERENCES {schema}.portfolio_policy_recommendation_snapshots (organization_id, recommendation_id, version),
        CHECK (length(occurred_at) = 27 AND substr(occurred_at, 20, 1) = '.' AND substr(occurred_at, 27, 1) = 'Z'),
        CHECK ((idempotency_key IS NULL AND request_digest IS NULL) OR (idempotency_key IS NOT NULL AND request_digest IS NOT NULL)),
        CHECK ((event_type = 'generated' AND public_state = 'pending' AND reason_code = 'eligible')
            OR (event_type = 'accepted' AND public_state = 'accepted' AND reason_code = 'accepted')
            OR (event_type = 'rejected' AND public_state = 'rejected' AND reason_code = 'rejected')
            OR (event_type = 'expired' AND public_state = 'expired' AND reason_code = 'expired')
            OR (event_type = 'invalidated' AND public_state = 'invalidated' AND reason_code IN ('policy_drift','scorecard_drift'))
            OR (event_type = 'superseded' AND public_state = 'superseded' AND reason_code = 'superseded')
            OR (event_type = 'applied' AND public_state = 'accepted' AND reason_code = 'not_applicable'))
    );

CREATE TABLE {schema}.portfolio_policy_recommendation_read_audit (
        organization_id TEXT NOT NULL CHECK (length(organization_id) BETWEEN 1 AND 128),
        event_id TEXT NOT NULL CHECK (length(event_id) = 36),
        sequence BIGINT NOT NULL CHECK (sequence BETWEEN 1 AND 9223372036854775807),
        actor_id TEXT NOT NULL CHECK (length(actor_id) BETWEEN 1 AND 128),
        operation TEXT NOT NULL CHECK (operation IN ('list','show')),
        recommendation_id TEXT CHECK (recommendation_id IS NULL OR length(recommendation_id) BETWEEN 1 AND 128),
        recommendation_version INTEGER CHECK (recommendation_version IS NULL OR recommendation_version BETWEEN 1 AND 2147483647),
        filter_digest TEXT NOT NULL CHECK (length(filter_digest) = 64),
        snapshot_sequence BIGINT NOT NULL CHECK (snapshot_sequence >= 0),
        result_count INTEGER NOT NULL CHECK (result_count BETWEEN 0 AND 100),
        occurred_at TEXT NOT NULL,
        PRIMARY KEY (organization_id, event_id),
        UNIQUE (organization_id, sequence),
        CHECK (length(occurred_at) = 27 AND substr(occurred_at, 20, 1) = '.' AND substr(occurred_at, 27, 1) = 'Z'),
        CHECK ((operation = 'list' AND recommendation_id IS NULL AND recommendation_version IS NULL)
            OR (operation = 'show' AND recommendation_id IS NOT NULL AND recommendation_version IS NOT NULL))
    );

CREATE TABLE {schema}.portfolio_policy_recommendation_cursors (
        organization_id TEXT NOT NULL CHECK (length(organization_id) BETWEEN 1 AND 128),
        cursor_id TEXT NOT NULL CHECK (length(cursor_id) = 64),
        actor_id TEXT NOT NULL CHECK (length(actor_id) BETWEEN 1 AND 128),
        authority_digest TEXT NOT NULL CHECK (length(authority_digest) = 64),
        schema_id TEXT NOT NULL CHECK (schema_id = 'hormuz.policy-recommendation-page'),
        schema_version INTEGER NOT NULL CHECK (schema_version = 1),
        as_of TEXT NOT NULL,
        expires_at TEXT NOT NULL,
        snapshot_sequence BIGINT NOT NULL CHECK (snapshot_sequence >= 0),
        page_limit INTEGER NOT NULL CHECK (page_limit BETWEEN 1 AND 100),
        after_at TEXT NOT NULL,
        after_id TEXT NOT NULL CHECK (length(after_id) BETWEEN 1 AND 128),
        filters_json TEXT NOT NULL CHECK (length(filters_json) BETWEEN 2 AND 4096),
        PRIMARY KEY (organization_id, cursor_id),
        CHECK (length(as_of) = 27 AND substr(as_of, 20, 1) = '.' AND substr(as_of, 27, 1) = 'Z'),
        CHECK (length(expires_at) = 27 AND substr(expires_at, 20, 1) = '.' AND substr(expires_at, 27, 1) = 'Z'),
        CHECK (length(after_at) = 27 AND substr(after_at, 20, 1) = '.' AND substr(after_at, 27, 1) = 'Z'),
        CHECK (as_of < expires_at)
    );

CREATE INDEX portfolio_recommendation_scope_created ON {schema}.portfolio_policy_recommendation_snapshots (organization_id, work_scope_id, work_scope_version, created_at, recommendation_id, version);

CREATE INDEX portfolio_recommendation_latest ON {schema}.portfolio_policy_recommendation_snapshots (organization_id, recommendation_id, version DESC);

CREATE INDEX portfolio_recommendation_event_latest ON {schema}.portfolio_policy_recommendation_events (organization_id, recommendation_id, recommendation_version, sequence DESC);

CREATE INDEX portfolio_recommendation_audit_window ON {schema}.portfolio_policy_recommendation_read_audit (organization_id, occurred_at, event_id);

CREATE INDEX portfolio_recommendation_cursor_expiry ON {schema}.portfolio_policy_recommendation_cursors (organization_id, expires_at, cursor_id);

ALTER TABLE {schema}.portfolio_policy_recommendation_snapshots ENABLE ROW LEVEL SECURITY;
ALTER TABLE {schema}.portfolio_policy_recommendation_snapshots FORCE ROW LEVEL SECURITY;
CREATE POLICY portfolio_policy_recommendation_snapshots_tenant
    ON {schema}.portfolio_policy_recommendation_snapshots
    USING (organization_id=current_setting('hormuz.organization_id', true))
    WITH CHECK (organization_id=current_setting('hormuz.organization_id', true));
CREATE TRIGGER portfolio_policy_recommendation_snapshots_immutable
    BEFORE UPDATE OR DELETE OR TRUNCATE ON {schema}.portfolio_policy_recommendation_snapshots
    FOR EACH STATEMENT EXECUTE FUNCTION {schema}.portfolio_reject_mutation();
REVOKE ALL ON {schema}.portfolio_policy_recommendation_snapshots FROM PUBLIC;
GRANT SELECT, INSERT ON {schema}.portfolio_policy_recommendation_snapshots TO {runtime_role};

ALTER TABLE {schema}.portfolio_policy_recommendation_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE {schema}.portfolio_policy_recommendation_events FORCE ROW LEVEL SECURITY;
CREATE POLICY portfolio_policy_recommendation_events_tenant
    ON {schema}.portfolio_policy_recommendation_events
    USING (organization_id=current_setting('hormuz.organization_id', true))
    WITH CHECK (organization_id=current_setting('hormuz.organization_id', true));
CREATE TRIGGER portfolio_policy_recommendation_events_immutable
    BEFORE UPDATE OR DELETE OR TRUNCATE ON {schema}.portfolio_policy_recommendation_events
    FOR EACH STATEMENT EXECUTE FUNCTION {schema}.portfolio_reject_mutation();
REVOKE ALL ON {schema}.portfolio_policy_recommendation_events FROM PUBLIC;
GRANT SELECT, INSERT ON {schema}.portfolio_policy_recommendation_events TO {runtime_role};

ALTER TABLE {schema}.portfolio_policy_recommendation_read_audit ENABLE ROW LEVEL SECURITY;
ALTER TABLE {schema}.portfolio_policy_recommendation_read_audit FORCE ROW LEVEL SECURITY;
CREATE POLICY portfolio_policy_recommendation_read_audit_tenant
    ON {schema}.portfolio_policy_recommendation_read_audit
    USING (organization_id=current_setting('hormuz.organization_id', true))
    WITH CHECK (organization_id=current_setting('hormuz.organization_id', true));
CREATE TRIGGER portfolio_policy_recommendation_read_audit_immutable
    BEFORE UPDATE OR DELETE OR TRUNCATE ON {schema}.portfolio_policy_recommendation_read_audit
    FOR EACH STATEMENT EXECUTE FUNCTION {schema}.portfolio_reject_mutation();
REVOKE ALL ON {schema}.portfolio_policy_recommendation_read_audit FROM PUBLIC;
GRANT SELECT, INSERT ON {schema}.portfolio_policy_recommendation_read_audit TO {runtime_role};

ALTER TABLE {schema}.portfolio_policy_recommendation_cursors ENABLE ROW LEVEL SECURITY;
ALTER TABLE {schema}.portfolio_policy_recommendation_cursors FORCE ROW LEVEL SECURITY;
CREATE POLICY portfolio_policy_recommendation_cursors_tenant
    ON {schema}.portfolio_policy_recommendation_cursors
    USING (organization_id=current_setting('hormuz.organization_id', true))
    WITH CHECK (organization_id=current_setting('hormuz.organization_id', true));
CREATE TRIGGER portfolio_policy_recommendation_cursors_immutable
    BEFORE UPDATE OR DELETE OR TRUNCATE ON {schema}.portfolio_policy_recommendation_cursors
    FOR EACH STATEMENT EXECUTE FUNCTION {schema}.portfolio_reject_mutation();
REVOKE ALL ON {schema}.portfolio_policy_recommendation_cursors FROM PUBLIC;
GRANT SELECT, INSERT ON {schema}.portfolio_policy_recommendation_cursors TO {runtime_role};

-- Recommendation application receipts bind the current managed-policy pointer
-- to its unique immutable activation event without widening the runtime role's
-- access to the policy-control ledger.
CREATE FUNCTION {schema}.portfolio_policy_recommendation_active_policy_receipt()
RETURNS TABLE (
    organization_id TEXT,
    version_id TEXT,
    generation BIGINT,
    activated_at TIMESTAMPTZ,
    activated_by_kind TEXT,
    activated_by_identity_key TEXT,
    content_sha256 TEXT,
    event_id TEXT,
    event_type TEXT,
    occurred_at TIMESTAMPTZ,
    actor_kind TEXT,
    actor_identity_key TEXT
)
LANGUAGE sql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
    SELECT active.organization_id,
           active.version_id,
           active.generation,
           active.activated_at,
           active.activated_by_kind,
           active.activated_by_identity_key,
           versions.content_sha256,
           events.event_id,
           events.event_type,
           events.occurred_at,
           events.actor_kind,
           events.actor_identity_key
      FROM {schema}.policy_active_versions AS active
      JOIN {schema}.policy_versions AS versions
        ON versions.organization_id = active.organization_id
       AND versions.version_id = active.version_id
      JOIN {schema}.policy_control_events AS events
        ON events.organization_id = active.organization_id
       AND events.version_id = active.version_id
       AND events.generation = active.generation
     WHERE active.organization_id = current_setting('hormuz.organization_id', true)
       AND events.event_type IN ('policy_activated', 'policy_rolled_back')
     ORDER BY events.event_id
     LIMIT 2
$$;
REVOKE ALL ON FUNCTION {schema}.portfolio_policy_recommendation_active_policy_receipt()
    FROM PUBLIC;
GRANT EXECUTE ON FUNCTION {schema}.portfolio_policy_recommendation_active_policy_receipt()
    TO {runtime_role};
