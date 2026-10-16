---
name: koto
description: Manage Koto local routing, cloud-readiness preflight, connection status, troubleshooting, disconnect, or opt-in device-local history review, spending insights, deletion and export through the shared CLI.
---

# Koto

Use the installed Koto CLI as the owner of router setup and configuration
restoration. Read [the shared workflow](references/workflow.md) before running
commands. These instructions travel with this skill across agent hosts.

1. Identify the requested operation and target coding client. The implemented
   persistent adapter supports exact **Codex CLI 0.147.0 and 0.160.0** versions.
   For 0.160.0 execution, use the qualified single-agent HTTP launch profile in
   the shared workflow. Gemini CLI, Copilot, Claude Code,
   Cursor and OpenCode can load these instructions and manage that Codex
   connection; their own routing adapters remain unimplemented. For another
   target, report that limit before changing configuration.
2. Check `koto --version` and the target executable's version. If Koto is
   missing, use the source-install reference in the workflow; the plugin does
   not install Python, the router or models on activation.
3. Follow the shared `client setup` branch to generate a new local-only
   configuration from existing verified files, or the connect branch for an
   existing configuration. Use status/troubleshooting or disconnect as requested.
   For cloud authorization or readiness questions, follow the shared
   [cloud-readiness preflight](references/workflow.md#inspect-cloud-readiness)
   before proposing settings changes.
   Preserve the user's chosen client home, local-only policy and setup owner.
   For history, follow its separate consent/review branch in the shared workflow.
   For diagnosis, explanations, bounded probes, recommendations, policy staging
   or usage insights, read [capability workflows](references/capabilities.md).
4. Report the operation's public result, the connection/routing scope, any
   prerequisite still missing and the next safe action. A plugin being loaded
   or a router being ready does not establish a completed model task.
