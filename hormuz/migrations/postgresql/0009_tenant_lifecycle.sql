CREATE TABLE gateway_tenant_lifecycle (
  tenant_id text PRIMARY KEY,
  state text NOT NULL DEFAULT 'active' CHECK (
    state IN ('active', 'deactivated', 'purge_scheduled')
  ),
  state_version bigint NOT NULL DEFAULT 1 CHECK (state_version > 0),
  deactivated_at timestamptz,
  deactivation_reason_code text,
  purge_not_before timestamptz,
  required_export_id text,
  required_export_ciphertext_sha256 char(64),
  updated_at timestamptz NOT NULL DEFAULT statement_timestamp(),
  FOREIGN KEY (tenant_id) REFERENCES tenants (tenant_id) ON DELETE CASCADE,
  CHECK (
    deactivation_reason_code IS NULL OR (
      deactivation_reason_code ~ '^[a-z][a-z0-9_.:-]{0,127}$'
    )
  ),
  CHECK (
    (state = 'active' AND deactivated_at IS NULL AND deactivation_reason_code IS NULL
      AND purge_not_before IS NULL AND required_export_id IS NULL
      AND required_export_ciphertext_sha256 IS NULL)
    OR
    (state = 'deactivated' AND deactivated_at IS NOT NULL
      AND deactivation_reason_code IS NOT NULL AND purge_not_before IS NULL
      AND required_export_id IS NULL AND required_export_ciphertext_sha256 IS NULL)
    OR
    (state = 'purge_scheduled' AND deactivated_at IS NOT NULL
      AND deactivation_reason_code IS NOT NULL AND purge_not_before IS NOT NULL
      AND required_export_id IS NOT NULL AND required_export_ciphertext_sha256 IS NOT NULL)
  )
);

CREATE TABLE gateway_tenant_exports (
  tenant_id text NOT NULL,
  export_id text NOT NULL,
  created_at timestamptz NOT NULL,
  export_schema text NOT NULL CHECK (export_schema = 'hormuz.tenant-export.v1'),
  encryption_algorithm text NOT NULL CHECK (encryption_algorithm = 'AES-256-GCM'),
  lifecycle_state_version bigint NOT NULL CHECK (lifecycle_state_version > 0),
  payload_sha256 char(64) NOT NULL,
  ciphertext_sha256 char(64) NOT NULL,
  table_counts_json jsonb NOT NULL,
  PRIMARY KEY (tenant_id, export_id),
  UNIQUE (tenant_id, ciphertext_sha256),
  FOREIGN KEY (tenant_id) REFERENCES tenants (tenant_id) ON DELETE CASCADE,
  CHECK (export_id ~ '^tex_[a-f0-9]{32}$'),
  CHECK (payload_sha256 ~ '^[a-f0-9]{64}$'),
  CHECK (ciphertext_sha256 ~ '^[a-f0-9]{64}$'),
  CHECK (jsonb_typeof(table_counts_json) = 'object')
);
CREATE INDEX gateway_tenant_export_time_idx
  ON gateway_tenant_exports (tenant_id, created_at, export_id);

-- A tenant hard purge deletes its principals. The original session foreign
-- keys deliberately protected a principal deletion, so make the lifecycle
-- cascade explicit before the tenant deletion path becomes reachable.
ALTER TABLE gateway_session_enrollments
  DROP CONSTRAINT gateway_session_enrollments_tenant_id_actor_id_fkey,
  ADD CONSTRAINT gateway_session_enrollments_tenant_id_actor_id_fkey
    FOREIGN KEY (tenant_id, actor_id)
    REFERENCES principals (tenant_id, principal_id) ON DELETE CASCADE;
ALTER TABLE gateway_human_sessions
  DROP CONSTRAINT gateway_human_sessions_tenant_id_actor_id_fkey,
  ADD CONSTRAINT gateway_human_sessions_tenant_id_actor_id_fkey
    FOREIGN KEY (tenant_id, actor_id)
    REFERENCES principals (tenant_id, principal_id) ON DELETE CASCADE;

-- The tombstone contains only a deprovisioning receipt. It is deliberately
-- outside the tenant-owned tables so an explicit re-onboarding decision is
-- required after the tenant row and all tenant-owned records are deleted.
CREATE TABLE gateway_tenant_purge_tombstones (
  tenant_id text PRIMARY KEY,
  purged_at timestamptz NOT NULL,
  export_id text NOT NULL,
  export_ciphertext_sha256 char(64) NOT NULL,
  CHECK (tenant_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'),
  CHECK (export_id ~ '^tex_[a-f0-9]{32}$'),
  CHECK (export_ciphertext_sha256 ~ '^[a-f0-9]{64}$')
);

DO $$
DECLARE
  table_name text;
BEGIN
  FOREACH table_name IN ARRAY ARRAY[
    'gateway_tenant_lifecycle',
    'gateway_tenant_exports'
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
