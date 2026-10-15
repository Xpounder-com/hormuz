# Connected-client lifetime qualification

This qualifies the schema1 setup/request/status/disconnect contract using synthetic
coding-client executables, loopback providers, temporary files and an OS-startup
fault fixture. It does not qualify a real Codex installation, desktop app,
operating-system support row, model quality, subscription authorization or plugin
marketplace acceptance.

The approved seam is connect -> submit coding-client request through the real
router -> inspect result/status -> disconnect. No private state is used as an
outcome oracle. Corrupt journal input, an interrupted setup process, client
configuration edits and disposable provider behavior are external fault fixtures.
The active user configuration and native authentication caches are untouched.

```sh
python -m unittest tests.test_connected_client_lifetime_workflow -v
```

18 workflows pass on Python3.12.12. They cover restored defaults and pre-existing
settings, concurrent setup/reuse, added provider preferences, copied and multiline
instruction text, version drift, changed setup intent, read-only preview,
interrupted setup and delayed old-host fencing, corrupt/future journals, managed
configuration, policy denial, stale receipts, invalid post-setup configuration and provider-table shape,
idle/in-flight disconnect and external-provider preservation. RED preceded each
new behavior; later cases also qualify shared restoration/cancellation behavior.
The installed-wheel run and source fingerprint record are recorded after final
review.

Affected regression command:

```sh
python -m unittest tests.test_connected_client_lifetime_workflow tests.test_cli tests.test_cli_client_ownership tests.test_cli_runtime_ownership tests.test_client_relay tests.test_personal_optimizer tests.test_execution_destination_workflow tests.test_work_transport tests.test_portfolio_intelligence_contract tests.test_deployment_contract -v
```

256 tests passed,3 skipped: the inherited optional OS-keyring/real-client checks
remain unqualified. The preceding admission CI fixture corrections change no
production source: current-manifest negative cases, schema declaration affinity
expectation and the intentionally stalled client's bounded close behavior.

Browser/mechanics evidence remains separate from connected-client acceptance.
Its source fingerprint includes lifetime/CLI sources so changes are visible, but
the browser workflow does not exercise persistent host setup. Re-run the genuine
proof generators after freezing those sources; never relabel old receipt hashes.
See [user behavior and rollback](../../CONNECTED_CLIENT_LIFETIME.md) and
[ADR0017](../../decisions/0017-connected-client-lifetime.md).
