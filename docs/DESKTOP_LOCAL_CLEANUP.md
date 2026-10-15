> Dated operational evidence. Current resource state must be reverified; the active product is the [local router](ROUTER_SCOPE.md). The operational cleanup issues remain open.

# Local Hormuz workspace cleanup

Checkpoint: 2026-10-13. Parent [#494](https://github.com/Xpounder-com/hormuz/issues/494)
and focused [#504](https://github.com/Xpounder-com/hormuz/issues/504), in the
separate cleanup track. The user requested local cleanup for the
Electron/llama.cpp direction and explicitly approved the exact generated-folder
manifest. This is separate from account retirement and real app implementation.

## Verified cleanup

An inventory covered 144 Hormuz Git worktrees under the local aggregate
workspace. The approved operation removed **699 generated folders across 121
inactive checkouts**. Every approved path was subsequently verified absent.

| Generated material | Removed folders |
| --- | ---: |
| Installed `node_modules` dependencies | 19 |
| Next.js `.next` build output | 19 |
| Swift `.build` output | 12 |
| Rust `target` output | 1 |
| Python `__pycache__` | 641 |
| mypy caches | 6 |
| pytest cache | 1 |

Filesystem free space increased by approximately **11.4 GiB** during the
operation. The pre-operation sum of allocated file sizes was approximately
12.7 GiB; it was an estimate, not additive proof of reclaimable storage on APFS.
The filesystem delta is an observation during cleanup, not a guarantee against
concurrent system writes or later cache regeneration.

Tracked working diffs were unchanged in every checked repository. Git status
was identical in 120 checkouts. The primary checkout's only status difference
was disappearance of the untracked `deploy/clients/` entry: that directory had
contained only the approved `node_modules` tree and was empty afterward. This
expected generated-content change was inspected separately from the strict
status-hash check; no source loss was inferred or hidden.

No project builds or tests were run to regenerate removed outputs. Reopening
an old checkout can require installing its pinned dependencies or rebuilding.
The retained source, package manifests and lockfiles remain the recovery inputs.

## Retained material and current dependencies

Preserve the primary dirty checkout, the checkout observed active, configured
virtualenvs, unique/uncommitted source, Git history, credentials, SQLite/runtime
state, financial/private material, release assets/evidence and Apple restore
installers. The aggregate MemoWiki app's active build and unrelated projects
remain intact. Exact private process/path/hash observations live in the task's
operator ledger rather than this public document.

The primary checkout is older than the planning baseline. Many apparently
untracked tests and source files are already tracked in newer main; untracked
status is not permission to discard them.

Current root Dockerfiles, Compose, Helm/Kubernetes, Render and Ceph references
still have explicit CI, test or source-distribution consumers. These source
files remain. No Docker requirement is added to ordinary installed desktop
setup: these checks qualify retained gateway/library contracts. Any future
deletion or reorganization follows #480's accepted reuse boundary and its
concrete CI/package changes, rather than a local cache purge.

Docker's daemon was stopped at the bounded read. A separate earlier Mac-storage
task had already removed its local data disk; this checkpoint claims no new
Docker image/container/volume reclamation. Docker itself was preserved and no
daemon, network workload or VM was started.

## Completed checkout and test-export disposition

The owner then explicitly requested completion of #504, including old checkouts
and test exports, while preserving unique work and release assets. The refreshed
inventory used fetched main `46449d2a3f05de09d7d0a18c9688e189a6c3a68e`.

| Completed disposition | Result |
| --- | --- |
| Clean merged Git checkouts retired | 113 of the 144 in-scope worktrees; all retired paths absent and Git registrations removed through normal, non-forced worktree removal. |
| Checkouts retained in place | 31, with dirty/unique work, primary/previously active usage or private/runtime holds. Both outside-scope paths remained untouched. |
| Ignored material consolidated | 100 content roots preserved through same-volume rename, with identical bytes, symlink targets, modes, owner/group, timestamps, inode/device and flags. Release outputs, configured environments and inert test state were retained. |
| Old non-Git export/test folders retired | 19, after native archive creation and independent restore of every member. Nine installed test environments, four differing source exports and configuration were preserved rather than assumed reproducible. |
| Distinct package/build assets retained | Seven files, kept outside the compressed export archives with their original bytes, hashes and same-volume file identity. |

Every retired checkout's working-file bytes and executable/symlink modes matched
its exact Git commit. Activity, ownership, clean status, ignored-state coverage,
merge ancestry and hidden-index/sparse cases were checked before removal.
Original branches were preserved. Dedicated exact-commit recovery refs and a
self-contained source-history bundle were created before retirement; a separate
mirror and restored checkout verified recovery without shared loose objects.

All 31 retained checkouts' recorded source state remained unchanged. The primary
dirty source, unique commits, active app, credentials/private records, retained
SQLite data, release evidence and Apple restore installers remain protected.
Two closed temporary test databases were preserved as inert retained assets;
no database connection or historical application was activated.

The test-export archives passed native restoration checks for the complete
member set, content hashes, symlink targets, modes, owner/group and nanosecond
timestamps. Native pax carries macOS ACLs and extended attributes; the recorded
checks do not make a broader signing or release-qualification claim. Each
archive and all seven retained package assets have verified SHA-256 records.

The private recovery shelf lives outside the application repository and is
excluded from local Git snapshots. It contains the source bundle, original
path/branch/ref manifests, retained content, export archives, exact package
assets and an opt-in restore helper. The helper's read-only preflight was checked
against a checkout with preserved Mac assets. Restoration requires explicit
intent, refuses an existing destination and never builds or activates old code.

Local-only Git exclusions were extended for regenerable dependency/build/cache
paths in the aggregate workspace and Hormuz repository. Existing exclusions
and tracked source rules were preserved. No shared CI, package inventory or
application source was deleted. Large Git snapshots and retained restore
installers are deliberately outside this completed cleanup scope.

The additional observed filesystem free-space increase after this second pass
was approximately **4.4 GiB**, with approximately **147.7 GiB free** at read-back.
This is separate from the earlier 11.4 GiB observation. Overlapping operation
estimates are not added together as proof of capacity recovery.

Future cleanup remains bounded and explicit: no broad Docker volume prune,
forced dirty worktree removal, unrelated process termination or deletion of
private/runtime records. Read back each selected path and report exact scope
and measured results. #504's audited local disposition is complete; keeping
the protected checkouts and recovery assets is intentional, not pending cleanup.
The [account cleanup ledger](DESKTOP_ACCOUNT_CLEANUP.md) records the independent
hosted-resource work, which remains open.
