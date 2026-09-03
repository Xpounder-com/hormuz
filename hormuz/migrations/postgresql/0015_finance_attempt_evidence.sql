-- Immutable provider-native usage and configured estimates at one gateway
-- egress-attempt grain. Existing roots are explicit legacy coverage gaps;
-- every new root freezes its configured price identity before provider I/O.

ALTER TABLE {schema}.gateway_request_attempts
    ADD COLUMN configured_rate_card_state TEXT NOT NULL DEFAULT 'legacy_unavailable',
    ADD COLUMN configured_rate_card_id TEXT,
    ADD COLUMN configured_rate_card_version INTEGER,
    ADD COLUMN configured_rate_card_digest TEXT,
    ADD COLUMN configured_rate_card_currency TEXT;

ALTER TABLE {schema}.gateway_request_attempts
    ALTER COLUMN configured_rate_card_state DROP DEFAULT;

ALTER TABLE {schema}.gateway_request_attempts
    ADD CONSTRAINT gateway_request_attempt_finance_binding_check CHECK (
        (
            configured_rate_card_state = 'legacy_unavailable'
            AND configured_rate_card_id IS NULL
            AND configured_rate_card_version IS NULL
            AND configured_rate_card_digest IS NULL
            AND configured_rate_card_currency IS NULL
        )
        OR (
            configured_rate_card_state = 'configured'
            AND configured_rate_card_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{{0,127}}$'
            AND configured_rate_card_version BETWEEN 1 AND 2147483647
            AND configured_rate_card_digest ~ '^[0-9a-f]{{64}}$'
            AND configured_rate_card_currency ~ '^[A-Z]{{3}}$'
        )
    );

CREATE OR REPLACE FUNCTION {schema}.require_request_attempt_finance_binding()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog
AS $$
BEGIN
    IF length(NEW.organization_id) NOT BETWEEN 1 AND 128
       OR NEW.organization_id ~ E'[\\r\\n]'
       OR NEW.configured_rate_card_state IS DISTINCT FROM 'configured'
       OR NEW.configured_rate_card_id IS NULL
       OR NEW.configured_rate_card_version IS NULL
       OR NEW.configured_rate_card_digest IS NULL
       OR NEW.configured_rate_card_currency IS NULL THEN
        RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'finance attempt binding required';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER gateway_request_attempt_finance_binding_required
BEFORE INSERT ON {schema}.gateway_request_attempts
FOR EACH ROW EXECUTE FUNCTION {schema}.require_request_attempt_finance_binding();

CREATE TRIGGER gateway_request_attempt_finance_binding_immutable
BEFORE UPDATE OF configured_rate_card_state, configured_rate_card_id,
    configured_rate_card_version, configured_rate_card_digest,
    configured_rate_card_currency
ON {schema}.gateway_request_attempts
FOR EACH STATEMENT EXECUTE FUNCTION {schema}.portfolio_reject_mutation();

CREATE UNIQUE INDEX gateway_request_attempt_event_organization_id
    ON {schema}.gateway_request_attempt_events (organization_id, id);

ALTER TABLE {schema}.gateway_audit_chain_entries
    DROP CONSTRAINT gateway_audit_chain_entries_source_identity_check;
ALTER TABLE {schema}.gateway_audit_chain_entries
    ADD CONSTRAINT gateway_audit_chain_entries_source_identity_check CHECK (
        (
            entry_schema_version = 1
            AND source_schema_id IS NULL
            AND source_schema_version IS NULL
            AND source_event_id IS NULL
        )
        OR (
            entry_schema_version = 2
            AND (
                (source_schema_id = 'hormuz.custody-control-event' AND source_schema_version = 1)
                OR (source_schema_id = 'hormuz.custody-execution-attempt' AND source_schema_version = 2)
                OR (source_schema_id = 'hormuz.custody-execution-event' AND source_schema_version = 1)
                OR (source_schema_id = 'hormuz.custody-lifecycle-event' AND source_schema_version = 1)
                OR (source_schema_id = 'hormuz.custody-envelope-attestation' AND source_schema_version = 1)
                OR (source_schema_id = 'hormuz.custody-deletion-event' AND source_schema_version = 1)
                OR (source_schema_id = 'hormuz.finance-attempt-evidence' AND source_schema_version = 1)
            )
            AND source_event_id IS NOT NULL
        )
    );

CREATE TABLE {schema}.gateway_finance_attempt_evidence (
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
        REFERENCES {schema}.gateway_request_attempts (organization_id, attempt_id),
    FOREIGN KEY (organization_id, terminal_attempt_event_id)
        REFERENCES {schema}.gateway_request_attempt_events (organization_id, id),
    FOREIGN KEY (organization_id, usage_event_id)
        REFERENCES {schema}.gateway_usage_events (organization_id, id),
    CHECK ((terminal_state = 'outcome_unknown') = (usage_event_id IS NULL)),
    CHECK (
        terminal_state = 'outcome_unknown'
        OR observation_reason_code IS NULL
        OR observation_reason_code NOT IN (
            'stale_pending','provider_transport_ambiguous','provider_stream_interrupted'
        )
    ),
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
);

CREATE INDEX gateway_finance_attempt_time
    ON {schema}.gateway_finance_attempt_evidence (organization_id, occurred_at, request_attempt_id);
CREATE INDEX gateway_finance_attempt_rate_card
    ON {schema}.gateway_finance_attempt_evidence (organization_id, configured_rate_card_id, configured_rate_card_version, occurred_at);
CREATE INDEX gateway_finance_attempt_provider
    ON {schema}.gateway_finance_attempt_evidence (organization_id, provider_schema_id, provider_service_tier, occurred_at);

CREATE OR REPLACE FUNCTION {schema}.enforce_finance_attempt_evidence_consistency()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog
AS $$
DECLARE
    v_root RECORD;
    v_terminal RECORD;
    v_evidence JSONB;
    v_evidence_canonical TEXT;
    v_native JSONB;
    v_native_canonical TEXT;
    v_numeric JSONB;
    v_numeric_values JSONB[];
    v_occurred_at TIMESTAMPTZ;
    v_occurred_text TEXT;
    v_provider_input BIGINT;
    v_provider_output BIGINT;
    v_cache_read BIGINT;
    v_cache_write BIGINT;
    v_cache_write_5m BIGINT;
    v_cache_write_1h BIGINT;
    v_reasoning BIGINT;
    v_total BIGINT;
    v_server_tools BIGINT;
    v_search_requests BIGINT;
    v_fetch_requests BIGINT;
    v_required_present BOOLEAN;
    v_intrinsically_invalid BOOLEAN := FALSE;
BEGIN
    SELECT protocol, configured_rate_card_state, configured_rate_card_id,
           configured_rate_card_version, configured_rate_card_digest,
           configured_rate_card_currency
      INTO v_root
      FROM {schema}.gateway_request_attempts
     WHERE organization_id = NEW.organization_id
       AND attempt_id = NEW.request_attempt_id;
    IF NOT FOUND
       OR v_root.configured_rate_card_state IS DISTINCT FROM 'configured'
       OR v_root.configured_rate_card_id IS DISTINCT FROM NEW.configured_rate_card_id
       OR v_root.configured_rate_card_version IS DISTINCT FROM NEW.configured_rate_card_version
       OR v_root.configured_rate_card_digest IS DISTINCT FROM NEW.configured_rate_card_digest
       OR v_root.configured_rate_card_currency IS DISTINCT FROM NEW.configured_estimate_currency
       OR NOT (
           (v_root.protocol = 'openai' AND NEW.provider_schema_id = 'openai.responses.usage.v1')
           OR (v_root.protocol = 'anthropic' AND NEW.provider_schema_id = 'anthropic.messages.usage.v1')
       ) THEN
        RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'finance attempt evidence binding mismatch';
    END IF;

    SELECT terminal.state, terminal.occurred_at, terminal.usage_event_id,
           usage.cost_microusd AS usage_cost_microusd
      INTO v_terminal
      FROM {schema}.gateway_request_attempt_events AS terminal
      LEFT JOIN {schema}.gateway_usage_events AS usage
        ON usage.organization_id = terminal.organization_id
       AND usage.id = terminal.usage_event_id
     WHERE terminal.organization_id = NEW.organization_id
       AND terminal.attempt_id = NEW.request_attempt_id
       AND terminal.id = NEW.terminal_attempt_event_id;
    IF NOT FOUND
       OR v_terminal.state IS DISTINCT FROM NEW.terminal_state
       OR v_terminal.occurred_at IS DISTINCT FROM NEW.occurred_at
       OR v_terminal.usage_event_id IS DISTINCT FROM NEW.usage_event_id
       OR (
           NEW.configured_estimate_availability = 'available'
           AND v_terminal.usage_cost_microusd
               IS DISTINCT FROM NEW.configured_estimate_microusd
       ) THEN
        RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'finance attempt evidence terminal mismatch';
    END IF;

    IF NEW.observation_reason_code IS NOT NULL
       AND NEW.observation_reason_code NOT IN (
           'provider_usage_absent', 'provider_usage_incomplete',
           'provider_usage_invalid', 'stale_pending',
           'provider_transport_ambiguous', 'provider_stream_interrupted'
       ) THEN
        RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'finance attempt observation is invalid';
    END IF;
    IF (NEW.observation_state = 'complete' AND NEW.observation_reason_code IS NOT NULL)
       OR (NEW.observation_state <> 'complete' AND NEW.observation_reason_code IS NULL)
       OR (NEW.observation_state = 'partial' AND NEW.observation_reason_code = 'provider_usage_absent')
       OR (NEW.observation_state = 'absent' AND NEW.observation_reason_code = 'provider_usage_incomplete')
       OR (
           NEW.terminal_state <> 'outcome_unknown'
           AND NEW.observation_reason_code IN (
               'stale_pending', 'provider_transport_ambiguous',
               'provider_stream_interrupted'
           )
       ) THEN
        RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'finance attempt observation is invalid';
    END IF;
    IF NEW.configured_estimate_reason_code NOT IN (
           'estimated', 'missing_native_usage', 'invalid_native_usage',
           'estimate_outside_precision', 'attempt_outcome_unknown'
       )
       OR NEW.provider_final IS DISTINCT FROM FALSE THEN
        RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'finance attempt estimate is invalid';
    END IF;
    IF NEW.configured_estimate_availability = 'available' THEN
        IF NEW.configured_estimate_amount IS NULL
           OR NEW.configured_estimate_microusd IS NULL
           OR NEW.configured_estimate_reason_code <> 'estimated'
           OR NEW.configured_estimate_amount !~ '^(0|[1-9][0-9]{{0,17}})([.][0-9]{{0,17}}[1-9])?$' THEN
            RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'finance attempt estimate is invalid';
        END IF;
        IF NEW.configured_estimate_amount::NUMERIC * 1000000
           IS DISTINCT FROM NEW.configured_estimate_microusd::NUMERIC THEN
            RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'finance attempt estimate is invalid';
        END IF;
        IF NEW.provider_input_tokens IS NULL
           OR NEW.provider_output_tokens IS NULL
           OR NEW.cache_read_input_tokens IS NULL
           OR NEW.cache_write_input_tokens IS NULL
           OR (
               NEW.provider_schema_id = 'openai.responses.usage.v1'
               AND (
                   NEW.cache_write_input_tokens > NEW.provider_input_tokens
                   OR NEW.cache_read_input_tokens
                      > NEW.provider_input_tokens - NEW.cache_write_input_tokens
               )
           ) THEN
            RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'finance attempt estimate lacks native pricing support';
        END IF;
    ELSIF NEW.configured_estimate_amount IS NOT NULL
       OR NEW.configured_estimate_microusd IS NOT NULL
       OR NEW.configured_estimate_reason_code = 'estimated' THEN
        RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'finance attempt estimate is invalid';
    END IF;
    IF NEW.terminal_state = 'outcome_unknown' THEN
        IF NEW.configured_estimate_availability <> 'unavailable'
           OR NEW.configured_estimate_reason_code <> 'attempt_outcome_unknown'
           OR NEW.observation_state = 'complete' THEN
            RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'finance attempt unknown outcome is invalid';
        END IF;
    ELSIF NEW.configured_estimate_reason_code = 'attempt_outcome_unknown' THEN
        RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'finance attempt estimate is invalid';
    END IF;

    -- The runtime role may insert the sidecar, so bind its two JSON fields to
    -- the reviewed finite schema here rather than trusting application code.
    -- Exact canonical-text comparison also rejects duplicate members and
    -- prevents either field from becoming an arbitrary JSON side channel.
    BEGIN
        v_evidence := NEW.evidence_json::JSONB;
    EXCEPTION WHEN others THEN
        RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'finance attempt evidence contract is invalid';
    END;
    v_occurred_text := v_evidence ->> 'occurred_at';
    IF jsonb_typeof(v_evidence) IS DISTINCT FROM 'object'
       OR jsonb_typeof(v_evidence -> 'occurred_at') IS DISTINCT FROM 'string'
       OR v_occurred_text !~ '^[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]([.][0-9][0-9][0-9][0-9][0-9][0-9])?[+]00:00$' THEN
        RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'finance attempt evidence contract is invalid';
    END IF;
    BEGIN
        v_occurred_at := v_occurred_text::TIMESTAMPTZ;
    EXCEPTION WHEN others THEN
        RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'finance attempt evidence contract is invalid';
    END;
    IF v_occurred_at IS DISTINCT FROM NEW.occurred_at THEN
        RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'finance attempt evidence contract is invalid';
    END IF;
    v_evidence_canonical := replace(replace(json_build_object(
        'billable_input_tokens', NEW.billable_input_tokens,
        'billable_output_tokens', NEW.billable_output_tokens,
        'cache_read_input_tokens', NEW.cache_read_input_tokens,
        'cache_write_1h_input_tokens', NEW.cache_write_1h_input_tokens,
        'cache_write_5m_input_tokens', NEW.cache_write_5m_input_tokens,
        'cache_write_input_tokens', NEW.cache_write_input_tokens,
        'configured_estimate_amount', NEW.configured_estimate_amount,
        'configured_estimate_availability', NEW.configured_estimate_availability,
        'configured_estimate_basis', NEW.configured_estimate_basis,
        'configured_estimate_currency', NEW.configured_estimate_currency,
        'configured_estimate_microusd', NEW.configured_estimate_microusd,
        'configured_estimate_reason_code', NEW.configured_estimate_reason_code,
        'configured_rate_card_digest', NEW.configured_rate_card_digest,
        'configured_rate_card_id', NEW.configured_rate_card_id,
        'configured_rate_card_version', NEW.configured_rate_card_version,
        'evidence_event_id', NEW.evidence_event_id,
        'native_payload_digest', NEW.native_payload_digest,
        'native_payload_json', NEW.native_payload_json,
        'observation_reason_code', NEW.observation_reason_code,
        'observation_state', NEW.observation_state,
        'occurred_at', v_occurred_text,
        'organization_id', NEW.organization_id,
        'provider_final', NEW.provider_final,
        'provider_inference_geo', NEW.provider_inference_geo,
        'provider_input_tokens', NEW.provider_input_tokens,
        'provider_output_tokens', NEW.provider_output_tokens,
        'provider_schema_id', NEW.provider_schema_id,
        'provider_schema_version', NEW.provider_schema_version,
        'provider_service_tier', NEW.provider_service_tier,
        'reasoning_output_tokens', NEW.reasoning_output_tokens,
        'request_attempt_id', NEW.request_attempt_id,
        'schema_id', NEW.event_schema_id,
        'schema_version', NEW.event_schema_version,
        'server_tool_request_count', NEW.server_tool_request_count,
        'terminal_attempt_event_id', NEW.terminal_attempt_event_id,
        'terminal_state', NEW.terminal_state,
        'total_tokens', NEW.total_tokens,
        'usage_event_id', NEW.usage_event_id
    )::TEXT, ' : ', ':'), ', ', ',');
    IF NEW.evidence_json IS DISTINCT FROM v_evidence_canonical THEN
        RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'finance attempt evidence contract is invalid';
    END IF;

    IF NEW.native_payload_json IS NULL THEN
        IF NEW.native_payload_digest IS NOT NULL
           OR NEW.observation_state <> 'absent'
           OR NEW.provider_input_tokens IS NOT NULL
           OR NEW.provider_output_tokens IS NOT NULL
           OR NEW.cache_read_input_tokens IS NOT NULL
           OR NEW.cache_write_input_tokens IS NOT NULL
           OR NEW.cache_write_5m_input_tokens IS NOT NULL
           OR NEW.cache_write_1h_input_tokens IS NOT NULL
           OR NEW.reasoning_output_tokens IS NOT NULL
           OR NEW.total_tokens IS NOT NULL
           OR NEW.billable_input_tokens IS NOT NULL
           OR NEW.billable_output_tokens IS NOT NULL
           OR NEW.server_tool_request_count IS NOT NULL
           OR NEW.provider_service_tier IS NOT NULL
           OR NEW.provider_inference_geo IS NOT NULL THEN
            RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'finance attempt native payload is invalid';
        END IF;
        RETURN NEW;
    END IF;
    BEGIN
        v_native := NEW.native_payload_json::JSONB;
    EXCEPTION WHEN others THEN
        RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'finance attempt native payload is invalid';
    END;
    IF jsonb_typeof(v_native) IS DISTINCT FROM 'object'
       OR v_native = '{{}}'::JSONB
       OR encode(sha256(convert_to(NEW.native_payload_json, 'UTF8')), 'hex')
          IS DISTINCT FROM NEW.native_payload_digest THEN
        RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'finance attempt native payload is invalid';
    END IF;

    IF NEW.provider_schema_id = 'openai.responses.usage.v1' THEN
        v_numeric_values := ARRAY[
            v_native -> 'input_tokens',
            v_native #> '{{input_tokens_details,cached_tokens}}',
            v_native #> '{{input_tokens_details,cache_write_tokens}}',
            v_native -> 'output_tokens',
            v_native #> '{{output_tokens_details,reasoning_tokens}}',
            v_native -> 'total_tokens'
        ];
        IF v_native ? 'service_tier' AND (
            jsonb_typeof(v_native -> 'service_tier') IS DISTINCT FROM 'string'
            OR (v_native ->> 'service_tier') NOT IN ('default', 'flex', 'priority', 'ultrafast')
        ) THEN
            RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'finance attempt native payload is invalid';
        END IF;
        IF (v_native ? 'input_tokens_details' AND v_native -> 'input_tokens_details' = '{{}}'::JSONB)
           OR (v_native ? 'output_tokens_details' AND v_native -> 'output_tokens_details' = '{{}}'::JSONB) THEN
            RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'finance attempt native payload is invalid';
        END IF;
        v_native_canonical := json_strip_nulls(json_build_object(
            'input_tokens', v_native -> 'input_tokens',
            'input_tokens_details', CASE WHEN v_native ? 'input_tokens_details' THEN
                json_strip_nulls(json_build_object(
                    'cache_write_tokens', v_native #> '{{input_tokens_details,cache_write_tokens}}',
                    'cached_tokens', v_native #> '{{input_tokens_details,cached_tokens}}'
                )) ELSE NULL END,
            'output_tokens', v_native -> 'output_tokens',
            'output_tokens_details', CASE WHEN v_native ? 'output_tokens_details' THEN
                json_strip_nulls(json_build_object(
                    'reasoning_tokens', v_native #> '{{output_tokens_details,reasoning_tokens}}'
                )) ELSE NULL END,
            'service_tier', v_native -> 'service_tier',
            'total_tokens', v_native -> 'total_tokens'
        ))::TEXT;
    ELSIF NEW.provider_schema_id = 'anthropic.messages.usage.v1' THEN
        v_numeric_values := ARRAY[
            v_native -> 'input_tokens',
            v_native -> 'cache_read_input_tokens',
            v_native -> 'cache_creation_input_tokens',
            v_native #> '{{cache_creation,ephemeral_5m_input_tokens}}',
            v_native #> '{{cache_creation,ephemeral_1h_input_tokens}}',
            v_native -> 'output_tokens',
            v_native #> '{{output_tokens_details,thinking_tokens}}',
            v_native #> '{{server_tool_use,web_search_requests}}',
            v_native #> '{{server_tool_use,web_fetch_requests}}'
        ];
        IF v_native ? 'service_tier' AND (
            jsonb_typeof(v_native -> 'service_tier') IS DISTINCT FROM 'string'
            OR (v_native ->> 'service_tier') NOT IN ('standard', 'priority', 'batch')
        ) THEN
            RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'finance attempt native payload is invalid';
        END IF;
        IF v_native ? 'inference_geo' AND (
            jsonb_typeof(v_native -> 'inference_geo') IS DISTINCT FROM 'string'
            OR octet_length(v_native ->> 'inference_geo') NOT BETWEEN 1 AND 128
            OR (v_native ->> 'inference_geo') !~ '^[A-Za-z0-9][A-Za-z0-9._:-]*$'
        ) THEN
            RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'finance attempt native payload is invalid';
        END IF;
        IF (v_native ? 'cache_creation' AND v_native -> 'cache_creation' = '{{}}'::JSONB)
           OR (v_native ? 'output_tokens_details' AND v_native -> 'output_tokens_details' = '{{}}'::JSONB)
           OR (v_native ? 'server_tool_use' AND v_native -> 'server_tool_use' = '{{}}'::JSONB) THEN
            RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'finance attempt native payload is invalid';
        END IF;
        v_native_canonical := json_strip_nulls(json_build_object(
            'cache_creation', CASE WHEN v_native ? 'cache_creation' THEN
                json_strip_nulls(json_build_object(
                    'ephemeral_1h_input_tokens', v_native #> '{{cache_creation,ephemeral_1h_input_tokens}}',
                    'ephemeral_5m_input_tokens', v_native #> '{{cache_creation,ephemeral_5m_input_tokens}}'
                )) ELSE NULL END,
            'cache_creation_input_tokens', v_native -> 'cache_creation_input_tokens',
            'cache_read_input_tokens', v_native -> 'cache_read_input_tokens',
            'inference_geo', v_native -> 'inference_geo',
            'input_tokens', v_native -> 'input_tokens',
            'output_tokens', v_native -> 'output_tokens',
            'output_tokens_details', CASE WHEN v_native ? 'output_tokens_details' THEN
                json_strip_nulls(json_build_object(
                    'thinking_tokens', v_native #> '{{output_tokens_details,thinking_tokens}}'
                )) ELSE NULL END,
            'server_tool_use', CASE WHEN v_native ? 'server_tool_use' THEN
                json_strip_nulls(json_build_object(
                    'web_fetch_requests', v_native #> '{{server_tool_use,web_fetch_requests}}',
                    'web_search_requests', v_native #> '{{server_tool_use,web_search_requests}}'
                )) ELSE NULL END,
            'service_tier', v_native -> 'service_tier'
        ))::TEXT;
    ELSE
        RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'finance attempt native payload is invalid';
    END IF;

    FOREACH v_numeric IN ARRAY v_numeric_values LOOP
        IF v_numeric IS NOT NULL THEN
            IF jsonb_typeof(v_numeric) IS DISTINCT FROM 'number'
               OR v_numeric::TEXT !~ '^(0|[1-9][0-9]*)$' THEN
                RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'finance attempt native payload is invalid';
            END IF;
            IF (v_numeric::TEXT)::NUMERIC > 9223372036854775807 THEN
                RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'finance attempt native payload is invalid';
            END IF;
        END IF;
    END LOOP;
    IF NEW.native_payload_json IS DISTINCT FROM v_native_canonical THEN
        RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'finance attempt native payload is invalid';
    END IF;

    IF NEW.billable_input_tokens IS NOT NULL OR NEW.billable_output_tokens IS NOT NULL THEN
        RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'finance attempt normalized usage is invalid';
    END IF;
    IF NEW.provider_schema_id = 'openai.responses.usage.v1' THEN
        v_provider_input := (v_native ->> 'input_tokens')::BIGINT;
        v_provider_output := (v_native ->> 'output_tokens')::BIGINT;
        v_cache_read := (v_native #>> '{{input_tokens_details,cached_tokens}}')::BIGINT;
        v_cache_write := (v_native #>> '{{input_tokens_details,cache_write_tokens}}')::BIGINT;
        v_reasoning := (v_native #>> '{{output_tokens_details,reasoning_tokens}}')::BIGINT;
        v_total := (v_native ->> 'total_tokens')::BIGINT;
        v_required_present := v_provider_input IS NOT NULL
            AND v_provider_output IS NOT NULL AND v_total IS NOT NULL;
        IF v_total IS NOT NULL AND v_provider_input IS NOT NULL AND v_provider_output IS NOT NULL
           AND v_total::NUMERIC <> v_provider_input::NUMERIC + v_provider_output::NUMERIC THEN
            v_total := NULL;
            v_intrinsically_invalid := TRUE;
        END IF;
        IF v_provider_input IS NOT NULL AND v_cache_read IS NOT NULL
           AND v_cache_read > v_provider_input THEN
            v_cache_read := NULL;
            v_intrinsically_invalid := TRUE;
        END IF;
        IF v_provider_input IS NOT NULL AND v_cache_write IS NOT NULL
           AND v_cache_write > v_provider_input THEN
            v_cache_write := NULL;
            v_intrinsically_invalid := TRUE;
        END IF;
        IF ROW(
            NEW.provider_input_tokens, NEW.provider_output_tokens,
            NEW.cache_read_input_tokens, NEW.cache_write_input_tokens,
            NEW.cache_write_5m_input_tokens, NEW.cache_write_1h_input_tokens,
            NEW.reasoning_output_tokens, NEW.total_tokens,
            NEW.server_tool_request_count, NEW.provider_service_tier,
            NEW.provider_inference_geo
        ) IS DISTINCT FROM ROW(
            v_provider_input, v_provider_output, v_cache_read, v_cache_write,
            NULL::BIGINT, NULL::BIGINT, v_reasoning, v_total, NULL::BIGINT,
            v_native ->> 'service_tier', NULL::TEXT
        ) THEN
            RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'finance attempt normalized usage is invalid';
        END IF;
    ELSE
        v_provider_input := (v_native ->> 'input_tokens')::BIGINT;
        v_provider_output := (v_native ->> 'output_tokens')::BIGINT;
        v_cache_read := (v_native ->> 'cache_read_input_tokens')::BIGINT;
        v_cache_write := (v_native ->> 'cache_creation_input_tokens')::BIGINT;
        v_cache_write_5m := (v_native #>> '{{cache_creation,ephemeral_5m_input_tokens}}')::BIGINT;
        v_cache_write_1h := (v_native #>> '{{cache_creation,ephemeral_1h_input_tokens}}')::BIGINT;
        v_reasoning := (v_native #>> '{{output_tokens_details,thinking_tokens}}')::BIGINT;
        v_search_requests := (v_native #>> '{{server_tool_use,web_search_requests}}')::BIGINT;
        v_fetch_requests := (v_native #>> '{{server_tool_use,web_fetch_requests}}')::BIGINT;
        v_required_present := v_provider_input IS NOT NULL AND v_provider_output IS NOT NULL;
        IF v_provider_input IS NOT NULL AND v_cache_read IS NOT NULL
           AND v_cache_write IS NOT NULL AND v_provider_output IS NOT NULL THEN
            IF v_provider_input::NUMERIC + v_cache_read::NUMERIC
               + v_cache_write::NUMERIC + v_provider_output::NUMERIC <= 9223372036854775807 THEN
                v_total := (
                    v_provider_input::NUMERIC + v_cache_read::NUMERIC
                    + v_cache_write::NUMERIC + v_provider_output::NUMERIC
                )::BIGINT;
            ELSE
                v_total := NULL;
                v_intrinsically_invalid := TRUE;
            END IF;
        ELSE
            v_total := NULL;
        END IF;
        IF v_cache_write_5m IS NOT NULL AND v_cache_write_1h IS NOT NULL
           AND v_cache_write IS NOT NULL THEN
            IF v_cache_write_5m::NUMERIC + v_cache_write_1h::NUMERIC
               <> v_cache_write::NUMERIC THEN
                v_cache_write_5m := NULL;
                v_cache_write_1h := NULL;
                v_intrinsically_invalid := TRUE;
            END IF;
        ELSIF v_cache_write IS NOT NULL THEN
            IF v_cache_write_5m IS NOT NULL AND v_cache_write_5m > v_cache_write THEN
                v_cache_write_5m := NULL;
                v_intrinsically_invalid := TRUE;
            END IF;
            IF v_cache_write_1h IS NOT NULL AND v_cache_write_1h > v_cache_write THEN
                v_cache_write_1h := NULL;
                v_intrinsically_invalid := TRUE;
            END IF;
        END IF;
        IF v_search_requests IS NOT NULL AND v_fetch_requests IS NOT NULL THEN
            IF v_search_requests::NUMERIC + v_fetch_requests::NUMERIC <= 9223372036854775807 THEN
                v_server_tools := (v_search_requests::NUMERIC + v_fetch_requests::NUMERIC)::BIGINT;
            ELSE
                v_server_tools := NULL;
                v_intrinsically_invalid := TRUE;
            END IF;
        ELSE
            v_server_tools := NULL;
        END IF;
        IF ROW(
            NEW.provider_input_tokens, NEW.provider_output_tokens,
            NEW.cache_read_input_tokens, NEW.cache_write_input_tokens,
            NEW.cache_write_5m_input_tokens, NEW.cache_write_1h_input_tokens,
            NEW.reasoning_output_tokens, NEW.total_tokens,
            NEW.server_tool_request_count, NEW.provider_service_tier,
            NEW.provider_inference_geo
        ) IS DISTINCT FROM ROW(
            v_provider_input, v_provider_output, v_cache_read, v_cache_write,
            v_cache_write_5m, v_cache_write_1h, v_reasoning, v_total,
            v_server_tools, v_native ->> 'service_tier', v_native ->> 'inference_geo'
        ) THEN
            RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'finance attempt normalized usage is invalid';
        END IF;
    END IF;
    IF (NEW.observation_state = 'complete'
        AND (NOT v_required_present OR v_intrinsically_invalid))
       OR (NEW.observation_reason_code = 'provider_usage_incomplete'
           AND (v_required_present OR v_intrinsically_invalid)) THEN
        RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'finance attempt observation is invalid';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER gateway_finance_attempt_evidence_consistency
BEFORE INSERT ON {schema}.gateway_finance_attempt_evidence
FOR EACH ROW EXECUTE FUNCTION {schema}.enforce_finance_attempt_evidence_consistency();

CREATE TRIGGER gateway_finance_attempt_evidence_immutable
BEFORE UPDATE OR DELETE OR TRUNCATE ON {schema}.gateway_finance_attempt_evidence
FOR EACH STATEMENT EXECUTE FUNCTION {schema}.portfolio_reject_mutation();

ALTER TABLE {schema}.gateway_finance_attempt_evidence ENABLE ROW LEVEL SECURITY;
ALTER TABLE {schema}.gateway_finance_attempt_evidence FORCE ROW LEVEL SECURITY;
CREATE POLICY gateway_finance_attempt_evidence_tenant
    ON {schema}.gateway_finance_attempt_evidence
    USING (organization_id = current_setting('hormuz.organization_id', true))
    WITH CHECK (organization_id = current_setting('hormuz.organization_id', true));
REVOKE ALL ON {schema}.gateway_finance_attempt_evidence FROM PUBLIC;
GRANT SELECT, INSERT ON {schema}.gateway_finance_attempt_evidence TO {runtime_role};

-- Preserve the strict custody source union and add exactly one internal
-- finance source. Version-2 entries remain incapable of carrying arbitrary
-- JSON through the gateway runtime's audit-table INSERT privilege.
CREATE OR REPLACE FUNCTION {schema}.enforce_custody_audit_chain_entry_insert()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
    v_source_json TEXT;
BEGIN
    IF NEW.entry_schema_version = 1 THEN
        RETURN NEW;
    END IF;
    IF NEW.entry_schema_version <> 2
       OR NEW.event_id IS DISTINCT FROM NEW.source_event_id THEN
        RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'audit chain entry is invalid';
    END IF;
    IF NOT (
        (NEW.source_schema_id = 'hormuz.custody-control-event' AND NEW.source_schema_version = 1)
        OR (NEW.source_schema_id = 'hormuz.custody-execution-attempt' AND NEW.source_schema_version = 2)
        OR (NEW.source_schema_id = 'hormuz.custody-execution-event' AND NEW.source_schema_version = 1)
        OR (NEW.source_schema_id = 'hormuz.custody-lifecycle-event' AND NEW.source_schema_version = 1)
        OR (NEW.source_schema_id = 'hormuz.custody-envelope-attestation' AND NEW.source_schema_version = 1)
        OR (NEW.source_schema_id = 'hormuz.custody-deletion-event' AND NEW.source_schema_version = 1)
        OR (NEW.source_schema_id = 'hormuz.finance-attempt-evidence' AND NEW.source_schema_version = 1)
    ) THEN
        RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'audit source schema is unsupported';
    END IF;
    IF NEW.source_schema_id = 'hormuz.custody-control-event' AND NEW.source_schema_version = 1 THEN
        SELECT evidence_json INTO v_source_json
          FROM {schema}.custody_control_events
         WHERE organization_id = NEW.organization_id AND event_id = NEW.source_event_id;
    ELSIF NEW.source_schema_id = 'hormuz.custody-execution-attempt' AND NEW.source_schema_version = 2 THEN
        SELECT evidence_json INTO v_source_json
          FROM {schema}.custody_execution_attempts
         WHERE organization_id = NEW.organization_id AND execution_id = NEW.source_event_id;
    ELSIF NEW.source_schema_id = 'hormuz.custody-execution-event' AND NEW.source_schema_version = 1 THEN
        SELECT evidence_json INTO v_source_json
          FROM {schema}.custody_execution_events
         WHERE organization_id = NEW.organization_id
           AND execution_id || ':' || sequence::TEXT = NEW.source_event_id;
    ELSIF NEW.source_schema_id = 'hormuz.custody-lifecycle-event' AND NEW.source_schema_version = 1 THEN
        SELECT evidence_json INTO v_source_json
          FROM {schema}.custody_lifecycle_events
         WHERE organization_id = NEW.organization_id AND lifecycle_event_id = NEW.source_event_id;
    ELSIF NEW.source_schema_id = 'hormuz.custody-envelope-attestation' AND NEW.source_schema_version = 1 THEN
        SELECT evidence_json INTO v_source_json
          FROM {schema}.custody_envelope_attestations
         WHERE organization_id = NEW.organization_id
           AND execution_id || ':' || attestation_kind = NEW.source_event_id;
    ELSIF NEW.source_schema_id = 'hormuz.custody-deletion-event' AND NEW.source_schema_version = 1 THEN
        SELECT evidence_json INTO v_source_json
          FROM {schema}.custody_deletion_events
         WHERE organization_id = NEW.organization_id AND deletion_event_id = NEW.source_event_id;
    ELSIF NEW.source_schema_id = 'hormuz.finance-attempt-evidence' AND NEW.source_schema_version = 1 THEN
        SELECT evidence_json INTO v_source_json
          FROM {schema}.gateway_finance_attempt_evidence
         WHERE organization_id = NEW.organization_id AND evidence_event_id = NEW.source_event_id;
    END IF;
    IF v_source_json IS NULL OR NEW.event_json IS DISTINCT FROM v_source_json THEN
        RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'audit source evidence mismatch';
    END IF;
    RETURN NEW;
END;
$$;
