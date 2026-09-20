# Synthetic GitHub outcome fixture proposals

This pack records eight **handcrafted, offline** examples reviewed against the
[GitHub webhook event reference](https://docs.github.com/en/webhooks/webhook-events-and-payloads)
on 2026-09-20. `cases.json` links each event section and separates the minimal
source-shaped `input` from its `expectation`. Its `field_mapping` entry explains
every field of the existing internal source-observation contract. All IDs,
timestamps, and commit-shaped hashes are invented; there are no repository
locators, credentials, work content, or copied live payloads.

| Case | Expectation |
| --- | --- |
| PR opened | `mapping_pending`; proposed `created` observation |
| PR closed and merged | `mapping_pending`; proposed `completed` observation |
| PR closed without merge | `mapping_pending` (closure meaning and reopening) |
| Submitted PR review | `mapping_pending` (review meaning and PR association) |
| Completed successful check run | `mapping_pending` (CI meaning and PR association) |
| Completed failed check run | `mapping_pending` (no PR association in this example) |
| PR opened without an object ID | Invalid projected observation |
| PR opened with a boolean object ID | Invalid projected observation |

All six real-event expectations remain **`mapping_pending`** until #219 accepts
their delivery and event mappings. The two nested candidate observations are
**proposals conditional on a future verified delivery**. Their source-event IDs
illustrate a distinct per-delivery slot,
but #219 must decide and prove delivery identity, event/action allowlists,
source ordering, and exact mappings. The fixture's `synthetic_server_binding`
is test-owned configuration, separate from each event body. Neither an
installation nor a repository ID in a payload grants organization or work-scope
authority. GitHub head and merge commit SHAs are not assumed to order PR
events, so all candidate ordering fields are null. A merge is not a quality
verdict; a successful check is not accepted work; a failed check is not proof of
an AI defect or poor employee performance. GitHub documents that a `check_run`
can have an empty `pull_requests` array for a fork push, which is one reason
check association remains pending.

The malformed examples intentionally contain invalid *projected* observations
so the existing validator can reject them. These fixtures do not include a
GitHub normalizer, signature verifier, webhook endpoint, or live delivery test.
The existing `SyntheticOutcomeAdapter` is a test double, not a provider adapter.

From a source checkout after the setup in [CONTRIBUTING.md](../../../../CONTRIBUTING.md):

```bash
python -m unittest -v tests.test_github_connector_fixtures
python -m unittest -v tests.test_outcome_contract tests.test_portfolio_wire_contract
git diff --check
```

After #219 accepts its #214 compatibility/security checkpoint, a real adapter
can use these examples as review inputs while adding its own authenticated
delivery, normalization, replay, ordering, and live GitHub tests. A passing
fixture/schema test does not prove any of those behaviors or close #219, #214,
or the external pilot #225.
