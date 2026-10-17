# ADR 0020: Own verified llama.cpp through connected-client lifetime

- Status: Implemented managed-runtime slice; external qualification remains separate
- Date: 2026-10-15
- Decision owner: Product owner
- Approval: The owner requested managed llama.cpp lifetime with TDD at the approved connect/request/result/status/disconnect workflow seam, using existing verified files and stopping only engines Koto owns.
- Parent specification: [#523](https://github.com/Xpounder-com/hormuz/issues/523)

Connecting an existing coding client still required separately supervising its
local model server. The connected-client lifetime now owns one optional verified
llama.cpp child, so CLI and skills-only setup callers share setup,
readiness, recovery and shutdown authority. A second installer, model acquisition
service or coding application would duplicate that authority.

A strict version-1 profile names an existing runtime archive and digest,
extracted executable, model file and digest, qualified local route and bounded
startup time. Verification checks every bundled regular file and symbolic link,
including adjacent libraries; hashing only the small launcher cannot identify
the loaded runtime. The owner verifies before activation and its guardian
verifies immediately before execution. No archive extraction or network
acquisition occurs. The configured route must preserve local/no-authentication
destination ownership and its exact model revision. Managed profiles require
automatic or hard local-only admission; a configured unrelated baseline cannot
be presented as managed local readiness.

The guardian holds the actual child process handle, an owner pipe and an
inherited runtime lease. This is deliberately stronger than storing a PID or
adopting an endpoint: an occupied listener stays external, a foreign health
response cannot establish ownership, and parent loss still initiates cleanup.
Startup requires the live child's native listening marker and bounded exact
health response. The fixed offline, single-slot, bounded-context command retains
the existing pinned llama.cpp envelope. Output is drained without retaining raw
engine logs. No automatic restart or inference replay is introduced.

Disconnect conditionally restores existing owned client settings, stops only
the child created by this lifetime and waits for lease release. Bounded
termination may escalate against that same child. A still-held lease remains
pending cleanup, including after router loss; it cannot be reported as a
completed disconnect or replaced with a fresh owner. Runtime archives, model
files, external engines and coding clients remain outside shutdown authority.
Reconnection creates a new router epoch and needs fresh conversation state.

Managed public preview/status are version 2 and add bounded `local_runtime`
ownership/state. Unmanaged public version 1 remains unchanged. The existing
private connection journal becomes version 2 only for managed ownership, adding
`managed_llama: true`; exact legacy v1 compatibility remains. It stores neither
an engine PID nor credentials. Before downgrade or profile removal, the current
binary must complete disconnect and restoration. No AI Work ledger migration,
second journal or general process-control protocol is needed.

The approved public workflow tests the actual shared lifecycle and router
against external runtime/client/provider fixtures. Synthetic lifecycle evidence
does not qualify model quality, Codex desktop, operating systems, plugin
activation or cloud subscription access. Historical qualification failures stay
visible. See [profile, limits, version fixtures and rollback](../MANAGED_LLAMA_LIFETIME.md).
