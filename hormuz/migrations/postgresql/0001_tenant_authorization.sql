CREATE TABLE tenants (
  tenant_id text PRIMARY KEY,
  display_name text NOT NULL,
  authorization_version bigint NOT NULL DEFAULT 1 CHECK (authorization_version > 0),
  created_at timestamptz NOT NULL DEFAULT statement_timestamp(),
  updated_at timestamptz NOT NULL DEFAULT statement_timestamp(),
  CHECK (
    tenant_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'
    AND length(display_name) BETWEEN 1 AND 256
  )
);

CREATE TABLE workspaces (
  tenant_id text NOT NULL,
  workspace_id text NOT NULL,
  display_name text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT statement_timestamp(),
  updated_at timestamptz NOT NULL DEFAULT statement_timestamp(),
  PRIMARY KEY (tenant_id, workspace_id),
  FOREIGN KEY (tenant_id) REFERENCES tenants (tenant_id) ON DELETE CASCADE,
  CHECK (length(workspace_id) BETWEEN 1 AND 128),
  CHECK (length(display_name) BETWEEN 1 AND 256)
);

CREATE TABLE projects (
  tenant_id text NOT NULL,
  workspace_id text NOT NULL,
  project_id text NOT NULL,
  repository_identity text,
  created_at timestamptz NOT NULL DEFAULT statement_timestamp(),
  updated_at timestamptz NOT NULL DEFAULT statement_timestamp(),
  PRIMARY KEY (tenant_id, project_id),
  FOREIGN KEY (tenant_id, workspace_id)
    REFERENCES workspaces (tenant_id, workspace_id) ON DELETE CASCADE,
  CHECK (length(project_id) BETWEEN 1 AND 128),
  CHECK (repository_identity IS NULL OR length(repository_identity) BETWEEN 1 AND 512)
);
CREATE INDEX projects_tenant_workspace_idx
  ON projects (tenant_id, workspace_id, project_id);

CREATE TABLE teams (
  tenant_id text NOT NULL,
  team_id text NOT NULL,
  display_name text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT statement_timestamp(),
  updated_at timestamptz NOT NULL DEFAULT statement_timestamp(),
  PRIMARY KEY (tenant_id, team_id),
  FOREIGN KEY (tenant_id) REFERENCES tenants (tenant_id) ON DELETE CASCADE,
  CHECK (length(team_id) BETWEEN 1 AND 128),
  CHECK (length(display_name) BETWEEN 1 AND 256)
);

CREATE TABLE principals (
  tenant_id text NOT NULL,
  principal_id text NOT NULL,
  principal_kind text NOT NULL CHECK (principal_kind IN ('human', 'workload')),
  display_name text NOT NULL,
  owner_principal_id text,
  disabled_at timestamptz,
  authorization_version bigint NOT NULL DEFAULT 1 CHECK (authorization_version > 0),
  created_at timestamptz NOT NULL DEFAULT statement_timestamp(),
  updated_at timestamptz NOT NULL DEFAULT statement_timestamp(),
  PRIMARY KEY (tenant_id, principal_id),
  FOREIGN KEY (tenant_id) REFERENCES tenants (tenant_id) ON DELETE CASCADE,
  FOREIGN KEY (tenant_id, owner_principal_id)
    REFERENCES principals (tenant_id, principal_id),
  CHECK (length(principal_id) BETWEEN 1 AND 128),
  CHECK (length(display_name) BETWEEN 1 AND 256),
  CHECK (
    (principal_kind = 'human' AND owner_principal_id IS NULL)
    OR principal_kind = 'workload'
  )
);

CREATE TABLE external_identities (
  tenant_id text NOT NULL,
  issuer text NOT NULL,
  subject text NOT NULL,
  principal_id text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT statement_timestamp(),
  PRIMARY KEY (tenant_id, issuer, subject),
  FOREIGN KEY (tenant_id, principal_id)
    REFERENCES principals (tenant_id, principal_id) ON DELETE CASCADE,
  CHECK (length(issuer) BETWEEN 1 AND 1024),
  CHECK (length(subject) BETWEEN 1 AND 512)
);
CREATE INDEX external_identities_tenant_principal_idx
  ON external_identities (tenant_id, principal_id);

CREATE TABLE roles (
  tenant_id text NOT NULL,
  role_id text NOT NULL,
  display_name text NOT NULL,
  built_in boolean NOT NULL DEFAULT false,
  created_at timestamptz NOT NULL DEFAULT statement_timestamp(),
  updated_at timestamptz NOT NULL DEFAULT statement_timestamp(),
  PRIMARY KEY (tenant_id, role_id),
  FOREIGN KEY (tenant_id) REFERENCES tenants (tenant_id) ON DELETE CASCADE,
  CHECK (length(role_id) BETWEEN 1 AND 128),
  CHECK (length(display_name) BETWEEN 1 AND 256)
);

CREATE TABLE role_capabilities (
  tenant_id text NOT NULL,
  role_id text NOT NULL,
  capability text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT statement_timestamp(),
  PRIMARY KEY (tenant_id, role_id, capability),
  FOREIGN KEY (tenant_id, role_id)
    REFERENCES roles (tenant_id, role_id) ON DELETE CASCADE,
  CHECK (capability ~ '^[a-z][a-z0-9_.:-]{0,127}$')
);

CREATE TABLE team_memberships (
  tenant_id text NOT NULL,
  membership_id text NOT NULL,
  principal_id text NOT NULL,
  team_id text NOT NULL,
  role_id text NOT NULL,
  effective_from timestamptz NOT NULL,
  effective_until timestamptz,
  authorization_version bigint NOT NULL CHECK (authorization_version > 0),
  created_at timestamptz NOT NULL DEFAULT statement_timestamp(),
  updated_at timestamptz NOT NULL DEFAULT statement_timestamp(),
  PRIMARY KEY (tenant_id, membership_id),
  FOREIGN KEY (tenant_id, principal_id)
    REFERENCES principals (tenant_id, principal_id) ON DELETE CASCADE,
  FOREIGN KEY (tenant_id, team_id)
    REFERENCES teams (tenant_id, team_id) ON DELETE CASCADE,
  FOREIGN KEY (tenant_id, role_id)
    REFERENCES roles (tenant_id, role_id),
  CHECK (length(membership_id) BETWEEN 1 AND 128),
  CHECK (effective_until IS NULL OR effective_until > effective_from)
);
CREATE INDEX team_memberships_tenant_principal_idx
  ON team_memberships (tenant_id, principal_id, effective_from, effective_until);
CREATE INDEX team_memberships_tenant_team_idx
  ON team_memberships (tenant_id, team_id, effective_from, effective_until);

CREATE FUNCTION reject_tenant_id_change() RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog
AS $$
BEGIN
  IF NEW.tenant_id IS DISTINCT FROM OLD.tenant_id THEN
    RAISE EXCEPTION 'tenant_id is immutable' USING ERRCODE = '23514';
  END IF;
  RETURN NEW;
END;
$$;

DO $$
DECLARE
  table_name text;
BEGIN
  FOREACH table_name IN ARRAY ARRAY[
    'tenants',
    'workspaces',
    'projects',
    'teams',
    'principals',
    'external_identities',
    'roles',
    'role_capabilities',
    'team_memberships'
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
