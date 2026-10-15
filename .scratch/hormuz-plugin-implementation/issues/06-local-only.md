# 06: Apply and verify local-only policy

**What to build:** Previewed policy configuration activated through the shared connection owner.

**Blocked by:** None (can start immediately).

**Status:** resolved

- [x] Preview validates a proposed local-only configuration without publishing output or changing the source/client.
- [x] Staging writes a new private file beside the source and refuses overwrites.
- [x] Activation uses the existing disconnect/preview/connect workflow and never transfers history consent.
- [x] The staged policy demonstrably blocks unsupported work without cloud fallback.

## Scope

User approved this breakdown and requested implementation. Conservative first-release choices are documented in the implementation handoff; pending Wayfinder questions must not be presented as previously resolved decisions.

## Verification

Implemented within the documented conservative scope. The source and installed-wheel suites each passed 144 workflow tests; standards and spec review findings were addressed. See the [qualification report](../../../docs/qualifications/plugin-capabilities-20261015.md) for evidence and remaining product limits.
