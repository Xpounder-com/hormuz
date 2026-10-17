# Local history and spending insights: first-release plan

Status: owner approved encrypted content plus metadata and the public CLI/router
test boundary on October 15, 2026. Implementation acceptance is verified against
this plan and [ADR 0022](decisions/0022-opt-in-device-local-history.md).

## Outcome

An individual using the customer-local router can choose to retain a bounded
history on that device, inspect individual entries and spending, export a
selected history, and delete an entry or all entries. Existing coding clients
keep their editor, tools and permissions. Fine-tuning, dataset uploads, cloud
sync and a standalone Koto UI are outside this release.

## Proposed behavior

- Collection starts disabled. Enabling applies only to future requests for the
  selected local configuration and actor. Disabling immediately prevents new
  entries, including requests already in progress; existing entries remain
  reviewable until deleted or expired.
- Retain encrypted redacted text content plus usage metadata. Text capture
  excludes headers, credentials, opaque
  provider state, binary attachments and raw tool payloads. Secret filtering is
  mandatory for retained text even when upstream filtering is disabled.
- Data has a separate versioned private local store. It never enters the
  immutable usage/audit database, client recovery journal or provider headers.
  Deleting history leaves accounting and budget evidence intact.
- Default retention is 30 days with finite entry/content/storage limits.
  Expiration runs during capture and management commands; there is no resident
  network worker. The CLI reports the capture mode, retention and storage path.
- Review lists bounded metadata by default. An explicit entry review reveals
  its retained text. Missing, truncated or unsupported content is labeled.
- Export is an explicit local-file action, refuses accidental overwrite and
  warns in the command description that the selected export contains decrypted
  content. It performs no upload. Delete-all invalidates pending captures.
- Spending reports cover retained intercepted requests only. Configured-price
  estimates, provider-confirmed amounts and unknown costs remain distinct.
  Missing rates, incomplete usage and local infrastructure cost are not zeros.
  No savings, task success or model-accuracy claims are inferred from traffic.

## Public workflow validation

Owner-confirmed seam: use the actual CLI and router with a
disposable coding client and bounded synthetic loopback provider. Observe opt
in, submitted requests, history review, spending, export, deletion, opt out,
retention and restart behavior through public commands. Also cover malformed
or unsafe local storage, unsupported contract versions, secret filtering,
streaming/interruption and a request that finishes after disable/delete-all.
Do not call external model providers or query private accounting tables.

## Compatibility and rollback

Publish a new v1 history/settings/export contract and a compatibility fixture.
Existing configurations collect nothing without an explicit enable action.
The history store has no migration from audit evidence or old app histories.
Unsupported versions fail closed. Disabling collection and reverting the
feature leaves the existing router and usage schemas unchanged. Existing
history can be exported or deleted before rollback; rollback never silently
converts ciphertext into plaintext or changes capture consent.
