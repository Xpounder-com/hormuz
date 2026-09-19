# Native gateway transport

`hormuz-client-transport` implements [#334](https://github.com/Xpounder-com/hormuz/issues/334)
for the planned v1.5.0 connected companion. It is an unpublished development
library (`1.5.0-dev.1`), separate from the credential-free models in `core`.
Neither the shipping Swift app nor the Python gateway loads it yet. Their
versions and behavior are unchanged; rollback requires no data migration.

## Calling and cancelling requests

`GatewayTransport` is a stateless handle. Pass a validated `ConnectionProfile`,
a gateway path, optional JSON request bytes and an optional session access token.
No body means GET; a body means POST. The returned `RequestTask` can be awaited
from any executor. An explicit cancellation handle or dropping the task cancels
local work. The caller must consume or drop completed tasks to release capacity.

Every process has one lazily initialized Tokio runtime with one network worker
and at most two blocking resolver workers. HTTPS uses normal native certificate
and hostname verification, with TLS 1.2 as the minimum (Schannel on Windows,
Security Framework on macOS, OpenSSL on Linux). There is no public certificate
override or verification bypass. The client does not start a runtime per view,
request, transport handle or connection profile.

| Resource | Bound |
| --- | --- |
| Admitted work, including unread completed replies | 8 requests |
| Concurrent network requests | 2 |
| Request body and response body | 128 KiB each |
| Total deadline, including waiting for a network slot | 15 seconds |
| Connection and individual read timeout | 10 seconds |
| Cached origin-specific connection pools | 4, with 2 idle connections each |
| Idle connection expiry | 30 seconds |
| HTTP response header count | 32; the pinned HTTP stack also bounds its parsing buffer |
| Uncancellable OS DNS calls | 2, with at most 64 returned addresses per lookup |

The limits bound work and buffered payloads, not total process RSS. Native TLS,
the operating system resolver and the HTTP stack have additional allocations.
OS DNS calls cannot be force-stopped; each holds its resolver slot until it
returns, even if its request was cancelled. TLS initialization and OS trust
evaluation can also run synchronously on the private network worker. The
deadline is cooperative and cannot preempt those OS calls. Measure packaged
footprint and responsiveness separately under #332/#347.

## Security and failure behavior

- The profile owns the origin. Paths must start with `/v1/`, contain only ASCII
  letters, digits, `/`, `_` and `-`, have no empty segments, and fit in 256 bytes.
  Queries, fragments, escapes, dot segments, alternate origins and parser
  rewrites are rejected before network work. This deliberately restricts unused
  path spellings beyond the existing Swift transport. HTTP still requires the
  profile's explicit literal-loopback development opt-in. `localhost` resolves
  only to loopback addresses.
- Session access tokens must have the existing `hox_a_` format. Authorization
  belongs to the individual request and is marked sensitive in the HTTP stack;
  it is never a client-wide default. Cookies, automatic redirects, automatic
  decompression, environment/system proxies and Referer forwarding are disabled.
  Requests use `Accept-Encoding: identity` and `Cache-Control: no-store`.
- Responses must stay at the original URL, use JSON MIME, have no non-identity
  content encoding, fit the byte limit while streaming, and contain one complete
  JSON value. Schema, organization/client identity, enrollment and refresh
  decisions remain in the core and session controller. A transport success is
  not an authenticated session or a valid usage snapshot.
- Non-2xx JSON replies retain their status for the caller, including enrollment
  409. `require_success()` supplies a safe HTTP-status error when appropriate.
  Error kinds and display strings contain no response text, token, URL, native
  error or decoder diagnostic. Reply debug output redacts its body; explicit
  body access is intended for validated decoding, never UI diagnostics.
- Protocol retries are disabled. `NotSent` means the worker did not dispatch;
  cancellation races are arbitrated atomically. `Unconfirmed` conservatively
  means the gateway may have received the request. `ResponseReceived` describes
  a completely read reply. None of these authorize replay. Cancellation cannot
  undo a server action. Session refresh recovery belongs to #335; streaming
  model traffic belongs to #341.

## Verification

From `clients/rust`:

```sh
cargo fmt --all -- --check
cargo test --workspace --locked
cargo clippy --workspace --all-targets --locked -- -D warnings
```

The same tests run on Windows, Linux and macOS in `native-client-contracts.yml`.
The transport tests use ephemeral local HTTP/TLS servers and synthetic values.
They exercise redirect non-forwarding, cookie/auth isolation, connection reuse,
declared and streamed limits, malformed/truncated replies, HTTP statuses,
header/body/queue timeouts, cancellation before and after dispatch, capacity
retained by unread replies, refusal, and ambiguous POST disconnection without
replay. The existing 41 gateway and 10 raw-number vectors also pass through the
actual transport before core validation. TLS tests cover a private in-memory
test CA, an untrusted certificate, hostname mismatch and expiry; they do not
modify any OS trust store. No provider, saved profile or real credential is used.

These checks qualify this development library, not connected native UI, session
custody, clean-machine packaging, an enterprise proxy or a published release.
