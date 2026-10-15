# ADR 0015: Execution destination ownership

- Status: Implemented first slice; external qualification remains separate
- Date: 2026-10-14
- Decision owner: Product owner
- Approval: The owner requested execution destination ownership first, using TDD
  at the approved connect/request/result/disconnect workflow seam.
- Parent specification: [#523](https://github.com/Xpounder-com/hormuz/issues/523)

Wire protocol no longer identifies a unique origin or credential. An explicitly
selected model route resolves through one immutable startup destination
generation containing origin, authentication, locality, model revision and
declared capabilities. Forwarding, response/status metadata, exact reuse and
observational evidence consume that owner. Legacy protocol-named upstreams
remain the default. No production reload interface is introduced.

Destination generations are opaque random values, not hashes of credential
bytes. Live evidence uses the versioned `destination-v1` fingerprint payload.
Router restart conservatively invalidates comparable route evidence and
continuation affinity; old work/cost records remain readable. A successful
response in matching job context/cache generation owns continuation affinity;
failed or pending attempts cannot advance it. Recovery starts a fresh
conversation/job. This avoids adding a persisted credential-verification key
or a second evidence ledger.

ADR 0013 still permits only one constrained capacity alternate, now explicitly
within the same destination. No ambiguity, stream or cancellation replay is
introduced. ADR 0014's unknown outcomes, observational uncertainty, policy caps
and separately opt-in exact reuse remain in force.

New destination metadata is opt-in and versioned; legacy status/relay shapes
remain version 1. Named destinations with managed custody lifecycle are rejected
until that lifecycle is qualified. See [configuration and migration](../EXECUTION_DESTINATIONS.md)
for the field-level contract and recovery/rollback conditions.

This decision implements explicit destination ownership only. Hard local-only
admission, automatic task selection, supported subscription authentication,
llama.cpp acquisition/qualification and CLI/plugin connected-client lifetime
remain later slices. It does not establish installed client, model quality,
OS, marketplace or all-network offline support.
