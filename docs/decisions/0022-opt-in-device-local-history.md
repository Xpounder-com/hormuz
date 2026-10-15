# ADR 0022: Opt-in encrypted history under the local user's control

- Status: Accepted by product owner, October 15, 2026
- Approval: The owner requested local history and spending insights with
  review/delete/export, then explicitly selected encrypted content plus
  metadata and the public CLI/router workflow test boundary.

The customer-local router may retain bounded, redacted request/response text
and usage metadata only after the local operator opts in for a configuration
and actor. Use a separately encrypted private local store, with a local key,
30-day default retention, explicit review/export and deletion. This narrowly
supersedes the content-persistence prohibition in ADRs 0003 and 0014; audit and
budget ledgers retain their existing content-free contracts. Separate storage
lets users delete content without altering accounting evidence.

The CLI and shared skills-only plugin remain the product surfaces from ADR
0018. Collection starts disabled and is checked again at completion, so opt-out
and deletion invalidate pending captures. Expired records are removed on next
use; no network polling or resident cleanup service is added. Exports are
explicitly decrypted local artifacts under the user's control. Filesystem
snapshots, backups, exported copies and other processes running as the same
user are outside this store's encryption/deletion boundary.

History is for review and spending insights. It is never uploaded, used for
training, injected into requests or used as an answer cache. Prices are
configured-rate estimates with unknowns preserved; retained provider responses
do not prove task success, accuracy or savings. See the
[implementation plan](../LOCAL_HISTORY_PLAN.md) and [usage](../LOCAL_HISTORY.md).
