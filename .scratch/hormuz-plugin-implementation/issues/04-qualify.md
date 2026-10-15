# 04: Qualify a model for one defined task envelope

**What to build:** Bounded tool-use, structured-output and task checks with setup-bound evidence.

**Blocked by:** None (can start immediately).

**Status:** resolved

- [x] All four bounded synthetic checks must pass: task fact, structured output, tool call and tool-result replay.
- [x] Independent failure fixtures for each requirement produce a failed report and nonzero exit.
- [x] Probes enforce local-only routing, finite requests/time/bytes, owned transport cleanup and no route enrollment.
- [x] Private reports bind supplied assets and setup identity without claiming endpoint attestation or broader coding quality.

## Scope

User approved this breakdown and requested implementation. Conservative first-release choices are documented in the implementation handoff; pending Wayfinder questions must not be presented as previously resolved decisions.

## Verification

Implemented within the documented conservative scope. The source and installed-wheel suites each passed 144 workflow tests; standards and spec review findings were addressed. See the [qualification report](../../../docs/qualifications/plugin-capabilities-20261015.md) for evidence and remaining product limits.
