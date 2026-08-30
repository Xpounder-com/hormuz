# Preview a policy change and inspect its evidence

Start with the stable-source quickstart, then run the zero-network workflow:

```bash
hormuz policy demo
```

The real output shows a standard baseline and strict local candidate, valid
documents, three semantic changes, and two scenario changes: an 8,000-token
request becomes capped at 4,000, and the synthetic `demo-deep` model becomes
denied. It reports zero network calls, provider credentials, and managed policy
mutations. This uses disposable SQLite state, not a managed PostgreSQL store.

To retain owner-only synthetic artifacts, choose a **new** directory:

```bash
hormuz policy demo --output policy-demo
```

Inspect the generated candidate, scenario results, and baseline before proposing
a change. The command prints managed follow-up examples, but does not execute
them. Do not paste those examples into a live environment without the authority,
organization, current version, and prerequisites described in the maintained
[policy-control guide](../../docs/POLICY_CONTROL.md).

## Move from a tour to a managed change deliberately

1. Name the policy owner and desired model/budget/secret outcome. Keep identity
   facts separate from policy authority.
2. Use the built-in template/create/check/preview/scenario workflow documented
   in the policy guide. Lower scopes can tighten, not weaken, parent controls.
3. Verify PostgreSQL roles, policy-admin credentials, organization, and active
   version before any shared mutation. Use the documented compare-and-swap
   activation guard; do not invent a SHA or bypass a stale-version error.
4. Execute only an approved, bounded change. Inspect show/history/export and
   the documented rollback procedure before expanding scope.
5. For governed inference use, inspect current-month metadata with:

```bash
hormuz --config hormuz.json status --group-by model
hormuz --config hormuz.json status --group-by person --json
```

These report only captured gateway traffic in the current UTC month. Cost is a
configured-rate-card estimate. Token volume is not productivity or work quality.
Policy-admin events and generation-usage events are different evidence surfaces.

The website's [synthetic JSONL sample](https://xpounder-com.github.io/hormuz/demo/#evidence)
comes from a separate provider-free gateway run. See [audit](../../docs/AUDIT.md),
[usage](../../docs/USAGE.md), and [policy usability](../../docs/POLICY_ADMIN_USABILITY.md)
for the precise contracts. Neither tutorial counts toward the independent,
exact-archive [onboarding study](../../docs/EXTERNAL_ONBOARDING.md).
