# Local route admission validation

Historical admission execution source: `95488df72972678f19fea55a76e21e88f28a6888`.
Parent destination slice: `93eb480518a7f7545545a684e1613b2951864990`.
The parent test-only fixture correction was merged after execution; all 35
executed runtime/proof source fingerprints remain unchanged.
This is a partial implementation of issue #523. The
[admission guide](../../LOCAL_ROUTE_ADMISSION.md) states configuration, contracts,
privacy scope, qualification and rollback.

## Approved workflow seam

All added cases use connect, submit a coding-client request through the router,
inspect its result/status, disconnect. The configuration CLI, router, governance,
transport and temporary storage are real. Only disposable coding clients and
loopback providers stand in for external systems. Test inputs are synthetic;
there are no real provider, payment or subscription calls.

Representative observed red/green outcomes:

| Case | Before | After |
| --- | --- | --- |
| Model loading under local-only | Inference dispatched with HTTP 200 | HTTP 503; no local/cloud inference |
| Request tightens auto to local-only | Unknown request dispatched to cloud | HTTP 503; no inference |
| Work ID without successful prior response | Existing tool state dispatched as local affinity | HTTP 409; no inference |
| Fresh function parameter schema | False continuation HTTP 409 | Qualified local HTTP 200 |
| Unqualified capacity alternate | Second model dispatched, HTTP 200 | Original HTTP 429; no alternate/cloud request |
| Periodic upstream bytes | Response completed after configured deadline | Partial response closed, unknown outcome, lease released |
| Connected client stops reading | Local lease remained active after deadline | Downstream write times out; lease released |
| Client resets before headers | Public work attempt remained pending | Owned response closes; public attempt becomes unknown |

The stalled-reader case uses one finite 16 MiB synthetic response to exceed the
system's send buffer. It measures functional deadline/cleanup behavior, not load
or throughput. Other cases use small batches, at most two concurrent requests,
finite waits, explicit timeouts and owned-resource cleanup.

## Validation results

- Source workflow plus the existing redirect ownership regression: **36 passed**.
  Command: `python -m unittest tests.test_gateway.GatewayIntegrationTests.test_finance_candidate_redirect_keeps_unknown_outcome_and_budget_hold tests.test_execution_destination_workflow -v`.
- Final installed wheel, isolated from the source checkout: **35 workflow cases passed**.
  Command: `python -I -m unittest discover -s tests -p test_execution_destination_workflow.py -v`.
  All 12 changed runtime module fingerprints matched the source checkout.
- Final affected gateway/work/client/contract/construction regression set:
  **157 tests passed with 3 skips**, including every added workflow case.
  Command: `python -m unittest tests.test_execution_destination_workflow tests.test_work_gateway tests.test_gateway tests.test_client_relay tests.test_cli_runtime_ownership tests.test_contracts tests.test_config_construction_equivalence -v`.
- Earlier broader work/client/configuration/policy regression run:
  **364 tests passed with 3 skips**. This preceded the final delivery fixes;
  it is supporting evidence rather than an exact-final-source result.
- State-detector repair plus work-runtime regressions: **50 passed**.
- Parent connection-evidence fixture under the admission source: **17 passed**.
  Its credential-unavailability case now uses an actual missing startup credential
  instead of mocking the immutable owner's compatibility property.
- Isolated wheel build using pinned `setuptools==83.0.0`: passed.
- Secret inventory and deployment contract: passed. The existing secret inventory
  remains unchanged: `4a5e1ab3145b89366a2d9b8b7b88736a45cde26157229093edbe44b51d1febcf`.
- `git diff --check`: passed.

Wheel SHA-256:
`87f6e898f0f2c96b795dfa8ef4a619c1b901816e34fb146138647f52e66efa81`.
It is a local validation artifact, not a published release.

The existing [AI Work mechanics/browser evidence](../ai-work-functional/README.md)
is regenerated against the same executed source to keep its published source
fingerprints current. That journey validates existing behavior; the local route
and privacy evidence is the workflow suite above.
Regeneration passed 35 mechanics checks, 12 browser checks, 81 website tests and
the default production build. Both receipts match all 35 executed source hashes;
four public copies also match the genuine generated artifacts and exported assets
byte for byte.

## Standards

Final independent static review found no remaining hard standards violations or
actionable baseline smells. Frozen public status/relay fixtures, finite error
discovery, shared qualification bounds and response ownership resolved the
initial findings.

## Spec

Final independent static review found no remaining actionable admission findings.
Successful continuation evidence, absolute upstream/downstream deadlines,
header-phase cleanup and fresh-schema recognition resolved the initial findings.
Standards: **0 remaining**. Spec: **0 remaining**.

## Unproven scope

Synthetic fixtures do not qualify actual llama.cpp/model quality, tokenizer
context fit, installed runtime egress, real coding-client parity, native
subscription authorization, operating systems or marketplace acceptance.
Managed-policy activation during a live request was not exercised against a live
PostgreSQL service. The final gate re-reads its current policy digest; existing
managed-policy checks remain separate. CI and technical-lead evaluation must
qualify the exact PR head before merge. CLI/plugin installation and model/runtime
acquisition remain separate slices.

The shared browser/mechanics artifacts were subsequently regenerated by [connected-client lifetime qualification](../connected-client-lifetime/README.md) at source 7899f1b8de2f4865468de94247dd322f9e9d0c41 with 42 fingerprints. The preceding numbers describe the recorded admission slice, rather than the current shared artifact source.
