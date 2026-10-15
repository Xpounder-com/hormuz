---
name: hormuz
description: Manage Hormuz local routing, connection status, troubleshooting, disconnect, or opt-in device-local history review, spending insights, deletion and export through the shared CLI.
---

# Hormuz

Use the installed Hormuz CLI as the owner of router setup and configuration
restoration. Read [the shared workflow](references/workflow.md) before running
commands. These instructions travel with this skill across agent hosts.

1. Identify the requested operation and target coding client. The implemented
   persistent adapter is **Codex CLI 0.147.0**. Gemini CLI, Copilot, Claude Code,
   Cursor and OpenCode can load these instructions and manage that Codex
   connection; their own routing adapters remain unimplemented. For another
   target, report that limit before changing configuration.
2. Check `hormuz --version` and the target executable's version. If Hormuz is
   missing, use the source-install reference in the workflow; the plugin does
   not install Python, the router or models on activation.
3. Follow the shared `client setup` branch to generate a new local-only
   configuration from existing verified files, or the connect branch for an
   existing configuration. Use status/troubleshooting or disconnect as requested.
   Preserve the user's chosen client home, local-only policy and setup owner.
   For history, follow its separate consent/review branch in the shared workflow.
4. Report the operation's public result, the connection/routing scope, any
   prerequisite still missing and the next safe action. A plugin being loaded
   or a router being ready does not establish a completed model task.
