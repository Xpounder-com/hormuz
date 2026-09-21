# v1.3 candidate source/wheel identity preflight

Issue [#214](https://github.com/Xpounder-com/hormuz/issues/214) requires an
exact-artifact transition matrix before the v1.3.0 tag. This preflight supplies
one static input check for that later matrix. It can run without a tag, provider
credentials, a database, or a deployment.

At the final candidate commit, build the source archive and wheel from that
checkout, then run:

```sh
python -m build
python tools/verify_v13_candidate_artifacts.py \
  --repo-root . \
  --commit "$(git rev-parse HEAD)" \
  --source dist/hormuz-1.3.0.tar.gz \
  --wheel dist/hormuz-1.3.0-py3-none-any.whl
```

The verifier requires `1.3.0` in committed package metadata and in the built
source and wheel. It compares every committed `hormuz/` runtime file with both
artifacts, including SQL migrations and bundled wire/secret files. It also
compares the transition guides, plans, contract/wire JSON, transition tests,
verifiers and frozen v1.0.0 contract manifest with the source archive. Missing,
extra or changed selected files fail. The wheel must have the committed CLI
entry point and a complete, digest-consistent `RECORD`. The output is
content-free: exact commit, artifact SHA-256 digests, counts and bounded scope.

The current package version is `1.2.0`, so this command correctly fails with
`candidate_version_mismatch` today. A passing future result establishes only
that these particular source and wheel bytes match the named commit. It does
not establish v1.0.0/v1.2.0 upgrade behavior, SQLite or PostgreSQL recovery,
API compatibility, signed OCI or Compose execution, final-candidate acceptance,
or release eligibility. Keep #214 open until the complete exact-artifact
transition and rollback evidence is reviewed on protected main.
