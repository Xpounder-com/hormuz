---
name: hormuz-diagnose
description: Diagnose Hormuz connection failures, unsupported clients, configuration conflicts or unavailable owned runtimes without changing settings.
---

# Diagnose

1. Read [the shared workflow](../hormuz/references/workflow.md) for the selected client home and scope.
2. Check `hormuz client diagnose --help`, then run `hormuz --config CONFIG client diagnose codex --client-home HOME`. Add `--executable PATH` only for a known target executable.
3. Report the returned action, connection phase, configuration state and observed compatibility. Model readiness and task completion remain unobserved unless separate evidence establishes them.
4. Follow the shared workflow for an explicitly requested recovery. Do not stop external processes, delete journals or change privacy mode to clear a failure.
