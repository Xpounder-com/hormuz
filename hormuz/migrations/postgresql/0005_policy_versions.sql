CREATE TABLE gateway_policy_versions (
  tenant_id text NOT NULL,
  version_id text NOT NULL CHECK (
    version_id ~ '^hpv_v1_[0-9a-f]{64}$'
  ),
  projection_sha256 char(64) NOT NULL CHECK (
    projection_sha256 ~ '^[0-9a-f]{64}$'
  ),
  projection_schema text NOT NULL CHECK (
    projection_schema = 'hormuz.policy-projection.v2'
  ),
  projection_json jsonb NOT NULL,
  created_at timestamptz NOT NULL,
  created_by_actor_id text NOT NULL,
  created_by_actor_name text NOT NULL,
  change_summary_json jsonb NOT NULL,
  PRIMARY KEY (tenant_id, version_id),
  UNIQUE (tenant_id, projection_sha256),
  FOREIGN KEY (tenant_id) REFERENCES tenants (tenant_id) ON DELETE CASCADE,
  CHECK (jsonb_typeof(projection_json) = 'object'),
  CHECK (projection_json ->> 'schema' = projection_schema),
  CHECK (projection_json ->> 'organization_id' = tenant_id),
  CHECK (jsonb_typeof(change_summary_json) = 'object')
);

CREATE TABLE gateway_active_policies (
  tenant_id text NOT NULL,
  version_id text NOT NULL,
  activated_at timestamptz NOT NULL,
  activated_by_actor_id text NOT NULL,
  activated_by_actor_name text NOT NULL,
  activation_sequence bigint NOT NULL CHECK (activation_sequence > 0),
  PRIMARY KEY (tenant_id),
  FOREIGN KEY (tenant_id, version_id)
    REFERENCES gateway_policy_versions (tenant_id, version_id),
  FOREIGN KEY (tenant_id) REFERENCES tenants (tenant_id) ON DELETE CASCADE
);

CREATE TABLE gateway_policy_events (
  tenant_id text NOT NULL,
  id text NOT NULL,
  occurred_at timestamptz NOT NULL,
  decision_actor_id text NOT NULL,
  decision_actor_name text NOT NULL,
  action text NOT NULL CHECK (action IN ('staged', 'activated', 'rolled_back')),
  version_id text NOT NULL,
  prior_version_id text,
  change_summary_json jsonb NOT NULL,
  activation_sequence bigint CHECK (activation_sequence IS NULL OR activation_sequence > 0),
  PRIMARY KEY (tenant_id, id),
  FOREIGN KEY (tenant_id, version_id)
    REFERENCES gateway_policy_versions (tenant_id, version_id),
  FOREIGN KEY (tenant_id, prior_version_id)
    REFERENCES gateway_policy_versions (tenant_id, version_id),
  CHECK (jsonb_typeof(change_summary_json) = 'object'),
  CHECK (
    (action = 'staged' AND activation_sequence IS NULL) OR
    (action IN ('activated', 'rolled_back') AND activation_sequence IS NOT NULL)
  )
);
CREATE INDEX gateway_policy_event_time_idx
  ON gateway_policy_events (tenant_id, occurred_at, id);

CREATE FUNCTION reject_policy_history_mutation()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog
AS $$
BEGIN
  RAISE EXCEPTION 'policy history is immutable' USING ERRCODE = '23514';
END;
$$;

CREATE TRIGGER policy_version_immutable
BEFORE UPDATE OR DELETE ON gateway_policy_versions
FOR EACH ROW EXECUTE FUNCTION reject_policy_history_mutation();

CREATE TRIGGER policy_event_immutable
BEFORE UPDATE OR DELETE ON gateway_policy_events
FOR EACH ROW EXECUTE FUNCTION reject_policy_history_mutation();

DO $$
DECLARE
  table_name text;
BEGIN
  FOREACH table_name IN ARRAY ARRAY[
    'gateway_policy_versions',
    'gateway_active_policies',
    'gateway_policy_events'
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
