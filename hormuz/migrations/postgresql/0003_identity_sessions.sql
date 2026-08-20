CREATE TABLE gateway_identity_projections (
  tenant_id text NOT NULL,
  projection_sha256 char(64) NOT NULL,
  applied_at timestamptz NOT NULL,
  PRIMARY KEY (tenant_id),
  FOREIGN KEY (tenant_id) REFERENCES tenants (tenant_id) ON DELETE CASCADE,
  CHECK (projection_sha256 ~ '^[0-9a-f]{64}$')
);

CREATE TABLE gateway_principal_projections (
  tenant_id text NOT NULL,
  principal_id text NOT NULL,
  projection_sha256 char(64) NOT NULL,
  actor_name text NOT NULL,
  team_id text NOT NULL,
  team_name text NOT NULL,
  clearance text NOT NULL CHECK (
    clearance IN ('public', 'internal', 'confidential', 'restricted')
  ),
  allowed_clients_json jsonb NOT NULL,
  capabilities_json jsonb NOT NULL,
  applied_at timestamptz NOT NULL,
  PRIMARY KEY (tenant_id, principal_id),
  FOREIGN KEY (tenant_id, principal_id)
    REFERENCES principals (tenant_id, principal_id) ON DELETE CASCADE,
  CHECK (projection_sha256 ~ '^[0-9a-f]{64}$'),
  CHECK (jsonb_typeof(allowed_clients_json) = 'array'),
  CHECK (jsonb_typeof(capabilities_json) = 'array')
);

CREATE TABLE gateway_session_enrollments (
  tenant_id text NOT NULL,
  id text NOT NULL,
  secret_hash bytea,
  issuer text NOT NULL,
  client_name text NOT NULL CHECK (client_name IN ('codex', 'claude-code')),
  status text NOT NULL CHECK (
    status IN ('pending', 'authorizing', 'exchanging', 'authorized', 'redeemed', 'failed')
  ),
  state_hash bytea,
  browser_cookie_hash bytea,
  encrypted_flow bytea,
  subject text,
  actor_id text,
  team_id text,
  clearance text,
  authorization_version bigint,
  created_at timestamptz NOT NULL,
  expires_at timestamptz NOT NULL,
  authorization_started_at timestamptz,
  authorized_at timestamptz,
  redeemed_at timestamptz,
  PRIMARY KEY (tenant_id, id),
  UNIQUE (tenant_id, state_hash),
  FOREIGN KEY (tenant_id) REFERENCES tenants (tenant_id) ON DELETE CASCADE,
  FOREIGN KEY (tenant_id, actor_id)
    REFERENCES principals (tenant_id, principal_id),
  CHECK (authorization_version IS NULL OR authorization_version > 0),
  CHECK (expires_at > created_at)
);
CREATE INDEX gateway_session_enrollment_expiry_idx
  ON gateway_session_enrollments (tenant_id, status, expires_at);

CREATE TABLE gateway_human_sessions (
  tenant_id text NOT NULL,
  id text NOT NULL,
  issuer text NOT NULL,
  subject text NOT NULL,
  client_name text NOT NULL CHECK (client_name IN ('codex', 'claude-code')),
  access_hash bytea NOT NULL,
  refresh_hash bytea NOT NULL,
  access_expires_at timestamptz NOT NULL,
  absolute_expires_at timestamptz NOT NULL,
  generation bigint NOT NULL CHECK (generation >= 0),
  created_at timestamptz NOT NULL,
  refreshed_at timestamptz NOT NULL,
  actor_id text NOT NULL,
  team_id text NOT NULL,
  clearance text NOT NULL CHECK (
    clearance IN ('public', 'internal', 'confidential', 'restricted')
  ),
  authorization_version bigint NOT NULL CHECK (authorization_version > 0),
  revoked_at timestamptz,
  PRIMARY KEY (tenant_id, id),
  UNIQUE (tenant_id, access_hash),
  UNIQUE (tenant_id, refresh_hash),
  FOREIGN KEY (tenant_id, actor_id)
    REFERENCES principals (tenant_id, principal_id),
  CHECK (absolute_expires_at > created_at),
  CHECK (access_expires_at <= absolute_expires_at)
);
CREATE INDEX gateway_human_session_subject_idx
  ON gateway_human_sessions (tenant_id, issuer, subject, revoked_at);
CREATE INDEX gateway_human_session_admin_idx
  ON gateway_human_sessions (
    tenant_id, revoked_at, actor_id, team_id, created_at, id
  );

CREATE TABLE gateway_consumed_refresh_credentials (
  tenant_id text NOT NULL,
  credential_hash bytea NOT NULL,
  session_id text NOT NULL,
  consumed_at timestamptz NOT NULL,
  expires_at timestamptz NOT NULL,
  PRIMARY KEY (tenant_id, credential_hash),
  FOREIGN KEY (tenant_id, session_id)
    REFERENCES gateway_human_sessions (tenant_id, id) ON DELETE CASCADE,
  CHECK (expires_at > consumed_at)
);
CREATE INDEX gateway_consumed_refresh_expiry_idx
  ON gateway_consumed_refresh_credentials (tenant_id, expires_at);

CREATE TABLE gateway_session_security_events (
  tenant_id text NOT NULL,
  id text NOT NULL,
  occurred_at timestamptz NOT NULL,
  session_id text NOT NULL,
  event_type text NOT NULL CHECK (
    event_type IN (
      'refresh_replay', 'logout', 'authorization_mapping_removed',
      'admin_revocation'
    )
  ),
  target_actor_id text NOT NULL,
  target_team_id text NOT NULL,
  decision_actor_id text,
  decision_scope text CHECK (
    decision_scope IS NULL OR decision_scope IN ('session', 'actor', 'team', 'organization')
  ),
  reason_code text CHECK (
    reason_code IS NULL OR reason_code IN (
      'access_change', 'employment_change', 'security_incident', 'administrative'
    )
  ),
  PRIMARY KEY (tenant_id, id),
  FOREIGN KEY (tenant_id, session_id)
    REFERENCES gateway_human_sessions (tenant_id, id) ON DELETE CASCADE
);
CREATE INDEX gateway_session_security_admin_idx
  ON gateway_session_security_events (
    tenant_id, event_type, target_actor_id, target_team_id, occurred_at, id
  );

DO $$
DECLARE
  table_name text;
BEGIN
  FOREACH table_name IN ARRAY ARRAY[
    'gateway_identity_projections',
    'gateway_principal_projections',
    'gateway_session_enrollments',
    'gateway_human_sessions',
    'gateway_consumed_refresh_credentials',
    'gateway_session_security_events'
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
