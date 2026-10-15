# ADR 0021: Generate local configuration through shared setup

- Status: Implemented setup slice; external qualification remains separate
- Date: 2026-10-15
- Decision owner: Product owner
- Approval: Implement shared local setup with TDD at the approved workflow seam, reusing existing verified engine/model files and generating configuration for CLI and plugin.
- Parent specification: [#523](https://github.com/Xpounder-com/hormuz/issues/523)

Managed lifetime required a manually assembled router configuration. Add one
`client setup codex` operation shared by terminal and skills-only callers. It
uses the existing verifier and connection owner, generates one local-only
configuration, previews the client changes, and connects. Keep discovery,
downloads and credential bootstrap separate from this existing-file slice.

A bundled version-1 setup profile retains the exact existing experimental
client/platform/artifact pins and request limits. An advanced explicit profile
is an operator qualification declaration, not automatic certification. Its
digests must come from independently trusted evidence. Matching files never
upgrades historical model-quality failures into passes.

Creation is exclusive and atomic; existing configuration must exactly match
the same intent before reuse. A bounded lock serializes callers. Repeated setup
reuses active ownership, while a disconnected profile can start a new owned
generation. Unsafe files/state and changed configuration block without
overwriting edits. Generated local evidence and assets remain after disconnect.
Connection failure/restoration continues to use the existing journal rather
than a second process owner or recovery ledger.

The new version-1 setup receipt nests existing version-2 connection receipts.
The strict version-1 profile input has a compatibility fixture. Existing
configuration and connection/work schemas are unchanged. See
[setup, contracts, limits and rollback](../SHARED_LOCAL_SETUP.md).
