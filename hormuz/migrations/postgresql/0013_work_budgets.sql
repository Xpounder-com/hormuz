CREATE TABLE {schema}.portfolio_work_budget_plan_versions (
        organization_id TEXT NOT NULL CHECK (length(organization_id) BETWEEN 1 AND 128),
        budget_plan_id TEXT NOT NULL CHECK (length(budget_plan_id) BETWEEN 1 AND 128),
        version INTEGER NOT NULL CHECK (version BETWEEN 1 AND 2147483647),
        work_scope_id TEXT NOT NULL CHECK (length(work_scope_id) BETWEEN 1 AND 128),
        work_scope_version INTEGER NOT NULL CHECK (work_scope_version BETWEEN 1 AND 2147483647),
        window_start_at TEXT NOT NULL CHECK (length(window_start_at) BETWEEN 20 AND 27),
        window_end_at TEXT NOT NULL CHECK (length(window_end_at) BETWEEN 20 AND 27),
        currency TEXT NOT NULL CHECK (length(currency) = 3),
        amount TEXT NOT NULL CHECK (length(amount) BETWEEN 1 AND 28),
        allowed_models_json TEXT CHECK (allowed_models_json IS NULL OR length(allowed_models_json) BETWEEN 2 AND 65536),
        output_token_cap BIGINT CHECK (output_token_cap IS NULL OR output_token_cap BETWEEN 0 AND 9007199254740991),
        per_request_cost_cap TEXT CHECK (per_request_cost_cap IS NULL OR length(per_request_cost_cap) BETWEEN 1 AND 28),
        content_digest TEXT NOT NULL CHECK (length(content_digest) = 64),
        supersedes_version INTEGER CHECK (supersedes_version IS NULL OR supersedes_version BETWEEN 1 AND 2147483647),
        actor_id TEXT NOT NULL CHECK (length(actor_id) BETWEEN 1 AND 128),
        reason_code TEXT NOT NULL CHECK (reason_code IN ('created','corrected')),
        created_at TEXT NOT NULL CHECK (length(created_at) BETWEEN 20 AND 27),
        sequence BIGINT NOT NULL CHECK (sequence BETWEEN 1 AND 9223372036854775807),
        PRIMARY KEY (organization_id, budget_plan_id, version),
        UNIQUE (organization_id, sequence),
        UNIQUE (organization_id, budget_plan_id, content_digest),
        FOREIGN KEY (organization_id, work_scope_id, work_scope_version)
            REFERENCES {schema}.portfolio_work_scope_versions (organization_id, work_scope_id, version),
        FOREIGN KEY (organization_id, budget_plan_id, supersedes_version)
            REFERENCES {schema}.portfolio_work_budget_plan_versions (organization_id, budget_plan_id, version),
        CHECK (window_start_at < window_end_at),
        CHECK ((version = 1 AND supersedes_version IS NULL AND reason_code = 'created')
               OR (version > 1 AND supersedes_version = version - 1 AND reason_code = 'corrected'))
    );

CREATE TABLE {schema}.portfolio_work_budget_activation_events (
        organization_id TEXT NOT NULL CHECK (length(organization_id) BETWEEN 1 AND 128),
        activation_event_id TEXT NOT NULL CHECK (length(activation_event_id) = 32),
        budget_plan_id TEXT NOT NULL CHECK (length(budget_plan_id) BETWEEN 1 AND 128),
        activation_generation BIGINT NOT NULL CHECK (activation_generation BETWEEN 1 AND 9007199254740991),
        previous_version INTEGER CHECK (previous_version IS NULL OR previous_version BETWEEN 1 AND 2147483647),
        current_version INTEGER NOT NULL CHECK (current_version BETWEEN 1 AND 2147483647),
        actor_id TEXT NOT NULL CHECK (length(actor_id) BETWEEN 1 AND 128),
        reason_code TEXT NOT NULL CHECK (reason_code IN ('accepted','reactivated')),
        policy_version TEXT NOT NULL CHECK (length(policy_version) BETWEEN 1 AND 128),
        policy_digest TEXT NOT NULL CHECK (length(policy_digest) = 64),
        committed_at TEXT NOT NULL CHECK (length(committed_at) BETWEEN 20 AND 27),
        PRIMARY KEY (organization_id, activation_event_id),
        UNIQUE (organization_id, budget_plan_id, activation_generation),
        UNIQUE (organization_id, budget_plan_id, current_version, activation_generation),
        UNIQUE (organization_id, budget_plan_id, current_version, activation_generation,
                activation_event_id, committed_at),
        UNIQUE (organization_id, budget_plan_id, current_version, activation_generation,
                policy_version, policy_digest),
        FOREIGN KEY (organization_id, budget_plan_id, current_version)
            REFERENCES {schema}.portfolio_work_budget_plan_versions (organization_id, budget_plan_id, version),
        FOREIGN KEY (organization_id, budget_plan_id, previous_version)
            REFERENCES {schema}.portfolio_work_budget_plan_versions (organization_id, budget_plan_id, version),
        CHECK ((activation_generation = 1 AND previous_version IS NULL AND reason_code = 'accepted')
               OR (activation_generation > 1 AND previous_version IS NOT NULL))
    );

CREATE TABLE {schema}.portfolio_work_budget_active_plans (
    organization_id TEXT NOT NULL CHECK (length(organization_id) BETWEEN 1 AND 128),
    budget_plan_id TEXT NOT NULL CHECK (length(budget_plan_id) BETWEEN 1 AND 128),
    active_version INTEGER NOT NULL CHECK (active_version BETWEEN 1 AND 2147483647),
    activation_generation BIGINT NOT NULL CHECK (activation_generation BETWEEN 1 AND 9007199254740991),
    current_activation_event_id TEXT NOT NULL CHECK (length(current_activation_event_id) = 32),
    changed_at TEXT NOT NULL CHECK (length(changed_at) BETWEEN 20 AND 27),
    PRIMARY KEY (organization_id, budget_plan_id),
    FOREIGN KEY (organization_id, budget_plan_id, active_version)
        REFERENCES {schema}.portfolio_work_budget_plan_versions (organization_id, budget_plan_id, version),
    FOREIGN KEY (organization_id, budget_plan_id, active_version, activation_generation,
                 current_activation_event_id, changed_at)
        REFERENCES {schema}.portfolio_work_budget_activation_events
            (organization_id, budget_plan_id, current_version, activation_generation,
             activation_event_id, committed_at)
);

CREATE TABLE {schema}.portfolio_work_budget_reservation_bindings (
        organization_id TEXT NOT NULL CHECK (length(organization_id) BETWEEN 1 AND 128),
        request_attempt_id TEXT NOT NULL CHECK (length(request_attempt_id) BETWEEN 1 AND 128),
        attribution_event_id TEXT NOT NULL CHECK (length(attribution_event_id) = 32),
        budget_plan_id TEXT NOT NULL CHECK (length(budget_plan_id) BETWEEN 1 AND 128),
        budget_plan_version INTEGER NOT NULL CHECK (budget_plan_version BETWEEN 1 AND 2147483647),
        activation_generation BIGINT NOT NULL CHECK (activation_generation BETWEEN 1 AND 9007199254740991),
        work_scope_id TEXT NOT NULL CHECK (length(work_scope_id) BETWEEN 1 AND 128),
        work_scope_version INTEGER NOT NULL CHECK (work_scope_version BETWEEN 1 AND 2147483647),
        window_start_at TEXT NOT NULL CHECK (length(window_start_at) BETWEEN 20 AND 27),
        window_end_at TEXT NOT NULL CHECK (length(window_end_at) BETWEEN 20 AND 27),
        currency TEXT NOT NULL CHECK (length(currency) = 3),
        reserved_amount TEXT NOT NULL CHECK (length(reserved_amount) BETWEEN 1 AND 37),
        reserved_output_tokens BIGINT NOT NULL CHECK (reserved_output_tokens BETWEEN 0 AND 9007199254740991),
        provider_id TEXT NOT NULL CHECK (length(provider_id) BETWEEN 1 AND 128),
        model_id TEXT NOT NULL CHECK (length(model_id) BETWEEN 1 AND 128),
        model_version TEXT CHECK (model_version IS NULL OR length(model_version) BETWEEN 1 AND 128),
        activation_policy_version TEXT NOT NULL CHECK (length(activation_policy_version) BETWEEN 1 AND 128),
        activation_policy_digest TEXT NOT NULL CHECK (length(activation_policy_digest) = 64),
        request_policy_version TEXT NOT NULL CHECK (length(request_policy_version) BETWEEN 1 AND 128),
        request_policy_digest TEXT NOT NULL CHECK (length(request_policy_digest) = 64),
        rate_card_id TEXT NOT NULL CHECK (length(rate_card_id) BETWEEN 1 AND 128),
        rate_card_version INTEGER NOT NULL CHECK (rate_card_version BETWEEN 1 AND 2147483647),
        rate_card_digest TEXT NOT NULL CHECK (length(rate_card_digest) = 64),
        rate_card_currency TEXT NOT NULL CHECK (length(rate_card_currency) = 3),
        valuation_rule_id TEXT NOT NULL CHECK (length(valuation_rule_id) BETWEEN 1 AND 128),
        valuation_rule_version INTEGER NOT NULL CHECK (valuation_rule_version BETWEEN 1 AND 2147483647),
        valuation_rule_digest TEXT NOT NULL CHECK (length(valuation_rule_digest) = 64),
        bound_at TEXT NOT NULL CHECK (length(bound_at) BETWEEN 20 AND 27),
        PRIMARY KEY (organization_id, request_attempt_id, budget_plan_id),
        FOREIGN KEY (organization_id, budget_plan_id, budget_plan_version)
            REFERENCES {schema}.portfolio_work_budget_plan_versions (organization_id, budget_plan_id, version),
        FOREIGN KEY (organization_id, attribution_event_id)
            REFERENCES {schema}.portfolio_attribution_events (organization_id, attribution_event_id),
        FOREIGN KEY (organization_id, budget_plan_id, budget_plan_version, activation_generation,
                     activation_policy_version, activation_policy_digest)
            REFERENCES {schema}.portfolio_work_budget_activation_events
                (organization_id, budget_plan_id, current_version, activation_generation,
                 policy_version, policy_digest),
        FOREIGN KEY (organization_id, work_scope_id, work_scope_version)
            REFERENCES {schema}.portfolio_work_scope_versions (organization_id, work_scope_id, version),
        CHECK (window_start_at < window_end_at),
        CHECK (currency = rate_card_currency)
    );

CREATE TABLE {schema}.portfolio_work_budget_audit_events (
        organization_id TEXT NOT NULL CHECK (length(organization_id) BETWEEN 1 AND 128),
        event_id TEXT NOT NULL CHECK (length(event_id) = 32),
        sequence BIGINT NOT NULL CHECK (sequence BETWEEN 1 AND 9223372036854775807),
        actor_id TEXT CHECK (actor_id IS NULL OR length(actor_id) BETWEEN 1 AND 128),
        operation TEXT NOT NULL CHECK (operation IN ('create','activate','preview','report','reserve_denied')),
        entity_id TEXT NOT NULL CHECK (length(entity_id) BETWEEN 1 AND 128),
        entity_version INTEGER CHECK (entity_version IS NULL OR entity_version BETWEEN 1 AND 2147483647),
        reason_code TEXT NOT NULL CHECK (reason_code IN ('created','corrected','accepted','reactivated','observed','budget_ceiling','model_intersection','output_token_ceiling','request_cost_ceiling','attribution_invalid','unsupported_currency')),
        occurred_at TEXT NOT NULL CHECK (length(occurred_at) BETWEEN 20 AND 27),
        PRIMARY KEY (organization_id, event_id),
        UNIQUE (organization_id, sequence)
    );

ALTER TABLE {schema}.portfolio_work_budget_reservation_bindings ADD CONSTRAINT portfolio_work_budget_binding_attempt_fk FOREIGN KEY (organization_id, request_attempt_id) REFERENCES {schema}.gateway_request_attempts (organization_id, attempt_id);

CREATE FUNCTION {schema}.portfolio_work_budget_binding_guard() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN IF NOT EXISTS (SELECT 1 FROM {schema}.portfolio_attribution_events e WHERE e.organization_id=NEW.organization_id AND e.attribution_event_id=NEW.attribution_event_id AND e.request_attempt_id=NEW.request_attempt_id) THEN RAISE EXCEPTION 'portfolio_budget_attempt_invalid' USING ERRCODE = '23514'; END IF; RETURN NEW; END; $$;

CREATE FUNCTION {schema}.portfolio_work_budget_pointer_guard() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN IF NEW.organization_id<>OLD.organization_id OR NEW.budget_plan_id<>OLD.budget_plan_id OR NEW.activation_generation<>OLD.activation_generation+1 THEN RAISE EXCEPTION 'portfolio_budget_pointer_invalid' USING ERRCODE = '23514'; END IF; RETURN NEW; END; $$;

ALTER TABLE {schema}.portfolio_work_budget_plan_versions ENABLE ROW LEVEL SECURITY;

ALTER TABLE {schema}.portfolio_work_budget_plan_versions FORCE ROW LEVEL SECURITY;

CREATE POLICY portfolio_work_budget_plan_versions_tenant ON {schema}.portfolio_work_budget_plan_versions USING (organization_id = current_setting('hormuz.organization_id', true)) WITH CHECK (organization_id = current_setting('hormuz.organization_id', true));

REVOKE ALL ON {schema}.portfolio_work_budget_plan_versions FROM PUBLIC;

CREATE TRIGGER portfolio_work_budget_plan_versions_immutable BEFORE UPDATE OR DELETE OR TRUNCATE ON {schema}.portfolio_work_budget_plan_versions FOR EACH STATEMENT EXECUTE FUNCTION {schema}.portfolio_reject_mutation();

GRANT SELECT, INSERT ON {schema}.portfolio_work_budget_plan_versions TO {runtime_role};

ALTER TABLE {schema}.portfolio_work_budget_activation_events ENABLE ROW LEVEL SECURITY;

ALTER TABLE {schema}.portfolio_work_budget_activation_events FORCE ROW LEVEL SECURITY;

CREATE POLICY portfolio_work_budget_activation_events_tenant ON {schema}.portfolio_work_budget_activation_events USING (organization_id = current_setting('hormuz.organization_id', true)) WITH CHECK (organization_id = current_setting('hormuz.organization_id', true));

REVOKE ALL ON {schema}.portfolio_work_budget_activation_events FROM PUBLIC;

CREATE TRIGGER portfolio_work_budget_activation_events_immutable BEFORE UPDATE OR DELETE OR TRUNCATE ON {schema}.portfolio_work_budget_activation_events FOR EACH STATEMENT EXECUTE FUNCTION {schema}.portfolio_reject_mutation();

GRANT SELECT, INSERT ON {schema}.portfolio_work_budget_activation_events TO {runtime_role};

ALTER TABLE {schema}.portfolio_work_budget_active_plans ENABLE ROW LEVEL SECURITY;

ALTER TABLE {schema}.portfolio_work_budget_active_plans FORCE ROW LEVEL SECURITY;

CREATE POLICY portfolio_work_budget_active_plans_tenant ON {schema}.portfolio_work_budget_active_plans USING (organization_id = current_setting('hormuz.organization_id', true)) WITH CHECK (organization_id = current_setting('hormuz.organization_id', true));

REVOKE ALL ON {schema}.portfolio_work_budget_active_plans FROM PUBLIC;

CREATE TRIGGER portfolio_work_budget_active_plans_pointer_guard BEFORE UPDATE ON {schema}.portfolio_work_budget_active_plans FOR EACH ROW EXECUTE FUNCTION {schema}.portfolio_work_budget_pointer_guard();

CREATE TRIGGER portfolio_work_budget_active_plans_immutable BEFORE DELETE OR TRUNCATE ON {schema}.portfolio_work_budget_active_plans FOR EACH STATEMENT EXECUTE FUNCTION {schema}.portfolio_reject_mutation();

GRANT SELECT, INSERT ON {schema}.portfolio_work_budget_active_plans TO {runtime_role};

GRANT UPDATE (active_version, activation_generation, current_activation_event_id, changed_at) ON {schema}.portfolio_work_budget_active_plans TO {runtime_role};

ALTER TABLE {schema}.portfolio_work_budget_reservation_bindings ENABLE ROW LEVEL SECURITY;

ALTER TABLE {schema}.portfolio_work_budget_reservation_bindings FORCE ROW LEVEL SECURITY;

CREATE POLICY portfolio_work_budget_reservation_bindings_tenant ON {schema}.portfolio_work_budget_reservation_bindings USING (organization_id = current_setting('hormuz.organization_id', true)) WITH CHECK (organization_id = current_setting('hormuz.organization_id', true));

REVOKE ALL ON {schema}.portfolio_work_budget_reservation_bindings FROM PUBLIC;

CREATE TRIGGER portfolio_work_budget_reservation_bindings_immutable BEFORE UPDATE OR DELETE OR TRUNCATE ON {schema}.portfolio_work_budget_reservation_bindings FOR EACH STATEMENT EXECUTE FUNCTION {schema}.portfolio_reject_mutation();

CREATE TRIGGER portfolio_work_budget_reservation_bindings_attempt_guard BEFORE INSERT ON {schema}.portfolio_work_budget_reservation_bindings FOR EACH ROW EXECUTE FUNCTION {schema}.portfolio_work_budget_binding_guard();

GRANT SELECT, INSERT ON {schema}.portfolio_work_budget_reservation_bindings TO {runtime_role};

ALTER TABLE {schema}.portfolio_work_budget_audit_events ENABLE ROW LEVEL SECURITY;

ALTER TABLE {schema}.portfolio_work_budget_audit_events FORCE ROW LEVEL SECURITY;

CREATE POLICY portfolio_work_budget_audit_events_tenant ON {schema}.portfolio_work_budget_audit_events USING (organization_id = current_setting('hormuz.organization_id', true)) WITH CHECK (organization_id = current_setting('hormuz.organization_id', true));

REVOKE ALL ON {schema}.portfolio_work_budget_audit_events FROM PUBLIC;

CREATE TRIGGER portfolio_work_budget_audit_events_immutable BEFORE UPDATE OR DELETE OR TRUNCATE ON {schema}.portfolio_work_budget_audit_events FOR EACH STATEMENT EXECUTE FUNCTION {schema}.portfolio_reject_mutation();

GRANT SELECT, INSERT ON {schema}.portfolio_work_budget_audit_events TO {runtime_role};
