# ADR 0019: Bind native Codex conversations to owned work

- Status: Implementation slice; one bounded native protocol case qualified
- Date: 2026-10-15
- Decision owner: Product owner
- Approval: The owner requested automatic conversation work-ID binding with TDD
  at the approved connect/request/result/status/disconnect workflow seam.
- Parent specification: [#523](https://github.com/Xpounder-com/hormuz/issues/523)

Native Codex retains thread identity between requests but does not copy Hormuz's
work receipt header into its next model request. Requiring a manual per-process
work-ID override prevents automatic tool continuation. The router now associates
a validated native thread with authenticated actor-owned work, using the existing
work runtime rather than adding a client launcher, agent loop or second ledger.

The HTTP boundary compares canonical UUID `thread-id` and `session-id` headers
with their flat `client_metadata` copies. Thread and session may differ. The
thread/session copies inside optional bounded turn metadata must also agree;
duplicate JSON object names are rejected for recognized native requests. Only
the thread identifies the conversation; turn, cache, window and installation
identities cannot create alternate bindings. Caller-supplied identifiers are
association hints within an authenticated owner, not authentication or proof of
a native installation. Existing policy, redaction and route qualification still
govern execution.

`WorkRuntime` owns atomic creation and lookup in a private additive version-1
table in its existing SQLite ledger. Keys use a domain-separated HMAC with the
existing process-local key, scoped by organization, actor, client, protocol and
endpoint. No raw conversation identifier is retained in this table or exposed in
work receipts. A global limit of 1,024 bindings per runtime fails closed without
eviction. Initialization clears only associations; historical jobs, attempts and
unknown reservations remain readable.

Lookup occurs after policy and secret inspection, before route admission.
Admission-denied fresh requests do not allocate a binding. Creation occurs during
work preparation under the ledger's immediate write transaction. A recognized
unknown continuation cannot bootstrap an association, including in configured
routing mode. Automatic continuation requires prior successful response affinity
to an eligible route in the current destination generation. A stopped, canceled
or budget-limited association is not replaced with new work to escape its gates.

Manual `X-Hormuz-Work-ID` remains a compatibility path. It does not establish an
automatic association. A matching existing association is accepted; a conflicting
owned work ID is rejected, and foreign work remains inaccessible. Existing relay
headers and owned work endpoints expose the result without a new configuration
flag, command or HTTP endpoint.

Associations deliberately end with the router epoch. Restart recovery needs a
fresh conversation; the router neither adopts old history nor replays inference.
This preserves [ADR 0015](0015-execution-destination-ownership.md)'s destination
boundary instead of persisting a key that could imply restart-safe affinity.
The [version and rollback contract](../CONVERSATION_WORK_BINDING.md) retains
historical evidence and keeps downgrade explicit.

The [PR #529 qualification](../qualifications/codex-0147-llama-b11617-qwen3-4b.md)
remains the historical manual-binding baseline. This decision removes that
manual requirement for the recognized envelope; it does not retroactively
qualify automatic binding, other Codex releases, desktop clients, model quality,
subscription authentication or plugin activation.
The new [bounded native protocol case](../CONVERSATION_WORK_BINDING.md#bounded-native-protocol-qualification--october-15-2026)
records its own exact source, frozen acceptance, two fixture requests, one owned
work item and cleanup evidence; it does not perform model inference.
