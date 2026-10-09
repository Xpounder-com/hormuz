# Executed provider example evidence

The [receipt](receipt.json) records all 21 scenarios passing through the actual
gateway: 25 inference requests, 22 synthetic provider replies, two exact answer
hits and one budget refusal. The correction is declared synthetic workflow
evidence. No model response closes a job.

The run used disposable loopback servers, SQLite state, synthetic credentials
and configured fixture rates. It made zero external provider calls or payments,
executed no tool actions, and establishes no customer savings or model quality.
Timing describes this small functional run, not a load or performance benchmark.

`source_commit` is the starting checkout revision,
`ea863c42eda462f2872719aa3e8a9a505649ae2e`. The refreshed run included
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

These retained Anthropic receipts describe their tested source, rather than the
later runner and storage-error fixes. They cover the tested model and Messages
paths. Model quality, customer savings, native-client compatibility and a hosted
customer gateway remain unqualified. The 21-scenario receipt above uses local
synthetic replies throughout.

## Bounded live OpenAI runs

The [Responses canary](openai-live-canary.json) completed one real
`gpt-4.1-nano` arithmetic request with a gateway estimate of 3 micro-USD. The
[CI explanation run](openai-live-incomplete-ci.json) is **failed**: code
explanation and code review completed, then the CI explanation returned an
incomplete response. Its post-cleanup ledger retained three attempts, one
failure and 264 micro-USD of configured estimates.

The [subsequent explanation run](openai-live-incomplete-explanation.json) is also
**failed**: its arithmetic request completed, then its code explanation was
incomplete. The ledger retained two attempts, one failure and 109 micro-USD of
configured estimates. Both failed batches stopped immediately without retries;
partial checks do not establish a passing tour. They expose no request or answer
text, credentials or account identifiers.

All requests used a 256-token output limit. The failed receipts preserve their
original 30-file source fingerprints and starting checkout identities,
including the earlier prompt versions. An earlier batch emitted only a generic failure and removed its
temporary ledger; its attempt count and charges are unknown and are not included
in the estimates above. The runner now saves a sanitized failed receipt after
gateway execution starts.

Short explanation prompts passed the
[eleven-example live run](openai-live-examples.json): Responses arithmetic, code
explanation, code review, CI diagnosis, Chat arithmetic, JSON extraction, release
note, Responses and Chat streaming, function declaration and speed priority.
All eleven uncached responses completed, with no failed attempts, retries,
executed tools, payments or completed jobs. The gateway estimate is 118 micro-USD
($0.000118), with no provider invoice reconciliation. The runner verified its
30-file source boundary before and after the run; the starting checkout remains
`92b34b43e4bf4d8489c042e0800d0354edc864fa`, with tested uncommitted changes
identified by their hashes. This success does not erase earlier failures or
establish model quality, customer savings, native-agent compatibility or a paid
customer experience.

This live run precedes the SQLite connection-cleanup fix. Its original receipts
remain unchanged and do not qualify the later gateway source. The synthetic
tour above was regenerated against that fix; no additional billed requests were
made to refresh these live receipts.
