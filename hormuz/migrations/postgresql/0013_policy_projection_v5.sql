-- Policy projection v5 adds the reviewed provider-cache capability catalog.
-- Existing v2/v3/v4 snapshots remain immutable and readable.

ALTER TABLE gateway_policy_versions
  DROP CONSTRAINT gateway_policy_versions_projection_schema_check;

ALTER TABLE gateway_policy_versions
  ADD CONSTRAINT gateway_policy_versions_projection_schema_check CHECK (
    projection_schema IN (
      'hormuz.policy-projection.v2',
      'hormuz.policy-projection.v3',
      'hormuz.policy-projection.v4',
      'hormuz.policy-projection.v5'
    )
  );
