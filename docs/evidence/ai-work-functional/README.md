# AI Work implementation evidence

These artifacts exercise the actual implementation with local synthetic identity,
provider and payment fixtures. They establish control mechanics and observed
timing for their recorded source snapshots. They do not establish customer
savings, model quality, live payments, a signed distribution or production
deployment readiness.

The bounded gateway, provider tour, browser journey and installed SDK executions
were regenerated after the reviewed billing portal and durable checkout fixes.
Their source hashes identify the tested runtime, including its request, storage,
configuration-loader and stream guards.

| Artifact | What it establishes |
| --- | --- |
| [receipt.json](receipt.json) | 35 real gateway checks, 13 simulated provider calls and source-file hashes |
| [local-benchmark.json](local-benchmark.json) | Historical predecessor measurement over 10,000 synthetic jobs and 99,990 attempts; does not qualify this candidate |
| [AI_WORK_BROWSER_QA.json](AI_WORK_BROWSER_QA.json) | 12 actual authenticated journey checks, three simulated provider responses, desktop/mobile rendering and 28 source hashes |
| [AI_WORK_DEMO.webm](AI_WORK_DEMO.webm) | Recorded connection, synthetic billing activation, budgets, ordinary API work, route/cache evidence, a cap, continuation and a signed workflow result |
| [PACKAGING_SMOKE.json](PACKAGING_SMOKE.json) | Offline wheel/source build, isolated installed imports/CLI/styles, owned SQLite history/totals/restart, and a source-matched installed HTTP smoke |
| [INSTALLED_HTTP_SMOKE.json](INSTALLED_HTTP_SMOKE.json) | Exactly two authenticated local installed-gateway requests: create and read an owned job; zero provider inference or external calls |

The browser seeds no cost. Its estimate is calculated from captured fixture usage
and configured synthetic rates. The passing run makes zero external network calls,
real-provider calls or payments; its signed Stripe and GitHub events are synthetic.
This is not an invoice or a customer saving. A completed workflow condition
establishes that declared condition; an HTTP success or cache hit never supplies
task completion.

The package receipts name clean source snapshot `d78f416c3d118dd9481286c96f4ca74657c94776`
used for the offline build. Their 33 runtime/example and 10 packaging-source fingerprints identify the tested
files; later commits that retain those fingerprints add evidence metadata. The installed
HTTP smoke makes exactly two local create/read requests and performs no provider
inference. These unsigned artifacts do not qualify a native release or updater.

The retained metadata benchmark identifies older source fingerprints. Its
latency numbers are historical and must not be used for this candidate or as
customer quality, savings or speed evidence. Repeating its large seeded workload
requires explicit benchmark authorization. Gateway timing evidence comes from
the bounded mechanics proof for its recorded source snapshot; successor source
changes require regeneration and fingerprint verification.

Reproduce the bounded gateway proof from the source root:

```sh
python tools/ai_work_proof.py --output /tmp/hormuz-work-proof.json
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
