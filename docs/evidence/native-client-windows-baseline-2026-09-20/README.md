# Synthetic Windows companion footprint protocol

This is a bounded measurement for [#332](https://github.com/Xpounder-com/hormuz/issues/332).
The [separate hosted-Windows workflow](../../../.github/workflows/windows-footprint-baseline.yml)
builds one unsigned release-mode development executable from its checked-out
source, verifies a same-runner package-clean rebuild has identical bytes, and
downloads that one artifact into the visible, folded and hidden **synthetic
preview** measurement jobs. An [independent verifier](../../../tools/verify_windows_footprint_baseline.py)
then checks that all nine runs and 549 raw samples bind to that executable hash.
It does not launch the connected sign-in path, relay or optimizer. The published
product and release gates are unchanged.

Each state runs in its own `windows-latest` job. Within that job, the
[collector](../../../clients/rust/windows/collect-footprint-baseline.ps1)
starts three independent preview processes in separate private directories.
For each process it waits 60 seconds after verifying the requested state,
then records 61 samples at requested five-second intervals over five minutes.
The observer and independent verifier reject a sample more than 1.5 seconds
late or an adjacent gap more than 1.5 seconds from the requested cadence;
overloaded runner recordings therefore do not count as this protocol.
An independent Windows PowerShell 5.1
[observer](../../../clients/rust/windows/FootprintBaseline.cs) verifies the
owned synthetic window and numeric PID/parent process tree at every sample.
The empty preview must have exactly one observed process; a discovered helper
fails the run. The parent watchdog bounds each run and terminates only its
owned preview/observer if collection fails. Success evidence is written only
after all three previews exit cleanly. The build manifest's source, proposed
head, compiler and executable hash must match the measurement checkout and
downloaded file before collection starts.

Run it with the workflow's PR, main-push or manual trigger, or repeat one state
on a Windows machine from the `clients/rust` directory after building the
release candidate:

```powershell
./windows/collect-footprint-baseline.ps1 `
  -Executable ./target/release/hormuz-windows.exe `
  -BuildManifest ./target/release/windows-footprint-build.json `
  -Output ./target/release/windows-footprint-visible.json `
  -Scenario visible -WarmupSeconds 60 -DurationSeconds 300 `
  -IntervalMilliseconds 5000 -Repetitions 3
```

Use a new output path for each invocation; the collector refuses to overwrite
evidence. Replace `visible` with `folded` or `hidden` to collect the other
states. `-BuildManifest` is required for the workflow's single-executable
binding; a local standalone build may omit it but cannot claim that binding.
Shorter custom runs can check the collector but have
`complete_three_run_protocol: false` and do not count as this baseline.
The workflow uploads the exact executable and repeat-build proof once, plus
each state's numeric JSON and an independent verification summary as 14-day
artifacts. JSON binds the source commit and proposed
PR head, executable SHA-256 and size, Rust version, Windows version, runner
image, architecture, logical processor count and collection parameters. It
contains no process IDs, paths, command lines, identities, window text,
screenshots, credentials, prompts or responses.

The verifier applies the proposed ceilings in
[`native-client-footprint-budgets-v1.json`](../native-client-footprint-budgets-v1.json):
3.5 MiB executable size, 24 MiB working set, 4 MiB private bytes, 0.1% of one
core, a 40-second first-run window-ready upper bound, and an 8-second bound for
the six subsequent runs. The first-run allowance includes cold PowerShell and
UI Automation compilation on each fresh hosted runner; it is not an app-startup
claim. The separate native acceptance job applies the same file to its 100-cycle
before/after samples: at most 1 MiB working-set growth, 512 KiB private-byte
growth, five CPU seconds, four handles, and two GDI or USER objects. These
deliberately loose values detect material regressions; they are not product
guarantees or cross-runner performance claims.
The current exact-main [CI run 36504256235](https://github.com/Xpounder-com/hormuz/actions/runs/36504256235)
at `04799698392c6196dd39d28ce36753c706180e43` completed all 100 cycles with
zero settled growth in working set, private bytes, handles, GDI objects or USER
objects; cumulative app CPU increased by 2.234375 seconds. The downloaded
content-free acceptance artifact and executable both matched that run's build
manifest. This is the basis for the interaction ceilings, not physical input
acceptance.

`working_set_bytes_sum` and `private_bytes_sum` are sampled process-tree sums
of distinct Windows counters. Neither is total physical memory. App CPU time
comes from `System.Diagnostics.Process`; percentage of one core uses the first
and last cumulative counters divided by their actual elapsed interval. A small
or zero counter delta is not zero activity or energy. The observer records its
own CPU during sampling and peak working set across setup and sampling;
watchdog, WMI service and artifact-upload cost are outside those counters.
The window-ready time is only an **upper bound** from process launch to the
observer's first verified window: it includes PowerShell startup, UI Automation
setup and polling, and does not establish cold start or rendered pixels.
Fold state is verified with UI Automation before and after sampling. During
sampling the observer checks window-manager visibility and geometry without
repeated UI Automation property requests to the measured process.

No verified per-process wake-up counter is available in this hosted-runner
procedure, so the wake-up field is null with an explicit reason. A helper that
starts and exits between five-second samples can be missed. Each state runs
on a different hosted runner; their power, thermal, display and background
conditions are uncontrolled, so state-to-state differences are descriptive,
not a controlled performance comparison. This procedure does not measure GPU
activity, physical keyboard or tray input, screen-reader usability, real
sign-in/active-client behavior, or connected helpers. Wake-up and GPU budgets
remain unset because those counters are unavailable in this hosted procedure.
Physical Windows acceptance remains in
[#331](https://github.com/Xpounder-com/hormuz/issues/331).
