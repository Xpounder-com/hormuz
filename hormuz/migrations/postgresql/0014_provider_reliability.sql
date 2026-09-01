-- Content-free per-attempt latency metrics and one-hop failover linkage.
-- Both tables are append-only, tenant-isolated, and reference the immutable
-- request-attempt roots that were committed before provider egress.

CREATE TABLE {schema}.gateway_provider_attempt_metrics (
        event_id TEXT NOT NULL CHECK (length(event_id) = 36),
        event_schema_id TEXT NOT NULL CHECK (event_schema_id = 'hormuz.provider-attempt-metrics'),
        event_schema_version INTEGER NOT NULL CHECK (event_schema_version = 1),
        organization_id TEXT NOT NULL CHECK (length(organization_id) BETWEEN 1 AND 128),
        attempt_id TEXT NOT NULL CHECK (length(attempt_id) BETWEEN 1 AND 128),
        recorded_at TEXT NOT NULL,
        provider_status INTEGER CHECK (provider_status BETWEEN 100 AND 599),
        response_headers_us BIGINT CHECK (response_headers_us >= 0),
        first_body_byte_us BIGINT CHECK (first_body_byte_us >= 0),
        total_us BIGINT NOT NULL CHECK (total_us >= 0),
        provider_bytes_read BIGINT NOT NULL CHECK (provider_bytes_read >= 0),
        downstream_bytes_sent BIGINT NOT NULL CHECK (downstream_bytes_sent >= 0),
        PRIMARY KEY (organization_id, event_id),
        UNIQUE (organization_id, attempt_id),
        FOREIGN KEY (organization_id, attempt_id)
            REFERENCES {schema}.gateway_request_attempts (organization_id, attempt_id),
        CHECK (response_headers_us IS NULL OR response_headers_us <= total_us),
        CHECK (
            first_body_byte_us IS NULL
            OR (
                response_headers_us IS NOT NULL
                AND first_body_byte_us >= response_headers_us
                AND first_body_byte_us <= total_us
            )
        ),
        CHECK (downstream_bytes_sent <= provider_bytes_read)
    );

CREATE TABLE {schema}.gateway_provider_failover_events (
        event_id TEXT NOT NULL CHECK (length(event_id) = 36),
        event_schema_id TEXT NOT NULL CHECK (event_schema_id = 'hormuz.provider-failover'),
        event_schema_version INTEGER NOT NULL CHECK (event_schema_version = 1),
        organization_id TEXT NOT NULL CHECK (length(organization_id) BETWEEN 1 AND 128),
        original_attempt_id TEXT NOT NULL CHECK (length(original_attempt_id) BETWEEN 1 AND 128),
        failover_attempt_id TEXT NOT NULL CHECK (length(failover_attempt_id) BETWEEN 1 AND 128),
        trigger_status INTEGER NOT NULL CHECK (trigger_status IN (429, 529)),
        reason_code TEXT NOT NULL CHECK (reason_code IN ('provider_rate_limited', 'provider_overloaded')),
        recorded_at TEXT NOT NULL,
        PRIMARY KEY (organization_id, event_id),
        UNIQUE (organization_id, original_attempt_id),
        UNIQUE (organization_id, failover_attempt_id),
        FOREIGN KEY (organization_id, original_attempt_id)
            REFERENCES {schema}.gateway_request_attempts (organization_id, attempt_id),
        FOREIGN KEY (organization_id, failover_attempt_id)
            REFERENCES {schema}.gateway_request_attempts (organization_id, attempt_id),
        CHECK (original_attempt_id <> failover_attempt_id),
        CHECK (
            (trigger_status = 429 AND reason_code = 'provider_rate_limited')
            OR (trigger_status = 529 AND reason_code = 'provider_overloaded')
        )
    );

ALTER TABLE {schema}.gateway_provider_attempt_metrics ENABLE ROW LEVEL SECURITY;

ALTER TABLE {schema}.gateway_provider_attempt_metrics FORCE ROW LEVEL SECURITY;

CREATE POLICY gateway_provider_attempt_metrics_tenant ON {schema}.gateway_provider_attempt_metrics USING (organization_id = current_setting('hormuz.organization_id', true)) WITH CHECK (organization_id = current_setting('hormuz.organization_id', true));

CREATE TRIGGER gateway_provider_attempt_metrics_immutable BEFORE UPDATE OR DELETE OR TRUNCATE ON {schema}.gateway_provider_attempt_metrics FOR EACH STATEMENT EXECUTE FUNCTION {schema}.portfolio_reject_mutation();

REVOKE ALL ON {schema}.gateway_provider_attempt_metrics FROM PUBLIC;

GRANT SELECT, INSERT ON {schema}.gateway_provider_attempt_metrics TO {runtime_role};

ALTER TABLE {schema}.gateway_provider_failover_events ENABLE ROW LEVEL SECURITY;

ALTER TABLE {schema}.gateway_provider_failover_events FORCE ROW LEVEL SECURITY;

CREATE POLICY gateway_provider_failover_events_tenant ON {schema}.gateway_provider_failover_events USING (organization_id = current_setting('hormuz.organization_id', true)) WITH CHECK (organization_id = current_setting('hormuz.organization_id', true));

CREATE TRIGGER gateway_provider_failover_events_immutable BEFORE UPDATE OR DELETE OR TRUNCATE ON {schema}.gateway_provider_failover_events FOR EACH STATEMENT EXECUTE FUNCTION {schema}.portfolio_reject_mutation();

REVOKE ALL ON {schema}.gateway_provider_failover_events FROM PUBLIC;

GRANT SELECT, INSERT ON {schema}.gateway_provider_failover_events TO {runtime_role};
