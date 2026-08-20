# Hormuz threat model

Hormuz keeps its versioned threat register in [`security/threat-model.json`](../security/threat-model.json). The register covers the current CLI-first gateway, OIDC/session, policy, DLP, provider relay, accounting, persistence, build, and release boundaries. It deliberately records incomplete PostgreSQL, KMS, provider-cache, reverse-proxy, and multi-region systems while retaining the threats they must resolve before enterprise release. The deprecated built-in context experiment is outside the supported product threat model; its compatibility surface remains subject to the general controls in [SECURITY.md](../SECURITY.md) and [ADR 0008](decisions/0008-gateway-product-boundary.md).

Run the same fail-closed contract used by CI:

```bash
python scripts/threat_model_contract.py \
  --model security/threat-model.json \
  --project-root . \
  --output threat-model-evidence.json
```

The validator rejects duplicate or unknown fields, non-standard numbers, invalid or duplicate identifiers, missing STRIDE coverage, missing issue #9 incident scenarios, unresolved asset/boundary/threat links, evidence paths outside the repository, nonexistent evidence, and an unsupported threat status. It emits only the register digest and aggregate counts; it does not copy scenarios, architecture text, findings, prompts, credentials, or customer content into CI evidence.

The evidence file is created with mode `0600`, refuses symlinks where the platform supports `O_NOFOLLOW`, and never overwrites an existing path. Choose a new output path or deliberately remove stale local evidence before rerunning the command.

Ordinary CI retains the evidence for seven days. Tag verification runs the same contract again and retains it with the other release-verification artifacts for thirty days. A later publish job cannot turn an internally pending review into a completed one because tag verification must succeed first.

The separate [`operations/incident-drills.json`](../operations/incident-drills.json) catalog turns each required scenario into an exact repository-local regression and links it to the role-based procedures in [INCIDENT_RESPONSE.md](INCIDENT_RESPONSE.md). `scripts/incident_drill.py` executes those tests and emits only a catalog digest, aggregate counts, scope, and explicit false readiness flags. It does not convert an internal control regression into production incident-response, disaster-recovery, privacy-compliance, or external-communications evidence.

## Review boundary

This is an internal engineering threat model, not an independent security assessment. The register must remain `independent_review.status=pending` until an identified independent reviewer completes the work and repository evidence records either resolved findings or explicit product-owner risk acceptance. The validator refuses a completed-review claim without that evidence.

Likewise, `enterprise_release_ready=false` remains mandatory while any threat is open or partially mitigated, or while independent review is pending. A green threat-model contract proves that known risks and evidence links are structurally complete; it does not prove that the risks have been eliminated.

## Updating the register

Update the model when a new trust boundary, durable store, credential class, provider endpoint, connector, cache, deployment target, or privileged role enters scope. Each threat must identify affected assets and boundaries, one STRIDE category, current status, concrete control evidence, residual risk, and its release gate. Do not convert an open risk to mitigated based on documentation or a narrow unit test when the stated deployment boundary remains unimplemented.
