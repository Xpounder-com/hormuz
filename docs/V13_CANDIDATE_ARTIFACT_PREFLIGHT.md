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
source and wheel, and requires a clean tracked checkout. It compares every
tracked source-archive file with the exact
Git commit, including `README.md`, `LICENSE`, runtime SQL and wire files, and
the transition guides, plans, tests and verifiers. Required files cannot be
omitted. Untracked source files, archive path collisions, and changed generated
`setup.cfg` or `hormuz.egg-info` metadata fail. The source and wheel dependency
declarations must match the committed `pyproject.toml`; wheel metadata and
the CLI entry point must match the source. The wheel must have a complete,
digest-consistent `RECORD` and no encrypted entries. The output is
content-free: exact commit, artifact SHA-256 digests, counts and bounded scope.
Unsupported dependency syntax fails closed until the verifier is reviewed and
updated.

The current package version is `1.2.0`, so this command correctly fails with
`candidate_version_mismatch` today. A passing future result establishes only
that these particular source and wheel bytes match the named commit. It does
not establish v1.0.0/v1.2.0 upgrade behavior, SQLite or PostgreSQL recovery,
API compatibility, signed OCI or Compose execution, final-candidate acceptance,
or release eligibility. Keep #214 open until the complete exact-artifact
transition and rollback evidence is reviewed on protected main.
