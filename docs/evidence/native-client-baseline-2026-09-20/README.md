# Mac companion footprint follow-up: 2026-09-20

This is a further content-free measurement checkpoint for [#332](https://github.com/Xpounder-com/hormuz/issues/332), not a release qualification. It measures only the released Mac v1.2.0 arm64 app from source `d854a5a453fcbe20cb3f4c1e261e146f2da93855`. The downloaded ZIP SHA-256 is `0a18536765245a3b2510644303a0a4ce253af2ab4d08168e129feb9d7e2501f0`; the GUI SHA-256 is `2ece94a031a039d0e27c7ce873787f83f704045e44b0e63cedb71841b9bb3ecc`. The ZIP matched the release manifest, and every isolated app copy passed `codesign --verify --deep --strict` before launch. [The initial static record and single-run samples](../native-client-baseline-2026-09-19/README.md) remain historical evidence.

The host was Mac17,2 / Apple M5, arm64 macOS 26.2 (25C56), 10 logical CPUs and 32 GiB RAM. An internal 2704 × 1756 display was set to 1352 × 878 at 120 Hz. Other desktop applications were active, and display/thermal conditions were not locked. Power source was checked immediately before and after each run. These are developer-workstation observations, not a controlled clean-machine baseline.

Each hidden, visible and folded `connected` synthetic preview request used a fresh extraction, a 60-second warm-up and a five-minute sample at requested five-second intervals. The main series ran serially in hidden → visible → folded order for three rounds. Its first hidden recording was excluded after AC-to-battery transition and replayed at the end on battery. Source review confirms that this preview bypasses saved-session restore and refresh. The collector pins the executable hash, rejects an existing copy or existing output, observes the root process, descendants and processes within the app bundle, and terminates only its identified app process. Reports retain numeric aggregates rather than command lines, arguments, account data or process identities.

The revised [collector](../native-client-baseline-2026-09-19/collect_macos_preview.py) emits schema v3. `/bin/ps` supplies RSS and coarse CPU time; Darwin `proc_pid_rusage` supplies per-process physical footprint, Mach CPU ticks, package-idle wake-ups and interrupt wake-ups. The CPU ticks require the host's `mach_timebase_info` ratio to calculate nanoseconds and CPU percentage. On this M5 host the ratio is 125/3. The physical-footprint field is the OS's per-process accounting, not RSS; summing separate processes would not necessarily equal total machine memory. The collector also records its own and its `ps` children's CPU time during sampling. A process that exists entirely between samples can still be missed. Package-idle and interrupt counters do not establish total energy use.

The nine runs were captured with an earlier schema-v2 collector that mislabeled the Mach tick field as nanoseconds. Its derived CPU percentages are wrong. [Apple's XNU `proc_pid_rusage` test](https://github.com/apple-oss-distributions/xnu/blob/main/tests/recount/recount_perf_tests.c#L195-L216) converts these counters from Mach time before treating them as nanoseconds. The untouched source reports are archived in [raw-v2](raw-v2/README.md); [normalization](normalize_macos_idle.py) records each raw SHA-256, renames the tick field and recomputes CPU percentage using the measured 125/3 timebase. Every accepted run had only the continuously checked root process at every sample, so a helper-topology change cannot corrupt those interval deltas. [Verification](verify_repeats.py) reproduces each corrected report from its raw source and checks its summaries. Schema v3 rejects CPU/wake-up interval summaries if the observed process membership changes.

The optional [numeric window probe](window_geometry.swift) filters CoreGraphics on-screen windows to the measured process and prints only bounds, layer and alpha. It was used as a read-only spot check of requested preview modes; it does not capture pixels or prove interaction readiness. The separate [startup probe](measure_macos_startup.py) measures sequential launch requests to the first owned, nontransparent, on-screen panel-shaped window. Those sequential launches leave OS caches intact and are **not** cold starts. Its first-panel time is bounded by the probe cadence, not a frame-render timestamp.

## Repeated idle observations

All nine accepted runs had [battery power at both boundary checks](macos-run-conditions.json), 61 samples, one observed app process and stable observed process membership. Each row combines three separate five-minute samples; it does not average away a run's extremes. The app CPU figure is the range of corrected `proc_pid_rusage` percentages of one core, while the `ps` CPU counter remained unchanged at its coarser precision.

| Requested synthetic preview | RSS range (MiB) | Physical footprint range (MiB) | App CPU per run (% one core) | Package-idle / interrupt wake-ups per run |
| --- | ---: | ---: | ---: | ---: |
| Hidden | 99.219–99.500 | 34.641–34.891 | 0.000045–0.000757 | 0 / 2–6 |
| Visible | 100.969–101.047 | 35.970–36.095 | 0.000022–0.000068 | 0–1 / 1–2 |
| Folded | 99.438–100.172 | 34.204–35.501 | 0.000058–0.000726 | 0–1 / 2–3 |

The collector and its `ps` children consumed 3.1616–3.4236 CPU seconds during each five-minute sample; this observer cost is separate from the app CPU counter. No helper appeared at the five-second sampling cadence, but a short-lived helper between samples would be missed. The earlier single-run schema-v1 observations used different power conditions and are not a performance comparison. Informal numeric window spot checks found no on-screen window for requested hidden mode, a settled 70 × 399 panel and 58 × 58 handle for visible mode, and a 34 × 79 window for folded mode; their numeric output was not archived. The separate startup report retains its own earlier animation-phase panel bounds. These spot checks do not prove rendered content or interaction readiness.

The [ten sequential visible startup observations](macos-visible-startup.json) used a separate verified app extraction. The first launch reached a nontransparent panel-shaped window at 1.8377 seconds; the next nine ranged from 0.3439 to 0.5062 seconds, with a median of 0.3534 seconds. The last negative probe preceded each positive observation by 0.1380–0.1796 seconds. LaunchServices and OS caches were not reset, and the host remained an active developer workstation. These timings describe only the measured window-appearance proxy for the synthetic preview; they do not measure cold start, rendered pixels, sign-in, or interaction readiness.

## Reproduce and verify

Verify the v1.2.0 release ZIP, executable hash and app signature using the [pinned static record](../native-client-baseline-2026-09-19/static-artifacts.json). For each of the nine runs, copy that verified app to a fresh `extracted/Hormuz.app` below a new recording directory, then run:

```sh
python3 ../native-client-baseline-2026-09-19/collect_macos_preview.py hidden --recording-directory "$recording_dir" --warmup 60 --duration 300
```

Substitute `visible` or `folded` as appropriate. Keep each recording directory fresh; the collector refuses to overwrite a report. Record OS, display, power source and background-workload conditions. The archived v2 files retain the exact original samples; the sibling v3 filename identifies the scenario and series repeat. Their internal `repeat: 1` field means one recording in that fresh directory. Recompute the v3 reports from the v2 archives with `python3 normalize_macos_idle.py raw-v2/macos-visible-idle-repeat-1.json /tmp/normalized.json`, or verify all committed idle and startup reports and summaries with `python3 verify_repeats.py` (also valid under `python3 -O`).

With a matching Xcode toolchain, compile and run the optional window/startup probe against a separate verified extraction after idle collection:

```sh
xcrun swiftc window_geometry.swift -o "$probe_binary"
python3 measure_macos_startup.py --recording-directory "$startup_dir" --window-probe "$probe_binary" --trials 10
```

The Mac released app and the Windows synthetic shell have different features and architectures, so their numbers cannot establish a cross-platform performance improvement. Real sign-in, active client/relay with optimization Off and On, GPU activity, physical interaction growth, controlled Windows/Mac baselines, cold startup and numerical regression budgets remain open in #332.
