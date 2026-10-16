# PR 548 review follow-ups

Origin: the owner's request to review, fix and merge
[PR 548](https://github.com/Xpounder-com/hormuz/pull/548). Its automatic review
arrived after merge; all five findings must be corrected and independently
reviewed before the follow-up merges. Baseline: `f57246781c470e4c913c77287e8f6a92e3e25579`.

- Reject non-POSIX acquisition before platform-specific imports or filesystem
  primitives, using the existing fixed `platform_unsupported` response.
- Use one finite size-aware checksum policy for acquisition and managed runtime
  verification. Preserve bytes on timeout; only a completed mismatch may reset
  an owned partial. Retain file mutation checks and the separate archive bound.
  Include verification in both parent startup wait allowances.
- Lock before generation inspection through publication. Read-only operations
  open existing locks without creation. Active download/extraction returns
  `busy`; unknown or abandoned state remains preserved and blocked.
- Add only the independently observed exact Hugging Face CDN redirect host.
  Keep artifact pins and hashes; preserve earlier digest-bound generations.
- Fresh ready receipts report zero remaining storage and current free space.
  Existing version-1 receipts remain compatible and unchanged on reuse.

Use public CLI workflows with synthetic artifacts, real locks and external
file/clock stand-ins; no model downloads, inference or internal decision mocks.
Keep contracts at version 1 and preserve shared setup, plugin workflows,
coding-evidence admission and client-adapter work proceeding in parallel.
