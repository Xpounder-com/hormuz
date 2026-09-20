# Native platform services

This unpublished `1.5.0-dev.1` library implements the custody, private-file and
coordination foundations of [#333](https://github.com/Xpounder-com/hormuz/issues/333),
plus application ownership and worker-side helper lifecycle policy for a bounded
slice of [#339](https://github.com/Xpounder-com/hormuz/issues/339). It defines the
browser, supervised-process and lifecycle-event interfaces for their owning
native integrations. Constructing these types starts no browser, process, event
subscription, timer, network runtime or tokenizer.

The shipping Mac app and Python gateway do not load this library. Their versions,
credential records and configuration are unchanged. A native library test is
not sign-in acceptance or a packaging/release claim.

## Credential custody

`CredentialStore` reads, replaces or deletes one opaque `SecretRecord`. The
record's owned bytes are zeroized on drop and its debug representation is fixed
and redacted. There is no serialization implementation or plaintext fallback.
OS APIs and callers can own other copies; this is not a claim of complete memory
erasure. Session format validation and pending-state transitions belong to #335.

| Platform | Authoritative record | Bound and behavior |
| --- | --- | --- |
| macOS | Existing file Keychain generic-password service `com.hormuz.mac.session.v1`, account `active-connection-v1`, synchronization disabled | 32,767 bytes; update in place, add only if missing; noninteractive native calls fail closed on unavailable/locked records |
| Windows | Current-user Credential Manager generic target `Hormuz/session/v1/active-connection` | 2,560 bytes; native replacement; local-machine persistence means this user's record survives logons on this machine, not access for every machine user |
| Linux | Deferred to #343 | No native credential backend or file fallback is supplied |

No second Rust credential namespace is introduced on Mac. The file Keychain
preserves the existing app's behavior; it does not claim Data Protection,
device binding, screen-lock protection or approval for a different executable.
Signed-app integration must verify the app/helper access identity under #345.
File-Keychain calls are serialized while the process interaction setting is
temporarily disabled, then restore its exact prior value; per-query UI rejection
is also requested. Native integration must route credential calls through this
adapter instead of concurrently changing that process-wide setting elsewhere.

Before a session operation, callers must check `maximum_record_bytes()`, validate
their record, and hold the shared connection coordination guard through the
complete read/pending-write/request/commit sequence. A Windows session that would
exceed the native blob limit must fail before a state-changing network request;
do not split credentials, truncate a profile or invent an alternate storage
location. Pending refresh recovery and ambiguous request handling remain #335.

## Private configuration and refresh coordination

`PrivateDirectory::open` takes an absolute application-data directory chosen by
the native shell. It creates only the final directory and never repairs an
unsafe existing object. All cooperating app/helper processes must use the same
directory and the fixed `connection.lock`; choosing a different root is not an
independent session store. Do not put credentials in this directory.

`RefreshCoordinator` exposes nonblocking acquisition. `PrivateTransaction` owns
the kernel lock until dropped, including across awaits. `Busy` leaves retry and
deadline policy with the scheduler; the platform layer never blocks the UI in a
retry loop. Process termination releases the lock without deleting its sentinel.
The `PrivateFiles` interface is available only through the retained transaction.

- Mac creates the directory as 0700 and files as 0600, checks the current UID,
  and rejects extended ACL entries. Files are opened relative to the retained
  directory descriptor with no symlink following. Regular files must have one
  hard link. No existing permissions are silently broadened or repaired.
- Windows creates objects with an explicit current-user owner and a protected
  DACL granting only that user full access. Every opened handle is checked using
  `GetSecurityInfo`; inherited/public/null/other-user ACLs fail. The adapter
  rejects reparse points and non-disk/non-regular objects and checks hard-link
  counts. A held root handle prevents directory replacement; the lock handle
  disallows deletion. Read-only attributes are never treated as privacy.
- Portable names are bounded to 128 ASCII bytes and exclude separators, streams,
  Windows devices and reserved `connection.lock`, `instance.lock` and staging
  names, including case aliases. Files
  are bounded while reading and writing at 1 MiB. They are non-executable.
- Writes use a private exclusive staging file, flush its bytes, then use a
  native atomic exchange/replacement. The displaced content is compared with the
  expected snapshot. A detected external edit is restored; failed rollback
  preserves both files instead of deleting the displaced data. All normal
  writers must cooperate with the connection lock; this is not isolation from a
  malicious process running as the same user or an administrator.
- Windows replacement preserves the destination's DACL. If a raced ACL change
  makes it unsafe, recovery compares the installed file with the staging file's
  native identity and bounded bytes before rollback; public reads still reject
  the unsafe ACL. A destination created during an exclusive write returns
  `Changed` and removes only the untouched staging file.
- An interrupted process can leave a private `.write-` staging/backup file.
  Readers ignore it, the committed file remains independently readable, and
  retries never adopt it as configuration. This layer deliberately does not
  delete arbitrary abandoned files. A write failure after the native commit
  can have an uncertain durability outcome: re-read before retrying. Power-loss,
  network-filesystem and every disk-failure combination are not qualified here.

All native APIs are synchronous worker operations. Errors are fixed variants
and fixed display messages; they contain no paths, SIDs, OS diagnostics or data.
`BrowserOpener`, `ProcessSupervisor`, `SupervisedProcess` and `LifecycleEvents`
still require native implementations. Browser validation/launch, signed binary
and environment policy, process groups/job objects, bounded termination and
native event delivery remain work for the owning shell issues.

## Application ownership and helper lifecycle

`PrivateDirectory::try_claim_instance()` takes a separate, nonblocking kernel
lock on the fixed private `instance.lock`. `ApplicationInstance` retains that
lock and the directory until dropped. The sentinel must be an empty regular
file with the same owner, mode/ACL, no-follow and single-hard-link checks as
private configuration. Mac also checks that the opened and current sentinel
identities match after acquisition; Windows denies deletion/replacement while
its handle is open. Unsafe or nonempty sentinels fail without repair or removal.
If Mac's concurrent first creation returns `ENOENT`, acquisition makes one
attempt to open the existing winner's sentinel and still takes the kernel lock;
an absent sentinel remains `Unavailable`. Other unsafe failures are not retried.
Dropping the guard or process death releases ownership; an existing empty file
does not mean an instance is running. The sentinel is never deleted on exit.

Every manual, login-start and reopen path must choose the same shell-provided
application-data root. A second process receives `Busy` and must use the native
shell's activation/reopen path without creating a helper owner. The long-lived
application lock is independent of `connection.lock`: app/helper refresh
transactions remain available while the application is resident. Linux still
returns `Unsupported`; no ordinary-file fallback is introduced.

`lifecycle::HelperLifecycle` owns an application guard and at most one
`ProcessSupervisor::Child`. `tick` runs on a bounded worker; the shell supplies
monotonic elapsed time and schedules the next worker turn. It provides these
source policies:

- `close_panel` and `reopen` emit only `Hide` and `Reopen` intents for #338/native
  presentation. They do not stop clients, launch a helper or reset retry state.
- Explicit helper demand starts one child. A successful launch reserves its
  handle until confirmed exit/termination. Failed liveness or stop operations
  retain ownership, so they cannot lead to a duplicate launch or a false
  ready-to-exit result. `HelperStatus::Owned` describes that retained handle;
  it does not claim service readiness, healthy authentication or connectivity.
- Each admitted client gets a noncloneable `ClientLease`, held until that client
  exits. Counts are bounded at 1,024. Removing demand cannot stop a helper while
  any accepted clients remain. The shell must verify readiness before admission
  and roll back the lease if its subsequent client launch fails.
- Quit and update immediately stop admissions and drain existing clients. Only
  after the final lease drops and the helper is confirmed stopped does the
  controller report `ReadyToExit`. A quit request cancels a pending update.
  Explicit `force_quit` may stop the owned helper while clients still exist;
  it becomes a quit and never authorizes a forced update. It does not kill
  external client processes. Panel close alone never initiates this sequence.
- Sleep and session lock independently pause admissions and new/recovery
  launches. They preserve already-owned helpers and client leases; waking or
  unlocking cannot create a duplicate. Network hints belong to the session
  scheduler and have no helper-launch effect here.
- Launch failure and confirmed unexpected exit use bounded exponential delay.
  The default budget is three attempts including initial launch; successful
  short launches and reopen/wake/network events do not reset it. The shell can
  offer an explicit retry after exhaustion. No request or authentication retry
  is performed by this controller.

`HelperCommand` rejects relative/NUL-containing/oversized command inputs and
redacts debug output. This is structural validation only; the native supervisor
must enforce executable identity, allowed operational arguments and environment
policy. Neither credentials nor request/response bodies belong in arguments.
Snapshots expose only phase, booleans, counts and monotonic retry timing.

Shutdown failures retain the child and instance guard for retry. Controller drop
attempts cleanup and drops the child before releasing the guard. Native child
adapters must guarantee non-detaching drop and parent-death cleanup using their
own process ownership mechanisms. Fake-supervisor tests prove controller policy,
not orphan prevention after a real shell crash. No native supervisor is supplied
by this checkpoint, so that crash containment and process cleanup remain open.

No socket or named pipe is added, so there is no IPC peer/message surface to
qualify in this slice. Native activation/reopen, opt-in login registration,
trusted executable launch, helper readiness, real parent/helper crash recovery,
actual sleep/resume, updater handoff and final cross-platform shell integration
remain #339/#341/#345 acceptance. This work does not close #339, qualify a
shipping application, or complete the unreleased v1.4 gates.

## Native verification

From `clients/rust`:

```sh
cargo fmt --all -- --check
cargo test --workspace --locked
cargo clippy --workspace --all-targets --locked -- -D warnings
```

The native-contract workflow runs the workspace on Windows, Linux and macOS.
Mac tests create/delete an explicit ephemeral Keychain with a synthetic password
and use only that Keychain. They verify replacement, locked-store read/write
failure and preservation after unlock. The Mac credential operations run in a
bounded child process with content-free phase markers; its parent retains
ownership of temporary Keychain cleanup. Windows tests use a unique synthetic
Credential Manager target, verify replacement/size failures and delete only
that target. Neither test addresses the user's real Hormuz session.

Native filesystem tests verify atomic snapshots, staged-write failure, real
permission/ACL rejection, unsafe paths/hard links, concurrent updates, a second
process denied by the held lock, and abrupt process exit before commit followed
by recovery. The crash worker intentionally exits without running destructors.
Synthetic marker/debug checks protect diagnostics. Linux tests explicitly
verify `Unsupported` without creating an ordinary-file fallback; Linux storage
acceptance remains in #343. Windows type-checking on a Mac is supplementary and
never substitutes for native Windows execution.

Application tests race competing startups, deny a second process while permitting
refresh, and recover after a child exits without destructors. Negative tests
cover malformed/nonempty sentinels, hard links, mode/ACL/reparse rejection and
Mac sentinel replacement. Portable fake-supervisor tests exercise bounded
commands and client counts, concurrent client lease release, panel-close
behavior, drain/force-quit/update distinctions, uncertain helper operations,
exhausted crash recovery and independent sleep/lock gates. Native Windows and
Linux runtime results come from their own CI runners; the local Mac suite is
not a substitute.
