# ADR 0017: Own persistent connected-client lifetime

- Status: Implemented lifetime slice; external qualification remains separate
- Date: 2026-10-14
- Decision owner: Product owner
- Approval: The owner requested setup, disconnect and configuration restoration with TDD at the previously approved connect/request/result/status/disconnect workflow seam.
- Parent specification: [#523](https://github.com/Xpounder-com/hormuz/issues/523)

Persistent host configuration outlives a launched child client. Reusing the transient launch adapter's lifetime would duplicate setup/recovery ordering in CLI and plugin callers and give launch adapters configuration authority they do not currently own.

One deep connected-client lifetime module owns executable qualification, precise configuration staging, private journal generations, setup serialization, owned router process control, recovery and conditional restoration. CLI commands are thin adapters; a skills-only plugin can invoke those same commands. Existing transient launch behavior and printed client configuration remain distinct. No additional coding app or mandatory MCP is introduced.

The first persistent path controls Codex user defaults for the pinned CLI version. It adds a unique provider table and records only the values and metadata needed to restore its owned fields. Managed restrictions and unsafe or unsupported configuration shapes fail explicitly. The module does not import native authentication files, persist credential values or claim that user defaults override project/profile/command-line or managed settings.

Setup writes a schema-versioned private journal before activating owned configuration. Bounded file locks serialize lifecycle callers. Prepared, connected, disconnecting, restore_required and disconnected phases distinguish incomplete external writes from a live owner; restoration failures remain explicit. Rerun uses recorded intent and generation rather than blindly recreating resources. A changed setup requires explicit disconnect/reconnect, and stale-generation cleanup cannot remove a newer owner.

Restoration compares each current owned value with the installed value. Matching values restore to the recorded original; subsequent user edits remain intact and are reported. An edited provider table is retained. This avoids replaying a whole-document backup over newer choices. Configuration writes are bounded and atomic, with a comparison against the document observed before the write.

Runtime authority is a generation-owned private control socket and a kernel-held lease, not a stored PID alone. Disconnect requests owned shutdown, drains/cancels owned connections and verifies lease release. A five-second shutdown watchdog can exit only its own host. No process search, unrelated termination, inference replay or optimistic settlement is introduced. Existing unknown-work and conservative accounting ownership remain authoritative.

Lifecycle JSON and the private journal start at schema version 1. Unknown journal versions fail explicitly. The commands are additive and do not migrate the AI Work ledger, change credential formats or replace historical relay contracts. Before downgrading, use the current binary to disconnect and resolve any retained restoration conflict. Gateway changes require deliberate disconnect/reconnect; restarting still invalidates destination-generation evidence and continuation as specified by ADR 0015 and ADR 0016.

The approved public workflow observes real CLI setup, a disposable coding client's request/result, public status and disconnect against external file/process/provider fixtures. Tests cover restored settings, preserved user edits, repeat setup, interrupted setup, concurrent ownership and bounded owned-process cleanup without using private state as an outcome oracle. Corrupt private-file inputs and OS startup faults are external fixtures. Synthetic behavior establishes this contract only. POSIX primitives and a pinned terminal executable do not establish installed OS support, Codex desktop bundled-engine qualification, real llama.cpp quality, native subscription eligibility or marketplace acceptance. Claude main-model substitution, model acquisition, startup registration and native subscription adapters remain separate work.

See [persistent connection behavior and rollback](../CONNECTED_CLIENT_LIFETIME.md).
