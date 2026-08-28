# Repository governance

Hormuz's source repository is a security boundary for the public alpha. The
checked-in contract at
`.github/repository-governance-v1.json` fixes the intended repository metadata,
workflow permissions, required checks, ruleset payloads, and public-transition
evidence. Run its fail-closed verifier with:

```bash
python tools/verify_repository_governance.py
```

This verifier proves the checked-in contract and workflow safety properties. It
does not claim that GitHub's remote settings match the files; maintainers must
also capture allowlisted API evidence when applying or reviewing those settings.

## Two explicit phases

| Control | Pre-public audit | Public alpha |
|---|---|---|
| Repository visibility | Private | Public only after the disclosure gate closes and the owner authorizes the transition |
| Action references | Every external Action pinned to a full commit SHA | Same |
| Action selection | `all`, because repository-level third-party patterns are not enforced for this private, non-Enterprise repository | `selected`: GitHub-owned Actions plus `docker/*@*` and `sigstore/cosign-installer@*` |
| Workflow token | Read-only by default; workflows declare narrower writes explicitly | Same |
| Secret scanning | Deferred; no paid private-repository security claim | Enabled and verified before promotion |
| Fork pull requests | Not applicable | Read-only `pull_request`; no `pull_request_target` and no repository secrets |
| Anonymous/public checks | Not applicable | Clone, templates, Discussions, license detection, and GHCR pull verified without owner credentials |

The blocking CI workflow runs once for each pull request and once after merge to
`main`. A feature-branch push does not also start a duplicate 11-job run.

GitHub documents both the full-SHA requirement and the limitation on selected
third-party patterns for private repositories outside an enterprise in its
[Actions permissions API](https://docs.github.com/en/rest/actions/permissions)
and [repository Actions settings](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/enabling-features-for-your-repository/managing-github-actions-settings-for-a-repository).

## Protected source and releases

The importable ruleset payloads under `.github/rulesets/` define four separate
controls:

1. Only the GitHub Actions integration may create a
   `candidate-v1.0.0-*` tag. The governance verifier requires the steward-gated
   candidate-freeze job to be the repository's only job with an effective
   contents-write grant. It resolves workflow and job overrides, treats
   `write-all` as contents-write, and rejects unsupported permission syntax.
2. `main` cannot be deleted or force-pushed. Every change uses a pull request,
   resolves review threads, is tested against current `main`, and passes all 11
   release-blocking checks from the GitHub Actions app.
3. Only an organization administrator may create a `v*` tag.
4. After creation, neither a `v*` nor a `candidate-v1.0.0-*` tag can be updated,
   force-moved, or deleted. The immutability ruleset has no bypass actor.

Separating tag creation from tag immutability is intentional: the organization
owner can create the protected annotated release tag without receiving an
ordinary path to rewrite it afterward. An emergency change requires an explicit,
auditable ruleset-administration action.

The release workflow independently rejects a private repository, an unprotected
or lightweight tag, the wrong repository/workflow identity, a non-version tag,
or a tag that is not the exact current `main` commit. GHCR is the first
publication registry, while the verified signed OCI digest remains the portable
artifact contract.

## Discussions and public surfaces

The maintained Discussions categories are Announcements, General, Ideas, Polls,
Q&A, and Show and tell. Public issue forms and Discussions must never request or
contain provider credentials, customer prompts, outputs, or proprietary data.
Security reports use GitHub's private security-advisory path.

The repository description and topics describe only the public-alpha gateway
boundary: policy enforcement, routing, redaction, and content-free usage/cost
evidence for Codex and Claude Code. They do not claim enterprise readiness or
include the separately packaged context experiment.

The website, social preview, public organization profile, organization pin, and
anonymous checks remain release evidence—not assumptions. They are completed
only after the disclosure decision and visibility authorization are recorded.

## Live review evidence

A remote review must use allowlisted output and confirm at least:

- repository visibility, description, topics, enabled surfaces, and homepage;
- the four active rulesets and their full rule parameters;
- all 11 required checks bound to GitHub Actions application ID `15368`;
- Actions enabled, full-SHA pinning required, default token permission `read`,
  and workflow approval disabled;
- Dependabot vulnerability alerts and security updates enabled;
- phase-appropriate selected-Action and secret-scanning state; and
- the exact Discussion category names and answerable Q&A behavior.

Never archive the broad repository API response as evidence. GitHub may include
ephemeral or unrelated fields. Capture only the fields named above.
