---
name: koto-maintain
description: Check Koto client compatibility before reconnecting, recover a connection, or disconnect and restore owned settings safely.
---

# Maintain

1. Read [the shared workflow](../koto/references/workflow.md). Identify the original client home and, if applicable, the owned generation.
2. Use `client diagnose codex --client-home HOME --executable PATH` to check a selected target before reconnecting. Diagnose is read-only and does not install updates.
3. Follow the shared connect/disconnect branches for requested changes. Preserve newer user edits, retained settings and external engines.
4. Completed disconnect requires disconnected/stopped/not-owned status and a stopped owned local runtime where applicable. Report pending recovery as pending. Disconnect before uninstalling the plugin or binary.
