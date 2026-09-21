# Internal optimizer workload boundary

The private organization-owned
[`hormuz-code-optimizer`](https://github.com/Xpounder-com/hormuz-code-optimizer)
app runs outside this repository. Its GitHub App installation may be limited
to Hormuz to receive PR events and publish review checks. The app controller,
webhook secret, private key, job queue, model adapter, and candidate runner do
not live in this source tree or in the Hormuz gateway runtime.

This checkpoint covers local validation with synthetic inputs. Live model
candidate generation and transmission of repository source or profiling data
to a model service require separate explicit authorization. GitHub App
registration, installation, and operational qualification are separate work.

This repository contains the reference synthetic compaction workload at
`benchmarks/code_optimizer/compaction.py` and a test that keeps its cases
executable against the current Hormuz source. The app carries a trusted copy
of the workload so a candidate PR cannot alter the benchmark used to judge
its own edit. When updating the workload, review both copies and run the app's
cross-repository hash check with `HORMUZ_CODE_OPTIMIZER_TEST_REPO` pointing at
a local Hormuz checkout.

The trusted benchmark parent generates fixtures, measures batched round-trip
time, and hashes the returned outputs. A persistent child loads the exact
`hormuz/compaction.py` and `hormuz/compaction_formats.py` source bytes,
bypassing candidate package exports and bytecode caches. Both source files
are read and compiled before either candidate module executes, so helper
patches to source reads, compilation, or execution cannot replace the
measured module's snapshotted code object. The child receives bounded
requests over stdin and returns bounded responses over stdout. Its synthetic
package path supports relative imports without adding
the candidate root to Python's absolute import path, where forged bytecode
could shadow a standard-library module. Candidate Python cannot patch the
parent's clocks or hashes through shared modules. Before candidate import,
the child sets a zero process-creation hard limit; a macOS probe confirms
both `fork` and `posix_spawn` are denied. The private runner must still
confine the child process and the candidate's access to files, subprocesses,
and the network. That sandbox boundary needs separate review before
adversarial qualification. The child
inherits the runner's process group so outer timeout/cancellation cleanup can
reach it, but an adversarial child could call `setsid`; private containment
must cover forced parent death and such self-detachment. Profile
hotspots are child-reported guidance, not an acceptance signal.

For a private job, the trusted parent accepts `--seed-stdin`: one
newline-terminated, lowercase 64-character hex value representing 32 random
bytes. It reads this value before starting the candidate child and derives
timed-input markers with HMAC-SHA256. The raw seed is absent from child
arguments, environment, stdout, and benchmark reports. The controller must
reuse one job seed across baseline, candidate, and confirmation benchmarks so
their timed-output fingerprints remain comparable. Without the flag, the
reference workload uses deterministic variants for local offline checks.

The workload times each batch from the parent's first pipe write through its
full response-frame read. That interval includes pipe transport and the
child's JSON decoding, compaction calls, and JSON encoding. Parent request
encoding, response parsing, and output hashing happen outside the timed
interval. Path-list, line-run, search-line, and JSON-table cases include
small, typical, large, empty, malformed, duplicate, and Unicode inputs, plus
held-out correctness cases.
Empty and malformed inputs are checked for behavior but excluded from timing.
Every timed invocation gets a distinct, structurally equivalent input, and the
timed outputs are fingerprinted alongside the canonical outputs. This prevents
repeated-input cache hits from masquerading as a runtime gain. A result on
this helper does not establish a gateway or customer throughput improvement.
Held-out mixed line runs, framed search output, and a separate JSON table
must each compact and round-trip; mutation regressions prove that disabling
those paths changes their held-out correctness fingerprints.
The app's **Optimize** and **Super Optimize** controls differ in search depth;
both use the same behavior and performance acceptance rules. Neither mode
has a pricing, billing, or budget feature.
