# ADR 0027: Rank admitted local models using personal benchmark scorecards

- Status: Accepted for implementation by product owner
- Date: 2026-10-16

Add a benchmark CLI for explicit user-supplied tasks, independent reference
checks, observable performance metrics and workload targets. Run through the
customer-local router with finite sequential requests, absolute deadlines,
no retries or model failover, and normal credential/policy/privacy controls.
Store bounded, private local scorecards containing metadata and check outcomes;
retain failed and unknown measurements. Do not collect history automatically
or let the model's confidence determine its own success.

An explicit `route_admission.scorecard` opt-in permits deterministic ranking
within the existing policy-eligible, ready, qualified local set. Require current
owner/route/profile-compatible held-out evidence, sufficient distinct inputs,
complete execution and configured targets. Match tested request/output bounds.
Missing or unsuitable evidence preserves existing admission behavior. Successful
conversation affinity remains authoritative; scores cannot migrate opaque state,
grant cloud permission, or supply missing coding qualification.

This adds a local measurement and preference layer to ADR 0016. ADR 0024's
classifier stays observational: this feature does not promote learned routing
or treat model advice as empirical capability. ADR 0018's CLI and existing
coding-client workflow remain the product interfaces. There is no new coding
app, hosted benchmark, enterprise portfolio scorecard or training pipeline.

The public test seams are the benchmark CLI and connect → coding-client request
through the router → result/status → disconnect. Routine tests use bounded
loopback model stand-ins; representative real-model runs establish quality
separately. Reports are user-owned evidence, not signed endpoint/hardware
attestation. Format, compatibility, interpretation, migration and rollback are
documented in [personal model scorecards](../MODEL_SCORECARDS.md).
