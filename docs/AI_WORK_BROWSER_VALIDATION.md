# Reproduce the AI Work customer journey

This development check runs the actual authenticated gateway UI and its HTTP
forms against disposable local fixtures. It does not contact a real provider,
Stripe, GitHub, an email service, a booking service, or an advertising service.

The browser runner requires an installed Python environment with project test
dependencies, Playwright, and Chrome. The Browser plugin is not available in the
recorded environment, so this check uses regular Playwright. No browser dependency
or Chrome installation is added to the customer package.

```sh
HORMUZ_QA_PYTHON=/path/to/project-venv/bin/python \
HORMUZ_QA_PLAYWRIGHT_MODULE=/path/to/playwright/index.mjs \
node website/scripts/ai-work-browser-qa.mjs
```

The runner starts `tools/ai_work_browser_fixture.py`, completes the existing
synthetic OIDC console sign-in, and keeps its temporary cookie and credential
in a mode-0600 temporary file. All browser requests are intercepted. The
synthetic HTTPS origin is served from loopback; it does not prove DNS, TLS,
a live identity provider, or a deployment. The real Checkout form returns its
server-owned validated 303 destination; the runner turns that intercepted
redirect into a fresh local browser navigation so Playwright intercepts the
checkout request itself. No Stripe network transport occurs in the passing run.

The recorded journey confirms explicit source consent, connection qualification,
server-created checkout references, synthetic signed subscription and invoice
activation, workspace budget and speed priority, exploration choice, job creation,
a configured signed GitHub object association, ordinary OpenAI Responses requests,
exact reuse, corrective recurrence, budget denial, approved continuation, signed
merge completion, and owned job/support receipts. The cost comes from configured
synthetic rates and observed fixture usage, not seeded money or a provider bill.

The provider account evidence stays unavailable because a loopback provider is
not a qualified first-party provider account. The real-owner finance adapter
fixture is covered separately by `tests/test_work_learning_accounting.py`; its
aggregate is not allocated to the browser job. Missing attempt confirmations stay
unknown, including a zero subtotal with no confirmed attempts.

`docs/evidence/ai-work-functional/AI_WORK_BROWSER_QA.json` records the actual
conditions, source hashes, checks, viewports, and cost. The WebM and PNG files in
that directory come from the same successful run. Public copies must remain byte
identical; website tests enforce source freshness and artifact equality. Rerun
this check after changing its source or the UI, then regenerate the mechanics
receipt after all gateway source changes are frozen. Do not present either run
as a customer result, installed native-agent qualification, billed saving, live
payment, or production performance result.
