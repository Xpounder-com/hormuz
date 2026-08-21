# Migrating the deprecated context experiment

Hormuz 0.2 removes the experimental context-pack kernel from the core gateway distribution and runtime. The core `hormuz` wheel has no context retrieval, lifecycle, cache, provenance, memory, or content-storage implementation. Normal gateway startup does not import experimental modules or create context storage.

The experiment remains in this repository as the separately buildable `hormuz-context-experiment` package. It is outside the core release, security, and operational claims.

## Compatibility behavior

During the 0.2 release line, the former command is a thin migration shim:

```text
hormuz context-pack ...
```

It exits with status `2` and the stable error code `context_experiment_moved`. It does not load the gateway configuration, initialize any storage, or execute retrieval. The shim will be removed before Hormuz 0.3.

There were no active context HTTP routes in the core gateway. Requests to a `/v1/context/...` path remain ordinary `404 not_found` responses.

## Move an experimental workflow

From a checkout of this repository, install matching releases in the same environment:

```bash
python -m pip install .
python -m pip install ./experiments/context
```

Then invoke the separate command with the former arguments:

```bash
hormuz-context-experiment --config hormuz.json context-pack \
  --records path/to/context-records.jsonl \
  --query "Your question" \
  --organization your-organization \
  --actor your-actor \
  --token-budget 2000
```

The experiment emits content-bearing output. It should not be assumed to have the core gateway's metadata-only audit, persistence, deployment, or support boundary.
