# Connect existing API agents to AI work

AI work adds a durable job identity, scoped budgets, cost/speed/outcome priorities,
attempt accounting, and sourced workflow observations to gateway requests. It is
explicitly enabled in the server profile. It does not migrate native subscriptions,
certify provider invoices, or declare a job complete because a model answered.

## Authentication and prerequisites

The `hormuz work` commands and Python work client in this guide require this
reviewed AI Work source candidate on the client and gateway. The published
v1.8.0 source, signed Mac, and OCI artifacts do not include these new commands.
An operator installs the reviewed source revision and qualifies the exact agent
and provider before activation; a stable installer alone is insufficient.

Install the candidate CLI in a separate environment with Python 3.11+ and Git.
Replace `<REVIEWED_40_CHARACTER_COMMIT>` with the complete 40-character lowercase
reviewed commit supplied by your administrator or the website source pin. Use
that immutable revision, never a branch or release tag.

```sh
python3 -m venv .venv-hormuz-ai-work
source .venv-hormuz-ai-work/bin/activate
python -m pip install 'hormuz[client,context] @ git+https://github.com/Xpounder-com/hormuz.git@<REVIEWED_40_CHARACTER_COMMIT>'
hormuz work --help
```

This installs the candidate client package. The administrator still configures
and qualifies the gateway. It does not update the signed Mac app, provision a
hosted service, or activate paid inference.

The gateway administrator enables `ai_work`, configures supported model routes and
provider credentials, and authorizes your identity through the existing static or
OIDC/native enrollment flow. Provider keys stay on the gateway server.

Version-1 identity policy names the OpenAI API authorization category `codex`.
OpenAI-compatible Chat Completions adapters use that same category; the label
does not require that requests come from the Codex application. Grant this
capability explicitly for an OpenAI API connection, together with allowed models.
Anthropic API connections similarly use the existing `claude-code` category.

Public workspace sign-up creates a workspace, not inference entitlement. A newly
created workspace has no enabled native clients; the administrator must approve
application access before enrollment succeeds. The dashboard displays this state.
Hosted payment state and inference access are separate gates. A workspace address,
configured price, or completed checkout redirect does not establish paid activation.

Use your actual HTTPS gateway address and load your authorized Hormuz token into
`HORMUZ_TOKEN` through your existing local secret environment. Do not paste it into
chat, URL queries, command arguments, or committed agent configuration.

```sh
export HORMUZ_GATEWAY_URL=https://your-gateway.example
hormuz work connect
hormuz work create --repository company/service --title 'Repair integration CI' \
  --task-type ci-repair --context-revision commit-abc123
```

The create response contains a `work_id`. Preserve it for all requests, agent
iterations, retries, and fallbacks belonging to that job. Create a separate work ID
for a different task. A context revision identifies an immutable context; exact
answer reuse requires the gateway's explicit cache opt-in and validity conditions.

For local development only, `--allow-loopback-http` permits a loopback HTTP address.
The Python environment equivalent is `HORMUZ_ALLOW_LOOPBACK_HTTP=1`. Remote plain
HTTP is refused by the client, and gateway redirects are refused to protect tokens.

## Codex and Claude Code: bind a native session

The existing native enrollment must already authorize the client and model alias.
For a browser-enrolled account with credentials in the operating system secure
store, print the client configuration without reading a server configuration:

```sh
hormuz client config codex --auth-mode session --url "$HORMUZ_GATEWAY_URL" \
  --profile default --model your-approved-alias --work-id work-REPLACE_WITH_RETURNED_ID
hormuz client config claude --auth-mode session --url "$HORMUZ_GATEWAY_URL" \
  --profile default --work-id work-REPLACE_WITH_RETURNED_ID
```

Merge the printed Codex settings into the user-level `~/.codex/config.toml`, or
the Claude settings into its user settings. They contain a secure-store credential
helper and the work header; no credential values are printed. Preserve existing
allowed headers when merging a header map. The configured work ID applies to every
request/retry using that configuration: use a distinct ID for a different job and
remove/change the header when switching jobs. Successful responses never mark
the job complete.

For an existing managed Hormuz desktop/context profile, the launcher can bind one
agent session without editing agent credential configuration:

```sh
hormuz context run --profile YOUR_MANAGED_PROFILE \
  --work-id work-REPLACE_WITH_RETURNED_ID
```

It uses the profile's existing private credential helper and local context relay.
The relay preserves its context-format headers and forwards the work ID to Hormuz,
while replacing local agent authentication with the authorized gateway credential.
Work IDs are not forwarded to direct provider endpoints. The gateway verifies the
job owner on every request. Agent process exit is not a completion observation.

These bindings use Codex model-provider `http_headers` and Claude Code
`ANTHROPIC_CUSTOM_HEADERS`, documented by the vendors:
[Codex configuration](https://developers.openai.com/codex/config-reference) and
[Claude environment variables](https://code.claude.com/docs/en/env-vars).
The launcher's existing native-version checks still apply; local fixture tests of
the binding do not replace qualification of the actual native/provider combination.

## Python client: automatic work attachment

```python
from hormuz.work_client import WorkClient

client = WorkClient.from_environment()
created = client.create_job(
    "company/service",
    title="Repair integration CI",
    task_type="ci-repair",
    context_revision="commit-abc123",
)
job = client.job(created["work_id"])

answer = job.request("/v1/chat/completions", {
    "model": "your-approved-alias",
    "messages": [{"role": "user", "content": "Diagnose this CI failure."}],
    "max_tokens": 1000,
})

# Further iterations use the same job object, including explicit retries.
follow_up = job.request("/v1/chat/completions", {
    "model": "your-approved-alias",
    "messages": [{"role": "user", "content": "Investigate the remaining error."}],
    "max_tokens": 1000,
})
```

`WorkJob.request` attaches `X-Hormuz-Work-Id` and the client's authorized credential.
Use `request_kind="polling"`, `"automatic_retry"` or `"legitimate_iteration"`
for machine repeats and legitimate continuations; the corresponding CLI flag is
`--request-kind`. These classifications exclude recurrence from quality evidence,
while every request still consumes its ordinary applicable allowance.
It supports non-streaming OpenAI Responses, OpenAI Chat Completions, and Anthropic
Messages. For streaming or an existing agent SDK, pass `job.headers` as its
additional HTTP headers and configure its normal gateway credential separately.
The `headers` property contains only the work ID, not a secret.

Agents with no configurable API endpoint/header support need an explicit adapter;
their subscriptions or browser traffic are outside this gateway's spend controls.
Responses and Messages remain distinct provider protocols. Normal gateway model,
tool, privacy, and identity policy checks continue to apply.

`GET /v1/models` lists authenticated policy-permitted OpenAI aliases for SDK model
discovery. With a work budget enabled, Chat requests support one text completion
(`n` omitted or `1`); audio, image URL inputs and hosted search options are rejected
before dispatch because their cost is not covered by the text reservation.
Responses images and Anthropic image/document blocks are also unbounded, even
when supplied as inline base64. Compressed bytes cannot bound image tokens or
document expansion. Budgeted AI Work accepts supported text requests; additional
modalities require independently validated reservation rules before admission.
Budgeted inference uses qualified standard processing tiers. Explicit premium,
automatic tier selection, fast inference and unqualified geography overrides
are refused before dispatch; a speed preference selects a qualified model route.
The operator must qualify account defaults and all applicable rates. Unexpected
provider pricing dimensions remain an unknown charge in the work receipt.

## Scoped plans and continuation

```sh
# Shared policies require an explicit configured administrator or verified
# member-admin browser session. Expected version 0 creates a new plan.
hormuz work plan --scope repository --scope-id company/service \
  --budget-usd 200 --objective speed --expected-version 0

# Ordinary authorized identities can set a plan on their own job.
hormuz work plan --scope job --scope-id work-REPLACE_WITH_RETURNED_ID \
  --budget-usd 5 --objective cost --expected-version 0

hormuz work pause --work-id work-REPLACE_WITH_RETURNED_ID
hormuz work approve --work-id work-REPLACE_WITH_RETURNED_ID \
  --budget-usd 8 --expected-version 1
```

Workspace and repository budgets use the current UTC month. A job budget covers
its lifetime. Approval supplies a **new total job allowance**, not an increment,
and does not raise workspace or repository limits. Zero blocks paid calls;
`plan --uncapped` removes only that scope's limit. All active parent limits and
normal policy remain enforced on subsequent requests. Stop/pause controls cannot
cancel already dispatched provider work.

Cost first, speed first, and quality within budget select the local routing objective.
Routing uses available observations and retains compatibility/state boundaries;
it does not promise correctness or faster completion from insufficient evidence.
Optional `plan --exploration` permits only operator-approved aliases and rate within
a configured monthly exploration reservation allowance. Parent opt-outs dominate;
`--no-exploration` opts out and `--inherit-exploration` clears this plan override.
A budget-only edit preserves the existing exploration setting.

## Passive observations from actual checks

An API response is activity. Workflow evidence must come from the workflow itself.
If a passing integration check is the declared completion condition for this job:

```sh
hormuz work check --work-id work-REPLACE_WITH_RETURNED_ID \
  --reference ci/run-42 --completes-work -- python -m unittest tests.test_integration
```

The CLI executes the explicit argument list without a shell. It submits a
`workflow` observation: a zero exit status records completion of the declared
condition; a nonzero status records a correction signal. It returns the check's
exit status. An intermediate passing check without `--completes-work` does not
close the job.

Equivalent Python integration:

```python
import subprocess

check = subprocess.run(["python", "-m", "unittest", "tests.test_integration"], check=False)
job.observe_check(check.returncode, reference="ci/run-42", completes_work=True)
```

Submitted observations are sourced reports, not independently verified ratings.
References deduplicate repeat events. A check passing establishes that specified
condition; it does not prove all aspects of software quality. To connect existing
GitHub/Linear outcomes, bind their actual identifiers to the work ID explicitly.
This client does not infer that association through textual similarity.

## Automatic signed workflow evidence

For an enrolled signed source in the full gateway profile, declare the condition
when creating the job, then associate exactly one actual object:

```sh
hormuz work create --repository company/service \
  --completion-condition github.pull_request.merged.v1
hormuz work bind --work-id work-REPLACE_WITH_RETURNED_ID --provider github \
  --connector-id REVIEWED_CONNECTOR --container-id REVIEWED_REPOSITORY_ID \
  --object-id ACTUAL_PULL_REQUEST_OBJECT_ID \
  --completion-condition github.pull_request.merged.v1
```

Use immutable provider object IDs, not a pull request's displayed number. The
operator must first enroll the tenant-qualified connector, source binding and
signed channel. The dashboard lists only that organization's configured choices.
GitHub merge, GitHub successful check and Linear issue completion are separate
conditions. A review approval cannot satisfy a merge/check condition. Verified
reopen/correction events invalidate cache generations; late events cannot override
newer source facts. No textual similarity guesses an object association.

The existing source endpoint verifies exact signed bytes before the work bridge
journals metadata; source delivery replay can complete an interrupted observation
without duplicating it. Raw events, issue text, prompts and plain object IDs are
not retained in this work store. The fixed hosted provider-pilot excludes enrolled
portfolio connectors and supports explicit workflow checks instead; do not present
it as automatic signed-source coverage.

## Dashboard and API

Open `/work` through the signed workspace or console. Browser changes require the
same configured/verified host, exact Origin, a current session, and CSRF token.
Member administrators can view their organization; ordinary API identities see
their own jobs. Jobs belonging to another member are visible to administrators
but continuation and observation controls remain owned by that member.

API routes:

| Route | Purpose |
| --- | --- |
| `GET /v1/work/connect` | Configured routes, application access, and billing state |
| `GET /v1/work/state` | Scoped jobs, plans, totals, coverage, and uncertainty |
| `POST /v1/work/jobs` | Create a durable work identity |
| `GET /v1/work/jobs/{work_id}` | Read an owned job and its captured attempts |
| `POST /v1/work/policies` | Set an authorized scope's budget/objective |
| `POST /v1/work/jobs/{work_id}/actions` | Pause, resume, stop, or approve allowance |
| `POST /v1/work/jobs/{work_id}/observations` | Record sourced workflow evidence |

Bearer requests use JSON, no browser Cookie or Origin, and an authorized Hormuz
credential. Tenant/actor/role identity cannot be supplied through request fields
or authority headers. Shared policy permission is an explicit server grant.

Totals include captured retries/fallbacks, exact reuse, denied attempts, and
unsettled reservations. Provider usage and configured rate cards produce
estimates. Unknown usage remains held conservatively; missing completion evidence
stays unknown. Attempt-time totals and observed completion elapsed are separate
measurements. Similar/repeated requests are recurrence signals, not quality scores.

## Validation boundary

Local fixtures validate the HTTP/session/CSRF/tenant boundaries, SDK attachment,
and check-to-observation behavior without paid model calls. Production rollout
still requires actual provider, rate-card, deployment, account enrollment,
payment/webhook, invoice, and support qualification for the chosen deployment.
