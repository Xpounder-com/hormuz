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

## Remaining local disposition

The audit found old clean/merged worktree candidates, dirty/unique worktrees
and packaging/test exports. They are recorded in #504. Whole-checkout removal
must recheck current activity, owner, Git status, unique commits and ignored
retained-state holds, with a recovery manifest/ref. A clean status alone is
insufficient because ignored files can contain credentials or unique evidence.

Old sdists, wheels and source exports need byte/provenance and sole-copy/release
evidence checks before removal. A standard test virtualenv layout alone does not
prove every installed package disposable. Large Git snapshots and retained
restore installers are outside the approved generated-folder deletion.

Future cleanup remains bounded and explicit: no broad Docker volume prune,
forced dirty worktree removal, unrelated process termination or deletion of
private/runtime records. Read back each selected path and report exact scope
and measured results. The [account cleanup ledger](DESKTOP_ACCOUNT_CLEANUP.md)
records the independent hosted-resource work.
