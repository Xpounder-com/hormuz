CREATE TABLE gateway_usage_events (
  tenant_id text NOT NULL,
  id text NOT NULL,
  occurred_at timestamptz NOT NULL,
  actor_id text NOT NULL,
  actor_name text NOT NULL,
  team_id text NOT NULL,
  team_name text NOT NULL,
  client text NOT NULL,
  protocol text NOT NULL CHECK (protocol IN ('openai', 'anthropic')),
  requested_model text NOT NULL,
  resolved_alias text,
  upstream_model text,
  actual_model text,
  policy_action text NOT NULL,
  status text NOT NULL,
  input_tokens bigint NOT NULL DEFAULT 0 CHECK (input_tokens >= 0),
  output_tokens bigint NOT NULL DEFAULT 0 CHECK (output_tokens >= 0),
  cache_read_tokens bigint NOT NULL DEFAULT 0 CHECK (cache_read_tokens >= 0),
  cache_write_tokens bigint NOT NULL DEFAULT 0 CHECK (cache_write_tokens >= 0),
  reasoning_tokens bigint NOT NULL DEFAULT 0 CHECK (reasoning_tokens >= 0),
  billable_tokens bigint NOT NULL DEFAULT 0 CHECK (billable_tokens >= 0),
  cost_microusd bigint NOT NULL DEFAULT 0 CHECK (cost_microusd >= 0),
  cost_basis text NOT NULL DEFAULT 'not_available',
  currency text NOT NULL DEFAULT 'USD' CHECK (currency = 'USD'),
  rate_card_version text NOT NULL DEFAULT 'unversioned',
  provider_usage_json jsonb NOT NULL DEFAULT '{}'::jsonb,
  provider_request_id text,
  redaction_count bigint NOT NULL DEFAULT 0 CHECK (redaction_count >= 0),
  redaction_rules jsonb NOT NULL DEFAULT '[]'::jsonb,
  context_injection_mode text NOT NULL DEFAULT 'off',
  context_injection_outcome text NOT NULL DEFAULT 'not_evaluated',
  context_injection_reason text NOT NULL DEFAULT 'policy_off',
  context_pack_id text,
  context_record_ids_json jsonb NOT NULL DEFAULT '[]'::jsonb,
  context_policy_version text,
  context_retrieval_version text,
  context_render_version text,
  context_repository_revision text,
  context_estimated_tokens bigint NOT NULL DEFAULT 0 CHECK (context_estimated_tokens >= 0),
  context_assembly_milliseconds bigint NOT NULL DEFAULT 0 CHECK (context_assembly_milliseconds >= 0),
  context_reuse_status text NOT NULL DEFAULT 'not_applicable',
  gateway_latency_milliseconds bigint CHECK (gateway_latency_milliseconds >= 0),
  policy_latency_milliseconds bigint CHECK (policy_latency_milliseconds >= 0),
  provider_latency_milliseconds bigint CHECK (provider_latency_milliseconds >= 0),
  PRIMARY KEY (tenant_id, id),
  FOREIGN KEY (tenant_id) REFERENCES tenants (tenant_id) ON DELETE CASCADE
);
CREATE INDEX gateway_usage_tenant_occurred_idx
  ON gateway_usage_events (tenant_id, occurred_at, id);
CREATE INDEX gateway_usage_tenant_actor_idx
  ON gateway_usage_events (tenant_id, actor_id, occurred_at);
CREATE INDEX gateway_usage_tenant_team_idx
  ON gateway_usage_events (tenant_id, team_id, occurred_at);

CREATE TABLE gateway_budget_reservations (
  tenant_id text NOT NULL,
  id text NOT NULL,
  created_at timestamptz NOT NULL,
  expires_at timestamptz NOT NULL,
  actor_id text NOT NULL,
  team_id text NOT NULL,
  model_alias text,
  reserved_tokens bigint NOT NULL CHECK (reserved_tokens >= 0),
  reserved_cost_microusd bigint NOT NULL CHECK (reserved_cost_microusd >= 0),
  PRIMARY KEY (tenant_id, id),
  FOREIGN KEY (tenant_id) REFERENCES tenants (tenant_id) ON DELETE CASCADE
);
CREATE INDEX gateway_reservation_tenant_expires_idx
  ON gateway_budget_reservations (tenant_id, expires_at, id);
CREATE INDEX gateway_reservation_tenant_actor_idx
  ON gateway_budget_reservations (tenant_id, actor_id, model_alias, expires_at);
CREATE INDEX gateway_reservation_tenant_team_idx
  ON gateway_budget_reservations (tenant_id, team_id, model_alias, expires_at);

CREATE TABLE gateway_admin_access_events (
  tenant_id text NOT NULL,
  id text NOT NULL,
  occurred_at timestamptz NOT NULL,
  decision_actor_id text NOT NULL,
  decision_actor_name text NOT NULL,
  action text NOT NULL CHECK (action = 'usage.report.read'),
  group_by text NOT NULL,
  actor_filter_sha256 text,
  team_filter_sha256 text,
  window_start timestamptz NOT NULL,
  window_end timestamptz NOT NULL,
  result_count bigint NOT NULL CHECK (result_count >= 0),
  PRIMARY KEY (tenant_id, id),
  FOREIGN KEY (tenant_id) REFERENCES tenants (tenant_id) ON DELETE CASCADE
);
CREATE INDEX gateway_admin_access_tenant_time_idx
  ON gateway_admin_access_events (tenant_id, occurred_at, id);

CREATE TABLE gateway_provider_cost_imports (
  tenant_id text NOT NULL,
  id text NOT NULL,
  imported_at timestamptz NOT NULL,
  provider text NOT NULL CHECK (provider IN ('openai', 'anthropic')),
  source_sha256 text NOT NULL,
  report_start timestamptz NOT NULL,
  report_end timestamptz NOT NULL,
  page_count bigint NOT NULL CHECK (page_count > 0),
  bucket_count bigint NOT NULL CHECK (bucket_count >= 0),
  item_count bigint NOT NULL CHECK (item_count >= 0),
  PRIMARY KEY (tenant_id, id),
  UNIQUE (tenant_id, provider, source_sha256),
  FOREIGN KEY (tenant_id) REFERENCES tenants (tenant_id) ON DELETE CASCADE
);
CREATE INDEX gateway_provider_cost_import_tenant_scope_idx
  ON gateway_provider_cost_imports (tenant_id, provider, imported_at, id);

CREATE TABLE gateway_provider_cost_items (
  tenant_id text NOT NULL,
  id text NOT NULL,
  import_id text NOT NULL,
  item_ordinal bigint NOT NULL CHECK (item_ordinal >= 0),
  bucket_start timestamptz NOT NULL,
  bucket_end timestamptz NOT NULL,
  amount_usd text NOT NULL,
  currency text NOT NULL CHECK (currency = 'USD'),
  provider_scope_kind text NOT NULL CHECK (
    provider_scope_kind IN ('project', 'workspace', 'unscoped')
  ),
  provider_scope_id text,
  line_item text,
  cost_type text,
  model text,
  service_tier text,
  token_type text,
  context_window text,
  inference_geo text,
  PRIMARY KEY (tenant_id, id),
  UNIQUE (tenant_id, import_id, item_ordinal),
  FOREIGN KEY (tenant_id, import_id)
    REFERENCES gateway_provider_cost_imports (tenant_id, id) ON DELETE CASCADE
);
CREATE INDEX gateway_provider_cost_item_tenant_import_idx
  ON gateway_provider_cost_items (tenant_id, import_id, bucket_start, bucket_end);

CREATE TABLE gateway_provider_cost_sources (
  tenant_id text NOT NULL,
  id text NOT NULL,
  import_id text NOT NULL,
  observed_at timestamptz NOT NULL,
  source_kind text NOT NULL CHECK (source_kind IN ('offline_upload', 'authenticated_api')),
  api_contract text,
  query_start timestamptz,
  query_end timestamptz,
  query_scope text,
  PRIMARY KEY (tenant_id, id),
  FOREIGN KEY (tenant_id, import_id)
    REFERENCES gateway_provider_cost_imports (tenant_id, id) ON DELETE CASCADE
);
CREATE INDEX gateway_provider_cost_source_tenant_import_idx
  ON gateway_provider_cost_sources (tenant_id, import_id, source_kind, observed_at, id);

DO $$
DECLARE
  table_name text;
BEGIN
  FOREACH table_name IN ARRAY ARRAY[
    'gateway_usage_events',
    'gateway_budget_reservations',
    'gateway_admin_access_events',
    'gateway_provider_cost_imports',
    'gateway_provider_cost_items',
    'gateway_provider_cost_sources'
  ]
  LOOP
    EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', table_name);
    EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', table_name);
    EXECUTE format(
      'CREATE POLICY tenant_isolation ON %I '
      'USING (tenant_id = NULLIF(current_setting(''hormuz.tenant_id'', true), '''')) '
      'WITH CHECK (tenant_id = NULLIF(current_setting(''hormuz.tenant_id'', true), ''''))',
      table_name
    );
    EXECUTE format(
      'CREATE TRIGGER tenant_id_immutable BEFORE UPDATE ON %I '
      'FOR EACH ROW EXECUTE FUNCTION reject_tenant_id_change()',
      table_name
    );
  END LOOP;
END;
$$;
