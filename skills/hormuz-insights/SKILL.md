---
name: hormuz-insights
description: Review Hormuz’s opted-in local usage, estimated spending, unknown costs, history consent, deletion or local export.
---

# Insights

1. Read [the shared history workflow](../hormuz/references/workflow.md#device-local-history-and-spending).
2. Run `hormuz --config CONFIG usage insights` for metadata-only totals, observed period, token coverage and observations. Report only retained opt-in coverage; configured costs are estimates, local compute cost may be unknown, and savings are not measured by this report.
3. Enable capture only on explicit request. Honor requested disable/delete/export using existing history commands and the shared workflow. Retained content review is a direct local-terminal action for the user, never an agent tool call.
4. Do not infer private content from labels or upload exports. Disabling retains prior entries; deletion does not delete exported copies or separate accounting.
