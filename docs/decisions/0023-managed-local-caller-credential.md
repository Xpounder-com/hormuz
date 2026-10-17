# ADR 0023: Own the local caller credential in shared setup

- Status: Implemented local credential setup slice
- Date: 2026-10-15
- Decision owner: Product owner
- Approval: Automatic local credential setup, TDD at the approved workflow seam,
  existing verified engine/model files, no manual token exports.
- Parent specification: [#523](https://github.com/Xpounder-com/hormuz/issues/523)

Default shared setup creates or reuses one private local caller credential.
Generated router configuration references its private versioned file; the
selected Codex provider uses documented command-backed authentication through
the installed Koto Python interpreter. The helper is bound to a connected
generation and credential digest. It returns no credential after disconnect,
configuration change or generation replacement.

Keep the existing connected-client owner and publication recovery. Preview
uses temporary credential material; apply publishes exclusively after preview.
Credential errors block rather than remove authentication or silently rotate
an existing installation. Disconnect restores exact owned provider/auth bytes
and retains the private credential for reconnection.

Environment-backed setup remains an explicit `--credential-env` operation.
Private journal version 3 and public setup receipt version 2 have fixtures and
documented downgrade rules. Existing journal and environment-backed receipt
versions remain supported. Engine/model discovery, acquisition, native login
access, cloud subscription authentication and additional client qualification
remain outside this slice. See [credential ownership and recovery](../LOCAL_CREDENTIAL_SETUP.md).
