# Mac v1.3.0 release footprint: 2026-09-29

This checkpoint measures the published, notarized Hormuz v1.3.0 Apple Silicon
application for [#332](https://github.com/Xpounder-com/hormuz/issues/332). It
uses content-free synthetic preview state and binds every report to source
`cc5f5c1d38b4732823496240bbb4ace309a6240d` and GUI SHA-256
`0726774b2c43695579633c920ad0e09bb8a6abb2399687a99d599807f314dd10`.
It does not treat a development build, an unsigned binary, or a cross-platform
comparison as release evidence.

## Artifact and installed content

The downloaded `Hormuz-1.3.0-notarized.zip` matched `SHA256SUMS.txt` and the
published distribution proof. A fresh extraction passed strict recursive code
signature verification, Gatekeeper assessment, and stapled-ticket validation.
Both Mach-O executables are arm64 and import only Apple system
libraries/frameworks; the context helper's embedded Python and tokenizer
payload remains part of the installed app.

| Artifact/component | Bytes |
| --- | ---: |
| Downloaded ZIP | 13,239,896 |
| Extracted app, logical sum of 16 regular files | 18,260,512 |
| Extracted app, allocated 512-byte block sum | 18,300,928 |
| Native GUI executable | 2,599,952 |
| Context helper backend | 10,219,792 |
| Resources, including tokenizer data | 5,432,484 |
| Signature and bundle metadata | 8,284 |

[`static-artifacts.json`](static-artifacts.json) records the exact hashes,
component totals, imports, architecture, version/build, host, and inspection
method. Compared with the earlier released v1.2.0 record, the v1.3.0 archive is
146,034 bytes larger (1.115%) and the extracted logical app is 146,816 bytes
larger (0.811%); that delta is entirely in the context-helper backend. This is
a size comparison, not a runtime-performance claim.

## Runtime protocol

The host was a Mac17,2 / Apple M5 with 10 logical CPUs and 32 GiB RAM, running
arm64 macOS 26.2 (25C56). The built-in 3024 × 1964 Retina display was main and
online at collection start. Low Power Mode was off. Thermal status was
unavailable from `pmset`; display scaling, refresh rate, thermal state, and
other developer-workstation activity were not locked.

Each hidden, visible, and folded `connected` synthetic preview uses an isolated
copy of the verified release app, a 60-second warm-up, and 61 numeric samples
over five minutes at requested five-second intervals. Three rounds use the
balanced order hidden/visible/folded, visible/folded/hidden, then
folded/hidden/visible. `pmset -g batt` was checked at the initial boundary,
between every run, and after the final run. The preview source path bypasses
saved-session restoration and refresh, so it neither reads a user's session nor
starts the bundled context helper.

The revised historical
[`collect_macos_preview.py`](../native-client-baseline-2026-09-19/collect_macos_preview.py)
keeps its v1.2.0 identity defaults but accepts an explicit release source,
version, executable hash, and repeat number. It rejects a wrong executable, an
already running copy, an existing output, or invalid bounds. `/bin/ps` provides
RSS and a coarse CPU counter. Darwin `proc_pid_rusage` provides physical
footprint, Mach CPU ticks, package-idle wake-ups, and interrupt wake-ups. The
collector follows descendants and in-bundle executables, suppresses interval
deltas if membership changes, records observer CPU separately, and terminates
only the exact process it launched. Reports contain numeric aggregates, not
PIDs, command lines, identities, credentials, prompts, or responses.

Ten sequential visible launches use a separate verified extraction and the
numeric CoreGraphics window probe from the
[2026-09-20 checkpoint](../native-client-baseline-2026-09-20/window_geometry.swift).
The monotonic-clock interval ends at the first owned, nontransparent,
panel-shaped on-screen window. OS and LaunchServices caches remain warm; this is
an upper-bound window-appearance proxy, not a cold-start or rendered-pixel
measurement.

Fresh signature-verified release copies also ran the built-in content-free
interaction audits. [`macos-layout-audit.json`](macos-layout-audit.json) records
all five live scale changes plus fold, hide, and reopen passing. The
[`macos-motion-audit.json`](macos-motion-audit.json) expired fixture records 20
opacity samples, an intermediate opacity, the 318-point compact layout, and
rapid fold/hide reversal recovery, all passing. macOS Accessibility automation
was disabled on this host, so a separate external 100-cycle Mac input loop was
not run or inferred. The existing Windows acceptance observer supplies the
content-free 100-cycle growth measurement for this scoped baseline.

[`macos-gpu-observation.json`](macos-gpu-observation.json) records a bounded
10-second Xcode Metal System Trace attached by numeric process ID to another
fresh release copy. Its exported target tables contained no Hormuz Metal command
buffers, GPU intervals, Metal allocation rows, or displayed-surface rows while
the system GPU table contained 220 rows from other processes. That establishes
that no direct Hormuz Metal work was attributed in this short visible-idle trace;
it does **not** claim zero GPU utilization. `powermetrics --show-process-gpu`
requires superuser access on this host, and the trace exposes no per-process
utilization percentage, so that value is explicitly unavailable. The raw trace
is not committed because it enumerates unrelated processes and environment
metadata; the committed summary is content-free and the procedure is repeatable.

## Observed results

All nine accepted idle runs retained one stable process with no observed child
departure. Five completed wholly on AC power and four wholly on battery power.
One hidden attempt that crossed from AC to battery was rejected and replaced;
[`macos-run-conditions.json`](macos-run-conditions.json) records that decision
and binds each accepted power boundary to its raw report timestamp.

| Requested state | RSS range (MiB) | Physical-footprint range (MiB) | Maximum precise CPU (% of one core) | Maximum package / interrupt wake-ups per 5 min |
| --- | ---: | ---: | ---: | ---: |
| Hidden | 99.109–99.734 | 33.391–35.126 | 0.000997 | 3 / 5 |
| Visible | 100.875–101.063 | 34.720–36.048 | 0.000057 | 1 / 2 |
| Folded | 99.469–100.109 | 34.001–35.141 | 0.003195 | 5 / 18 |

The first of ten sequential warm-LaunchServices trials reached the observed
panel in 0.8848 seconds. The remaining nine ranged from 0.3487 to 0.4856
seconds, with a 0.3556-second median. These values are descriptive observations
under the recorded workstation conditions, not cross-state causal comparisons.

## Proposed regression budgets

[`native-client-footprint-budgets-v1.json`](../native-client-footprint-budgets-v1.json)
rounds the observed release envelope upward to simple ceilings: 15 MiB archive,
22 MiB logical/allocated bundle, 128 MiB RSS, 48 MiB physical footprint, 0.01%
of one core, 10 package-idle and 25 interrupt wake-ups per five minutes, and
2.5 seconds for the warm panel upper bound. The evidence verifier fails if any
committed observation exceeds a ceiling. These are proposed regression alarms
for this synthetic scope, not supported-hardware requirements or performance
guarantees.

## Reproduce and verify

Download the v1.3.0 archive, checksum manifest, and macOS distribution proof
from the same GitHub release. Check their hashes, extract into a fresh
`extracted/Hormuz.app`, and validate the signature in a normal macOS shell.
The integrity commands used here were `codesign --verify --deep --strict`,
`spctl --assess --type execute`, and `xcrun stapler validate` against that
isolated app copy.
For each idle run, substitute the fresh directory, scenario, and repeat:

```sh
python3 ../native-client-baseline-2026-09-19/collect_macos_preview.py hidden \
  --recording-directory "$recording_dir" --warmup 60 --duration 300 --repeat 1 \
  --source-commit cc5f5c1d38b4732823496240bbb4ace309a6240d \
  --version 1.3.0 \
  --executable-sha256 0726774b2c43695579633c920ad0e09bb8a6abb2399687a99d599807f314dd10
```

Compile the window probe with the same Xcode toolchain, then run the startup
collector against another fresh extraction:

```sh
mkdir -p "$module_cache"
DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer \
CLANG_MODULE_CACHE_PATH="$module_cache" xcrun swiftc \
  ../native-client-baseline-2026-09-20/window_geometry.swift \
  -o "$probe_binary"
python3 ../native-client-baseline-2026-09-20/measure_macos_startup.py \
  --recording-directory "$startup_dir" --window-probe "$probe_binary" \
  --trials 10 --source-commit cc5f5c1d38b4732823496240bbb4ace309a6240d \
  --version 1.3.0 \
  --executable-sha256 0726774b2c43695579633c920ad0e09bb8a6abb2399687a99d599807f314dd10
```

Run `python3 verify_release_baseline.py` (and repeat under `python3 -O`) to
validate artifact identity, all 549 idle samples, summaries, topology,
conditions, startup observations, interaction audits, the sanitized GPU
observation, and proposed ceilings. To reproduce the GPU probe, launch a fresh
verified copy in `connected` synthetic preview, resolve that exact binary's PID,
and run:

```sh
DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer \
xcrun xctrace record --template 'Metal System Trace' --time-limit 10s \
  --output "$trace" --no-prompt --attach "$exact_pid"
xcrun xctrace export --input "$trace" --toc --output "$toc"
```

Export the target Metal command-buffer, GPU-interval, current-allocation and
displayed-surface tables by schema. Count only rows attributed to the exact
target; do not publish the raw trace or assign system-wide rows to Hormuz.

## Scope boundary

This v1.4 baseline covers the released Mac app and the empty synthetic Windows
shell. Connected Windows sign-in belongs to #340, relay/helper load to #341,
and all-platform interaction/performance release enforcement to #347. Cold
startup, clean-machine variance, connected Mac/Windows workloads, and physical
Windows input remain separate evidence; missing measurements are never reported
as zero. The Mac and Windows artifacts have different features, architectures,
hosts, and counters, so their numeric values are not compared.
