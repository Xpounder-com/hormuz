# Try Hormuz with your existing AI workflow

Start with a small local gateway run, inspect the source and receipt, then connect
one real workflow. You need Git and Python 3.11+. Hormuz is Apache-2.0 software;
no paid Hormuz subscription is required to self-host it. Providers bill their own
API usage when you explicitly enable live requests.

## 1. Run the provider-free tour

AI Work is available in current `main` as a 1.9.0 source candidate. Published v1.8.0 source,
signed Mac and OCI installers do not contain these new example commands.

```sh
git clone --branch main --depth 1 https://github.com/Xpounder-com/hormuz.git
cd hormuz
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --editable .
python tools/ai_work_provider_examples.py --tour
```

On Windows, activate `.venv\Scripts\Activate.ps1` in PowerShell and consult the
[support matrix](../SUPPORT.md). The gateway tour requires permission to bind
loopback ports. Installation downloads dependencies; the tour's provider calls
stay on loopback. It deletes temporary state at exit. The four readable `PASS`
lines confirm a gateway request, a budget refusal, exact answer reuse and a
correction that bypasses reuse. No provider key, account or payment is required.

## 2. Inspect what ran

Read [the tour source](../tools/ai_work_provider_examples.py), or save a full
metadata receipt alongside the short output:

```sh
python tools/ai_work_provider_examples.py --tour --receipt NEW_RECEIPT.json
```

The receipt identifies the executed source files, scenario results, gateway
attempts and ledger totals. Its provider mode is `local_synthetic_wire_responses`;
prompts, replies and credentials are omitted. An existing destination stops the
run before requests. Inspect the receipt before sharing it.

Explore the broader catalog after the first four checks:

```sh
python tools/ai_work_provider_examples.py --list
python tools/ai_work_provider_examples.py
```

The **21 scenarios** span Responses, Chat Completions, Messages, streaming, tool
declarations, illustrative engineering/marketing questions, priorities, a budget
stop, exact reuse, correction and context change. Synthetic reply text does not
grade those tasks. Inspect source conditions and status instead of treating
the simulator's answer as a real provider result.

Try one control at a time:

```sh
python tools/ai_work_provider_examples.py --example budget-stop
python tools/ai_work_provider_examples.py --example exact-answer-reuse
python tools/ai_work_provider_examples.py --example correction-bypasses-reuse
python tools/ai_work_provider_examples.py --example context-change
```

`--tour` selects its own four local scenarios; use separate commands for
`--example`, `--list` or `--live`.

## 3. Select a small real-provider example

Use [the live provider guide](../examples/providers/README.md) to load your own
credential privately and choose an accessible model with your account's qualified
rates. `--live openai`, `--live anthropic` or `--live both` explicitly permits
provider requests. The default live selection is three small requests. No key
or new account is needed for the first local tour.

Request limits and configured cost estimates are separate from authoritative
provider billing. Check usage and retained unknown holds. A successful API
connection does not qualify every agent, tool or model feature. Native provider
prompt caching and Hormuz exact answer reuse are also separate measurements.

## 4. Keep your agent or SDK

For a persistent gateway, follow [runtime configuration](AI_WORK_RUNTIME.md) and
[agent integration](AI_WORK_AGENT_INTEGRATION.md). The administrator authorizes
unique identities, approved models and qualified rates; provider keys stay on
the gateway. Pin its complete reviewed source SHA and qualify your exact client.

Use [SDK recipes](../examples/sdk/README.md) with OpenAI or Anthropic's familiar
SDK. Each request carries its work ID. Choose `cost`, `speed` or `quality` and a
job allowance. Existing policies and ancestor budgets remain effective.

Routing uses comparable customer-local episodes, attempts, observed spending,
timing and sourced rework. Unknown or insufficient evidence retains the approved
baseline. An inferred task label is not a universal quality benchmark, and a
cache hit is not independent task success.

## 5. Connect the actual delivery

Follow [the delivery guide](AI_WORK_DELIVERY_EXAMPLES.md) to associate an enrolled
GitHub PR or Linear issue with work before its relevant event. Alternatively,
use the [GitHub Actions template](../examples/delivery/github-actions.yml) for a
job owned by a dedicated workflow identity. Passing an intermediate check does
not close the whole job; declared completion and subsequent correction remain
separate source observations.

## 6. Share useful, sanitized feedback

Use the repository's [Installation or first-run failure](https://github.com/Xpounder-com/hormuz/issues/new?template=installation.yml)
form for a failed setup and [Bug report](https://github.com/Xpounder-com/hormuz/issues/new?template=bug.yml)
for incorrect behavior. Share a successful first run or discuss the workflow in
[Discussions](https://github.com/Xpounder-com/hormuz/discussions/211). Include the exact source SHA,
OS/Python/SDK versions, scenario name and mode, expected status and sanitized
error classification. Report simulated and live evidence separately.

Never upload credentials, raw prompts/responses, private rate cards, repository
details or unreviewed receipts. Live provider success, real source delivery,
repeated use and customer savings each require their own evidence. Stripe is
unnecessary for self-hosted examples; hosted paid activation remains a separate
qualified lifecycle.
