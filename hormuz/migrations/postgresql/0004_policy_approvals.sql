CREATE TABLE gateway_policy_projections (
  tenant_id text NOT NULL,
  projection_sha256 char(64) NOT NULL,
  projection_json jsonb NOT NULL,
  applied_at timestamptz NOT NULL,
  PRIMARY KEY (tenant_id),
  FOREIGN KEY (tenant_id) REFERENCES tenants (tenant_id) ON DELETE CASCADE,
  CHECK (projection_sha256 ~ '^[0-9a-f]{64}$'),
  CHECK (jsonb_typeof(projection_json) = 'object')
);

CREATE TABLE gateway_secret_events (
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
  routed_model text,
  action text NOT NULL CHECK (
    action IN ('detected', 'redacted', 'denied', 'approval_required', 'approved')
  ),
  detection_count bigint NOT NULL CHECK (detection_count >= 0),
  redaction_count bigint NOT NULL CHECK (redaction_count >= 0),
  rules_json jsonb NOT NULL,
  event_type text NOT NULL CHECK (event_type IN ('security.secret', 'security.dlp')),
  policy_version text NOT NULL,
  findings_json jsonb NOT NULL,
  PRIMARY KEY (tenant_id, id),
  FOREIGN KEY (tenant_id) REFERENCES tenants (tenant_id) ON DELETE CASCADE,
  CHECK (jsonb_typeof(rules_json) = 'array'),
  CHECK (jsonb_typeof(findings_json) = 'array')
);
CREATE INDEX gateway_secret_event_time_idx
  ON gateway_secret_events (tenant_id, occurred_at, id);
CREATE INDEX gateway_secret_event_actor_idx
  ON gateway_secret_events (tenant_id, actor_id, occurred_at, id);
CREATE INDEX gateway_secret_event_team_idx
  ON gateway_secret_events (tenant_id, team_id, occurred_at, id);

CREATE TABLE gateway_dlp_approval_requests (
  tenant_id text NOT NULL,
  id text NOT NULL,
  created_at timestamptz NOT NULL,
  updated_at timestamptz NOT NULL,
  expires_at timestamptz NOT NULL,
  actor_id text NOT NULL,
  actor_name text NOT NULL,
  team_id text NOT NULL,
  team_name text NOT NULL,
  client text NOT NULL,
  protocol text NOT NULL CHECK (protocol IN ('openai', 'anthropic')),
  requested_model text NOT NULL,
  routed_model text NOT NULL,
  policy_version text NOT NULL,
  payload_fingerprint text NOT NULL CHECK (
    payload_fingerprint ~ '^hdf_v1_[0-9a-f]{64}$'
  ),
  rules_json jsonb NOT NULL,
  detection_count bigint NOT NULL CHECK (detection_count > 0),
  status text NOT NULL CHECK (status IN ('pending', 'approved', 'consumed', 'expired')),
  approved_by_actor_id text,
  approved_by_actor_name text,
  approved_at timestamptz,
  consumed_at timestamptz,
  PRIMARY KEY (tenant_id, id),
  FOREIGN KEY (tenant_id) REFERENCES tenants (tenant_id) ON DELETE CASCADE,
  CHECK (jsonb_typeof(rules_json) = 'array'),
  CHECK (expires_at > created_at),
  CHECK (
    (approved_by_actor_id IS NULL) = (approved_by_actor_name IS NULL)
  ),
  CHECK (
    status IN ('pending', 'expired') OR
    (approved_by_actor_id IS NOT NULL AND approved_at IS NOT NULL)
  ),
  CHECK (status <> 'consumed' OR consumed_at IS NOT NULL)
);
CREATE INDEX gateway_dlp_approval_binding_idx
  ON gateway_dlp_approval_requests (
    tenant_id, actor_id, client, protocol, requested_model, routed_model,
    policy_version, payload_fingerprint, status, expires_at
  );
CREATE INDEX gateway_dlp_approval_expiry_idx
  ON gateway_dlp_approval_requests (tenant_id, status, expires_at);

CREATE TABLE gateway_dlp_approval_events (
  tenant_id text NOT NULL,
  id text NOT NULL,
  occurred_at timestamptz NOT NULL,
  request_id text NOT NULL,
  actor_id text NOT NULL,
  actor_name text NOT NULL,
  team_id text NOT NULL,
  team_name text NOT NULL,
  decision_actor_id text,
  decision_actor_name text,
  client text NOT NULL,
  protocol text NOT NULL CHECK (protocol IN ('openai', 'anthropic')),
  requested_model text NOT NULL,
  routed_model text NOT NULL,
  actual_model text,
  policy_version text NOT NULL,
  rules_json jsonb NOT NULL,
  action text NOT NULL CHECK (action IN ('requested', 'approved', 'consumed', 'model_mismatch')),
  PRIMARY KEY (tenant_id, id),
  FOREIGN KEY (tenant_id, request_id)
    REFERENCES gateway_dlp_approval_requests (tenant_id, id) ON DELETE CASCADE,
  CHECK (jsonb_typeof(rules_json) = 'array'),
  CHECK ((decision_actor_id IS NULL) = (decision_actor_name IS NULL)),
  CHECK (action <> 'model_mismatch' OR actual_model IS NOT NULL)
);
CREATE INDEX gateway_dlp_approval_event_time_idx
  ON gateway_dlp_approval_events (tenant_id, occurred_at, id);
CREATE INDEX gateway_dlp_approval_event_actor_idx
  ON gateway_dlp_approval_events (tenant_id, actor_id, occurred_at, id);
CREATE INDEX gateway_dlp_approval_event_team_idx
  ON gateway_dlp_approval_events (tenant_id, team_id, occurred_at, id);

DO $$
DECLARE
  table_name text;
BEGIN
  FOREACH table_name IN ARRAY ARRAY[
    'gateway_policy_projections',
    'gateway_secret_events',
    'gateway_dlp_approval_requests',
    'gateway_dlp_approval_events'
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
