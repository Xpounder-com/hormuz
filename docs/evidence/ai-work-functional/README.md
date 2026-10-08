# AI Work implementation evidence

These artifacts exercise the actual implementation with local synthetic identity,
provider and payment fixtures. They establish implemented control mechanics and
local performance. They do not establish customer savings, model quality, live
payments, a signed distribution or production deployment readiness.

| Artifact | What it establishes |
| --- | --- |
| [receipt.json](receipt.json) | 35 real gateway checks, 13 simulated provider calls and source-file hashes |
| [local-benchmark.json](local-benchmark.json) | Bounded routing and reporting over 10,000 synthetic jobs and 99,990 attempts |
| [AI_WORK_BROWSER_QA.json](AI_WORK_BROWSER_QA.json) | Actual authenticated dashboard interactions and mobile rendering |
| [AI_WORK_DEMO.webm](AI_WORK_DEMO.webm) | Recorded dashboard running against a local identity/provider fixture |
| [PACKAGING_SMOKE.json](PACKAGING_SMOKE.json) | Fresh installed wheel runs outside the checkout, includes styles, starts and creates an authenticated job |

The browser's seeded $1.25 is an estimated fixture amount. It is not an invoice or
a customer's saving. A completed workflow condition establishes that declared
condition; an HTTP success or cache hit never supplies task completion.

Reproduce the gateway proof and local metadata benchmark from the source root:

```sh
python tools/ai_work_proof.py --output /tmp/hormuz-work-proof.json
python tools/ai_work_benchmark.py --output /tmp/hormuz-work-benchmark.json
python -m unittest discover -s tests -t . -q
```

The public website download must match `receipt.json` byte for byte and its
declared source hashes must match this source. The recorded demo remains labeled
by its own fixture conditions. Production qualification is defined in
[AI_WORK_PRODUCT.md](../../AI_WORK_PRODUCT.md).
