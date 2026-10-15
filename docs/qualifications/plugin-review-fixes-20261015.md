# Plugin review-fix qualification — 2026-10-15

This report accompanies the fixes on `mehrdad/plugin-capabilities-20261015`
relative to `aaaf0b6160185ce356c4520ed96b6df78fd0bb89`. It supersedes the
earlier capability report for the behavior changed here. The unrelated primary
checkout is preserved.

## Corrected behavior

- Qualification readers reject missing, malformed, duplicate, nonfinite and
  inconsistent evidence. Complete legacy v1 reports require requalification.
  New v2 reports bind authenticated requested alias, destination, generation,
  upstream model and revision, checked on each response and before/after probes.
  Recommendation rechecks the generation without making model requests.
- Bounded client-version subprocess failures return content-free JSON and exit 2.
  A hung disposable executable is terminated and reaped in the regression.
- Retained routing evidence records policy fallback and capacity failover after
  HTTP 429/529. The original failed attempt and successful fallback remain
  separate observations. Legacy evidence remains unobserved.
- Diagnosis inspects selected project, profile file and supplied routing flags
  using pinned Codex 0.147.0 precedence, project trust and ignored-key rules.
  It reports source/key comparisons without emitting values. Running-session
  routing remains unobserved. Invalid legacy selectors, malformed trust and
  unsafe paths preserve the connection diagnosis and expose a fixed error.

The expanded override coverage includes nested repositories, custom project
roots, linked-worktree trust, lexical aliases, `-C`, repeated `-c`, dedicated
model/search/OSS flags, remote mode, missing profiles and inspection limits.
Generated qualification reports must remain readable for supported long model
names. No fix grants route enrollment or modifies privacy, trust or credentials.

## Verification

- Scope/import/manifest/link verifier: passed.
- Python syntax compilation and whitespace checks: passed. There is no separate
  configured typechecking gate.
- Full source workflow suite: **179 tests passed in 265.230 seconds**.
- Thirty-five new regression methods cover model evidence (10), fallback
  evidence (5) and override diagnosis (20), including independent negative cases.
- Standards and spec reviews: all actionable findings repaired and rechecked.
- Full installed-wheel workflow suite: **179 tests passed in 263.193 seconds**
  outside the source checkout. The verifier confirmed imports from the temporary
  installation. The wheel also matches all 105 runtime Python source files.
- Wheel and source archive: built offline with the repository-pinned backend.
  Verified wheel SHA-256:
  `7593190104fb189fb25bd58c176d2ff0d9a66b4f5aedfbee3d8c56daab8a886c`.

Commands used:

```sh
.venv/bin/python tools/verify_router_scope.py
.venv/bin/python -m compileall -q hormuz tests
.venv/bin/python -m unittest discover -s tests -p 'test_*workflow.py' -f -q
.venv/bin/python -m build --wheel --no-isolation
.venv/bin/python tools/verify_router_wheel.py dist/hormuz-2.0.0a1-py3-none-any.whl
.venv/bin/python -m build --sdist --no-isolation
git diff --check
```

All tests use disposable client homes, executables, bounded loopback providers
and private temporary storage. These checks establish synthetic workflow
behavior on macOS arm64/Python 3.12.12; they do not establish live model quality,
other-platform support, provider billing, CI, publication or merge readiness.

Contract versions, migration and downgrade boundaries are documented in the
[capability contract](../PLUGIN_CAPABILITIES.md) and compatibility fixture.
