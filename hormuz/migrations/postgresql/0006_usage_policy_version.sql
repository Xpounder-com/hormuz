ALTER TABLE gateway_usage_events
  ADD COLUMN governance_policy_version text NOT NULL DEFAULT 'bootstrap-legacy-v1'
  CHECK (
    octet_length(governance_policy_version) BETWEEN 1 AND 128
    AND governance_policy_version !~ E'[\\n\\r]'
  );
