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

This repository contains the reference synthetic compaction fixtures at
`benchmarks/code_optimizer/compaction.py`. The public module exports
`cases()`, `heldout_cases()`, and
`timed_variant(name, value, ordinal, seed=None)`; their case names and return
shapes form the shared fixture contract. The private app must carry a
byte-identical trusted copy, import these pure functions, and implement
candidate execution, measurement, hashing, sandboxing, and cleanup in its own
Worker. Review both copies when updating the fixture and run the app's
cross-repository hash check with `HORMUZ_CODE_OPTIMIZER_TEST_REPO` pointing at
a local Hormuz checkout.

The public CLI's `--root` must identify the checkout containing that CLI
file. It rejects another checkout and symlink or junction aliases in the root
or local source paths, and it has no candidate child mode. `validate`,
`heldout`, `profile`, and `benchmark` load exact
`hormuz/compaction.py` and `hormuz/compaction_formats.py` source bytes from
that same checkout, bypassing package exports and bytecode caches. The CLI is
for a trusted maintainer checkout and does not confine adversarial code.
It opens each source once as a regular file, checks file and path identity
before and after a read capped at 1 MiB, and compiles those bytes. A changed source
is rejected; this does not promise containment against a hostile process
rapidly replacing ancestor directories.
Invoke it with an isolated interpreter, for example
`python3 -I -S benchmarks/code_optimizer/compaction.py --root . --action validate`.
Without both flags it exits before non-built-in imports, so adjacent Python
source or legacy bytecode cannot shadow its standard-library imports.
Its JSON report identifies the local source path, and profile output may name
source files. Keep raw local reports in the trusted workspace; publish only
content-free counts and digests.
The private Worker must independently protect its trusted fixture copy,
isolate candidate code, verify exact candidate source, bound its protocol,
and clean up the candidate on every outcome. This public reference does not
qualify those private gates.

For reproducible local work, omit `--seed-stdin`. The optional flag accepts
one newline-terminated, lowercase 64-character hex value representing 32
bytes. HMAC-SHA256 derives content-distinct timed inputs. The raw seed is
absent from stdout and reports. The private Worker must generate one
unpredictable seed per job, reuse it across baseline, candidate, and
confirmation runs, and keep it out of candidate arguments and environment.
The public CLI does not hand a seed to candidate code because it does not
execute candidates.

The public `benchmark` action times direct local `compact_text` calls after
inputs are prepared, excluding fixture generation and output hashing.
Its peak RSS is the local process's `ru_maxrss`: bytes on macOS, and KiB
converted to bytes on Linux and FreeBSD. It is `null` when Python has no
`resource` module or the platform's units are unknown. These local samples
confirm finite, well-formed helper measurements; they are not directly
comparable to the private Worker's sandboxed IPC measurements or an
acceptance threshold.
Profile hotspots are advisory. The private app owns its measurement boundary
and must prove that real synthetic gains cross its unchanged >5% and 4×MAD
rule while regressions are rejected.

Path-list, line-run, search-line, and JSON-table cases include small,
typical, large, empty, malformed, duplicate, and Unicode inputs, plus
held-out correctness cases. Empty and malformed inputs are checked for
behavior but excluded from timing. Every timed invocation gets a distinct,
structurally equivalent input, and timed outputs are fingerprinted alongside
canonical outputs. Held-out mixed line runs, framed search output, and a
separate JSON table must each compact and round-trip; mutation regressions
prove disabling those paths changes their held-out fingerprints. A result on
this helper does not establish a gateway or customer throughput improvement.

The app's **Optimize** and **Super Optimize** controls differ in search
depth; both use the same behavior and performance acceptance rules. Neither
mode has a pricing, billing, or budget feature.
