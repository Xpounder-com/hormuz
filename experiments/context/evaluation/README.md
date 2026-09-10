# Context optimization evaluation

An offline experiment to select a small context-optimization feature for Hormuz.
No core gateway code, dependency, deployment, or persistent content store changes.
The parent context package is already experimental; this directory is not added
to its package or CLI. The core source manifest prunes `experiments`.

Read [FINDINGS.md](FINDINGS.md) for the decision and limitations. Machine-readable
results are in [results.json](results.json). All fixtures are generated synthetic
data; none are captured user requests, provider responses, or customer content.

The current coding assignment is specified in revision 3 of the
[implementation handoff](../../../docs/CONTEXT_COMPACTION_IMPLEMENTATION_HANDOFF.md):
implement the client-side feature with an On/Off toggle in the main Hormuz
product release, preserving gateway governance without server-side optimization.
This directory remains historical evaluation evidence, not the shipping package.

## Reproduce

Run from the Hormuz repository root. Setup downloads public source, a tokenizer,
and tokenizer vocabularies. The evaluator itself rejects network connections.
Use fresh temporary directories if these paths already contain unrelated work.

```sh
git clone https://github.com/headroomlabs-ai/headroom.git /private/tmp/headroom-eval-source
git -C /private/tmp/headroom-eval-source checkout --detach e67b3c8a29443a60d6b0018fb22f525c5cd7e709
python3 -m venv /private/tmp/hormuz-context-eval-venv
/private/tmp/hormuz-context-eval-venv/bin/python -m pip install tiktoken==0.12.0
export TIKTOKEN_CACHE_DIR=/private/tmp/hormuz-context-tokenizer-cache
/private/tmp/hormuz-context-eval-venv/bin/python -c 'import tiktoken; [tiktoken.get_encoding(n) for n in ("cl100k_base", "o200k_base")]'
export HEADROOM_SOURCE=/private/tmp/headroom-eval-source
/private/tmp/hormuz-context-eval-venv/bin/python experiments/context/evaluation/evaluate.py --headroom-source "$HEADROOM_SOURCE" --output /private/tmp/hormuz-context-eval-results.json
/private/tmp/hormuz-context-eval-venv/bin/python -m unittest discover -s experiments/context/evaluation -p 'test_*.py' -v
```

The run verifies the upstream Git commit and SHA-256 of both loaded modules.
Those modules use the Python standard library; the evaluator deliberately loads
them directly without executing Headroom's package initializer. This measures
specific source helpers, not the installed package, SmartCrusher's Rust engine,
automatic ContentRouter classification, ML compression, or a proxy integration.
No upstream implementation is vendored into Hormuz. The JSON table baseline is
a small independent implementation for comparison, not Headroom SmartCrusher.

## What the guards mean

- Text candidates must reconstruct the exact original; ANSI stripping and diff
  bookkeeping removal are rejected under this stricter rule.
- The native table baseline only accepts canonical JSON arrays of objects with
  identical ordered keys. It keeps every row, value, type, and key order. Duplicate
  keys, nonfinite values, noncanonical formatting, and heterogeneous shapes pass
  through. It is not an arbitrary JSON or code compressor.
- A candidate must reduce tokens under both test encodings. Request-envelope
  probes repeat the guard over the whole serialized JSON, including escaping.
- The fixture adapter only edits explicitly selected string tool-result fields
  in Chat Completions, Responses, and Anthropic message shapes. It does not infer
  tool relevance or classify formats. It is not a gateway hook.
- Reconstruction is a programmatic check, not proof that an LLM interprets a
  compact representation correctly. No model answer quality or billed savings
  is measured. Tokenizer encodings are comparators, not an Anthropic tokenizer or
  an exact reproduction of provider-internal request serialization.

Tests include exact reconstruction, a rare error among repetitions, marker
collisions, mixed JSON types, quoted/Unicode values, protocol-field preservation,
input nonmutation, token noninflation, and upstream schema counterexamples.
