## Outcome

<!-- Link one issue and state the one verifiable outcome delivered. -->

Closes #

## Verification

<!-- List exact commands and results. State every skipped live or platform check. -->

```text
command
result
```

## Contract and migration boundary

<!-- Identify affected public/durable schemas, config, CLI, HTTP behavior, storage, and rollback. Write "No public or durable contract change" when true. -->

## Security and disclosure review

- [ ] Tests and evidence use synthetic values only.
- [ ] Source, commits, logs, artifacts, images, and evidence contain no credentials, prompts, responses, customer data, private infrastructure, or private filesystem paths.
- [ ] Authentication, authorization, provider egress, redaction, custody, tenant isolation, budgets, and audit implications are tested or explicitly not applicable.
- [ ] New or changed public/durable fields have an explicit schema version, compatibility fixture, migration rule, and rollback boundary, or no such field changed.
- [ ] Documentation states the exact support and production-readiness boundary without expanding claims beyond the evidence.

## Remaining nonclaims

<!-- What important behavior, platform, provider, deployment, or failure mode is still unproven? -->
