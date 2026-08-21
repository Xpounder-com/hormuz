-- Shared, tenant-isolated SCIM directory.
--
-- Directory records are tenant-owned and protected by the same forced RLS
-- boundary as the gateway's other runtime state.  A gateway must resolve an
-- OIDC (issuer, subject) before it knows which tenant transaction to bind, so
-- the one deliberately global relation below contains only keyed HMAC tags.
-- It is never readable by the runtime role; narrowly scoped SECURITY DEFINER
-- functions permit exact-tag lookup and writes from an already-bound tenant.

CREATE TABLE gateway_directory_resources (
  tenant_id text NOT NULL,
  resource_type text NOT NULL CHECK (resource_type IN ('User', 'Group', 'Workload')),
  resource_id text NOT NULL,
  external_id text NOT NULL,
  active boolean NOT NULL,
  revision bigint NOT NULL CHECK (revision > 0),
  created_at timestamptz NOT NULL,
  updated_at timestamptz NOT NULL,
  PRIMARY KEY (tenant_id, resource_type, resource_id),
  UNIQUE (tenant_id, resource_id),
  UNIQUE (tenant_id, resource_type, external_id),
  FOREIGN KEY (tenant_id) REFERENCES tenants (tenant_id) ON DELETE CASCADE,
  CHECK (length(resource_id) BETWEEN 1 AND 128),
  CHECK (length(external_id) BETWEEN 1 AND 512)
);

CREATE TABLE gateway_directory_users (
  tenant_id text NOT NULL,
  resource_id text NOT NULL,
  issuer text NOT NULL,
  subject text NOT NULL,
  user_name text NOT NULL,
  display_name text NOT NULL,
  PRIMARY KEY (tenant_id, resource_id),
  FOREIGN KEY (tenant_id, resource_id)
    REFERENCES gateway_directory_resources (tenant_id, resource_id)
    ON DELETE CASCADE,
  CHECK (length(issuer) BETWEEN 1 AND 1024),
  CHECK (length(subject) BETWEEN 1 AND 512),
  CHECK (length(user_name) BETWEEN 1 AND 512),
  CHECK (length(display_name) BETWEEN 1 AND 256)
);

CREATE TABLE gateway_directory_groups (
  tenant_id text NOT NULL,
  resource_id text NOT NULL,
  display_name text NOT NULL,
  team_id text NOT NULL,
  team_name text NOT NULL,
  clearance text NOT NULL CHECK (clearance IN ('public', 'internal', 'confidential', 'restricted')),
  allowed_clients_json jsonb NOT NULL,
  capabilities_json jsonb NOT NULL,
  PRIMARY KEY (tenant_id, resource_id),
  FOREIGN KEY (tenant_id, resource_id)
    REFERENCES gateway_directory_resources (tenant_id, resource_id)
    ON DELETE CASCADE,
  CHECK (length(display_name) BETWEEN 1 AND 256),
  CHECK (length(team_id) BETWEEN 1 AND 128),
  CHECK (length(team_name) BETWEEN 1 AND 256),
  CHECK (jsonb_typeof(allowed_clients_json) = 'array'),
  CHECK (jsonb_typeof(capabilities_json) = 'array')
);

CREATE TABLE gateway_directory_group_memberships (
  tenant_id text NOT NULL,
  group_id text NOT NULL,
  user_id text NOT NULL,
  created_at timestamptz NOT NULL,
  PRIMARY KEY (tenant_id, group_id, user_id),
  FOREIGN KEY (tenant_id, group_id)
    REFERENCES gateway_directory_resources (tenant_id, resource_id)
    ON DELETE CASCADE,
  FOREIGN KEY (tenant_id, user_id)
    REFERENCES gateway_directory_resources (tenant_id, resource_id)
    ON DELETE CASCADE
);

CREATE TABLE gateway_directory_workloads (
  tenant_id text NOT NULL,
  resource_id text NOT NULL,
  issuer text NOT NULL,
  subject text NOT NULL,
  display_name text NOT NULL,
  identity_type text NOT NULL CHECK (identity_type IN ('service_account', 'ci', 'connector')),
  team_id text NOT NULL,
  team_name text NOT NULL,
  clearance text NOT NULL CHECK (clearance IN ('public', 'internal', 'confidential', 'restricted')),
  allowed_clients_json jsonb NOT NULL,
  capabilities_json jsonb NOT NULL,
  PRIMARY KEY (tenant_id, resource_id),
  FOREIGN KEY (tenant_id, resource_id)
    REFERENCES gateway_directory_resources (tenant_id, resource_id)
    ON DELETE CASCADE,
  CHECK (length(issuer) BETWEEN 1 AND 1024),
  CHECK (length(subject) BETWEEN 1 AND 512),
  CHECK (length(display_name) BETWEEN 1 AND 256),
  CHECK (length(team_id) BETWEEN 1 AND 128),
  CHECK (length(team_name) BETWEEN 1 AND 256),
  CHECK (jsonb_typeof(allowed_clients_json) = 'array'),
  CHECK (jsonb_typeof(capabilities_json) = 'array')
);

-- Dynamic SCIM records have their own effective projection.  This keeps the
-- deployment-owned desired-state projection owner-controlled while letting a
-- runtime process apply a validated SCIM mutation atomically.
CREATE TABLE gateway_directory_principal_projections (
  tenant_id text NOT NULL,
  principal_id text NOT NULL,
  projection_sha256 char(64) NOT NULL,
  actor_name text NOT NULL,
  team_id text NOT NULL,
  team_name text NOT NULL,
  clearance text NOT NULL CHECK (clearance IN ('public', 'internal', 'confidential', 'restricted')),
  allowed_clients_json jsonb NOT NULL,
  capabilities_json jsonb NOT NULL,
  applied_at timestamptz NOT NULL,
  PRIMARY KEY (tenant_id, principal_id),
  FOREIGN KEY (tenant_id, principal_id)
    REFERENCES principals (tenant_id, principal_id) ON DELETE CASCADE,
  CHECK (projection_sha256 ~ '^[a-f0-9]{64}$'),
  CHECK (length(actor_name) BETWEEN 1 AND 256),
  CHECK (length(team_id) BETWEEN 1 AND 128),
  CHECK (length(team_name) BETWEEN 1 AND 256),
  CHECK (jsonb_typeof(allowed_clients_json) = 'array'),
  CHECK (jsonb_typeof(capabilities_json) = 'array')
);

CREATE TABLE gateway_directory_events (
  tenant_id text NOT NULL,
  id text NOT NULL,
  occurred_at timestamptz NOT NULL,
  decision_actor_id text NOT NULL,
  decision_actor_name text NOT NULL,
  action text NOT NULL CHECK (action IN ('created', 'updated', 'deactivated')),
  resource_type text NOT NULL CHECK (resource_type IN ('User', 'Group', 'Workload')),
  resource_id text NOT NULL,
  target_actor_id text NOT NULL,
  prior_revision bigint,
  revision bigint NOT NULL CHECK (revision > 0),
  PRIMARY KEY (tenant_id, id),
  FOREIGN KEY (tenant_id) REFERENCES tenants (tenant_id) ON DELETE CASCADE,
  CHECK (length(id) BETWEEN 1 AND 128),
  CHECK (length(decision_actor_id) BETWEEN 1 AND 128),
  CHECK (length(decision_actor_name) BETWEEN 1 AND 256),
  CHECK (length(resource_id) BETWEEN 1 AND 128),
  CHECK (length(target_actor_id) BETWEEN 1 AND 128),
  CHECK (prior_revision IS NULL OR prior_revision > 0)
);

CREATE INDEX gateway_directory_group_memberships_user_idx
  ON gateway_directory_group_memberships (tenant_id, user_id, group_id);
CREATE INDEX gateway_directory_events_time_idx
  ON gateway_directory_events (tenant_id, occurred_at, id);

-- The global index deliberately contains no raw issuer or subject.  The
-- 32-byte HMAC tags are opaque without the gateway's routing secret.  It has
-- no RLS policy because the runtime receives neither table privileges nor a
-- broad lookup function; only exact HMAC-tag functions below may access it.
CREATE TABLE gateway_directory_subject_routes (
  subject_tag bytea PRIMARY KEY CHECK (octet_length(subject_tag) = 32),
  issuer_tag bytea NOT NULL CHECK (octet_length(issuer_tag) = 32),
  tenant_id text NOT NULL,
  resource_type text NOT NULL CHECK (resource_type IN ('User', 'Workload')),
  resource_id text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT statement_timestamp(),
  updated_at timestamptz NOT NULL DEFAULT statement_timestamp(),
  FOREIGN KEY (tenant_id, resource_id)
    REFERENCES gateway_directory_resources (tenant_id, resource_id)
    ON DELETE CASCADE
);
CREATE INDEX gateway_directory_subject_routes_issuer_idx
  ON gateway_directory_subject_routes (issuer_tag, tenant_id);

CREATE FUNCTION gateway_directory_subject_route_lookup(p_subject_tag bytea)
RETURNS TABLE (tenant_id text, resource_type text, resource_id text)
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path FROM CURRENT
AS $$
  SELECT route.tenant_id, route.resource_type, route.resource_id
  FROM gateway_directory_subject_routes AS route
  WHERE route.subject_tag = p_subject_tag
$$;

CREATE FUNCTION gateway_directory_issuer_route_lookup(p_issuer_tag bytea)
RETURNS TABLE (tenant_id text)
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path FROM CURRENT
AS $$
  SELECT DISTINCT route.tenant_id
  FROM gateway_directory_subject_routes AS route
  WHERE route.issuer_tag = p_issuer_tag
  ORDER BY route.tenant_id
$$;

CREATE FUNCTION gateway_directory_subject_route_upsert(
  p_subject_tag bytea,
  p_issuer_tag bytea,
  p_resource_type text,
  p_resource_id text
) RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path FROM CURRENT
AS $$
DECLARE
  bound_tenant text;
BEGIN
  bound_tenant := NULLIF(current_setting('hormuz.tenant_id', true), '');
  IF bound_tenant IS NULL THEN
    RAISE EXCEPTION 'directory route tenant context missing' USING ERRCODE = '42501';
  END IF;
  IF octet_length(p_subject_tag) <> 32 OR octet_length(p_issuer_tag) <> 32 THEN
    RAISE EXCEPTION 'directory route tag invalid' USING ERRCODE = '22023';
  END IF;
  IF p_resource_type NOT IN ('User', 'Workload') THEN
    RAISE EXCEPTION 'directory route resource type invalid' USING ERRCODE = '22023';
  END IF;
  INSERT INTO gateway_directory_subject_routes (
    subject_tag, issuer_tag, tenant_id, resource_type, resource_id
  ) VALUES (
    p_subject_tag, p_issuer_tag, bound_tenant, p_resource_type, p_resource_id
  )
  ON CONFLICT (subject_tag) DO UPDATE SET
    issuer_tag = EXCLUDED.issuer_tag,
    updated_at = statement_timestamp()
  WHERE gateway_directory_subject_routes.tenant_id = bound_tenant
    AND gateway_directory_subject_routes.resource_type = p_resource_type
    AND gateway_directory_subject_routes.resource_id = p_resource_id;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'directory subject route conflict' USING ERRCODE = '23505';
  END IF;
END;
$$;

CREATE FUNCTION gateway_directory_subject_route_delete(
  p_subject_tag bytea,
  p_resource_type text,
  p_resource_id text
) RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path FROM CURRENT
AS $$
DECLARE
  bound_tenant text;
BEGIN
  bound_tenant := NULLIF(current_setting('hormuz.tenant_id', true), '');
  IF bound_tenant IS NULL THEN
    RAISE EXCEPTION 'directory route tenant context missing' USING ERRCODE = '42501';
  END IF;
  IF octet_length(p_subject_tag) <> 32 OR p_resource_type NOT IN ('User', 'Workload') THEN
    RAISE EXCEPTION 'directory route request invalid' USING ERRCODE = '22023';
  END IF;
  DELETE FROM gateway_directory_subject_routes
  WHERE subject_tag = p_subject_tag
    AND tenant_id = bound_tenant
    AND resource_type = p_resource_type
    AND resource_id = p_resource_id;
END;
$$;

-- The runtime role may mutate SCIM records but must not gain direct write
-- access to deployment-owned principal or external-identity tables.  This
-- function projects one already-validated directory resource into the shared
-- session authorization model under the bound tenant context.  It refuses to
-- act on an arbitrary principal and revokes active human sessions atomically
-- whenever the effective authorization binding changes.
CREATE FUNCTION gateway_directory_principal_sync(
  p_principal_id text,
  p_active boolean,
  p_actor_name text,
  p_team_id text,
  p_team_name text,
  p_clearance text,
  p_allowed_clients jsonb,
  p_capabilities jsonb,
  p_issuer text,
  p_subject text,
  p_projection_sha256 text
) RETURNS boolean
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path FROM CURRENT
AS $$
DECLARE
  bound_tenant text;
  directory_type text;
  principal_kind_value text;
  current_version bigint;
  current_disabled_at timestamptz;
  current_projection_sha256 text;
  principal_found boolean;
  changed boolean;
  active_session record;
BEGIN
  bound_tenant := NULLIF(current_setting('hormuz.tenant_id', true), '');
  IF bound_tenant IS NULL THEN
    RAISE EXCEPTION 'directory principal tenant context missing' USING ERRCODE = '42501';
  END IF;
  SELECT resource_type INTO directory_type
  FROM gateway_directory_resources
  WHERE tenant_id = bound_tenant AND resource_id = p_principal_id
    AND resource_type IN ('User', 'Workload');
  IF NOT FOUND THEN
    RAISE EXCEPTION 'directory principal is not a managed resource' USING ERRCODE = '42501';
  END IF;
  principal_kind_value := CASE directory_type
    WHEN 'User' THEN 'human'
    WHEN 'Workload' THEN 'workload'
  END;
  SELECT principal.authorization_version, principal.disabled_at, projection.projection_sha256
  INTO current_version, current_disabled_at, current_projection_sha256
  FROM principals AS principal
  LEFT JOIN gateway_directory_principal_projections AS projection
    ON projection.tenant_id = principal.tenant_id
   AND projection.principal_id = principal.principal_id
  WHERE principal.tenant_id = bound_tenant AND principal.principal_id = p_principal_id
  FOR UPDATE OF principal;
  principal_found := FOUND;

  IF NOT p_active THEN
    IF NOT principal_found THEN
      RETURN false;
    END IF;
    changed := current_disabled_at IS NULL;
    DELETE FROM gateway_directory_principal_projections
    WHERE tenant_id = bound_tenant AND principal_id = p_principal_id;
    DELETE FROM external_identities
    WHERE tenant_id = bound_tenant AND principal_id = p_principal_id;
    IF changed THEN
      UPDATE principals SET disabled_at = statement_timestamp(),
        authorization_version = authorization_version + 1,
        updated_at = statement_timestamp()
      WHERE tenant_id = bound_tenant AND principal_id = p_principal_id;
    END IF;
  ELSE
    IF p_actor_name IS NULL OR p_team_id IS NULL OR p_team_name IS NULL
      OR p_clearance NOT IN ('public', 'internal', 'confidential', 'restricted')
      OR p_issuer IS NULL OR p_subject IS NULL
      OR p_projection_sha256 !~ '^[a-f0-9]{64}$'
      OR jsonb_typeof(p_allowed_clients) <> 'array'
      OR jsonb_typeof(p_capabilities) <> 'array' THEN
      RAISE EXCEPTION 'directory principal projection invalid' USING ERRCODE = '22023';
    END IF;
    IF principal_found AND EXISTS (
      SELECT 1 FROM gateway_principal_projections AS configured
      WHERE configured.tenant_id = bound_tenant
        AND configured.principal_id = p_principal_id
    ) AND NOT EXISTS (
      SELECT 1 FROM gateway_directory_principal_projections AS dynamic
      WHERE dynamic.tenant_id = bound_tenant
        AND dynamic.principal_id = p_principal_id
    ) THEN
      RAISE EXCEPTION 'directory principal conflicts with configured identity' USING ERRCODE = '23505';
    END IF;
    changed := NOT principal_found OR current_disabled_at IS NOT NULL
      OR current_projection_sha256 IS DISTINCT FROM p_projection_sha256;
    INSERT INTO teams (tenant_id, team_id, display_name)
    VALUES (bound_tenant, p_team_id, p_team_name)
    ON CONFLICT (tenant_id, team_id) DO NOTHING;
    INSERT INTO principals (
      tenant_id, principal_id, principal_kind, display_name, authorization_version
    ) VALUES (
      bound_tenant, p_principal_id, principal_kind_value, p_actor_name, 1
    ) ON CONFLICT (tenant_id, principal_id) DO UPDATE SET
      display_name = EXCLUDED.display_name,
      disabled_at = NULL,
      authorization_version = CASE WHEN changed
        THEN principals.authorization_version + 1
        ELSE principals.authorization_version END,
      updated_at = statement_timestamp();
    INSERT INTO gateway_directory_principal_projections (
      tenant_id, principal_id, projection_sha256, actor_name, team_id, team_name,
      clearance, allowed_clients_json, capabilities_json, applied_at
    ) VALUES (
      bound_tenant, p_principal_id, p_projection_sha256, p_actor_name, p_team_id,
      p_team_name, p_clearance, p_allowed_clients, p_capabilities, statement_timestamp()
    ) ON CONFLICT (tenant_id, principal_id) DO UPDATE SET
      projection_sha256 = EXCLUDED.projection_sha256,
      actor_name = EXCLUDED.actor_name,
      team_id = EXCLUDED.team_id,
      team_name = EXCLUDED.team_name,
      clearance = EXCLUDED.clearance,
      allowed_clients_json = EXCLUDED.allowed_clients_json,
      capabilities_json = EXCLUDED.capabilities_json,
      applied_at = EXCLUDED.applied_at;
    DELETE FROM external_identities
    WHERE tenant_id = bound_tenant AND principal_id = p_principal_id;
    INSERT INTO external_identities (tenant_id, issuer, subject, principal_id)
    VALUES (bound_tenant, p_issuer, p_subject, p_principal_id);
  END IF;

  IF changed THEN
    FOR active_session IN
      SELECT id, actor_id, team_id FROM gateway_human_sessions
      WHERE tenant_id = bound_tenant AND actor_id = p_principal_id
        AND revoked_at IS NULL FOR UPDATE
    LOOP
      UPDATE gateway_human_sessions SET revoked_at = statement_timestamp()
      WHERE tenant_id = bound_tenant AND id = active_session.id AND revoked_at IS NULL;
      INSERT INTO gateway_session_security_events (
        tenant_id, id, occurred_at, session_id, event_type, target_actor_id, target_team_id
      ) VALUES (
        bound_tenant,
        'sev_' || md5(clock_timestamp()::text || active_session.id || txid_current()::text),
        statement_timestamp(), active_session.id, 'authorization_mapping_removed',
        active_session.actor_id, active_session.team_id
      );
    END LOOP;
  END IF;
  RETURN changed;
END;
$$;

-- Dynamic projections take precedence for their own principals.  The view is
-- security invoker so every read remains subject to tenant RLS on both tables.
CREATE VIEW gateway_effective_principal_projections
WITH (security_invoker = true)
AS
  SELECT tenant_id, principal_id, projection_sha256, actor_name, team_id,
         team_name, clearance, allowed_clients_json, capabilities_json, applied_at
  FROM gateway_directory_principal_projections
UNION ALL
  SELECT configured.tenant_id, configured.principal_id, configured.projection_sha256,
         configured.actor_name, configured.team_id, configured.team_name,
         configured.clearance, configured.allowed_clients_json,
         configured.capabilities_json, configured.applied_at
  FROM gateway_principal_projections AS configured
  WHERE NOT EXISTS (
    SELECT 1 FROM gateway_directory_principal_projections AS dynamic
    WHERE dynamic.tenant_id = configured.tenant_id
      AND dynamic.principal_id = configured.principal_id
  );

REVOKE ALL ON TABLE gateway_directory_subject_routes FROM PUBLIC;
REVOKE ALL ON FUNCTION gateway_directory_subject_route_lookup(bytea) FROM PUBLIC;
REVOKE ALL ON FUNCTION gateway_directory_issuer_route_lookup(bytea) FROM PUBLIC;
REVOKE ALL ON FUNCTION gateway_directory_subject_route_upsert(bytea, bytea, text, text) FROM PUBLIC;
REVOKE ALL ON FUNCTION gateway_directory_subject_route_delete(bytea, text, text) FROM PUBLIC;
REVOKE ALL ON FUNCTION gateway_directory_principal_sync(text, boolean, text, text, text, text, jsonb, jsonb, text, text, text) FROM PUBLIC;

DO $$
DECLARE
  table_name text;
BEGIN
  FOREACH table_name IN ARRAY ARRAY[
    'gateway_directory_resources',
    'gateway_directory_users',
    'gateway_directory_groups',
    'gateway_directory_group_memberships',
    'gateway_directory_workloads',
    'gateway_directory_principal_projections',
    'gateway_directory_events'
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
