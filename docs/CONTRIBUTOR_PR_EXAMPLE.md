# Fictional documentation-only pull request example

This is a worked response to the existing
[pull-request template](../.github/pull_request_template.md), not a submitted
PR or a record of an actual issue, test run, review, or customer result. The
hypothetical change is one sentence in [CONTRIBUTING.md](../CONTRIBUTING.md)
clarifying where to run the community checks. Replace the issue marker and
every result placeholder with facts from your own change before submitting.
Leave checklist boxes unchecked until you have verified their statements.

The template starts with `Closes #` as a prompt. Use `Closes #<issue-number>`
only if the PR completes that issue. For one part of a larger issue, replace it
with `Refs #<parent-issue-number>` so merging the slice does not close the
parent automatically. The example below is a fictional partial slice.

## Outcome

Clarify the community-check instructions in `CONTRIBUTING.md` so a first-time
contributor knows to run them from the repository root. The verifiable result
is a corrected sentence and working links, with no new command or policy.

Refs #PARENT_ISSUE_NUMBER (replace with the actual parent issue)

## Verification

Run these from the repository root after the documented development setup, and
replace each bracketed line with your observed result. Do not report a pass or
test count you did not see.

```text
python tools/verify_public_community_paths.py
[Replace with the observed exit status and concise output.]
python -m unittest -v tests.test_public_community_paths
[Replace with the observed test summary and result.]
git diff --check
[Replace with the observed result.]
```

Preview the changed Markdown and follow its links. For this hypothetical
wording change, no live provider, physical platform, or migration check is
applicable; state that explicitly in the real PR rather than claiming one ran.

## Contract and migration boundary

No public or durable contract change. This hypothetical wording edit changes
no configuration, CLI or HTTP behavior, stored data, or schema, so it needs no
migration or rollback plan. Confirm those facts against your actual diff.

## Security and disclosure review

- [ ] Tests and evidence use synthetic values only.
- [ ] Source, commits, logs, artifacts, images, and evidence contain no credentials, prompts, responses, customer data, private infrastructure, or private filesystem paths.
- [ ] Authentication, authorization, provider egress, redaction, custody, tenant isolation, budgets, and audit implications are tested or explicitly not applicable.
- [ ] New or changed public/durable fields have an explicit schema version, compatibility fixture, migration rule, and rollback boundary, or no such field changed.
- [ ] Documentation states the exact support and production-readiness boundary without expanding claims beyond the evidence.

These boxes are illustrative and intentionally unchecked: no real diff has
been reviewed here. For a documentation-only wording change, explain that no
new tests or evidence records were added; inspect the diff for sensitive
content; mark runtime security areas and field-versioning requirements not
applicable only after confirming no such behavior or fields changed. Check the
last box only after the wording makes no broader readiness claim.

## Remaining nonclaims

The hypothetical wording edit does not implement product behavior or prove
live-provider results, platform acceptance, CI on a real PR, reviewer approval,
or release readiness. A real PR should name only the limits relevant to its
actual diff.
