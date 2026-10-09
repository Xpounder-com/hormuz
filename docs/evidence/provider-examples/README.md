# Executed local provider tour

The [receipt](receipt.json) records all 21 scenarios passing through the actual
gateway: 25 inference requests, 22 synthetic provider replies, two exact answer
hits and one budget refusal. The correction is declared synthetic workflow
evidence. No model response closes a job.

The run used disposable loopback servers, SQLite state, synthetic credentials
and configured fixture rates. It made zero external provider calls or payments,
executed no tool actions, and establishes no customer savings or model quality.
Timing describes this small functional run, not a load or performance benchmark.

`source_commit` is the starting checkout revision,
`fded8a5737e72e8b4496121c8fa4eed0eb9c3c62`. The refreshed run included
uncommitted changes; its 30 `source_files` hashes identify the actual working
tree tested. The execution wrapper checked the reviewed source manifest before
and after the run. The receipt is retained as executed rather than relabeled
with a later commit.

Reproduce from the current reviewed source:

```sh
python tools/ai_work_provider_examples.py --receipt NEW_RECEIPT.json
```

An existing receipt destination stops execution before provider requests.
Follow [the provider guide](../../../examples/providers/README.md) for account
and model qualification.

## Bounded live Anthropic run

The [one-call canary](anthropic-live-canary.json) and
[five remaining examples](anthropic-live-examples.json) record six successful
real `claude-haiku-5-5` Messages requests. They cover arithmetic, summary,
writing, streaming, a tool declaration and the quality-priority plan. Thinking
was explicitly disabled at low effort, with a maximum of 256 output tokens per
request. No tool action, payment or job completion occurred.

The two gateway estimates total 144 micro-USD ($0.000144); provider invoice
reconciliation remains false. These are estimates for this small run, not an
invoice or a provider billing cap. Both receipts identify the starting
`fded8a5737e72e8b4496121c8fa4eed0eb9c3c62` checkout with uncommitted source
changes. The execution wrapper verified all 30 source hashes before and
after each live subprocess and checked that the emitted manifest matched that
reviewed source.

This evidence covers the tested Anthropic model and Messages paths. OpenAI live
requests, model quality, customer savings, native-client compatibility and a
hosted customer gateway remain unqualified. The 21-scenario receipt above uses
local synthetic replies throughout.
