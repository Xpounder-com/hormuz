# Codex startup incident and explicit Hormuz chat qualification

## Observed failure and reproduced causes

The user reported a desktop restart blocked by “Organization settings could
not be loaded.” The installed desktop's launch construction uses:

```text
-c features.code_mode_host=true app-server --analytics-default-enabled
```

The old bridge rejected the leading `-c` before native initialization. The
exact-launch regression first failed with `unrecognized arguments: -c` and now
passes the initialize, configRequirements/read and config/read sequence.
An independent local-catalog 503 also prevented initialization; it now returns
control to the native engine before consuming any protocol frame. Native policy
decisions remain owned by that engine; no policy success is fabricated.

Normal-app installation is withdrawn. The CLI rejects real startup activation,
including preview and an isolated-test flag paired with the real launch
controller. Tests use temporary homes and a fake launch controller. Existing
`hormuz off` recovery remains compatible. That first check used desktop engine
0.162.0-alpha.17.2 and terminal Codex 0.160.0 without changing either installation.

## Replacement boundary

The Hormuz skill is a single explicit chat entry. Its task command delegates
the supplied task to a qualified local route or configured router default.
The native host model, account, tools, approvals and reasoning remain in Codex.
There is no startup hook, native model-picker injection or second tool loop.
See [the chat workflow](../HORMUZ_CHAT.md).

The first real task exposed a four-second response-header timeout. It failed
inside Hormuz, was not retried and its owner was stopped. A delayed-header
regression reproduced it. Connection establishment stays bounded separately
from the absolute task deadline. The second, fresh live check succeeded:

- Qwen returned `APP_LOCAL_OK: 7` at requested max effort.
- Public routing evidence recorded exactly one new successful local attempt,
  with no new retries, failovers or unknown attempts in that check.
- Plain `hormuz off` stopped the isolated router and owned model process.
- The current normal settings and app executable stayed unchanged. The bridge
  and launch hook remained disabled throughout.

Live proof: the machine-local connection evidence directory
`safe-chat-verification-20261016-r2/verification.json`. This is an explicit task
check, not a claim of visible desktop picker verification or general model
coding quality. The user subsequently selected one Hormuz chat entry with
router/model choices. Installation enables only that skills plugin after the
package checks, preserving all other recovered settings and leaving routing off.

A subsequent check used the installed Hormuz skill from the active Codex chat.
The user confirmed that the single Hormuz entry and its new starter prompt were
visible in the running app. The app then bundled 0.163.0-alpha.10.2; it stayed
untouched. The existing supported terminal CLI 0.160.0 only initialized the
separate router owner, without running another Codex conversation.

- Configured router returned `ROUTER_CHAT_OK: 7` with `selection: router_default`.
- Explicit Qwen returned `QWEN_CHAT_OK: 7` with `selection: model`.
- Both actual destinations were local Qwen, the configured default, at requested
  max effort; exactly two new attempts succeeded with no retries or failovers.
- Plain `hormuz off` stopped both owned processes. A subsequent chat operation
  was rejected before submission.
- The same desktop process remained running. Normal configuration bytes,
  plugin metadata, launch environment and app executable stayed unchanged.

That bounded live proof is retained in the machine-local
`live-chat-choices-20261016-3_nlap01/verification.json` evidence directory. Visible
entry verification came from the user; no desktop UI automation was performed.
The host model still owns the chat and tools. These checks establish explicit
task delegation, not native per-model provider switching or general coding quality.

## Automated release boundary

The existing workflow-test and installed-wheel CI jobs discover the new
cold-start and explicit-task tests; no separate unwired test command was added.
Coverage includes actual desktop option ordering, policy/config startup RPCs,
catalog outage, activation rejection, model qualification, provider ownership,
configured default, reasoning, delayed headers, bounded inputs, no retries or
cloud fallback, and disconnect. Versioned CLI receipts have compatibility
fixtures. Source and installed-wheel results are recorded with the local
verification evidence.

The missed boundary was desktop cold startup: an app-server model response
did not prove the desktop's startup command or organization-policy gate worked.
Any future native picker implementation requires a disposable full-app test
environment and independent visible-app evidence before normal-app activation.
