-- SQLite 17 / PostgreSQL 22 immutable model-scorecard snapshots.

CREATE TABLE {schema}.portfolio_scorecard_audit_events (
    organization_id TEXT NOT NULL CHECK (length(organization_id) BETWEEN 1 AND 128),
    event_id TEXT NOT NULL CHECK (length(event_id) = 36),
    sequence BIGINT NOT NULL CHECK (sequence >= 1),
    actor_id TEXT NOT NULL CHECK (length(actor_id) BETWEEN 1 AND 128),
    operation TEXT NOT NULL CHECK (operation = 'build'),
    scorecard_id TEXT NOT NULL CHECK (length(scorecard_id) BETWEEN 1 AND 128),
    scorecard_version INTEGER NOT NULL CHECK (scorecard_version BETWEEN 1 AND 2147483647),
    reason_code TEXT NOT NULL CHECK (reason_code IN ('eligible','missing_evidence')),
    occurred_at TEXT NOT NULL,
    PRIMARY KEY (organization_id, event_id),
    UNIQUE (organization_id, sequence)
);

CREATE TABLE {schema}.portfolio_model_scorecard_snapshots (
    organization_id TEXT NOT NULL CHECK (length(organization_id) BETWEEN 1 AND 128),
    scorecard_id TEXT NOT NULL CHECK (length(scorecard_id) BETWEEN 1 AND 128),
    version INTEGER NOT NULL CHECK (version BETWEEN 1 AND 2147483647),
    work_scope_id TEXT NOT NULL CHECK (length(work_scope_id) BETWEEN 1 AND 128),
    work_scope_version INTEGER NOT NULL CHECK (work_scope_version BETWEEN 1 AND 2147483647),
    window_start_at TEXT NOT NULL,
    window_end_at TEXT NOT NULL,
    evaluated_at TEXT NOT NULL,
    generated_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    review_after TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('eligible','inconclusive')),
    evidence_level TEXT NOT NULL CHECK (evidence_level = 'associated'),
    decision_owner_id TEXT NOT NULL CHECK (length(decision_owner_id) BETWEEN 1 AND 128),
    supersedes_version INTEGER,
    input_digest TEXT NOT NULL CHECK (length(input_digest) = 64),
    evaluation_digest TEXT NOT NULL CHECK (length(evaluation_digest) = 64),
    source_set_digest TEXT NOT NULL CHECK (length(source_set_digest) = 64),
    input_json TEXT NOT NULL CHECK (length(input_json) BETWEEN 2 AND 16777216),
    evaluation_json TEXT NOT NULL CHECK (length(evaluation_json) BETWEEN 2 AND 16777216),
    sequence BIGINT NOT NULL CHECK (sequence >= 1),
    PRIMARY KEY (organization_id, scorecard_id, version),
    UNIQUE (organization_id, sequence),
    UNIQUE (organization_id, scorecard_id, input_digest),
    FOREIGN KEY (organization_id, work_scope_id, work_scope_version)
        REFERENCES {schema}.portfolio_work_scope_versions (organization_id, work_scope_id, version),
    FOREIGN KEY (organization_id, scorecard_id, supersedes_version)
        REFERENCES {schema}.portfolio_model_scorecard_snapshots (organization_id, scorecard_id, version),
    FOREIGN KEY (organization_id, sequence)
        REFERENCES {schema}.portfolio_scorecard_audit_events (organization_id, sequence),
    CHECK (length(window_start_at) = 27 AND substr(window_start_at, 20, 1) = '.' AND substr(window_start_at, 27, 1) = 'Z'),
    CHECK (length(window_end_at) = 27 AND substr(window_end_at, 20, 1) = '.' AND substr(window_end_at, 27, 1) = 'Z'),
    CHECK (length(evaluated_at) = 27 AND substr(evaluated_at, 20, 1) = '.' AND substr(evaluated_at, 27, 1) = 'Z'),
    CHECK (length(generated_at) = 27 AND substr(generated_at, 20, 1) = '.' AND substr(generated_at, 27, 1) = 'Z'),
    CHECK (length(expires_at) = 27 AND substr(expires_at, 20, 1) = '.' AND substr(expires_at, 27, 1) = 'Z'),
    CHECK (length(review_after) = 27 AND substr(review_after, 20, 1) = '.' AND substr(review_after, 27, 1) = 'Z'),
    CHECK (window_start_at < window_end_at),
    CHECK (window_end_at <= evaluated_at AND evaluated_at <= generated_at),
    CHECK (generated_at < expires_at AND generated_at < review_after),
    CHECK ((version = 1 AND supersedes_version IS NULL) OR (version > 1 AND supersedes_version = version - 1))
);

CREATE INDEX portfolio_model_scorecard_scope_window
    ON {schema}.portfolio_model_scorecard_snapshots (
        organization_id, work_scope_id, work_scope_version,
        window_start_at, window_end_at, generated_at, scorecard_id, version
    );
CREATE INDEX portfolio_model_scorecard_latest
    ON {schema}.portfolio_model_scorecard_snapshots (
        organization_id, scorecard_id, version DESC
    );

ALTER TABLE {schema}.portfolio_scorecard_audit_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE {schema}.portfolio_scorecard_audit_events FORCE ROW LEVEL SECURITY;
CREATE POLICY portfolio_scorecard_audit_events_tenant
    ON {schema}.portfolio_scorecard_audit_events
    USING (organization_id=current_setting('hormuz.organization_id', true))
    WITH CHECK (organization_id=current_setting('hormuz.organization_id', true));
CREATE TRIGGER portfolio_scorecard_audit_events_immutable
    BEFORE UPDATE OR DELETE OR TRUNCATE ON {schema}.portfolio_scorecard_audit_events
    FOR EACH STATEMENT EXECUTE FUNCTION {schema}.portfolio_reject_mutation();
REVOKE ALL ON {schema}.portfolio_scorecard_audit_events FROM PUBLIC;
GRANT SELECT, INSERT ON {schema}.portfolio_scorecard_audit_events TO {runtime_role};

ALTER TABLE {schema}.portfolio_model_scorecard_snapshots ENABLE ROW LEVEL SECURITY;
ALTER TABLE {schema}.portfolio_model_scorecard_snapshots FORCE ROW LEVEL SECURITY;
CREATE POLICY portfolio_model_scorecard_snapshots_tenant
    ON {schema}.portfolio_model_scorecard_snapshots
    USING (organization_id=current_setting('hormuz.organization_id', true))
    WITH CHECK (organization_id=current_setting('hormuz.organization_id', true));
CREATE TRIGGER portfolio_model_scorecard_snapshots_immutable
    BEFORE UPDATE OR DELETE OR TRUNCATE ON {schema}.portfolio_model_scorecard_snapshots
    FOR EACH STATEMENT EXECUTE FUNCTION {schema}.portfolio_reject_mutation();
REVOKE ALL ON {schema}.portfolio_model_scorecard_snapshots FROM PUBLIC;
GRANT SELECT, INSERT ON {schema}.portfolio_model_scorecard_snapshots TO {runtime_role};
