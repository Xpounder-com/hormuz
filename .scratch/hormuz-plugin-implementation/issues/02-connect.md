# 02: Preview and establish a supported connection

**What to build:** Plugin preview, authorized connection, and verified status through the shared owner.

**Blocked by:** None (can start immediately).

**Status:** resolved

- [x] The focused plugin workflow uses the existing preview, connect and status operations without a second configuration owner.
- [x] Preview leaves client settings unchanged; a successful connection reports the owned generation and routing scope.
- [x] Existing version checks and explicit prerequisite failures remain enforced.

## Scope

User approved this breakdown and requested implementation. Conservative first-release choices are documented in the implementation handoff; pending Wayfinder questions must not be presented as previously resolved decisions.

## Verification

Implemented within the documented conservative scope. The source and installed-wheel suites each passed 144 workflow tests; standards and spec review findings were addressed. See the [qualification report](../../../docs/qualifications/plugin-capabilities-20261015.md) for evidence and remaining product limits.
