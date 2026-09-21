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
profile mismatches before starting a relay listener. It always uses
`Optimization::Off`; no Python optimizer or tokenizer is launched by this Linux
command. Off still forwards through the authenticated, bounded governed relay
and never automatically replays an uncertain upstream POST. The optional
on-demand Python optimizer path below currently applies to macOS and Windows
only.

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
and launcher. On CI without that manager it reports a host-only
skip; ordinary Rust tests still check the fail-closed property parser. Run
`cargo test -p hormuz-client-relay --locked linux_service` on an Ubuntu 24.04
user session for host evidence. Linux binary tests inject a synthetic session,
preflight, request client and gateway: failed preflight touches no private state,
custody or egress; invalid sessions stop before listener creation; a valid
session forwards one Off request. They use no real keyring or provider. A real
credential-backed user-manager launch, installed-client traffic, Secret Service
desktop behavior, native-shell lifecycle wiring and clean-install proof remain
open. Issue #341 remains open; this source checkpoint does not qualify Linux
support.

The relay admits the client's expected POST routes only. It checks Host,
Origin, one local bearer/API-key credential, content length and a 25 MiB request
limit before obtaining the current gateway credential. It forwards the selected
headers and exact Off body through a bounded stream, and forwards gateway
responses as they arrive. Requests are never replayed after an uncertain
outcome. A failed or unavailable gateway credential prevents upstream egress.

The existing private `context-optimization-<profile>.json` toggle is read for
each eligible request. Missing, invalid and Off settings keep the exact body.
On attempts invoke the existing Python optimizer through bounded stdin/stdout
only for requests at most 1 MiB. The Python helper must be available in the
selected `python3` (or `python.exe`) installation as an installed Hormuz wheel;
`-I` intentionally excludes imports from the current working directory. If the
helper, gateway capability or tokenizer resources are unavailable, the relay
forwards the exact original body once. No Python process runs while idle.
Claude token-count requests remain governed but bypass the optimizer entirely.
The configured `HORMUZ_CONTEXT_TOKENIZER_CACHE` path is retained for the
helper; direct provider credentials and Python import overrides are not.
The gateway origin and request body reach the helper only through bounded
stdin, never through process arguments.

On macOS, the optimizer exchange uses nonblocking owned pipe ends and one
30-second deadline for input delivery, output collection and helper exit.
It drains output while writing input, rejects excess output, and kills/reaps
the direct helper on failure or relay-owner cancellation. A process retaining
inherited pipe ends cannot extend the I/O deadline through a reader/writer
thread join. This owns and reaps only the direct Unix helper; it does not
contain a helper descendant after fork. Unix helper-tree containment remains a
separate acceptance gate.

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
first-party macOS implementation honors it by killing and reaping its direct
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
packaged optimizer interpreter, real
Codex/Claude sessions, Windows accessibility and clean-machine acceptance
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
overflow and direct-child reaping. Linux runs those fixtures without enabling
the unsupported native relay command. Windows fake-helper tests cover Job
Object containment, cancellation and pipe-worker completion without an
installed optimizer or provider. These tests do not prove cancellation of an
arbitrary non-cooperative in-process optimizer, Unix client or optimizer-helper
descendant containment; the separate Linux user-service fixture above runs only
with a real manager. These tests also do not prove macOS abrupt launcher-death
cleanup, real optimizer/provider sessions, or packaged native-shell lifecycle
behavior. The Linux parent-death tests cover a direct synthetic client only;
Linux's native executable still fails closed without a secure-store adapter.
