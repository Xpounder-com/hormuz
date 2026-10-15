# 09: Maintain or disconnect without losing user settings

**What to build:** Compatibility-aware restoration preserving newer edits and external resources.

**Blocked by:** None (can start immediately).

**Status:** resolved

- [x] The focused maintenance workflow checks a selected target’s compatibility before reconnecting.
- [x] Disconnect restores owned configuration while retaining newer user edits and external resources.
- [x] Pending shutdown/recovery stays pending; the workflow does not install runtime or client updates.

## Scope

User approved this breakdown and requested implementation. Conservative first-release choices are documented in the implementation handoff; pending Wayfinder questions must not be presented as previously resolved decisions.

## Verification

Implemented within the documented conservative scope. The source and installed-wheel suites each passed 144 workflow tests; standards and spec review findings were addressed. See the [qualification report](../../../docs/qualifications/plugin-capabilities-20261015.md) for evidence and remaining product limits.
