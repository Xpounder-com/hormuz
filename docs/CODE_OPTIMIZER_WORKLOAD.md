# Internal optimizer workload boundary

The private organization-owned
[`hormuz-code-optimizer`](https://github.com/Xpounder-com/hormuz-code-optimizer)
app runs outside this repository. Its GitHub App installation may be limited
to Hormuz to receive PR events and publish review checks. The app controller,
webhook secret, private key, job queue, model adapter, and candidate runner do
not live in this source tree or in the Hormuz gateway runtime.

This repository contains the reference synthetic compaction workload at
`benchmarks/code_optimizer/compaction.py` and a test that keeps its cases
executable against the current Hormuz source. The app carries a trusted copy
of the workload so a candidate PR cannot alter the benchmark used to judge
its own edit. When updating the workload, review both copies and run the app's
cross-repository hash check with `HORMUZ_CODE_OPTIMIZER_TEST_REPO` pointing at
a local Hormuz checkout.

The workload measures `compact_text` helper runtime for path-list, line-run,
and search-line cases. It includes small, typical, large, empty, malformed,
duplicate, and Unicode inputs, plus held-out correctness cases. A result on
this helper does not establish a gateway or customer throughput improvement.
The app's **Optimize** and **Super Optimize** controls differ in search depth;
both use the same behavior and performance acceptance rules. Neither mode
has a pricing, billing, or budget feature.
