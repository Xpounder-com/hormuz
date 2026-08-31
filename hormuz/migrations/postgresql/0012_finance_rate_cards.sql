CREATE TABLE {schema}.portfolio_finance_audit_events (
        organization_id TEXT NOT NULL CHECK (length(organization_id) BETWEEN 1 AND 128),
        event_id TEXT NOT NULL CHECK (length(event_id) = 32),
        sequence BIGINT NOT NULL CHECK (sequence >= 1),
        actor_id TEXT NOT NULL CHECK (length(actor_id) BETWEEN 1 AND 128),
        operation TEXT NOT NULL CHECK (operation IN ('register','read')),
        rate_card_id TEXT NOT NULL CHECK (length(rate_card_id) BETWEEN 1 AND 128),
        version INTEGER NOT NULL CHECK (version BETWEEN 1 AND 2147483647),
        content_digest TEXT NOT NULL CHECK (length(content_digest) = 64),
        occurred_at TEXT NOT NULL,
        PRIMARY KEY (organization_id, event_id),
        UNIQUE (organization_id, sequence)
    );

CREATE TABLE {schema}.portfolio_finance_rate_cards (
        organization_id TEXT NOT NULL CHECK (length(organization_id) BETWEEN 1 AND 128),
        rate_card_id TEXT NOT NULL CHECK (length(rate_card_id) BETWEEN 1 AND 128),
        version INTEGER NOT NULL CHECK (version BETWEEN 1 AND 2147483647),
        card_json TEXT NOT NULL CHECK (length(card_json) BETWEEN 1 AND 8192),
        content_digest TEXT NOT NULL CHECK (length(content_digest) = 64),
        receipt_id TEXT NOT NULL CHECK (length(receipt_id) = 32),
        registered_by TEXT NOT NULL CHECK (length(registered_by) BETWEEN 1 AND 128),
        registered_at TEXT NOT NULL,
        sequence BIGINT NOT NULL CHECK (sequence >= 1),
        PRIMARY KEY (organization_id, rate_card_id, version),
        UNIQUE (organization_id, receipt_id),
        FOREIGN KEY (organization_id, sequence)
            REFERENCES {schema}.portfolio_finance_audit_events (organization_id, sequence)
    );

ALTER TABLE {schema}.portfolio_finance_audit_events ENABLE ROW LEVEL SECURITY;

ALTER TABLE {schema}.portfolio_finance_audit_events FORCE ROW LEVEL SECURITY;

CREATE POLICY portfolio_finance_audit_events_tenant ON {schema}.portfolio_finance_audit_events USING (organization_id = current_setting('hormuz.organization_id', true)) WITH CHECK (organization_id = current_setting('hormuz.organization_id', true));

CREATE TRIGGER portfolio_finance_audit_events_immutable BEFORE UPDATE OR DELETE OR TRUNCATE ON {schema}.portfolio_finance_audit_events FOR EACH STATEMENT EXECUTE FUNCTION {schema}.portfolio_reject_mutation();

REVOKE ALL ON {schema}.portfolio_finance_audit_events FROM PUBLIC;

GRANT SELECT, INSERT ON {schema}.portfolio_finance_audit_events TO {runtime_role};

ALTER TABLE {schema}.portfolio_finance_rate_cards ENABLE ROW LEVEL SECURITY;

ALTER TABLE {schema}.portfolio_finance_rate_cards FORCE ROW LEVEL SECURITY;

CREATE POLICY portfolio_finance_rate_cards_tenant ON {schema}.portfolio_finance_rate_cards USING (organization_id = current_setting('hormuz.organization_id', true)) WITH CHECK (organization_id = current_setting('hormuz.organization_id', true));

CREATE TRIGGER portfolio_finance_rate_cards_immutable BEFORE UPDATE OR DELETE OR TRUNCATE ON {schema}.portfolio_finance_rate_cards FOR EACH STATEMENT EXECUTE FUNCTION {schema}.portfolio_reject_mutation();

REVOKE ALL ON {schema}.portfolio_finance_rate_cards FROM PUBLIC;

GRANT SELECT, INSERT ON {schema}.portfolio_finance_rate_cards TO {runtime_role};
