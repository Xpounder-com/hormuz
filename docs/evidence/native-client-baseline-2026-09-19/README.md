# Native companion baseline checkpoint: 2026-09-19

Partial evidence for [#332](https://github.com/Xpounder-com/hormuz/issues/332),
planned for v1.4.0. These measurements do not qualify a release or set regression
budgets. The Mac artifact is the complete released v1.2.0 arm64 app; the Windows
artifact is an unsigned x64 synthetic shell from an earlier CI build for
[#350](https://github.com/Xpounder-com/hormuz/pull/350). Their different feature
sets and architectures prevent a performance comparison.

## Artifact identity and installed content

[static-artifacts.json](static-artifacts.json) records exact sources, hashes,
component sizes, imports, platform and method. The Mac archive checksum matches
both the GitHub asset digest and release manifest; GUI/helper hashes match the
published distribution proof. Recursive strict signature verification passes
outside the restricted tool sandbox. No artifact was launched during this
static pass; subsequent Mac preview samples are described below.

| Artifact/component | Logical bytes |
| --- | ---: |
| Mac v1.2.0 downloaded ZIP | 13,093,862 |
| Mac extracted app, 16 regular files | 18,113,696 |
| Mac native GUI executable | 2,599,952 |
| Mac bundled context backend | 10,072,976 |
| Mac resources, including tokenizer data | 5,432,484 |
| Mac signature and bundle metadata | 8,284 |
| Earlier #350 Windows synthetic preview executable | 248,320 |

Logical file sizes come from regular-file `st_size`, not APFS allocation or
runtime memory. Mac imports were inspected with `otool -L`. The GUI imports
Apple system libraries/frameworks. The context backend's libSystem/libz
bootloader imports do not describe its embedded Python/tokenizer payload.

Windows source is CI merge `4ea91d7b598b63c8feed19221e68c955e96fcd78` for proposed
head `60f5f9a8fe8f8de82ba5a69b9bd381bcf3664bc0`. Its executable hash was checked
against the [CI artifact's provenance](https://github.com/Xpounder-com/hormuz/actions/runs/35467195983/artifacts/10591208374).
Unique case-normalized imports from `xcrun llvm-objdump --private-headers`
matched the CI `dumpbin /dependents` inventory. Review of an earlier binary
found a `VCRUNTIME140.dll` prerequisite; the corrected binary statically links
the MSVC runtime and imports only Windows system DLLs. Artifact retention is
14 days; the source, hash and numeric record remain here after download expiry.
The native Windows CI smoke passed with a registered tray icon. The Windows
binary was not run on this Mac and its runtime footprint is unmeasured.
The later [merged-main #350 artifact and short CI runtime observations](https://github.com/Xpounder-com/hormuz/issues/332)
are recorded separately in #332; they do not change this historical artifact's
source, hash or byte count.

## Preliminary Mac idle samples

Each recording uses the released v1.2.0 GUI, SHA-256
`2ece94a031a039d0e27c7ce873787f83f704045e44b0e63cedb71841b9bb3ecc`, from source
`d854a5a453fcbe20cb3f4c1e261e146f2da93855`. The launch requests a synthetic
`connected` preview at companion UI scale 1. Source review confirms that this
preview skips the saved-session restoration/refresh task. No real sign-in,
provider request, relay, optimizer or user interaction is part of this sample.

The scenarios are **requested launch modes**. The screen and display scale
were not independently inspected, so these recordings do not prove visual
state, folding/reopening acceptance or a particular display configuration.
Each scenario has one run, with a 60-second warm-up followed by 300 seconds of
sampling at five-second intervals. This is preliminary evidence, not the
three-run baseline required by the protocol below.

Host: Mac17,2, Apple M5, 10 logical CPUs, 32 GiB memory; arm64 macOS 26.2
(25C56). The collector uses Python 3.14.0 and the OS `/bin/ps` counters.
Other desktop applications were active. A power snapshot taken during the
collection session reported AC Power; power mode, thermal state and observer
overhead were not measured. These are uncontrolled developer-workstation
conditions.

| Requested mode | Raw samples | RSS range (MiB) | First to last RSS (KiB) | Max processes | CPU counter delta |
| --- | ---: | ---: | ---: | ---: | ---: |
| hidden | [61](macos-hidden-idle.json) | 99.312–99.359 | 101,696 → 101,696 | 1 | 0.00 s |
| visible | [61](macos-visible-idle.json) | 106.000–106.094 | 108,544 → 108,592 | 1 | 0.00 s |
| folded | [61](macos-folded-idle.json) | 104.719–104.719 | 107,232 → 107,232 | 1 | 0.00 s |

RSS is the sum of reported resident KiB for the application process, recursively
observed descendants, and processes whose executable is inside this exact app
bundle. It can double-count shared pages and is not physical footprint. A
process that appears and exits between samples can be missed. PID sets and
command names are used only in memory; records retain numeric aggregates.

CPU is `100 * (last CPU seconds - first CPU seconds) / elapsed seconds`, in
percent of one core. `/bin/ps` reports cumulative CPU time with limited
precision; a reported zero means no increase at that precision, not proof of
zero CPU use, timers, wake-ups or energy. If an observed process departs, the
collector returns no CPU percentage because summed cumulative counters are no
longer comparable. Memory deltas over this interval do not establish a leak
or the absence of interaction-related growth.

## Repeat these preview samples

1. Use a fresh directory for each repeat. Download the v1.2.0 Mac release ZIP,
   `SHA256SUMS.txt` and `macos-distribution-proof.json` from the
   [same release](https://github.com/Xpounder-com/hormuz/releases/tag/v1.2.0).
   Check archive/executable hashes against `static-artifacts.json` and the
   release records before extracting. Extract to `extracted/Hormuz.app` in
   that directory; do not overwrite an installed app.
2. Run `codesign --verify --deep --strict` on the extracted app in a normal
   macOS shell. Record OS/build, CPU, RAM, power/thermal conditions, displays,
   and any other active workloads. Inspect the requested preview state if
   collecting acceptance evidence, and keep the pointer away during idle runs.
3. Run the collector from this folder, setting `baseline_dir` to that fresh
   directory. It checks the pinned GUI hash, refuses an already-running copy,
   launches a new preview with `open -n -g`, and terminates only the newly
   identified process at that exact binary path when collection finishes.

   ```sh
   python3 collect_macos_preview.py hidden --recording-directory "$baseline_dir"
   python3 collect_macos_preview.py visible --recording-directory "$baseline_dir"
   python3 collect_macos_preview.py folded --recording-directory "$baseline_dir"
   ```

4. Retain the resulting `*-idle-sample.json` files. Each contains all raw
   numeric samples, source/hash, scenario, duration and limitations. Do not
   publish process command lines, identities or unrelated workstation data.
   Confirm the dedicated app process exited after each run. Use a new
   recording directory for the next repeat; the collector refuses to replace
   an existing result.

The first recordings used a prototype with the recording directory fixed
locally. The replay copy preserves its collection body and adds a directory
argument, input validation, explicit non-optional executable/process guards,
and exclusive output creation. It is pinned to this release: future versions
need a new source review of preview/session behavior and a new artifact hash.
It is not a general-purpose profiler or a release gate.

## Complete #332

Repeat each controlled scenario three times on each available platform. Include
visible, folded and hidden idle; synthetic sign-in; active client/relay with
optimization Off and On separately. Record every app/helper process and the
profiler's precise memory definition. Collect wake-ups and GPU activity with
native profiling tools. Measure launch-to-visible separately over repeated
cold and warm starts. Exercise at least 100 fold/hide/reopen cycles, then
measure settled growth. Record observer overhead and missing metrics.

Startup latency, wake-ups, GPU activity and sign-in/active-client scenarios
remain unmeasured here. #332 separately records short Windows CI process
observations and 100 synthetic interaction cycles; controlled longer baselines,
physical interaction growth and connected workload measurements remain open.
Display/keyboard/tray/UI Automation acceptance remains #331. Numerical budgets
remain unset until those baselines justify them. Missing measurements are not
zeros; this checkpoint leaves #332 open.
