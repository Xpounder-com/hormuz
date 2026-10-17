# Contributing

Work against [the customer-local router scope](docs/ROUTER_SCOPE.md). Use an
isolated checkout and preserve unrelated dirty work. New product work must
advance the approved public workflow: connect, submit through the real router,
inspect result/status, disconnect. CLI and plugin callers share the connection
implementation. Do not add a Koto coding UI or duplicate the host's agent loop.

Use Python 3.11–3.14. Install the source in a virtual environment. During
implementation, run the fast tests and the affected workflow module or methods:

```sh
python tools/verify_router_scope.py
python -m unittest discover -s tests -p 'test_*fast.py' -v
python -m unittest tests.test_model_benchmark_workflow -v
```

Choose the workflow module for the behavior being changed. For final
verification, build one candidate and run both layers against its installed
wheel outside the source checkout:

```sh
python -m build
python tools/verify_router_wheel.py dist/koto_router-*.whl
```

Install the pinned build frontend with `python -m pip install build==1.3.0` if
needed. CI builds the distributions once, then tests the same wheel outside the
source checkout on Python 3.11–3.14, with at most four test jobs in flight. The
source scope check runs in the build job, and `CI / required` requires the build
and both test layers on every Python version to pass. The wheel verifier reports
the 20 slowest tests in each layer on Python 3.12 and newer, including fixture
setup and cleanup; Python 3.11 runs both layers without that newer reporting
option.

## Choosing a test boundary

Use `test_*fast.py` for deterministic comparison, validation, and contract rules
through existing public module functions such as `check_answer`,
`validate_suite`, and `validate_contract`. Use literal expected results and
named table cases. Fast tests must not launch subprocesses, servers, model
downloads, or real providers, and must not inspect private helpers.

Use `test_*workflow.py` when a change crosses a CLI, HTTP, authentication,
process, persistence, or configuration boundary. Workflow tests use bounded
loopback providers, a disposable coding-client executable, real routing and
temporary private storage. Preserve integration coverage for credential and
destination isolation, local-only enforcement, streaming, continuation affinity,
cancellation, capacity release, deadlines, recovery, consent, and conditional
restoration of user settings. Observe client output/status and controlled
external traffic; do not query private ledgers or mock internal routing
collaborators.

New behavior follows a failing test at the narrowest public boundary that
protects the behavior, the smallest implementation, and a passing test. Keep
representative workflow checks proving that validation results reach the caller;
put additional pure input variants in fast tables instead of starting another
router for each one. Moving a validation table must preserve every distinct
input and expected result. Test count alone is not a quality target.

Remove a duplicate only after identifying the surviving test that exercises the
same condition, public path, and observable effects. Move any unique assertions
into that test first. Similar tests for different credentials, endpoints,
protocols, lifecycle stages, or failure paths are not automatically duplicates.

Test-only HTTP/TLS servers use an explicit bounded shutdown poll interval and
owned threads with `shutdown`, `server_close`, and `join` cleanup. Do not shorten
production deadlines, remove cancellation assertions, add wakeup requests, or
introduce busy retry loops to make tests pass faster.

Runtime and plugin releases follow the [versioning policy](docs/VERSIONING.md).
Run `python tools/version.py check` to validate their canonical sources and
mirrors. Preview a release change with
`python tools/version.py bump runtime prerelease --dry-run` or
`python tools/version.py bump plugin patch --dry-run`, choosing the operation
for the intended compatibility change. Omit `--dry-run` only to apply that
reviewed metadata change. CI and test-only PRs do not automatically bump a
release; schema versions, client pins and historical evidence stay independent.

Keep queues, requests, processes and retries finite. Default to at most eight
outbound HTTP requests in flight, three retries with backoff, explicit timeouts
and cleanup in finally-equivalent paths. Routine validation does not call real
providers, download models, run load tests or use real credentials.

Public or durable contract changes require a documented version, compatibility
fixture, migration rule and rollback boundary. Retain conservative reservations,
unknown outcomes, credential isolation, redaction, continuation affinity and
conditional restoration. Synthetic replies do not prove model quality,
subscription eligibility, OS support or marketplace acceptance.

Review documented standards and the originating spec against the exact commit.
Use a draft PR until checks and review are complete. Do not merge or change live
repository protections as a side effect of deleting a workflow. See the
[required-check cutover](docs/MIGRATION_2.md).
Merge also requires technical-lead sign-off against the exact PR head.

Report defects using synthetic inputs through the [issue tracker](https://github.com/mehrdadxzaker/hormuz/issues).
Use [SECURITY.md](SECURITY.md) for private vulnerability reporting.
