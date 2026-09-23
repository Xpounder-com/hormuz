# v1.3 development transition matrix for #214

This checkpoint combines existing, provider-free transition cases under one
strict runner. It runs both immutable published predecessors against the
current development source **and** a wheel built from that same runtime tree.
The current tree still declares package `1.2.0`, SQLite schema 12 and
PostgreSQL schema 17. It is not an exact v1.3.0 candidate, and this matrix
does not approve any planned 13/18 migration or a production rollback.

The runner requires the published v1.0.0 source archive and custody manifest
(SHA-256 `2c3b16c1742ee76032a33f3714492a8d8515c5291d4d57520441882cd8bc5b5a`
and `85774aa45a8b30be88d1cb1a7b543222cc1396523aec31c17de07470b09d56b2`)
and the published v1.2.0 source and wheel (SHA-256
`257c99b99af891838a1a0f1ba77bcc9c6baa74fba586d99381adab9f7b1af909`
and `5519df553d4a9c330e6cf1fa822d6f8efc956e8eb5a7ac178eee01a89ffaa2bb`).
Install each predecessor from those local artifacts into its own environment.
Their existing isolated drivers verify install identity again before seeding.
The runner also compares every installed v1.0.0 runtime file with the pinned
source archive before any transition case starts, so a modified interpreter
cannot pass merely by retaining its distribution metadata.

Build the development wheel from the checkout into a temporary directory and
install it into an isolated environment. The source run byte-compares every
candidate `hormuz/` runtime file in the source tree and wheel; the wheel run
also compares the installed runtime before running any case. It refuses missing
published artifacts, digest mismatches,
missing tests and all skipped cases. The source and wheel executions must each
report nine SQLite cases, zero skips and the same wheel digest. The runner
requires a clean checkout; its `source_head` is development context, not
final-candidate acceptance. Prepare all four release files and four isolated
interpreters from the repository root:

```console
ROOT="$PWD"
ARTIFACTS=/private/tmp/hormuz-v13-transition-artifacts
mkdir -p "$ARTIFACTS"
gh release download candidate-v1.0.0-2c3b16c1742ee76032a33f3714492a8d8515c5291d4d57520441882cd8bc5b5a \
  --repo Xpounder-com/hormuz --pattern 'hormuz-1.0.0.tar.gz' \
  --pattern 'hormuz-v1.0.0-candidate-manifest.json' --dir "$ARTIFACTS"
gh release download v1.2.0 --repo Xpounder-com/hormuz \
  --pattern 'hormuz-1.2.0.tar.gz' --pattern 'hormuz-1.2.0-py3-none-any.whl' \
  --dir "$ARTIFACTS"
python3.12 -m venv "$ARTIFACTS/v1"
python3.12 -m venv "$ARTIFACTS/v12"
python3.12 -m venv "$ARTIFACTS/source"
python3.12 -m venv "$ARTIFACTS/candidate"
"$ARTIFACTS/v1/bin/python" -m pip install "$ARTIFACTS/hormuz-1.0.0.tar.gz[postgres]"
"$ARTIFACTS/v12/bin/python" -m pip install "$ARTIFACTS/hormuz-1.2.0-py3-none-any.whl[postgres]"
"$ARTIFACTS/source/bin/python" -m pip install -e '.[postgres]' build==1.3.0
"$ARTIFACTS/source/bin/python" -m build --wheel \
  --outdir "$ARTIFACTS/candidate-wheel"
"$ARTIFACTS/candidate/bin/python" -m pip install \
  "$ARTIFACTS/candidate-wheel/hormuz-1.2.0-py3-none-any.whl[postgres]"
```

Then run both development modes:

```console
COMMON=(--source-root "$ROOT" \
  --candidate-wheel "$ARTIFACTS/candidate-wheel/hormuz-1.2.0-py3-none-any.whl" \
  --v1-archive "$ARTIFACTS/hormuz-1.0.0.tar.gz" \
  --v1-manifest "$ARTIFACTS/hormuz-v1.0.0-candidate-manifest.json" \
  --v1-python "$ARTIFACTS/v1/bin/python" \
  --v12-source "$ARTIFACTS/hormuz-1.2.0.tar.gz" \
  --v12-wheel "$ARTIFACTS/hormuz-1.2.0-py3-none-any.whl" \
  --v12-python "$ARTIFACTS/v12/bin/python")
"$ARTIFACTS/source/bin/python" -m tools.run_v13_development_transition_matrix \
  --mode source "${COMMON[@]}"
(cd /private/tmp && "$ARTIFACTS/candidate/bin/python" -I \
  "$ROOT/tools/run_v13_development_transition_matrix.py" \
  --mode wheel "${COMMON[@]}")
```

For owned disposable PostgreSQL, set `HORMUZ_TEST_POSTGRES_DSN` and
`HORMUZ_TEST_PG_CONTAINER` to the same local test service, and add `--postgres`
to **both** invocations. This requires all nine PostgreSQL cases; missing
container backup tools or a skipped case is a refusal. No production DSN or
customer data belongs in this run. The runner does not print DSNs, rows,
provider payloads or subprocess stderr.

The v1.0.0 path proves its actual released binary refuses the real additive
schema and partial ledger, preserves an uncertain reservation and audit/usage
facts, permits a verified quiesced old-pair restore with zero later writes,
and retains post-checkpoint writes for forward recovery. The v1.2.0 path seeds
populated synthetic finance and outcome history from its exact released wheel,
replays original receipts, and tests migration transaction failure, partial
and newer-state refusal, old-pair restore, and retained forward recovery using
**test-only successor DDL** already defined in the account-binding preflight.
Those witness tables are not a v1.3 schema. The current same-version 12/17
binary can read the published v1.2.0 state; this does not promise future
v1.2.0 compatibility after actual v1.3 migrations or semantic changes.

Successful output is a source/wheel development checkpoint only. #214 still
requires an exact final-candidate matrix for complete APIs, source install,
wheel, signed OCI, single-VM Compose, SQLite, PostgreSQL, populated
policy/custody recovery, and green protected-main CI before a release tag.
