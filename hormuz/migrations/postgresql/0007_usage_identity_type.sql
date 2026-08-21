ALTER TABLE gateway_usage_events
  ADD COLUMN identity_type text NOT NULL DEFAULT 'human'
  CHECK (identity_type IN ('human', 'service_account', 'ci', 'connector'));
