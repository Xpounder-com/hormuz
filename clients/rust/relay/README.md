# Governed client relay (#341 source checkpoint)

`hormuz-client-relay` is an unpublished `1.6.0-dev.1` executable and library.
It uses the shared native session controller with the existing Mac Keychain,
Windows Credential Manager, or Linux Secret Service record. A native shell can
launch it with the active profile UUID and its existing private state directory:

```text
hormuz-client-relay --profile <uuid> --state-directory <absolute-private-root>
```

The controller verifies that the stored profile matches and can supply a
current access credential before launching a client. It discovers only Codex
`0.147.0` or Claude Code `2.1.233`, supplies per-invocation local settings,
removes direct-provider credentials from the child environment, and opens a
fresh authenticated 127.0.0.1 relay. The relay exists only while the directly
launched client is alive. On Windows, the launcher assigns a suspended client
to a kill-on-close Job Object before resuming it. The job stops its descendants
after normal exit, failure or explicit cancellation, and the direct child is
reaped. Linux also arms a parent-death signal before each direct client or
version probe executes, so abrupt launcher death kills an ordinary direct
process; the pre-exec hook rejects a child if the launcher already died. Linux
clears this setting on fork, privileged exec and credential changes, so it does
not contain descendants. The kernel ties it to the spawning launcher thread:
that thread's exit kills the direct child even if other launcher threads remain.
The current launch and version-probe paths wait on that thread. macOS retains
direct-child-only cleanup,
and neither Unix path changes shell job control. A panel close must leave the
launcher alive; a shell quit/update must coordinate its termination separately.

### Linux user-service containment source checkpoint

The Linux terminal command first verifies its on-demand user service **before**
opening private state or contacting Secret Service. Client discovery repeats
the verification before a version probe or relay starts. Both checks require
this launcher to be the main PID of an active, transient **user systemd
service**. The guard verifies kernel cgroup membership against the live
service's `ControlGroup`, `MainPID`, `ExitType=main`,
`RemainAfterExit=no`, `Restart=no`, `KillMode=control-group`, and `KillSignal=SIGKILL`
properties. It does not trust an environment flag.
An otherwise exact unit may remain in `ActiveState=activating` briefly after
exec; production verification retries only that state within one three-second
deadline shared by process setup, polling, bounded output parsing, and final
acceptance. Missing, duplicate, or mismatched security properties fail
immediately.
The lookup uses the owned socket at `/run/user/<effective-uid>/bus`, with a
private 0700 runtime directory and non-root matching real/effective UID,
rather than a caller-supplied D-Bus address.
On a host with a user manager, `run-in-user-service.sh` starts a single
invocation with those properties. The caller chooses a unique token and owns
`hormuz-relay-<token>.service`; an explicit quit/update must stop that unit.
The Linux-only `hormuz-client-relay stop --unit-token <same-token>` command
provides that exact-unit control path from outside the service. It validates
the token and the canonical same-UID user bus, checks the live transient unit's
identity and tree-kill properties, asks systemd to stop it, and waits within a
five-second total deadline for inactive or collected state. It does not open
private state, contact Secret Service, discover a client, or start a relay.
Malformed tokens, an unavailable canonical bus, mismatched properties, and
timeout fail closed; caller-supplied bus variables are ignored. Stopping the
service sends its configured `SIGKILL` to the whole cgroup, so an already-sent
POST can have an uncertain upstream outcome; the relay does not replay it. A
panel close must not invoke this stop command.
The wrapper and stop command now limit tokens to 128 ASCII bytes. No shipping
launcher creates these units yet; a longer unit created with the earlier
source-only wrapper needs its exact unit stopped through the user manager
before adopting this control path.
The wrapper selects a PTY for interactive input and pipes otherwise. It passes
only the relay executable and its existing invocation arguments to systemd,
disables systemd environment expansion for that already-formed argv, and copies
the caller's `PATH` by variable name for session-local client discovery. The
`PATH` value and relay credentials are not added to the command line; relay
credentials are resolved inside the launcher. The literal-argument switch
requires systemd 254 or newer and fails closed when unavailable. This source
path is not wired to a resident native Linux shell yet.

On Linux, the command opens the validated private directory and uses the
Secret Service default collection through `NativeCredentialStore`. It rejects
missing or locked credentials, pending or lifetime-expired sessions, and
profile mismatches before starting a relay listener. With no extra argument,
it uses `Optimization::Off`: no Python optimizer or tokenizer is launched, and
the authenticated, bounded relay streams the exact body. A terminal caller may
explicitly add `--optimizer-python <absolute-executable>` to the supervised
invocation. The file must exist and be executable, and the Hormuz wheel must be
installed for that interpreter so its isolated
`-I -m hormuz.context_relay_bridge` invocation can find the module. An
absolute executable path does not prove the wheel is installed; the module is
not probed at launch. Interpreter file inspection follows the verified service
and session checks and precedes the listener. If the wheel is absent when an
On request arrives, the helper fails and the original body is forwarded once.
The existing private per-profile toggle
must also be enabled; an absent, invalid or Off toggle never starts Python.
The helper gets only the gateway origin and bounded request body over stdin,
with no provider credential or Python import override inherited. This path
is source-level opt-in, not a packaged interpreter or native-shell integration.
The relay never automatically replays an uncertain upstream POST.

The launcher, version probe, client and normal descendants inherit the service
cgroup on fork, even if a descendant double-forks or calls `setsid`. systemd
stops the whole group after its main launcher exits, is killed, or the unit is
explicitly stopped. The live unit must report `RemainAfterExit=no` so main
process exit enters that stop path. A scope unit does not have that
main-process lifetime.
The direct-child parent-death guard remains defense in depth. A same-UID
process that can deliberately migrate itself out of the user manager's cgroup
tree is outside this ordinary-client lifetime guarantee; stronger isolation
would require separate host authority and acceptance. No fallback to PID or
process-group enumeration is treated as equivalent containment.

The Linux-only synthetic test runs normal-exit, explicit-stop and abrupt
launcher-death cases with a detached listener when a real user systemd manager
is reachable. It checks the new session, cgroup membership, listener closure,
and empty/removed cgroup while passing spoofed bus variables to the wrapper
and launcher. The explicit-stop case calls the same production stop control
path as the terminal command. On CI without that manager it reports a host-only
skip; ordinary Rust tests still check the fail-closed property parser. Run
`cargo test -p hormuz-client-relay --locked linux_service` on an Ubuntu 24.04
user session for host evidence. Linux binary tests inject a synthetic session,
preflight, request client and gateway: failed preflight touches no private state,
custody or egress; invalid sessions stop before listener creation; a valid
session forwards one Off request. They use no real keyring or provider. A real
credential-backed synthetic launch can also be checked by the opt-in
`linux_secret_service_launch` host test. It runs only as a disposable
`hormuzrelaytest` account with home `/tmp/hormuz-relay-test-home`, its own active
user manager and an unlocked default Secret Service collection; set
`HORMUZ_RELAY_ISOLATED_SECRET_SERVICE_TEST=1` when invoking the compiled test
binary. It requires an empty Hormuz credential namespace, saves and later
deletes a synthetic session, then runs this executable through the user-service
wrapper with a fake client and gateway. The fixture verifies one Off request,
credential custody after a separate transient service confirms synthetic
inherited-key sentinels in the disposable user manager, a refused listener
connection, and an inactive or collected service after client exit.
Ordinary CI records this host test as ignored; the opt-in marker and dedicated
user guard remain required when running it with `--ignored`, and an attempted
explicit run without the marker fails. Installed-client traffic and desktop
Secret Service behavior beyond this synthetic account,
native-shell lifecycle wiring and clean-install proof remain open. Issue #341
remains open; this source checkpoint does not qualify Linux support.

A separate ignored `linux_stop_active_relay` host test needs only a disposable
systemd user manager, not Secret Service. With
`HORMUZ_RELAY_ISOLATED_USER_SERVICE_TEST=1`, it starts an Off relay and fake
gateway in one transient unit, stops it while one synthetic upstream POST is
waiting for a response, and checks upstream closure, one refused relay-listener
connection, an empty/removed cgroup, and no second POST. This proves forceful
source-lifetime cleanup for that fixture; it cannot establish the outcome of
the already-sent POST or a native shell's quit/update integration.

The relay admits the client's expected POST routes only. It checks Host,
Origin, one local bearer/API-key credential, content length and a 25 MiB request
limit before obtaining the current gateway credential. It forwards the selected
headers and exact Off body through a bounded stream, and forwards gateway
responses as they arrive. Requests are never replayed after an uncertain
outcome. A failed or unavailable gateway credential prevents upstream egress.

The existing private `context-optimization-<profile>.json` toggle is read for
each eligible request when an optimizer is configured. Missing, invalid and
Off settings keep the exact body. On attempts invoke the existing Python
optimizer through bounded stdin/stdout only for requests at most 1 MiB. The
helper must be available in the selected `python3` (macOS), `python.exe`
(Windows), or explicit absolute interpreter (Linux) as an installed Hormuz
wheel; `-I` intentionally excludes imports from the current working
directory. If the helper, gateway capability or tokenizer resources are
unavailable, the relay forwards the exact original body once. No Python
process runs while idle.
Claude token-count requests remain governed but bypass the optimizer entirely.
The configured `HORMUZ_CONTEXT_TOKENIZER_CACHE`, `SSL_CERT_FILE`, and
`SSL_CERT_DIR` paths are retained for the helper; direct provider credentials
and Python import overrides are not. On Linux, the helper gets the validated
`--state-directory` as its child-only `HORMUZ_CLIENT_STATE_DIRECTORY` so the
default tokenizer cache resolves under that private root when no explicit
cache path is configured. An explicit `HORMUZ_CONTEXT_TOKENIZER_CACHE` keeps
precedence. The helper does not inherit a caller-supplied state override.
The gateway origin and request body reach the helper only through bounded
stdin, never through process arguments.

On macOS and opt-in Linux, the optimizer exchange uses nonblocking owned pipe
ends and one 30-second deadline for input delivery, output collection and
helper exit.
It drains output while writing input, rejects excess output, and kills/reaps
the direct helper on failure or relay-owner cancellation. A process retaining
inherited pipe ends cannot extend the I/O deadline through a reader/writer
thread join. This owns and reaps only the direct Unix helper; it does not
contain a helper descendant after fork. Linux's verified user-service cgroup
contains ordinary helper descendants until unit exit; deliberate same-UID
cgroup migration remains outside this source guarantee. macOS helper-tree
containment remains a separate acceptance gate.

On Windows, the optimizer helper starts suspended and is assigned to its own
kill-on-close Job Object before running. The exchange reads and writes pipes
concurrently under one 30-second deadline. It closes the job before joining
the workers on failure, timeout or relay-owner cancellation, requests
cancellation of pending synchronous pipe I/O, and exits the dedicated relay
without a crash dump if workers cannot finish within a further two seconds;
it cannot return while threads still hold request or response bytes. Synthetic
Windows tests exercise inherited pipe ends, blocked I/O, oversized output and
bidirectional exchange. Process creation and kernel termination are not covered
by a hard elapsed-time guarantee. Request and response bytes stay in memory.
This is a #339 helper-lifetime and #341 relay checkpoint; both issues remain
open.

Relay shutdown publishes one sticky optimizer-cancellation signal before it
closes the optimizer job registry or stops request tasks. Registration and
shutdown share one lock, so no `spawn_blocking` job can register after closure.
Abort handles stop jobs that remain queued. A closure scheduled concurrently
with shutdown checks the sticky signal before calling the optimizer; an
optimizer already running receives that signal and must return promptly. The
first-party Unix implementation honors it by killing and reaping its direct
helper, while the Windows Job Object stops the helper and its descendants
before pipe workers join. Rust cannot forcibly stop arbitrary in-process
`RequestOptimizer` implementations that ignore the cooperative contract, so
this checkpoint does not claim that broader guarantee.

This source checkpoint is not loaded by the Windows panel or shipping Mac app.
Windows Job Object descendant cleanup is covered by fake clients. Linux has
synthetic direct-client launcher-death, pre-exec race and normal-exit tests,
plus a host-conditional user-service test for a detached grandchild. macOS
still has direct-child-only cleanup; Linux's new cgroup path needs real user
manager and shell-wiring acceptance. Native-shell panel and quit/update wiring,
packaged optimizer interpreter, real Codex/Claude sessions, Windows
accessibility and clean-machine acceptance
remain open. The blocked-optimizer shutdown fixture proves that cancellation
stops first-party work before gateway egress; it does not establish a general
linearization boundary between cancellation and an upstream POST that is
already starting or in flight. Such an uncertain POST is still never replayed.
No release or package version is changed.

From `clients/rust`, run `cargo test --workspace --locked` and
`cargo clippy --workspace --all-targets --locked -- -D warnings`. The relay
unit tests cover local auth, Off bytes, streaming, optional transforms,
oversized bodies, unavailable credentials, queued/running optimizer shutdown
and Windows fake-client job lifetime.
The Python bridge is covered by
`python -m unittest -v tests.test_context_relay_bridge`.
Synthetic Unix pipe tests also cover partial and bidirectional I/O, retained
pipe ends after direct-helper exit, a helper that never consumes stdin, output
overflow and direct-child reaping. Linux runs those fixtures alongside the
guarded default-Off and explicit opt-in native relay command. A fake
interpreter/gateway test checks transformed bytes only when both the explicit
path and private preference are present; it never invokes a model or provider.
Windows fake-helper tests cover Job Object containment, cancellation and
pipe-worker completion without an installed optimizer or provider. These
tests do not prove cancellation of an arbitrary non-cooperative in-process
optimizer, Unix client or optimizer-helper descendant containment; the
separate Linux user-service fixture above runs only with a real manager. These
tests also do not prove macOS abrupt launcher-death
cleanup, real optimizer/provider sessions, or packaged native-shell lifecycle
behavior. The Linux parent-death tests cover a direct synthetic client only.
The credential-backed host fixture proves the default-Off path under an
isolated Secret Service user manager; the explicit Python path still needs
packaged-interpreter and installed-client acceptance.
