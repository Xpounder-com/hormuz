# Find the way to Koto’s next plugin release

Labels: wayfinder:map

## Destination

Produce an implementation-ready specification for Koto’s next plugin release: user workflows, capability boundaries, evidence requirements, and release criteria. Finish when implementation can proceed without unresolved product decisions.

## Notes

- Conversation synthesis: [Koto plugin capabilities specification](spec.md). The spec does not resolve the open decision tickets or change this map's completion criteria.
- Planning only. Charting does not resolve decision tickets or authorize implementation. Resolve at most one non-research ticket per session.
- User approved considering all seven capabilities: diagnosis, request explanations, model selection, model qualification, usage insights, privacy and budget controls, maintenance and restoration. Their release placement is still a decision.
- First audience: individual developers using Codex CLI on macOS. Additional client routing adapters and team administration are later work.
- Host coding client owns coding tasks, tools, permissions and agent loop. Plugin guides workflows; router enforces policies within its supported interception scope. Plugin installation alone does not establish routing.
- Read-only diagnosis and explanations may run when relevant. Configuration changes require a preview and authorization through the user’s request. Cloud fallback, downloads, history capture and paid evaluations require explicit scope.
- Success must be evidenced: connect, diagnose a failure, explain an observed route, qualify a model for defined tasks, and restore owned configuration. Cost/token reductions are measured outcomes with unknown coverage stated.
- Qualification is task- and setup-specific, with bounded tool-use, structured-output and task-quality checks. Relevant client, runtime or model changes can invalidate it.
- Consult wayfinder for workflow; grilling and domain-modeling for decision sessions; prototype for prototype tickets. Keep proposed terminology distinct from the current source glossary until a decision resolves it.
- Source orientation: primary checkout is dirty. The inspected clean history-insights worktree was at bf0e6c8 when charting; it is a source reference, not proof of release or current deployed behavior. Recheck the selected baseline before implementation.
- Tracker: local Markdown fallback, per setup-matt-pocock-skills/issue-tracker-local.md. Child issue files carry Type, Labels, Status, Assignee and Blocked by metadata. Status open means unclaimed. Claim before work, resolve with an Answer and append only a gist/link here.
- Frontier query: scan issues by numeric prefix; take open, unassigned children only when every Blocked by issue is resolved. The map deliberately does not duplicate the open-ticket list.
- User agreements above are charting constraints, not closed-ticket resolutions.
- Honor network resource rules: bounded concurrency, explicit timeouts, no load testing or continuous polling; clean up owned resources.

## Decisions so far

No decision tickets resolved during charting.

## Not yet specified

- Exact implementation seams, tool schemas and compatibility migration work will emerge from the workflow and evidence decisions.
- Evaluation fixtures and useful thresholds beyond the agreed task/setup scope depend on the qualification decision.
- Meaningful optimization experiments and whether they justify later automation depend on observed coverage and the release selection.

## Out of scope

- Production implementation, deployment, publishing, or automatic activation during this planning effort.
- General coding-agent replacement, team administration and new client routing adapters in the first release.
- Universal model-quality claims, guaranteed savings and unsupported interception claims.
