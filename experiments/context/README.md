# Hormuz Context Experiment

This is the former explicit context-pack kernel, retained as a separate experimental distribution. It is not part of the core `hormuz` gateway, is never imported by normal gateway startup, and is outside the core release and security claims.

The experiment reads content-bearing JSONL records and writes a content-bearing pack. Treat its inputs and outputs as company data and deploy it only in an appropriate environment.

## Install from this repository

Install the matching core release first, then the experiment:

```bash
python -m pip install .
python -m pip install ./experiments/context
```

## Run

```bash
hormuz-context-experiment --config hormuz.json context-pack \
  --records examples/context-records.jsonl \
  --query "How should API retries work?" \
  --organization xpounder \
  --actor alice \
  --repository Xpounder-com/hormuz \
  --branch main \
  --token-budget 2000 \
  --policy-version engineering-v1
```

The former core command returns `context_experiment_moved` through Hormuz 0.2.x. See the core migration note at [`../../docs/CONTEXT_EXPERIMENT_MIGRATION.md`](../../docs/CONTEXT_EXPERIMENT_MIGRATION.md).
