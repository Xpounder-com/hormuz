# Connected-client lifetime qualification

Qualified combined source: `85266e687a411a099a092a2377d2917c9f1fff96`.
This includes the reviewed routing fixes and named lifecycle contracts. It is a
partial implementation of issue #523, which remains open.

The approved seam is connect -> submit a coding-client request through the real
router -> inspect result/status -> disconnect. Only disposable coding-client
executables, providers, files and OS startup faults are fixtures. Private setup
state is not an outcome oracle; corrupt/interrupted journal inputs are external
fault fixtures. The active user configuration and authentication caches are
untouched.

## Final review corrections

| Case | Before | After |
| --- | --- | --- |
| Public status, preview and errors | Missing canonical discovery/identifiers | Three named strict v1 contracts and finite error discovery |
| Earlier v1 interrupted setup journal | Identifier completion not specified | Exact earlier-v1 compatibility read and ordinary recovery; unknown ID/version rejected |
| Oversized selected model in preview | Traceback, exit 1 | Finite content-free lifecycle error, exit 2; no activation or inference; repair succeeds |
| Persistent setup plus automatic admission | Composition unverified | Qualified request reaches local; unknown request tightened to local-only blocks with zero cloud calls; disconnect restores original provider |

Four contract/recovery slices and the preview-bound correction each had a
failing approved workflow before implementation. The composition case verifies
existing combined behavior. Independent final Standards and Spec reviews found
zero remaining actionable findings.

## Final validation

- Source workflows plus affected CLI, contract, configuration, deployment and
  secret-inventory regressions: **189 passed**, zero skips. This includes all
  **24 lifecycle** and **38 routing** workflows.
- Outside the checkout, the installed wheel independently passed all **24
  lifecycle** and **38 routing** workflows. All **256 packaged files**, including
  **217 Python modules**, match both source and installed bytes.
- Wheel build used repository-pinned `setuptools==83.0.0`. SHA-256:
  `10bf09da8e87770519a1730f3489f32c6c912abfcaaa8b7be76d257ae34ac806`.
  This is a local validation artifact, not a published release.
- Source and installed secret inventories pass with **85 reviewed environment
  reads**. The schema 1 digest remains
  `bb174ab45861b7a7b74da13966bc09e56f840128e58a3b0d1f0837b94b31e637`.
  No secret purpose or custody mode was added by these final fixes.
- Genuine shared regeneration passed **35 mechanics checks** with 13 synthetic
  calls and **12 browser checks** with 3 synthetic calls. Both receipts match
  all **43 source fingerprints**. Zero real provider/payment/external browser
  calls. This browser journey does not exercise persistent setup.
- **81 website tests**, default production build, export verification and
  `git diff --check` pass. Four public/export pairs match genuine originals.

```sh
python -m unittest -v tests.test_connected_client_lifetime_workflow tests.test_execution_destination_workflow tests.test_contracts tests.test_cli tests.test_cli_client_ownership tests.test_cli_runtime_ownership tests.test_portfolio_intelligence_contract tests.test_config_construction_equivalence tests.test_deployment_contract tests.test_secret_inventory
# In the disposable installed-test directory, using the isolated wheel Python:
python -I -m unittest discover -s tests -t . -p test_connected_client_lifetime_workflow.py -v
python -I -m unittest discover -s tests -t . -p test_execution_destination_workflow.py -v
```

Earlier 256-, 105- and 95-case runs are supporting historical evidence, not the
exact-final-source result. The [qualification record](qualification-v1.json)
binds current source/wheel fingerprints and preserves earlier supporting runs.
See [behavior and rollback](../../CONNECTED_CLIENT_LIFETIME.md) and
[ADR 0017](../../decisions/0017-connected-client-lifetime.md).

This is synthetic qualification of the pinned Codex CLI 0.147.0 interface using
POSIX primitives. It does not qualify real Codex/Claude installations, native
or desktop bundled engines, OS support, actual managed-account behavior, model
quality, native subscription authorization or marketplace acceptance. No coding
UI, model/runtime acquisition, startup registration or mandatory MCP is added.
Local-only governs inference paths reaching Hormuz. Exact-head CI and the normal
protected merge remain separate gates.
