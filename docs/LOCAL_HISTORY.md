# Local history and spending insights

History is optional and starts disabled. It records only future provider
attempts admitted through this local router for the actor you select. It does
not collect activity from other apps, capture denied requests, import previous
conversations, or record process-memory answer-cache hits.

Use the same configuration path used to connect your coding client:

```sh
koto --config koto.json history status
koto --config koto.json history enable
koto --config koto.json history list
koto --config koto.json history show ENTRY_ID
koto --config koto.json history insights
koto --config koto.json history export --id ENTRY_ID --output ./history-export.json
koto --config koto.json history delete ENTRY_ID
koto --config koto.json history disable
koto --config koto.json history delete --all
```

When multiple actor IDs are configured, enable with `--actor ACTOR_ID`. The
default retention is 30 days; `enable --retention-days N` accepts 1–365 days.
Management works without a running router or model credentials. Only enabling
reads the configuration to select an actor. Use the original configuration path
to manage retained history even if that configuration file was removed.

`list` prints metadata, with at most 50 entries by default (`--limit`, maximum
1000). `show` explicitly decrypts one entry's retained text. `export` writes all
retained entries by default; repeat `--id` to select individual entries. It
requires an explicit new output path, uses owner-only permissions and refuses
overwrite. The export is decrypted and can contain confidential content. Choose
a destination on your device that is not synced or shared. Nothing is uploaded.
The plugin uses metadata for reports and provides local-terminal instructions
for content review. Reading retained text into another agent's tool results or
chat can send it to that agent's model; keep that separate from local review.

Disabling stops future entries and invalidates captures already in progress.
Existing entries remain reviewable. Deleting one or all entries also invalidates
in-progress captures, which may omit concurrent requests. Deletion affects this
history store; usage/accounting evidence, exported copies, backups and filesystem
snapshots remain separate. Re-enabling does not import activity while disabled.

## What is stored

The private store encrypts records and consent settings with AES-256-GCM. A
random device key is stored alongside them with owner-only permissions. This
protects against incidental plaintext disclosure; a process running as your
user and possessing that key can decrypt history. This is not an operating
system keychain or a backup-encryption service.

Records contain text messages and instructions, model/destination identifiers,
observed provider outcome, HTTP status, token usage when known, latency and
configured-price estimates when supported. They exclude request headers,
credentials, opaque provider state, tool arguments/results and non-text
attachments. Known secret patterns and configured credentials are filtered in
both directions, even if upstream secret filtering is off. Filtering cannot
identify every personal detail, confidential document or secret; review before
sharing an export.

Request text and response text are each limited to 64 KiB, and raw response
assembly to 256 KiB. Content that exceeds a limit is omitted and labeled rather
than retaining a potentially unsafe prefix. Malformed or unsupported responses
can retain metadata without text. Interrupted responses are marked as unknown
or partial; a successful response does not establish that the user's task
succeeded. Text from supported JSON and event-stream response shapes is
assembled locally without changing bytes sent to the coding client.

At most 1000 entries and 32 MiB of encrypted entries are retained, evicting the
oldest entries as needed. Retention is applied on capture and management
operations, including review and export. An idle or stopped router does not
promise deletion at the exact expiration instant.

The store defaults to `~/Library/Application Support/Koto/history-v1` on
macOS and `$XDG_DATA_HOME/koto/history-v1` (or
`~/.local/share/koto/history-v1`) elsewhere on supported POSIX platforms.
Existing history profiles are read from their original location until migrated.
Each canonical configuration path has a separate hashed directory. `status`
shows its exact location. `KOTO_HISTORY_HOME` can select another local base
directory, and must be identical for the router and management commands. Keep
the base on a private local filesystem outside synchronized folders. Unsafe
permissions or symlinks cause collection to fail closed with content-free
warnings while routing continues.

## Reading spending insights

Insights describe retained opted-in provider attempts only. They summarize by
model and UTC day, with counts of requests with known configured-price estimates
and unknown costs. Deleting or expiring history changes these summaries. The
existing usage/accounting ledger is independent.

Missing prices or usage are unknown, never zero. Known estimates require
explicit input/output prices, explicit cache prices for observed cached usage,
and a supported pricing mode and provider-reported model. Additional provider
tool charges are unknown. Local runtime hardware, electricity and operating
cost are unknown. The report does not reconcile provider invoices or claim
provider-confirmed charges, causal savings or model-quality improvements.

## Versioning and rollback

History settings, encrypted entries and CLI results use the separate
`hormuz.local-history-*` version 1 contract. Existing configurations remain
disabled until explicit enable; no accounting or old app-history data is
migrated. Unsupported or malformed durable versions fail closed. The
[compatibility fixture](../tests/fixtures/local-history-v1.json) records the
new schema vocabulary and limits. Existing provider-owned relay responses and
usage/work-state schemas retain their versions.

For compatibility tools, the fixture also specifies the encrypted-file
envelope: a versioned UTF-8 header, 12-byte nonce and AES-GCM ciphertext/tag.
Settings use the `settings-v1` authenticated-data label; entries use their
ASCII entry ID. The local key file contains 32 random bytes. These details
allow independent compatibility fixtures; normal review and export use the CLI.

Before rolling back to a version without history commands, disable collection
and export or delete retained data as desired. Older routers ignore the
separate store; rollback does not delete it or grant consent. There is no
training, dataset upload, cloud sync or automatic answer reuse in this feature.
