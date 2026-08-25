# Public disclosure gate

Hormuz stays private until a versioned, metadata-only disclosure report has no
unresolved blockers and the repository owner separately authorizes the
visibility change. The report is evidence for a bounded review; a clean scanner
result alone is not proof that a repository is safe to publish.

The current report is
[`hormuz.public-disclosure-report` v1](evidence/public-disclosure-report-v1.json).
It is marked `public_transition_verified`: the recorded audit reached zero
unresolved blockers, the owner authorized publication, and the repository's
public visibility and anonymous access were verified after the change. Raw Git
objects, GitHub API responses, workflow logs, artifacts, cache metadata,
matched values, addresses, and local paths are not committed.

## Audited boundary

The report covers a fresh mirror of every branch and pull-request ref advertised
by GitHub at the recorded baseline, its complete reachable object graph and
commit metadata, the current worktree, all then-downloadable Actions logs and
artifacts, and the recorded GitHub issue, pull-request, review, release,
environment, variable, secret, and package-count surfaces. Artifact archives
were checked for unsafe paths and encryption before recursive scanning.

The Git audit used two independent views. First, Gitleaks traversed every
non-merge diff reachable from all advertised refs while Git separately counted
all commits and checked the fresh mirror for unreachable objects. Second, every
unique reachable blob was materialized under its object ID into an owner-only
temporary directory and scanned again without relying on commit-diff behavior.
This second pass covered merge-resolution content and every historical blob,
including content later deleted from a branch. The report reconciles the two
scan-result sets separately so duplicate observations are not mistaken for new
secrets.

The sensitive-data checks included Gitleaks's default rules plus separate
searches for email-shaped strings, credential-bearing URLs, and private local
filesystem paths. Actions logs were scanned as downloaded archives. Every
available artifact was downloaded, structurally checked for encryption and
unsafe archive paths, recursively expanded to a bounded depth, and scanned.
GitHub API snapshots covered issues, pull requests, comments, reviews,
workflows, releases, deployments, environments, variables, secret metadata,
hooks, rulesets, and package count. A human reviewed every match category
against its source role before assigning `safe`, `removed`, or
`decision_required`; no matched value appears in the public report.

GitHub does not expose unadvertised, unreachable objects from its server object
database through an ordinary clone or repository API. The fresh all-ref mirror
contained no unreachable objects, but that is not evidence that GitHub retains
no unadvertised server-only object. The report preserves that limitation rather
than claiming impossible coverage. Expired artifact records were enumerated,
but their deleted bytes could not be recovered. Actions cache contents are not
downloadable through the API. The owner authorized deletion of the audited
rebuildable caches, and the final snapshot contains no cache entry; any cache
created after that snapshot must be removed or separately resolved before the
visibility transition.

Every scanner match is represented only by an allowlisted category, count, and
classification. The report never repeats a matched value. Its strict verifier
rejects duplicate JSON keys, unknown fields and categories, inconsistent
coverage counts, unsupported verdicts, actual email-shaped values, common
credential forms, and private local paths:

```bash
python3 tools/verify_public_disclosure_report.py \
  --report docs/evidence/public-disclosure-report-v1.json \
  --require-verdict public_transition_verified
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

## Completed transition evidence

The transition followed the recorded order: exact commit, CI, disclosure
verdict, and zero-cache preconditions were rechecked; visibility changed under
the owner's conditional authorization; the repository, contribution/security
paths, Discussions, license, settings, and protected rules were then verified
from an anonymous environment. A bounded scan covered the final PR/merge/run
delta without publishing raw audit material.

Repository publication, history rewriting, artifact or cache deletion,
credential rotation, and package publication remain separate operations. The
report records only the owner authorization and completed actions named in its
strict publication state.
