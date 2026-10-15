# 05: Recommend a suitable installed model

**What to build:** Recommendations from existing assets and current task qualification.

**Blocked by:** 04: Qualify a model for one defined task envelope.

**Status:** resolved

- [x] An eligible actor/model gets an advisory result limited to a matching unexpired report’s tested envelope.
- [x] Asset changes or expiry require requalification and make no model requests.
- [x] A model file exceeding physical memory affects the recommendation; missing hardware data stays unknown.
- [x] Recommendations never authorize a route, verify general inference fit or start downloads.

## Scope

User approved this breakdown and requested implementation. Conservative first-release choices are documented in the implementation handoff; pending Wayfinder questions must not be presented as previously resolved decisions.

## Verification

Implemented within the documented conservative scope. The source and installed-wheel suites each passed 144 workflow tests; standards and spec review findings were addressed. See the [qualification report](../../../docs/qualifications/plugin-capabilities-20261015.md) for evidence and remaining product limits.
