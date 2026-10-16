---
name: hormuz-diagnose
description: Diagnose Hormuz connection failures, cloud authorization blockers, unsupported clients, configuration conflicts or unavailable owned runtimes without changing settings.
---

# Diagnose

1. Read [the shared workflow](../hormuz/references/workflow.md) for the selected client home and scope.
   For cloud authorization blockers, use its [cloud-readiness preflight](../hormuz/references/workflow.md#inspect-cloud-readiness) and report each path's blockers before proposing settings changes.
   If an administrator enabled native catalog discovery, report it as discovery only; native inference and account/host qualification remain separate blockers.
2. Read [override inspection](../hormuz/references/capabilities.md#diagnose-and-explain) when investigating project, profile or launch conflicts. Run diagnosis for the selected project and known routing flags using the documented arguments. Add `--executable PATH` only for a known target executable.
3. Report the returned action, connection phase, configuration state, observed compatibility and conflict source/key names. Actual running-session routing, model readiness and task completion remain unobserved unless separate evidence establishes them. Preserve explicit inspection errors.
4. Follow the shared workflow for an explicitly requested recovery. Do not stop external processes, delete journals or change privacy mode to clear a failure.
