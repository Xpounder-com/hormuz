# Connect actual workflow delivery to AI Work

Use one of two existing paths: a verified GitHub/Linear connector, or an
authenticated workflow reporter. A real source observation can close or reopen
an explicitly associated job under its declared criterion. It does not prove
AI caused the result or that all aspects of the work were correct.

Install the [current source candidate](TRY_HORMUZ.md). Your operator enables
AI Work and authorizes a unique identity. Keep `HORMUZ_GATEWAY_URL` and
`HORMUZ_TOKEN` in a private environment; the work ID alone grants no access.
No provider request or Stripe payment is needed for these delivery operations.

## GitHub: signed pull request delivery

1. Configure an HTTPS gateway and a dedicated, selected-repository GitHub App
   using [the existing connector guide](GITHUB_WEBHOOK_AUTH.md). It uses only
   Metadata, Pull requests and Checks read permissions. Register the App
   installation/repository IDs in `portfolio_control.connectors`, with distinct
   private webhook/identity keys in `outcome_connectors`. The webhook destination
   is `POST /v1/connectors/github/events` on your own gateway.
2. Select a pull request whose merge is the declared completion criterion.
   Use the repository **database ID** and pull request **database ID**, not its
   visible `#number`, URL, title or branch. For your own repository, these reads
   expose the required IDs: `gh api repos/OWNER/REPO --jq .id` and
   `gh api repos/OWNER/REPO/pulls/NUMBER --jq .id`.
3. Preview the binding with the helper, then repeat with `--execute` using
   your actual registered IDs and connector name:

```sh
python tools/ai_work_delivery_examples.py bind --provider github \
  --connector-id github-primary --container-id 456 --object-id 1001 \
  --repository company/service --context-revision YOUR_COMMIT
```

The numbers above are synthetic, not a configured installation. Without
`--execute`, the helper performs no network operation or credential lookup.
Execution creates an owned job with `github.pull_request.merged.v1` and binds
it explicitly. Keep the returned work ID across your agent's requests using
[the work attachment guide](AI_WORK_AGENT_INTEGRATION.md).

Creation and binding are separate requests. If creation succeeds but binding
fails, the sanitized error includes `work_id` and `retry_with_existing_work`.
Inspect the binding and retry with that ID using `--work-id`; repeating without
it creates another job. A transport failure can leave binding status unknown,
so an exact retry with the existing ID is safe and preserves the association.

The signature and signed installation/repository IDs authenticate source
delivery. An accepted merge completes the bound job; a subsequent reopen or
changes-requested observation contributes rework and retires eligible cached
answers. A signed but unrelated event does not infer an association. Bind before
the relevant event occurs: old/ambiguous source timestamps are not promoted to
new completion. Inspect the actual GitHub App delivery response and private
job observations; do not assume receipt merely because a webhook was configured.

## Linear: signed issue delivery

Follow [the implemented Linear connector](LINEAR_CONNECTOR_RUNTIME.md) to enroll
the organization, project, team, issue and webhook channel and configure its
HTTPS receiver. Use the enrolled **project UUID** as `--container-id` and the
issue UUID as `--object-id`. Team enrollment is separate; a team UUID cannot
replace the project container. Preview first:

```sh
python tools/ai_work_delivery_examples.py bind --provider linear \
  --connector-id linear-primary \
  --container-id 11111111-1111-4111-8111-111111111111 \
  --object-id 22222222-2222-4222-8222-222222222222 \
  --repository company/service --context-revision YOUR_COMMIT
```

Replace both synthetic UUIDs and add `--execute` after enrollment. The helper
uses `linear.issue.completed.v1`; the receiver verifies actual signed source
events. Existing jobs supplied with `--work-id` must already have the matching
completion condition. Binding an ordinary `workflow.completed.v1` job to a
different criterion is refused, preserving the original definition of success.

## GitHub Actions: report an actual check

For jobs owned by a dedicated CI identity, copy
[the workflow template](../examples/delivery/github-actions.yml) into
`.github/workflows/hormuz-check.yml` in your repository. Set repository variables
`HORMUZ_GATEWAY_URL` and `HORMUZ_SOURCE_REVISION` (a complete reviewed candidate
SHA), plus a private `HORMUZ_TOKEN` secret for that unique, authorized CI identity.
That same identity must own the job. Do not share a human's token across people
or jobs to bypass ownership; use the signed connector to observe human-owned jobs.

Replace the sample unittest command with your actual acceptance check and any
dependency setup it needs. The workflow runs only on explicit dispatch, uses
read-only repository permissions and pinned actions, serializes runs within the
repository, and preserves check failure. Its package installer has a 30-second
socket timeout and at most two retries; gateway calls run serially with a
30-second request timeout and no automatic retry.
It never sends provider requests, opens a PR or posts comments. Select
`completes_work` only when this check was already declared sufficient to finish
the whole job. A passing intermediate check otherwise submits no completion.

Preview the reporter locally:

```sh
python tools/ai_work_delivery_examples.py report \
  --work-id work-REPLACE_WITH_OWNED_ID --result success \
  --reference github-run/123/1

# Only when that real check is the declared completion criterion:
python tools/ai_work_delivery_examples.py report \
  --work-id work-REPLACE_WITH_OWNED_ID --result success \
  --reference github-run/123/1 --complete-on-pass --execute
```

Report the **actual** result, not `success` to manufacture evidence. Failure
submits `corrected`; canceled, skipped and unknown checks submit `unknown`.
An interrupted CI attempt does not cancel the whole job. A stable reference
makes the observation idempotent. A CI
report is recorded as `workflow`, not independently `verified_github`. A failing
check is an observation of rework, not proof the model caused it.

The source helpers and preview paths have local contract checks. Actual App
enrollment, HTTPS delivery, customer workflow execution and signed event arrival
remain end-to-end qualification tasks on your own setup. Never upload raw
payloads, keys, customer data or an unreviewed private receipt in public feedback.
