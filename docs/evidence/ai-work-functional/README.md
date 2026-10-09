# AI Work implementation evidence

These artifacts exercise the actual implementation with local synthetic identity,
provider and payment fixtures. They establish implemented control mechanics and
local performance. They do not establish customer savings, model quality, live
payments, a signed distribution or production deployment readiness.

| Artifact | What it establishes |
| --- | --- |
| [receipt.json](receipt.json) | 35 real gateway checks, 13 simulated provider calls and source-file hashes |
| [local-benchmark.json](local-benchmark.json) | Bounded routing and reporting over 10,000 synthetic jobs and 99,990 attempts |
| [AI_WORK_BROWSER_QA.json](AI_WORK_BROWSER_QA.json) | 12 actual authenticated journey checks, three simulated provider responses, desktop/mobile rendering and 27 source hashes |
| [AI_WORK_DEMO.webm](AI_WORK_DEMO.webm) | Recorded connection, synthetic billing activation, budgets, ordinary API work, route/cache evidence, a cap, continuation and a signed workflow result |
| [PACKAGING_SMOKE.json](PACKAGING_SMOKE.json) | Fresh installed wheel runs outside the checkout, includes styles, starts and creates an authenticated job |

The browser seeds no cost. Its estimate is calculated from captured fixture usage
and configured synthetic rates. The passing run makes zero external network calls,
real-provider calls or payments; its signed Stripe and GitHub events are synthetic.
This is not an invoice or a customer saving. A completed workflow condition
establishes that declared condition; an HTTP success or cache hit never supplies
task completion.

Reproduce the gateway proof and local metadata benchmark from the source root:

```sh
python tools/ai_work_proof.py --output /tmp/hormuz-work-proof.json
python tools/ai_work_benchmark.py --output /tmp/hormuz-work-benchmark.json
python -m unittest discover -s tests -t . -q
```

Reproduce the browser journey with installed test dependencies, Playwright and
Chrome:

```sh
HORMUZ_QA_PYTHON=/path/to/project-venv/bin/python \
HORMUZ_QA_PLAYWRIGHT_MODULE=/path/to/playwright/index.mjs \
node website/scripts/ai-work-browser-qa.mjs
```

The [browser validation guide](../../AI_WORK_BROWSER_VALIDATION.md) explains the
local transport, recording and remaining production qualification boundaries.

The public website download must match `receipt.json` byte for byte and its
declared source hashes must match this source. The recorded demo remains labeled
by its own fixture conditions. Production qualification is defined in
[AI_WORK_PRODUCT.md](../../AI_WORK_PRODUCT.md).
