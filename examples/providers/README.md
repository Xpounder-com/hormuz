# OpenAI and Anthropic example tour

The tour includes **21 scenarios** through the actual AI Work gateway. It covers
OpenAI Responses, Chat Completions and Anthropic Messages, three streaming paths,
function declarations, code review, CI diagnosis, writing, structured extraction,
priorities, budget refusal, exact answer reuse, correction and context changes.

Install the [current source candidate](../../docs/TRY_HORMUZ.md) first. Then:

```sh
python tools/ai_work_provider_examples.py --list
python tools/ai_work_provider_examples.py
python tools/ai_work_provider_examples.py --example exact-answer-reuse
python tools/ai_work_provider_examples.py --example correction-bypasses-reuse
```

The default tour needs no provider credential. It runs disposable loopback
providers, the real authenticated gateway and the real SQLite work ledger.
The provider replies are explicitly synthetic; their text is intentionally not
a meaningful answer to every illustrative prompt. Temporary state is removed.
The [executed receipt](../../docs/evidence/provider-examples/README.md) records
all 21 local scenarios passing, with its exact source hashes and conditions.
You need permission to bind local ports. Installation downloads dependencies;
the default tour makes zero external provider calls.

## Small live runs with your accounts

Choose an accessible text model for each provider. Copy
[the rate-card template](rate-card.template.json) to an ignored private location,
replace the model IDs and **all four null rates** with your qualified USD prices
per million tokens. The script refuses missing, negative, nonfinite or boolean
rates. Input/output rates must be positive. No current model or price is guessed.

Qualify account, endpoint, context size, geography, standard processing and any
cache-write duration. The gateway enforces qualified processing modes; a
configured rate estimate is still separate from a provider-confirmed invoice.
Consult [the pricing boundary](../../docs/AI_WORK_RUNTIME.md#spending-and-continuation).

Load `OPENAI_API_KEY` and/or `ANTHROPIC_API_KEY` through your private environment.
Do not put keys in commands, URLs, the rate card, chat, or committed files.

```sh
# OpenAI: Responses, Chat Completions, and streaming (at most three requests).
python tools/ai_work_provider_examples.py --live openai \
  --rate-card .hormuz-examples/rates.runtime.json

# Anthropic: Messages, summary, and streaming (at most three requests).
python tools/ai_work_provider_examples.py --live anthropic \
  --rate-card .hormuz-examples/rates.runtime.json

# Both providers, three small requests total.
python tools/ai_work_provider_examples.py --live both \
  --rate-card .hormuz-examples/rates.runtime.json

# Select one particular request, with the same explicit live gate.
python tools/ai_work_provider_examples.py --live openai \
  --rate-card .hormuz-examples/rates.runtime.json --example openai-ci-diagnosis

# Haiku 5.5: one arithmetic canary with explicitly disabled thinking.
python tools/ai_work_provider_examples.py --live anthropic \
  --rate-card .hormuz-examples/rates.runtime.json --example anthropic-smoke \
  --anthropic-no-thinking --max-live-calls 1
```

`--anthropic-no-thinking` sends `thinking: {"type": "disabled"}` and
`output_config: {"effort": "low"}` only for selected live Anthropic requests.
Qualify that combination for the chosen model; it is supported by
[Haiku 5.5 at low effort](https://platform.claude.com/docs/en/build-with-claude/thinking-troubleshooting).
The option keeps the 256-token output limit and avoids spending it on default
adaptive thinking. It adds no sampling parameters. For this small Haiku 5.5
canary, qualify standard account rates for prompts up to 100,000 tokens and the
account's default geography. The example enables no provider prompt caching;
the rate card has one cache-write rate and does not distinguish five-minute and
one-hour TTLs or longer-context pricing. Consult the
[model pricing](https://platform.claude.com/docs/en/models/haiku-5-5/overview)
before extending the workload. Existing defaults keep the provider's thinking
behavior. OpenAI payloads and the offline tour are unchanged.

Each live command can incur provider charges. Default maximum output is 256
tokens per request, live request count is three, and the workspace estimate
allowance is 100,000 micro-USD ($0.10). Override `--max-live-calls` and
`--budget-microusd` explicitly when expanding. These limits bound the example
requests and gateway estimates; they are not an authoritative billing cap.
A failed/uncertain request may still be charged. The tour does not retry it.
Requests run sequentially, with a reusable loopback client, a five-second
connection timeout and bounded request/upstream timeouts. The local fixture
accepts at most four handlers. Both disposable servers close on exit.

One disposable gateway identity owns the example work. This is a connection
exercise, not a hosted tenant, native-client qualification or ongoing customer
deployment. Use [SDK recipes](../sdk/README.md) or the
[agent integration guide](../../docs/AI_WORK_AGENT_INTEGRATION.md) with your
persistent, qualified gateway for real work.

## Read the receipt honestly

`--receipt NEW_FILE.json` also saves the same metadata receipt, without replacing
an existing file. It contains scenario names, status, route metadata, attempt
counts, gateway totals and source file hashes. It omits keys, prompts and answers.
Inspect it before sharing; your model and configured spending are still metadata.

Synthetic receipts state zero real calls. Live receipts count successful uncached
provider responses observed by the gateway, not invoices or every possible charge.
Neither claims savings, faster task completion, model correctness or paid delivery.
Passing requires a complete endpoint response, including its stream terminal
markers when applicable, and a successful response observation in the gateway
ledger. A failed or truncated HTTP 200 stream receives no passing receipt.

The four cache/budget/context controls are local-only to avoid injecting tutorial
correction observations into a live workload. A passing HTTP request does not
close a job. The one correction is explicitly declared synthetic workflow evidence.
Tool scenarios declare a function and relay provider output; they execute no tool
and do not qualify a full agent/tool round trip. With one route per protocol,
priorities exercise plan selection and retain the baseline on missing comparable
evidence. The [existing local-learning proof](../../tools/ai_work_proof.py) separately
exercises alternate-route selection. Use actual customer-local episodes for learning.

Official references: [Responses](https://developers.openai.com/api/reference/resources/responses/methods/create),
[Chat Completions](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create),
[Anthropic Messages](https://platform.claude.com/docs/en/api/messages).
Live provider qualification remains pending. Qualify the chosen models, prices
and installed source on your account before treating these examples as live evidence.
