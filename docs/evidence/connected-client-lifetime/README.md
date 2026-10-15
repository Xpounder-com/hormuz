# Connected-client lifetime qualification

This qualifies the schema 1 setup/request/status/disconnect contract using synthetic
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

18 workflows pass on Python 3.12.12. They cover restored defaults and pre-existing
settings, concurrent setup/reuse, added provider preferences, copied and multiline
instruction text, version drift, changed setup intent, read-only preview,
interrupted setup and delayed old-host fencing, corrupt/future journals, managed
configuration, policy denial, stale receipts, invalid post-setup configuration and provider-table shape,
idle/in-flight disconnect and external-provider preservation. RED preceded each
new behavior; later cases also qualify shared restoration/cancellation behavior.
The final installed wheel also passed all 18 cases outside the checkout. All 255
packaged files (216 Python modules) match source bytes; the installed secret
inventory passed with 85 reviewed environment-read coordinates. See the
[versioned qualification record](qualification-v1.json) for exact source and wheel
fingerprints.

Affected regression command:

```sh
python -m unittest tests.test_connected_client_lifetime_workflow tests.test_cli tests.test_cli_client_ownership tests.test_cli_runtime_ownership tests.test_client_relay tests.test_personal_optimizer tests.test_execution_destination_workflow tests.test_work_transport tests.test_portfolio_intelligence_contract tests.test_deployment_contract -v
```

256 tests passed, 3 skipped: the opt-in real-client checks (context Codex 0.147.0, Personal Codex 0.148.0 and
Aider 0.86.2) remain unqualified. The preceding admission CI fixture corrections change no
production source: current-manifest negative cases, schema declaration affinity
expectation and the intentionally stalled client's bounded close behavior.

Browser/mechanics evidence remains separate from connected-client acceptance.
Its source fingerprint includes lifetime/CLI sources so changes are visible, but
the browser workflow does not exercise persistent host setup. Re-run the genuine
proof generators after freezing those sources; never relabel old receipt hashes.
See [user behavior and rollback](../../CONNECTED_CLIENT_LIFETIME.md) and
[ADR0017](../../decisions/0017-connected-client-lifetime.md).

Final qualified source is 7899f1b8de2f4865468de94247dd322f9e9d0c41. A 105-test frozen-source regression run passed before the final named-environment-read inventory follow-up; after that follow-up all 18 workflow cases passed in source and installed-wheel runs, and 18 secret-inventory checks passed. The schema 1 inventory digest is bb174ab45861b7a7b74da13966bc09e56f840128e58a3b0d1f0837b94b31e637. No secret purpose or custody mode was added.

Genuine shared mechanics/browser regeneration passed 35 checks with 13 synthetic provider calls and 12 browser checks with 3 calls, respectively. Both receipts match all 42 source fingerprints; they make zero real-provider/payment calls. The browser journey does not substitute for the persistent setup tests. Final static Standards and Spec reviews have zero remaining actionable findings after the documented fixes.

Final compatibility checks passed 95 cases on the qualified source. Website tests passed 81 cases; the default production build and export verification passed. Four public/export artifact pairs match the generated originals byte for byte.
