# 10: Verify the selected plugin release end to end

**What to build:** Documented plugin workflows and workflow/package verification.

**Blocked by:** 01: Diagnose a connected coding client, 02: Preview and establish a supported connection, 03: Explain an observed model request, 04: Qualify a model for one defined task envelope, 05: Recommend a suitable installed model, 06: Apply and verify local-only policy, 07: Apply and explain a budget limit, 08: Review usage and control local history, 09: Maintain or disconnect without losing user settings.

**Status:** resolved

- [x] Static scope/import/link checks and syntax compilation pass.
- [x] The complete workflow suite passes against the source checkout.
- [x] The built installed wheel passes workflow verification outside the source tree.
- [x] Standards and spec review findings are addressed, with synthetic/live evidence limits documented.

## Scope

User approved this breakdown and requested implementation. Conservative first-release choices are documented in the implementation handoff; pending Wayfinder questions must not be presented as previously resolved decisions.

## Verification

Implemented within the documented conservative scope. The source and installed-wheel suites each passed 144 workflow tests; standards and spec review findings were addressed. See the [qualification report](../../../docs/qualifications/plugin-capabilities-20261015.md) for evidence and remaining product limits.
