# Native platform services

This unpublished `1.5.0-dev.1` library implements the custody, private-file and
coordination foundations of [#333](https://github.com/Xpounder-com/hormuz/issues/333).
It also defines the browser, supervised-process and lifecycle-event interfaces
for their owning shell/scheduler issues. Those interfaces start no browser,
process, event subscription, timer, network runtime or tokenizer.

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
| macOS | Existing file Keychain generic-password service `com.hormuz.mac.session.v1`, account `active-connection-v1`, synchronization disabled | 32,767 bytes; update in place, add only if missing; per-query authentication UI is disabled so unavailable/locked records fail closed |
| Windows | Current-user Credential Manager generic target `Hormuz/session/v1/active-connection` | 2,560 bytes; native replacement; local-machine persistence means this user's record survives logons on this machine, not access for every machine user |
| Linux | Deferred to #343 | No native credential backend or file fallback is supplied |

No second Rust credential namespace is introduced on Mac. The file Keychain
preserves the existing app's behavior; it does not claim Data Protection,
device binding, screen-lock protection or approval for a different executable.
Signed-app integration must verify the app/helper access identity under #345.

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
  Windows devices and reserved lock/staging names, including case aliases. Files
  are bounded while reading and writing at 1 MiB. They are non-executable.
- Writes use a private exclusive staging file, flush its bytes, then use a
  native atomic exchange/replacement. The displaced content is compared with the
  expected snapshot. A detected external edit is restored; failed rollback
  preserves both files instead of deleting the displaced data. All normal
  writers must cooperate with the connection lock; this is not isolation from a
  malicious process running as the same user or an administrator.
- An interrupted process can leave a private `.write-` staging/backup file.
  Readers ignore it, the committed file remains independently readable, and
  retries never adopt it as configuration. This layer deliberately does not
  delete arbitrary abandoned files. A write failure after the native commit
  can have an uncertain durability outcome: re-read before retrying. Power-loss,
  network-filesystem and every disk-failure combination are not qualified here.

All native APIs are synchronous worker operations. Errors are fixed variants
and fixed display messages; they contain no paths, SIDs, OS diagnostics or data.
`BrowserOpener`, `ProcessSupervisor`, `SupervisedProcess` and `LifecycleEvents`
are interfaces only. Browser validation/launch, binary/environment policy,
process termination and event coalescing are implemented and verified in the
corresponding later shell/lifecycle issues.

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
failure and preservation after unlock. Windows tests use a unique synthetic
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
