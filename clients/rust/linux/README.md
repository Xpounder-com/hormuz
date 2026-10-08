# Linux GTK companion development shell

This is a real GTK4 ordinary-window companion, not a preview that displays
fabricated usage. It consumes `hormuz-client-desktop`'s existing single session
worker, `SessionController`, credential-free snapshots, visibility scheduler
and shared interaction policy. No new authentication or refresh state machine
is introduced.

Build on Linux with the existing pinned Rust toolchain and native GTK4/OpenSSL
development libraries:

```sh
cd clients/rust
cargo build -p hormuz-linux -p hormuz-client-relay --features hormuz-linux/gtk-ui --release --locked
./target/release/hormuz-linux
```

GTK is Linux-only and opt-in. Default workspace builds on macOS, Windows and
Linux do not require GTK. Running an executable built without `gtk-ui` exits
with status 2 and explicitly says the shell was not built; that stub is not
native acceptance evidence.

## Working connection and presentation

The standard GTK fields/buttons support custom HTTPS gateway setup, browser
sign-in, operation cancellation, saved-connection retry, deliberate coalesced
refresh, sign-out/disconnect, requests/tokens/cost details, pinning, fold/expand,
Escape dismissal and explicit reopen. Missing values remain an em dash, offline
readings retain their successful sample time, scope mismatches expose no usage,
and estimates are labeled gateway-only, not provider bills. GTK owns native
keyboard tab order, labeled mnemonic inputs and accessible button semantics.
The resizable screen scrolls vertically and wraps text at narrow widths.

Secret custody is the existing fixed-attribute Secret Service adapter, not a
file fallback. The private root is the native passwd home plus
`.hormuz-native`, using the existing private-directory checks and instance
lease. Gio application activation presents the same process/window. Startup,
credential/network work and shutdown joins do not run on GTK's main thread.
There is one bounded/coalesced notification for the UI and one pending command
in the shared session worker; no view-refresh timer is introduced.

The shell deliberately remains an ordinary recoverable window on X11, Wayland
and other GTK displays. No layer-shell or tray is implemented or claimed.
Folding/closing details does not hide the application or stop a client. Closing
the application window requests Quit; there is no invisible resident state that
depends on an unavailable tray extension. With an active, starting or stopping
client it presents Wait, Cancel and Stop-and-Quit choices. Wait keeps the lease
open and quits only after confirmed natural completion; its pending quit can be
cancelled. Cancel and hiding/minimizing the window do not stop the client. Stop
and Quit explicitly interrupts the client and retains ownership if stop fails.

Actual logind lock/sleep observations and Gio network-change signals feed the
existing scheduler. Missing logind properties, missing owners or lost services
pause automatic refresh and show an explicit notice. Focus alone is never
presented as proof that a desktop is unlocked. Session restore/sign-in and
sign-out still work when desktop lifecycle monitoring is unavailable. A logind
Manager reconnect restores refresh only with an owner and a valid current sleep
property or signal; missing/invalidated properties stay conservatively paused.

## Explicit governed terminal launch

After active sign-in, Launch opens the existing supported client through the
sibling `hormuz-client-relay`, a transient systemd user service and GNOME
Terminal. The existing relay remains the only custody/discovery/transport
authority, and verifies the exact systemd cgroup policy before custody or a
client version probe. This action requires `/usr/bin/gnome-terminal`, systemd
254+ and the normal user manager; an unavailable capability fails visibly and
does not hide or disable the connection screen.

The app owns one random exact service token and a private same-UID Unix socket.
The Linux relay must receive byte `0x01` from the app before custody/discovery,
then watches socket EOF for its lifetime. The app ACKs only an accepted same-UID
peer while still launching. Quit closes the lease and uses the existing
exact-unit stop command; failed stop retains ownership for an explicit retry.
Finished and failed launch handles are joined off GTK's thread and release the
Launch control; a completed launch never requires an extra Stop click to retry.
Sign-out also requests terminal stop. A panel fold, details dismissal or hidden
window never closes this lease. There is only one on-demand terminal manager,
one pending stop command and coalesced non-secret status notifications. Native
server startup may be delayed; closing the listener before ACK prevents a late
relay from starting an AI client. Credential values are never put in launch
arguments or copied into the terminal environment. The unit uses control-group
tree cleanup, not a process-group approximation.

The GTK launch path requires the optional Linux `--owner-socket` relay contract;
do not package it with a relay that lacks this contract. This source checkpoint
is not an automatic updater. Quit/update must drain the owned terminal before
releasing the app's instance lease; no installer may replace a live binary.

## Verification and remaining platform boundaries

The native-client workflow builds and lints the actual GTK feature on Ubuntu,
runs its actual GTK controls under Xvfb with isolated custody/transport/browser,
and records original release binary/relay hashes, compiler, GTK runtime, dependencies,
logs and desktop/narrow/details screenshots. It exercises successful readings,
retained offline time, wrong-scope clearing, sign-out clearing, shutdown and
native accessible roles. Linux-only native socket/child fixtures additionally
check accepted owner ACK, quit-before-ACK, exact-unit stop and retained ownership
on failure without retrying unchanged errors. These tests make no paid provider
request and do not expose a production test CLI or alternate credential target.
The widget fixtures run in an actual **debug Rust test executable**, whose exact
executed bytes and hash are included separately in schema-2 proof. They exercise
production GTK controls with isolated session/terminal peers, including final
handle drain and Wait/Cancel/Stop-and-Quit. The release executable is built and
hashed but its GUI startup, GApplication/Secret Service and real governed-client
paths are **not exercised by those screenshots or fixture tests**.

Before claiming supported Linux, verify the original exact-head/main GTK CI
artifact and screenshots, real GNOME/Wayland and X11 desktop rendering, native
Secret Service unlock/lock/restart behavior, actual logind events, interactive
terminal launch with the real user manager and qualified client, abrupt app
exit/cgroup cleanup, scaling/keyboard/screen-reader behavior, packaging/install
and footprint/power budgets. Synthetic GTK fixtures and an API-only macOS type
check cannot substitute for those desktop criteria. Development artifacts stay
unsigned, and this work does not publish a Linux customer release.
