# v1.3 candidate source/wheel identity preflight

This preflight proves that the v1.3.0 source archive and wheel contain the exact
committed package. It can run without a tag, provider credential, database, or
deployment. Broader transition and portfolio evidence tracked in
[issue #214](https://github.com/Xpounder-com/hormuz/issues/214) remains separate
from publishing the Personal Optimizer release.

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
tracked source-archive file with the exact Git commit, including every file
declared by the committed `MANIFEST.in`, `README.md`, `LICENSE`, runtime SQL and
wire files, and transition guides, plans, tests and verifiers. Required files
cannot be omitted. Untracked source files, archive path collisions, excess
members or bytes, and changed generated `setup.cfg` or `hormuz.egg-info`
metadata fail. The source and wheel dependency, author, classifier and other
declared metadata must match the committed `pyproject.toml`; wheel metadata and
the CLI entry point must match the source. The wheel must have a supported
`Wheel-Version`, a complete digest-consistent `RECORD`, no file/directory
collisions, no non-regular members, exactly the six generated `.dist-info`
files from the pinned build backend, and no encrypted entries. The output is
content-free: exact commit,
SHA-256 digests of the same bounded archive bytes that were validated, counts
and scope.
Unsupported dependency syntax fails closed until the verifier is reviewed and
updated.

The release package version is `1.3.0`. A passing result establishes that these
particular source and wheel bytes match the named commit. It does
not establish v1.0.0/v1.2.0 upgrade behavior, SQLite or PostgreSQL recovery,
API compatibility, signed OCI or Compose execution, final-candidate acceptance,
or the complete portfolio milestone. Keep #214 open until its own transition
and rollback evidence is reviewed on protected main.
