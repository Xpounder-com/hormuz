---
name: hormuz-policy
description: Preview local-only privacy or monthly configured-cost budget changes and activate them through Hormuz’s shared connection workflow.
---

# Policy

1. Read [the shared workflow](../hormuz/references/workflow.md) and [the capability workflow](../hormuz/references/capabilities.md).
2. Select a new output configuration beside the current configuration. Preview with `policy configure --output OUTPUT --dry-run` and the requested `--local-only` and/or `--monthly-budget-usd AMOUNT` options. Report changed fields and estimate-based budget limits.
3. A matching request authorizes staging the identical command without `--dry-run`. Staging is not activation. Preserve both the original configuration and the user's client home.
4. To activate an authorized change, disconnect through the existing owner, preview connection to the staged configuration, then connect and inspect status. If reconnect fails, report that failure and restore the previous connection through its public workflow when within the user's request; never claim the new policy is active.
5. A new configuration has separate history consent. Do not transfer it. Cloud fallback, content capture, downloads and paid probes require explicit scope.
