---
name: hormuz-model
description: Recommend an existing local model using current setup-bound probe evidence and hardware facts, or explain why requalification is needed.
---

# Model

1. Read [the capability workflow](../hormuz/references/capabilities.md). Identify the same setup assets and the explicit user-owned qualification report.
2. Run the documented `model recommend` command. It validates report structure, setup identity, expiry and live router generation as described in the workflow, without making model requests.
3. Report `consider_for_tested_envelope` or `requalify`, the evidence scope, available system memory and model file size. Inference memory fit remains unverified; do not turn file-size fit into a quality claim.
4. Do not download models, enroll routes, migrate continuation state or switch a connected client's configuration from a recommendation alone.
