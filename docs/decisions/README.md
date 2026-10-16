# Architecture decisions

[ADR 0018](0018-customer-local-router-scope.md) records the current owner-approved
product scope: a local router, CLI and shared skills-only plugin setup.

[ADR 0022](0022-opt-in-device-local-history.md) adds separately consented,
encrypted device-local history for review, deletion, export and spending
insights, preserving content-free accounting and the CLI/plugin scope.

Current routing protections are in [ADR 0013](0013-policy-bounded-provider-failover.md),
[0014](0014-customer-local-ai-work-and-exact-reuse.md),
[0015](0015-execution-destination-ownership.md), [0016](0016-local-route-admission.md)
and [0017](0017-connected-client-lifetime.md). Follow [implementation order](../ROUTER_SCOPE.md).

[ADR 0023](0023-managed-local-caller-credential.md) adds private local caller
credentials and command-backed client authentication to shared setup.

[ADR 0024](0024-shadow-local-model-routing.md) records local model recommendations
against existing attempts while retaining policy and conversation affinity.

[ADR 0027](0027-native-codex-catalog.md) separates native ChatGPT credentials
from gateway/API credentials for opt-in model discovery.
[ADR 0028](0028-native-codex-responses.md) extends that boundary with separately
opted-in HTTP/SSE inference; actual account/client qualification remains separate.

Earlier records remain historical evidence. Their enterprise/UI product scope
is superseded by ADR 0018; retaining a record does not reactivate its product or
acceptance gates. Do not rewrite accepted rationale; supersede it with a new
owner-approved decision. [Operational cleanup](../OPERATIONS_CLEANUP.md) remains
separate from product development.

The [first candidate selection](../CANDIDATE_SETUP.md) pins the experimental
host/client/runtime/model for #541 and prepares the #537 campaign. It retains
ADR 0025's coding gate and records remaining native/cloud/plugin release gates;
candidate selection is not a coding qualification or epic scope reduction.
