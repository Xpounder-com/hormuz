# Native Windows companion foundation

Development slice for [#331](https://github.com/Xpounder-com/hormuz/issues/331),
planned v1.4.0, using the contracts from #330. This is an unsigned synthetic
preview, not a connected client or an installer. Current build target:
Windows 10/11 x64 (`x86_64-pc-windows-msvc`); Windows ARM64 is not qualified here.
The workspace statically links the MSVC C runtime for this target. CI checks the
PE imports so a developer machine's installed Visual C++ redistributable cannot
silently become an undeclared runtime prerequisite.

The process owns one Win32 top-level window, standard text/button controls and
a notification-area icon. It can fold, hide and reopen. Close and Escape hide
the window; the tray menu or the visible Exit button quits. If the tray API is
unavailable, hiding minimizes to the taskbar so recovery and exit remain
available. Explorer restart re-registers the icon and shows the window if that
fails. The icon uses the system application artwork in this development slice.

The panel uses the current monitor's work area, accounts for its caption, and
repositions on display/work-area changes. Per-monitor DPI-v2 awareness and
`WM_DPICHANGED` resize both controls and font. Text/button controls retain native
names, keyboard navigation and accessibility providers. These are implementation
choices, not a claim of completed UI Automation or physical-monitor acceptance.

No browser runtime, networking, credentials, relay, optimizer or tokenizers are
initialized. There is no repeating timer in normal operation. The smoke mode has
a short-lived timer and closes itself. Samples are marked synthetic in the title,
subtitle and every metric. The shared core represents actual usage as absent.
Single-instance enforcement and IPC belong to #339; repeated manual launches can
currently create separate preview processes.

## Build and exercise on Windows

From `clients/rust` with rustup and the MSVC build tools installed:

```powershell
rustup toolchain install 1.98.1 --profile minimal --component rustfmt,clippy
cargo fmt --all -- --check
cargo test --workspace --locked
cargo clippy --workspace --all-targets --locked -- -D warnings
cargo build -p hormuz-windows --release --locked
./windows/verify-smoke.ps1 -Executable ./target/release/hormuz-windows.exe
./windows/verify-acceptance.ps1 -Executable ./target/release/hormuz-windows.exe -Output ./target/release/windows-acceptance.json
./target/release/hormuz-windows.exe
```

The toolchain and lockfile fix dependency resolution. MSVC `/Brepro` and
`/INCREMENTAL:NO` remove wall-clock linker metadata; CI cleans and rebuilds the
preview package, then requires the executable's SHA-256 to remain identical.
This checks repeat-build identity in the same checkout/runner with cached
dependencies; different paths, SDKs, linkers and machines remain unqualified.
See [Microsoft BuildXL's linker determinism flag](https://github.com/microsoft/BuildXL/blob/main/Public/Sdk/Experimental/Msvc/Native/Tools/Link/Link.dsc).
Use the CI artifact's source commit, compiler, target and SHA-256 when reporting
results. The CI release executable is development evidence only.
The artifact includes `windows-rebuild.json`, with both hashes and the tested
scope. Signing and distribution qualification remain separate.

The bounded smoke runner starts a real native process, checks HWND/control
creation, verifies actual window-height and child-visibility changes on fold and
expand, exercises close/hide and a synthesized tray callback, and checks exit.
It reports whether the tray registered; a machine without Explorer exercises
taskbar fallback. A synthesized callback does not prove an actual tray click,
keyboard navigation, accessibility, monitor migration or the visual layout.

## External accessibility and measurement check

`verify-acceptance.ps1` starts an ordinary release preview and an independent
Windows PowerShell UI Automation observer. It verifies the synthetic labels,
button roles/names, exposed InvokePattern actions, programmatic keyboard focus,
fold/expand geometry and metric visibility, hide, close/reopen, and clean Exit.
It runs 100 fold/expand/hide/reopen cycles. Reopen deliberately uses the same
synthetic notification as the smoke test; actual tray activation is still a
manual check. UIA focus and invocation do not prove physical keyboard input or
screen-reader usability. A failed/unavailable provider fails the check.

The observer samples visible, folded and hidden states three times each. Defaults
are a two-second warm-up followed by six samples at requested one-second
intervals, plus settled samples before and after the interaction cycles. These
are short CI observations, not the controlled five-minute baseline in #332.
Actual elapsed times and cumulative CPU times are preserved in every sample.
WMI process topology must show exactly the owned preview and no descendants at
each observation; an observed helper causes failure instead of a root-only
memory claim. Processes that start and exit between observations may be missed.

The JSON records working set, private bytes, CPU time, process/handle counts,
GDI/USER objects, observer CPU time, OS/architecture, and source/executable hashes.
Observer CPU covers only the worker measurement interval, excluding compilation,
the watchdog and WMI service work.
Working set and private bytes are different counters; neither represents total
physical footprint. Before/after values are observations with no numerical
regression budget. Startup, wake-ups, GPU, physical display changes, sign-in and
active-client scenarios remain unmeasured. Host power, thermal and display
conditions are uncontrolled. The observer reads only the owned preview's UI
and numeric process topology; it does not collect other window text, command
lines, process owners, credentials or screenshots.

Parameters `-WarmupSeconds`, `-SampleCount`, `-IntervalMilliseconds`,
`-Repetitions` and `-Cycles` support longer local observations. Existing output
is never overwritten. A parent watchdog owns both processes and stops them if
the provider hangs or the check fails. The success evidence is written only
after the preview exits cleanly. CI stores it beside the executable and pins
its SHA-256 in the build manifest. `manual_platform_acceptance` stays `pending`.
The Windows failure-path regression launches an owned, inert program without a
preview window, requires rejection and process cleanup, and verifies that an
existing evidence file is preserved before any new target is launched.

API contracts: [Microsoft UI Automation InvokePattern](https://learn.microsoft.com/en-us/dotnet/api/system.windows.automation.invokepattern.invoke)
and [Win32 GUI resource counters](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-getguiresources).

## Manual acceptance still required

Record OS/build, architecture, commit/artifact SHA, display arrangement, scale
factors and results. Use only the synthetic preview; attach content-free evidence.

1. Launch once. Verify the synthetic labels, one resident process and no helper
   processes. Inspect actual appearance, text fit and high-contrast behavior.
2. Use Tab/Shift-Tab, Space/Enter and button mnemonics. Inspect Fold/Expand, Hide
   and Exit names/actions with Accessibility Insights or Inspect. Confirm focus
   returns to the visible panel when opened from the tray.
3. Fold, expand, close, press Escape and reopen through actual mouse and keyboard
   tray activation. Test the tray menu and both Exit paths. Restart Explorer and
   verify recovery; test unavailable-tray fallback separately.
4. Move between 100%, 150% and 200% displays; change scale/resolution; disconnect
   the active display; change taskbar placement/auto-hide. Check reachability,
   crisp text, control hit areas and keyboard focus after every change.
5. Repeat at least 100 interactions and record process-tree memory, CPU, wake-ups
   and GPU activity using the #332 measurement protocol before proposing budgets.

#331 remains open until that evidence and protected-main checks are recorded.
Real sign-in/usage belongs to v1.5.0 (#340), launch/relay to v1.6.0 (#341), and
signed installers/update behavior to v1.9.0 (#346).

API references: [Microsoft notification-area guidance](https://learn.microsoft.com/en-us/windows/win32/shell/notification-area),
[per-monitor DPI messages](https://learn.microsoft.com/en-us/windows/win32/hidpi/wm-dpichanged),
and the pinned `windows-sys` bindings. Native handles and GDI fonts stay on the
GUI thread; callbacks borrow immutable Rust state with `Cell` for reentrancy.
