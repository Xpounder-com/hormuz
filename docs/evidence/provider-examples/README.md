# Executed local provider tour

The [receipt](receipt.json) records all 21 scenarios passing through the actual
gateway: 25 inference requests, 22 synthetic provider replies, two exact answer
hits and one budget refusal. The correction is declared synthetic workflow
evidence. No model response closes a job.

The run used disposable loopback servers, SQLite state, synthetic credentials
and configured fixture rates. It made zero external provider calls or payments,
executed no tool actions, and establishes no customer savings or model quality.
Timing describes this small functional run, not a load or performance benchmark.

`source_commit` is the starting checkout revision. The run included uncommitted
changes; its 30 `source_files` hashes identify the actual working tree tested.
The receipt is retained as executed rather than relabeled with a later commit.

Reproduce from the current reviewed source:

```sh
python tools/ai_work_provider_examples.py --receipt NEW_RECEIPT.json
```

An existing receipt destination stops execution before provider requests. Live
provider qualification remains separate; follow [the provider guide](../../../examples/providers/README.md).
