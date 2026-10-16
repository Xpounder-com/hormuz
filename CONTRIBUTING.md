# Contributing

Work against [the customer-local router scope](docs/ROUTER_SCOPE.md). Use an
isolated checkout and preserve unrelated dirty work. New product work must
advance the approved public workflow: connect, submit through the real router,
inspect result/status, disconnect. CLI and plugin callers share the connection
implementation. Do not add a Hormuz coding UI or duplicate the host's agent loop.

Use Python 3.11–3.14. Install the source in a virtual environment, then run:

```sh
python tools/verify_router_scope.py
python -m unittest discover -s tests -p 'test_*workflow.py'
python -m build
python tools/verify_router_wheel.py dist/hormuz-*.whl
```

Install the pinned build frontend with `python -m pip install build==1.3.0` if
needed. Workflow tests use bounded loopback providers, a disposable coding-client
executable, real routing and temporary private storage. New behavior follows
one failing workflow test, the smallest implementation, and a passing test.
Observe client output/status and controlled external traffic; do not query
private ledgers or mock internal routing collaborators.

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
