---
name: koto-qualify
description: Evaluate an existing local Responses route with bounded task, structured-output and tool-use probes; detect stale model qualification evidence.
---

# Qualify

1. Read [the capability workflow](../koto/references/capabilities.md). Establish the existing configuration, local alias, client executable, runtime executable, model file and a new private report destination.
2. Preview the bounded scope from the workflow, including model probes and router-state reads. A request to run these local probes authorizes that scope. Existing router policy still applies; denial does not authorize bypassing qualification admission.
3. Run `model qualify --help` and then the documented command. Never manufacture reviewed qualification declarations from this report.
4. Report every failed/unknown check, execution identity and the narrow synthetic box-count scope. A pass is not coding-quality certification, endpoint binary attestation or automatic route authorization. An error or exit code 2 is not success.
