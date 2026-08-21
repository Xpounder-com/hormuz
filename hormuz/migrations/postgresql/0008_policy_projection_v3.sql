ALTER TABLE gateway_policy_versions
  DROP CONSTRAINT gateway_policy_versions_projection_schema_check;

ALTER TABLE gateway_policy_versions
  ADD CONSTRAINT gateway_policy_versions_projection_schema_check CHECK (
    projection_schema IN (
      'hormuz.policy-projection.v2',
      'hormuz.policy-projection.v3'
    )
  );
