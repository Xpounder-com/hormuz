# Local route admission validation

Final reviewed source: `976060881d9caa6e6aee4d52547268b7352998c6`.
Merged destination prerequisite: `1b433e9e80a5efa9176b80cbc877907e1c946780`.
The final review fixes and genuine shared proof regeneration are qualified below.
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

## Final review validation

Review reproduced and corrected three interactions through the approved workflow:

| Case | Before | After |
| --- | --- | --- |
| Local destination base ends in `/v1` | Health requested `/v1/health`, blocking ready inference | Root `/health`; local response; zero cloud calls |
| Explicit eligible second local model, accounting on/off | First local model replaced the explicit choice | Requested model and destination retained in auto/local-only |
| Explicit local model receives 429/529 | Narrowed ranking set also removed its capacity alternate | One qualified same-destination alternate; zero cloud calls |

Initial selection and optional AI Work ranking respect the explicit local alias.
The full admitted set remains authoritative for final dispatch and governed
operational fallback; an unqualified alternate remains blocked.

- Final source regression: **52 passed**.
  `python -m unittest -v tests.test_execution_destination_workflow tests.test_work_gateway tests.test_config_construction_equivalence`.
- Final installed wheel outside the checkout: **38 workflows passed**.
  `python -I -m unittest discover -s tests -t . -p test_execution_destination_workflow.py -v`.
- Isolated wheel with repository-pinned `setuptools==83.0.0`: **253 packaged files match source**.
  SHA-256: `c4410eeefb7f476ba33775b5b58e496116fe783e8bb13f6300f3d4eb90b4ce52`.
  This is a local validation artifact, not a published release.
- Secret inventory and deployment contract: passed; the inventory remains
  `4a5e1ab3145b89366a2d9b8b7b88736a45cde26157229093edbe44b51d1febcf`.
- Genuine shared evidence: **35 mechanics checks**, 13 synthetic calls;
  **12 browser checks**, 3 synthetic calls. Both receipts match all **35 source
  fingerprints**, with zero real provider/payment/external browser calls.
- Website: **81 tests passed**, default production build and export passed;
  four public/export artifact pairs match the genuine originals byte for byte.
  Turbopack requires physical project-local dependencies; a temporary dependency
  symlink was replaced with a filesystem clone before the passing default build.
- `git diff --check`: passed.

Earlier 157-test affected and 364-test broader runs remain supporting evidence
from before the final review fixes; they are not exact-final-source results.
The installed workflows above qualify the final local admission behavior, while
[shared browser evidence](../ai-work-functional/README.md) qualifies its existing
journey. It does not establish a new user interface or local model quality.

## Standards

Final independent static review found no remaining hard standards violations or
actionable baseline smells. Frozen public status/relay fixtures, finite error
discovery, shared qualification bounds and response ownership resolved the
initial findings.

## Spec

Final independent static review found no remaining actionable admission findings.
Successful continuation evidence, absolute upstream/downstream deadlines,
header-phase cleanup and fresh-schema recognition resolved the initial findings.
Final review on the source above: Standards **0 remaining**, Spec **0 remaining**.
The readiness, explicit-selection and governed-fallback findings are resolved.

## Unproven scope

Synthetic fixtures do not qualify actual llama.cpp/model quality, tokenizer
context fit, installed runtime egress, real coding-client parity, native
subscription authorization, operating systems or marketplace acceptance.
Managed-policy activation during a live request was not exercised against a live
PostgreSQL service. The final gate re-reads its current policy digest; existing
managed-policy checks remain separate. CI and technical-lead evaluation must
qualify the exact PR head before merge. CLI/plugin installation and model/runtime
acquisition remain separate slices.
