# Native Windows connected development companion

The v1.4.0 foundation for [#331](https://github.com/Xpounder-com/hormuz/issues/331)
now integrates #339/#340 at unpublished `1.5.0-dev.1`. The published product
remains `1.2.0`. This is an unsigned development executable, not a qualified
installer. The build target is Windows 10/11 x64 (`x86_64-pc-windows-msvc`);
Windows ARM64 is not qualified. The workspace statically links the MSVC C runtime
and CI checks PE imports for an undeclared Visual C++ redistributable dependency.

An ordinary launch opens the connected panel. Enter the team's HTTPS gateway
origin, organization ID, approved model alias, optional issuer and AI client,
then choose **Sign in**. The existing session controller validates the enrollment
URL before opening the default browser; the browser completes identity-provider
sign-in. No password, provider key or session token is entered into this panel.
Saved profiles restore from private native storage. Credentials use the fixed
current-user Credential Manager target and never fall back to plaintext files.
Sign out before changing an active connection. **Retry connection** rechecks the
saved state; **Sign out / cancel** cancels enrollment or revokes a saved session.
A failed revocation remains pending and requires **Sign out** again. Retry never
silently replays an interrupted credential rotation.

The same shared controller verifies identity and displays only the current
actor's authoritative gateway usage. Missing values remain dashes. Current,
stale, offline and sign-in-required states are explicit; retained values keep
the last successful response time. Costs are estimates covering gateway-captured
requests. The panel does not infer organization-wide totals or local activity.
The separate unpublished v1.6 Rust relay is a #341 source checkpoint. This
v1.5 Windows panel does not launch or supervise it yet.

One owned worker performs credential and network transactions, with one pending
command and one coalesced notification. Dashboard views share the scheduler;
folded/expanded panels use summary/detail cadence, and hidden/minimized panels
do no new polling. WTS session registration and the initial desktop state are
established before enabling polling; unknown/locked/disconnected sessions pause
it. Power and coalesced native IP-interface notifications feed the scheduler.
An in-flight transaction is not cancelled just because a window is hidden or
the desktop locks. Explicit sign-out/quit cancels it, preserves durable recovery
states, and joins the worker before releasing app ownership. Windows Shell
browser dispatch runs on the worker and can delay its completion independently
of the bounded network transport; default-browser compatibility remains a native
acceptance item.

Use `--preview` for the original synthetic panel. Preview/smoke mode creates no
session worker, network subscription, browser launch or credential-store access.
It does retain the private single-instance listener. CI measures this explicit
preview mode; those measurements do not establish connected idle footprint.

The process owns one Win32 top-level window, standard text/button controls and
a notification-area icon. It can fold, hide and reopen. Reopening now expands
the panel and restores native button focus through the shared interaction
policy, including after an explicit Fold. Close and Escape hide
the window; the tray menu or the visible Exit button quits. If the tray API is
unavailable, hiding minimizes to the taskbar so recovery and exit remain
available. Explorer restart re-registers the icon and shows the window if that
fails. The icon uses the system application artwork in this development slice.

The panel uses the current monitor's work area, accounts for its caption, and
repositions on display/work-area changes. Per-monitor DPI-v2 awareness and
`WM_DPICHANGED` resize both controls and font. Text/button controls retain native
names, keyboard navigation and accessibility providers. These are implementation
choices, not a claim of completed UI Automation or physical-monitor acceptance.

No embedded browser, relay, optimizer or tokenizer is initialized. There is no
GUI refresh timer; one-shot interaction deadlines run only while a fold is
pending, and the worker sleeps until a native event or the shared scheduler's
single deadline. The synthetic smoke has a short-lived timer and
closes itself. Preview samples remain explicitly synthetic.
The #339 integration retains a separate private application-instance lock.
Repeated launches use a bounded, current-user/current-desktop-session named pipe
to reopen the existing window, then exit. Failed activation never permits a
second owner without a new successful kernel-lock acquisition. The resident
listener has no idle polling loop and coalesces at most one pending UI notice.
Its fixed v1 eight-byte command carries no arguments, credentials or content.
The server rejects remote clients; both peers verify process user/session and
the client verifies the protected user-only pipe ACL before sending anything.
Only cooperating builds using this root/protocol participate; older preview
executables without an instance lock must not be run alongside this candidate.

The default root is the current user's native Local AppData known folder plus
`HormuzNativeClient`; environment variables cannot change that root. Tests use
`--preview --state-directory <absolute-new-private-directory>` or the smoke
equivalent. Private storage is validated without repairing unsafe existing
permissions. The process exits nonzero if ownership or activation cannot be
established. A different desktop session cannot activate the owner's window.
Opt-in login registration and real helper supervision remain #339/#341 work.
Real power/network/desktop transitions still require native acceptance.

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
./windows/verify-lifecycle.ps1 -Executable ./target/release/hormuz-windows.exe -Output ./target/release/windows-lifecycle.json
./target/release/hormuz-windows.exe --preview
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

`verify-lifecycle.ps1` launches eight competing real executable instances after
hiding the primary, verifies their successful handoff to the original HWND,
kills only its owned primary, starts a replacement, checks explicit Exit and
rejects an inherited-permission state root. Cleanup is verified before success
evidence is written. `windows-lifecycle.json` is bound to source/executable hashes
and pinned by the build manifest. Platform tests additionally reject malformed,
oversized and stalled pipe requests, a different expected peer user, an invalid
peer handle and a public endpoint before sending any command. They do not create
another Windows account or prove physical keyboard/tray/sleep behavior.

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
It also injects Win32 `SendInput` keyboard events into the owned foreground
preview and verifies Tab/Shift-Tab focus movement, Space/Enter activation,
Escape hiding and focus after reopening. The check fails if the preview cannot
become foreground or input does not produce the expected native state. It runs
100 fold/expand/hide/reopen cycles. Reopen deliberately uses the same synthetic
notification as the smoke test; actual tray activation is still a manual check.
These synthetic events on a CI desktop do not prove physical keyboard use,
screen-reader usability or behavior on other desktop configurations. A failed
or unavailable provider fails the check.

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

## Shared interaction integration checkpoint

The #338 integration makes the shared reducer authoritative for the panel's
expanded, explicitly folded and hidden presentation. Whole-panel pointer and
keyboard-focus observations remain native, including child controls and owned
combo/menu popups. Moving between children does not create a false panel exit.
After pointer re-entry or reopening a folded panel, leaving both pointer and
focus outside starts the shared 250 ms fold delay. Explicit Fold remains usable
while its native button has focus. Escape and Close retain this development
shell's Hide action; no metric-card or settings-page UI is introduced here.

One posted GUI drain collects a bounded batch of at most 128 observations.
The reducer orders native input before callbacks already collected in that
batch, then the shell renders its final snapshot. Before draining, the adapter
samples the current native hit target and focus because Win32 delivers posted
messages before hardware input. This does not reorder future observations into
an already committed turn. Rendering, positioning, DPI, focus assignment and
accessibility providers stay in Win32; reducer focus requests are not evidence
that focus was obtained.

At most two token-bound timer slots exist. Each newly scheduled token receives
a never-reused native timer ID. `WM_TIMER` captures that ID's token and stops
the repeating Win32 timer before queuing completion; a cancelled/unknown ID
cannot be relabelled as the latest timer. Early callbacks rearm the original
token and deadline. Final-state reconciliation avoids arming a timer that was
scheduled and cancelled within one batch. Hide and destruction invalidate
queued callbacks and stop native timers. Timer/queue/clock failures terminate
the owned window with a nonzero result rather than leave partially applied
interaction state running. No periodic pointer polling or global input hook is
installed.

Portable adapter tests replay all 16 shared traces and cover early delivery,
same-batch reopen/focus, stale native IDs, Hide, bounded queues and atomic timer
identity exhaustion. A Windows-only test uses a real private message-window
queue and `SetTimer`/`KillTimer` to verify native delivery, early rearming and a
cancelled callback delivered after its replacement. Existing native connected
controls, smoke and external UIA/keyboard checks verify expanded reopening.
These are synthetic/CI checks. Physical hover travel, pointer geometry across
displays, native menus, keyboard and screen-reader usability remain unqualified;
the Windows metric-card/settings integration also remains outstanding. #338
remains open.

Timer/message contracts: [GetMessage ordering](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-getmessage)
and [KillTimer cancellation](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-killtimer).

## Connected integration evidence and remaining acceptance

Portable worker tests use the actual shared session controller with isolated
synthetic custody/transport adapters. They cover enrollment and reconnect,
locked-store refusal/recovery, actual session expiry, offline retention of the
last success time, scope rejection, sign-out failure/retry, cancellation of a
pending enrollment, late usage after sign-out, owned shutdown and hidden/locked/
sleeping suppression. These do not prove a real account or locked native store.

A Windows-only test drives the same connected native controls and worker using
a real private directory/instance listener, a synthetic in-memory credential
store and synthetic gateway/browser. It fills the form, signs in, observes
scoped usage, folds/hides/reopens through the pipe, observes offline retention,
signs out and verifies ownership release. It checks only its own process/window.
Lifecycle state in that harness is synthetic. There is no shipping test CLI,
credential-target override or plaintext credential fallback.

The release executable retains the independent preview smoke, external UIA,
100-cycle measurement, instance recovery and repeat-build gates. The build
manifest identifies an unsigned connected development candidate, while the
measurement manifest explicitly identifies `synthetic_preview` execution.

Issues #331, #332, #339 and #340 remain open for their broader criteria. An
actual authorized HTTPS gateway account, native Credential Manager denial,
default-browser dispatch, real keyboard/screen-reader/tray interaction,
monitor/taskbar/DPI transitions, sleep/network recovery, signed clean-machine
installation and connected exact-artifact footprint remain unqualified. No
Windows machine/license is available locally; CI is not a substitute for those
gates. No release publication or expanded support claim is implied.
