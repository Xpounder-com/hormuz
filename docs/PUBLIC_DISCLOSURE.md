# Public disclosure gate

Hormuz stays private until a versioned, metadata-only disclosure report has no
unresolved blockers and the repository owner separately authorizes the
visibility change. The report is evidence for a bounded review; a clean scanner
result alone is not proof that a repository is safe to publish.

The current report is
[`hormuz.public-disclosure-report` v1](evidence/public-disclosure-report-v1.json).
It is intentionally marked `decision_required`. Raw Git objects, GitHub API
responses, workflow logs, artifacts, cache metadata, matched values, addresses,
and local paths are not committed.

## Audited boundary

The report covers a fresh mirror of every branch and pull-request ref advertised
by GitHub at the recorded baseline, its complete reachable object graph and
commit metadata, the current worktree, all then-downloadable Actions logs and
artifacts, and the recorded GitHub issue, pull-request, review, release,
environment, variable, secret, and package-count surfaces. Artifact archives
were checked for unsafe paths and encryption before recursive scanning.

GitHub does not expose unadvertised, unreachable objects from its server object
database through an ordinary clone or repository API. The fresh all-ref mirror
contained no unreachable objects, but that is not evidence that GitHub retains
no unadvertised server-only object. The report preserves that limitation rather
than claiming impossible coverage. Expired artifact records were enumerated,
but their deleted bytes could not be recovered. Actions cache contents are not
downloadable through the API and remain a publication decision while any cache
exists.

Every scanner match is represented only by an allowlisted category, count, and
classification. The report never repeats a matched value. Its strict verifier
rejects duplicate JSON keys, unknown fields and categories, inconsistent
coverage counts, unsupported verdicts, actual email-shaped values, common
credential forms, and private local paths:

```bash
python3 tools/verify_public_disclosure_report.py \
  --report docs/evidence/public-disclosure-report-v1.json \
  --require-verdict decision_required
```

## Licensing boundary

Hormuz and the separated context experiment declare Apache 2.0 and carry the
canonical license in their wheels and source distributions. The OCI build
copies the license into the application wheel and declares
`org.opencontainers.image.licenses=Apache-2.0`.

CI installs the core wheel with every declared optional dependency plus the
experiment into a fresh environment. It fails closed if the resolved
distribution set, normalized license identity, or packaged license material
changes. The reviewed Python closure currently contains twelve permissive
dependencies and three separately distributed LGPL dependencies; those
dependencies retain their own licenses. Operating-system and base-image
components likewise retain their own licenses and remain visible in the OCI
SBOM. This is engineering evidence, not a legal opinion.

## Closure order

1. Resolve the explicit owner decisions recorded as blockers.
2. Merge the license and report-validation implementation while the repository
   remains private.
3. Rescan the resulting commit and every new GitHub surface created by the PR.
4. Update the versioned report to zero blockers and obtain a separate explicit
   owner authorization for the visibility transition.
5. Change visibility, verify the public repository surfaces, and record the
   transition without publishing raw audit material.

Repository publication, history rewriting, artifact or cache deletion,
credential rotation, and package publication are separate operations. This
document authorizes none of them.
