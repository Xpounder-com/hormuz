# Native Windows companion foundation

Development slice for [#331](https://github.com/Xpounder-com/hormuz/issues/331),
planned v1.4.0, using the contracts from #330. This is an unsigned synthetic
preview, not a connected client or an installer. Current build target:
Windows 10/11 x64 (`x86_64-pc-windows-msvc`); Windows ARM64 is not qualified here.

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
./target/release/hormuz-windows.exe
```

The toolchain and lockfile fix dependency resolution; this is a repeatable build
procedure, not a bit-for-bit reproducibility, signing or distribution claim.
Use the CI artifact's source commit, compiler, target and SHA-256 when reporting
results. The CI release executable is development evidence only.

The bounded smoke runner starts a real native process, checks HWND/control
creation, verifies actual window-height and child-visibility changes on fold and
expand, exercises close/hide and a synthesized tray callback, and checks exit.
It reports whether the tray registered; a machine without Explorer exercises
taskbar fallback. A synthesized callback does not prove an actual tray click,
keyboard navigation, accessibility, monitor migration or the visual layout.

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
