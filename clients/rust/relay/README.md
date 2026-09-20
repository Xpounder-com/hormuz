# Governed client relay (#341 source checkpoint)

`hormuz-client-relay` is an unpublished `1.6.0-dev.1` executable and library.
It uses the shared native session controller and the existing Mac Keychain or
Windows Credential Manager record. A native shell can launch it with the active
profile UUID and its existing private state directory:

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
reaped. Unix retains direct-child-only cleanup and shell job control. A panel
close must leave the launcher alive; a shell quit/update must coordinate its
termination separately.

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
the direct helper on failure. A process retaining inherited pipe ends cannot
extend the I/O deadline through a reader/writer thread join. Request and
response bytes stay in memory. This is a #339 helper-lifetime and #341 relay
checkpoint; both issues remain open. Windows retains its existing threaded
optimizer exchange; bounded native pipe cancellation remains unverified there.

This source checkpoint is not loaded by the Windows panel or shipping Mac app.
Windows Job Object descendant cleanup is covered by fake clients; Unix still
reaps only the direct child, and descendants can remain after it exits or when
the launcher is terminated. Unix process-tree containment, native-shell panel,
quit/update and installed-client wiring, packaged optimizer interpreter, real
Codex/Claude sessions, Windows accessibility and clean-machine acceptance
remain open. Relay-wide cancellation of queued/running optimizer tasks also
remains open; a bounded individual exchange does not prove those tasks have
stopped when the relay owner's shutdown timeout expires. No release or package
version is changed.

From `clients/rust`, run `cargo test --workspace --locked` and
`cargo clippy --workspace --all-targets --locked -- -D warnings`. The relay
unit tests cover local auth, Off bytes, streaming, optional transforms,
oversized bodies, unavailable credentials and Windows fake-client job lifetime.
The Python bridge is covered by
`python -m unittest -v tests.test_context_relay_bridge`.
Synthetic Unix pipe tests also cover partial and bidirectional I/O, retained
pipe ends after direct-helper exit, a helper that never consumes stdin, output
overflow and direct-child reaping. Linux runs those fixtures without enabling
the unsupported native relay command. These tests do not prove descendant
containment, abrupt launcher-death cleanup, real optimizer/provider sessions,
or packaged native-shell lifecycle behavior.
