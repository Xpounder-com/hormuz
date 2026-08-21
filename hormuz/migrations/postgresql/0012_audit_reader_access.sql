ALTER TABLE gateway_admin_access_events
  DROP CONSTRAINT gateway_admin_access_events_action_check;

ALTER TABLE gateway_admin_access_events
  ADD CONSTRAINT gateway_admin_access_events_action_check
  CHECK (action IN ('usage.report.read', 'audit.events.read'));
