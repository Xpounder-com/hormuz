//! Bounded, cancellable JSON transport for #334. No session or retry policy.
//!
//! Callers can await `RequestTask` from any executor. All network work runs on
//! one private process-wide runtime. Dropping or cancelling a task aborts its
//! local work, but cannot undo a request already received by the gateway.

#![forbid(unsafe_code)]

use hormuz_client_core::{ConnectionProfile, MAX_RESPONSE_BYTES};
use reqwest::header::{self, HeaderValue};
use std::collections::VecDeque;
use std::error::Error;
use std::fmt;
use std::future::Future;
use std::net::{Ipv4Addr, Ipv6Addr, SocketAddr, ToSocketAddrs};
use std::pin::Pin;
use std::sync::atomic::{AtomicU8, Ordering};
use std::sync::{Arc, Mutex, OnceLock};
use std::task::{Context, Poll};
use std::time::{Duration, Instant};
use tokio::runtime::{Builder, Runtime};
use tokio::sync::{oneshot, OwnedSemaphorePermit, Semaphore};
use tokio::task::AbortHandle;

pub const MAX_REQUEST_BYTES: usize = 128 * 1024;
pub const MAX_PENDING_REQUESTS: usize = 8;
pub const MAX_ACTIVE_REQUESTS: usize = 2;
pub const TOTAL_TIMEOUT: Duration = Duration::from_secs(15);
const CONNECT_TIMEOUT: Duration = Duration::from_secs(10);
const MAX_POOLED_ORIGINS: usize = 4;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum ErrorKind {
    InvalidRequest,
    RequestTooLarge,
    Busy,
    Cancelled,
    Timeout,
    Offline,
    Tls,
    Redirect,
    ResponseTooLarge,
    InvalidResponse,
    HttpStatus(u16),
    Unavailable,
}

/// `Unconfirmed` is conservative: a request may have reached the gateway.
/// Neither it nor cancellation authorizes automatic replay.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum RequestOutcome {
    NotSent,
    Unconfirmed,
    ResponseReceived,
}

/// Never contains a URL, header, server message, response body or raw OS error.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct TransportError {
    pub kind: ErrorKind,
    pub outcome: RequestOutcome,
}

impl TransportError {
    fn before_send(kind: ErrorKind) -> Self {
        Self {
            kind,
            outcome: RequestOutcome::NotSent,
        }
    }

    fn unconfirmed(kind: ErrorKind) -> Self {
        Self {
            kind,
            outcome: RequestOutcome::Unconfirmed,
        }
    }
}

impl fmt::Display for TransportError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(match self.kind {
            ErrorKind::InvalidRequest => "The gateway request is invalid.",
            ErrorKind::RequestTooLarge => "The request exceeds the client safety limit.",
            ErrorKind::Busy => "The gateway request queue is full.",
            ErrorKind::Cancelled => "The gateway request was cancelled locally.",
            ErrorKind::Timeout => "The gateway request timed out.",
            ErrorKind::Offline => "The gateway connection is unavailable.",
            ErrorKind::Tls => "The secure gateway connection could not be verified.",
            ErrorKind::Redirect => "The gateway redirected the request; it was not followed.",
            ErrorKind::ResponseTooLarge => "The response exceeds the client safety limit.",
            ErrorKind::InvalidResponse => "The gateway returned an invalid JSON response.",
            ErrorKind::HttpStatus(_) => "The gateway returned an unsuccessful HTTP status.",
            ErrorKind::Unavailable => "The gateway request could not be completed.",
        })
    }
}

impl Error for TransportError {}

pub struct GatewayReply {
    status: u16,
    body: Vec<u8>,
}

impl fmt::Debug for GatewayReply {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("GatewayReply")
            .field("status", &self.status)
            .field("body", &"<redacted>")
            .finish()
    }
}

impl GatewayReply {
    pub fn status(&self) -> u16 {
        self.status
    }
    pub fn body(&self) -> &[u8] {
        &self.body
    }

    /// Preserve non-success statuses for session policy (e.g. enrollment 409).
    /// Call this only when the caller expects an ordinary 2xx response.
    pub fn require_success(&self) -> Result<(), TransportError> {
        if (200..300).contains(&self.status) {
            return Ok(());
        }
        Err(TransportError {
            kind: ErrorKind::HttpStatus(self.status),
            outcome: RequestOutcome::ResponseReceived,
        })
    }

    pub fn decode<T: serde::de::DeserializeOwned>(&self) -> Result<T, TransportError> {
        serde_json::from_slice(&self.body).map_err(|_| TransportError {
            kind: ErrorKind::InvalidResponse,
            outcome: RequestOutcome::ResponseReceived,
        })
    }
}

#[derive(Clone)]
pub struct Cancellation {
    state: Arc<AtomicU8>,
    abort: AbortHandle,
}

impl Cancellation {
    pub fn cancel(&self) {
        // Atomically arbitrate cancellation against dispatch. A queued cancel
        // must never report NotSent while the worker races ahead and sends it.
        let _ = self
            .state
            .fetch_update(Ordering::SeqCst, Ordering::SeqCst, |value| match value {
                QUEUED => Some(CANCELLED_BEFORE),
                STARTED => Some(CANCELLED_AFTER),
                _ => None,
            });
        self.abort.abort();
    }
}

#[must_use = "Dropping a request cancels its local work"]
pub struct RequestTask {
    result: oneshot::Receiver<Result<GatewayReply, TransportError>>,
    cancellation: Cancellation,
    // Share admission with the worker: it is retained until both the worker
    // exits and the consumer takes/drops the result. Unread replies are bounded.
    _admission: Arc<OwnedSemaphorePermit>,
}

impl RequestTask {
    pub fn cancellation(&self) -> Cancellation {
        self.cancellation.clone()
    }
}

impl Future for RequestTask {
    type Output = Result<GatewayReply, TransportError>;

    fn poll(mut self: Pin<&mut Self>, cx: &mut Context<'_>) -> Poll<Self::Output> {
        let state = self.cancellation.state.load(Ordering::SeqCst);
        let cancelled = TransportError {
            kind: ErrorKind::Cancelled,
            outcome: outcome(state),
        };
        if matches!(state, CANCELLED_BEFORE | CANCELLED_AFTER) {
            return Poll::Ready(Err(cancelled));
        }
        match Pin::new(&mut self.result).poll(cx) {
            Poll::Ready(Ok(value)) => Poll::Ready(value),
            Poll::Ready(Err(_)) => Poll::Ready(Err(TransportError {
                kind: ErrorKind::Unavailable,
                outcome: outcome(self.cancellation.state.load(Ordering::SeqCst)),
            })),
            Poll::Pending => Poll::Pending,
        }
    }
}

impl Drop for RequestTask {
    fn drop(&mut self) {
        self.cancellation.cancel();
    }
}

const QUEUED: u8 = 0;
const STARTED: u8 = 1;
const CANCELLED_BEFORE: u8 = 2;
const CANCELLED_AFTER: u8 = 3;

fn outcome(state: u8) -> RequestOutcome {
    if matches!(state, STARTED | CANCELLED_AFTER) {
        RequestOutcome::Unconfirmed
    } else {
        RequestOutcome::NotSent
    }
}

/// Stateless handle. Constructing or cloning it creates no threads or sockets.
#[derive(Clone, Copy, Default)]
pub struct GatewayTransport;

impl GatewayTransport {
    pub fn request(
        &self,
        profile: &ConnectionProfile,
        path: &str,
        body: Option<&[u8]>,
        access_token: Option<&str>,
    ) -> Result<RequestTask, TransportError> {
        self.request_with_timeout(profile, path, body, access_token, TOTAL_TIMEOUT)
    }

    fn request_with_timeout(
        &self,
        profile: &ConnectionProfile,
        path: &str,
        body: Option<&[u8]>,
        access_token: Option<&str>,
        timeout: Duration,
    ) -> Result<RequestTask, TransportError> {
        let request = validate_request(profile, path, body, access_token)?;
        let shared = shared()?;
        let admission = Arc::new(
            shared
                .pending
                .clone()
                .try_acquire_owned()
                .map_err(|_| TransportError::before_send(ErrorKind::Busy))?,
        );
        let worker_admission = admission.clone();
        // Clone bounded body data only after gaining an admission slot.
        let body = body.map(<[u8]>::to_vec);
        let deadline = Instant::now() + timeout;
        let state = Arc::new(AtomicU8::new(QUEUED));
        let worker_state = state.clone();
        let origin = profile.gateway().to_owned();
        let (sender, result) = oneshot::channel();
        let worker = shared.runtime.spawn(async move {
            let _admission = worker_admission;
            let work = async {
                let _active = shared
                    .active
                    .acquire()
                    .await
                    .map_err(|_| TransportError::before_send(ErrorKind::Unavailable))?;
                if Instant::now() >= deadline {
                    return Err(TransportError::before_send(ErrorKind::Timeout));
                }
                let client = shared.client(&origin)?;
                if Instant::now() >= deadline {
                    return Err(TransportError::before_send(ErrorKind::Timeout));
                }
                if worker_state
                    .compare_exchange(QUEUED, STARTED, Ordering::SeqCst, Ordering::SeqCst)
                    .is_err()
                {
                    return Err(TransportError::before_send(ErrorKind::Cancelled));
                }
                exchange(client, request, body).await
            };
            let reply = match tokio::time::timeout_at(deadline.into(), work).await {
                Ok(result) => result,
                Err(_) => Err(TransportError {
                    kind: ErrorKind::Timeout,
                    outcome: outcome(worker_state.load(Ordering::SeqCst)),
                }),
            };
            let _ = sender.send(reply);
        });
        Ok(RequestTask {
            result,
            cancellation: Cancellation {
                state,
                abort: worker.abort_handle(),
            },
            _admission: admission,
        })
    }
}

struct ValidatedRequest {
    url: reqwest::Url,
    authorization: Option<HeaderValue>,
}

fn validate_request(
    profile: &ConnectionProfile,
    path: &str,
    body: Option<&[u8]>,
    token: Option<&str>,
) -> Result<ValidatedRequest, TransportError> {
    let invalid = TransportError::before_send(ErrorKind::InvalidRequest);
    // No URL joining, percent decoding, empty/dot segments or parser rewrites.
    // A path is data under the already-validated profile origin, never a URL.
    if !path.starts_with("/v1/")
        || path.len() > 256
        || path.split('/').skip(1).any(|s| s.is_empty())
        || !path
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || b"/_-".contains(&b))
    {
        return Err(invalid);
    }
    if body.is_some_and(|b| b.len() > MAX_REQUEST_BYTES) {
        return Err(TransportError::before_send(ErrorKind::RequestTooLarge));
    }
    let url = reqwest::Url::parse(&format!("{}{path}", profile.gateway())).map_err(|_| invalid)?;
    let authorization = token
        .map(|token| {
            if !token.starts_with("hox_a_")
                || token.len() != 49
                || !token[6..]
                    .bytes()
                    .all(|b| b.is_ascii_alphanumeric() || b"_-".contains(&b))
            {
                return Err(invalid);
            }
            let mut header =
                HeaderValue::from_str(&format!("Bearer {token}")).map_err(|_| invalid)?;
            header.set_sensitive(true);
            Ok(header)
        })
        .transpose()?;
    Ok(ValidatedRequest { url, authorization })
}

async fn exchange(
    client: reqwest::Client,
    request: ValidatedRequest,
    body: Option<Vec<u8>>,
) -> Result<GatewayReply, TransportError> {
    let mut builder = if body.is_some() {
        client.post(request.url.clone())
    } else {
        client.get(request.url.clone())
    };
    builder = builder
        .header(header::ACCEPT, "application/json")
        .header(header::ACCEPT_ENCODING, "identity")
        .header(header::CACHE_CONTROL, "no-store");
    if let Some(value) = request.authorization {
        builder = builder.header(header::AUTHORIZATION, value);
    }
    if let Some(body) = body {
        builder = builder
            .header(header::CONTENT_TYPE, "application/json")
            .body(body);
    }
    let mut response = builder.send().await.map_err(network_error)?;
    if response.url() != &request.url || response.status().is_redirection() {
        return Err(TransportError::unconfirmed(ErrorKind::Redirect));
    }
    let headers = response.headers();
    let mime = headers
        .get(header::CONTENT_TYPE)
        .and_then(|v| v.to_str().ok())
        .and_then(|v| v.split(';').next())
        .map(str::trim);
    if !mime.is_some_and(|v| v.eq_ignore_ascii_case("application/json"))
        || headers
            .get(header::CONTENT_ENCODING)
            .is_some_and(|v| v != "identity")
    {
        return Err(TransportError::unconfirmed(ErrorKind::InvalidResponse));
    }
    if response
        .content_length()
        .is_some_and(|n| n > MAX_RESPONSE_BYTES as u64)
    {
        return Err(TransportError::unconfirmed(ErrorKind::ResponseTooLarge));
    }
    let status = response.status().as_u16();
    let mut data = Vec::new();
    while let Some(chunk) = response.chunk().await.map_err(network_error)? {
        if chunk.len() > MAX_RESPONSE_BYTES - data.len() {
            return Err(TransportError::unconfirmed(ErrorKind::ResponseTooLarge));
        }
        data.extend_from_slice(&chunk);
    }
    // Validate framing/JSON without retaining a second parsed copy or exposing
    // decoder diagnostics. Domain-schema validation stays in the client core.
    serde_json::from_slice::<serde::de::IgnoredAny>(&data)
        .map_err(|_| TransportError::unconfirmed(ErrorKind::InvalidResponse))?;
    Ok(GatewayReply { status, body: data })
}

fn network_error(error: reqwest::Error) -> TransportError {
    let mut source: Option<&(dyn Error + 'static)> = Some(&error);
    let mut tls = false;
    while let Some(value) = source {
        tls |= value.is::<native_tls::Error>();
        source = value.source();
    }
    let kind = if error.is_timeout() {
        ErrorKind::Timeout
    } else if tls {
        ErrorKind::Tls
    } else if error.is_connect() {
        ErrorKind::Offline
    } else if error.is_decode() || error.is_body() {
        ErrorKind::InvalidResponse
    } else {
        ErrorKind::Unavailable
    };
    TransportError::unconfirmed(kind)
}

struct Shared {
    runtime: Runtime,
    pending: Arc<Semaphore>,
    active: Semaphore,
    resolver: Arc<BoundedResolver>,
    clients: Mutex<VecDeque<(String, reqwest::Client)>>,
}

fn shared() -> Result<&'static Shared, TransportError> {
    static SHARED: OnceLock<Result<Shared, TransportError>> = OnceLock::new();
    SHARED
        .get_or_init(|| {
            let runtime = Builder::new_multi_thread()
                .worker_threads(1)
                .max_blocking_threads(2)
                .thread_name("hormuz-network")
                .enable_all()
                .build()
                .map_err(|_| TransportError::before_send(ErrorKind::Unavailable))?;
            Ok(Shared {
                runtime,
                pending: Arc::new(Semaphore::new(MAX_PENDING_REQUESTS)),
                active: Semaphore::new(MAX_ACTIVE_REQUESTS),
                resolver: Arc::new(BoundedResolver {
                    slots: Arc::new(Semaphore::new(2)),
                }),
                clients: Mutex::new(VecDeque::new()),
            })
        })
        .as_ref()
        .map_err(|error| *error)
}

impl Shared {
    fn client(&self, origin: &str) -> Result<reqwest::Client, TransportError> {
        let mut clients = self
            .clients
            .lock()
            .map_err(|_| TransportError::before_send(ErrorKind::Unavailable))?;
        if let Some(index) = clients.iter().position(|(key, _)| key == origin) {
            let entry = clients.remove(index).expect("known cache entry");
            let client = entry.1.clone();
            clients.push_back(entry);
            return Ok(client);
        }
        let client = client_builder(self.resolver.clone())
            .build()
            .map_err(|_| TransportError::before_send(ErrorKind::Unavailable))?;
        if clients.len() == MAX_POOLED_ORIGINS {
            clients.pop_front();
        }
        clients.push_back((origin.to_owned(), client.clone()));
        Ok(client)
    }
}

fn client_builder(resolver: Arc<BoundedResolver>) -> reqwest::ClientBuilder {
    reqwest::Client::builder()
        .tls_backend_native()
        .tls_version_min(reqwest::tls::Version::TLS_1_2)
        .redirect(reqwest::redirect::Policy::none())
        .retry(reqwest::retry::never())
        .no_proxy()
        .referer(false)
        .http1_only()
        .http1_max_headers(32)
        .no_gzip()
        .no_brotli()
        .no_deflate()
        .no_zstd()
        .connect_timeout(CONNECT_TIMEOUT)
        .read_timeout(CONNECT_TIMEOUT)
        .timeout(TOTAL_TIMEOUT)
        .pool_max_idle_per_host(2)
        .pool_idle_timeout(Duration::from_secs(30))
        .connection_verbose(false)
        .dns_resolver(resolver)
}

/// A cancelled OS resolver cannot be force-stopped. Its permit therefore lives
/// inside the blocking call, preventing repeated timeouts from spawning an
/// unbounded backlog. localhost is pinned to literal loopback addresses.
struct BoundedResolver {
    slots: Arc<Semaphore>,
}

impl reqwest::dns::Resolve for BoundedResolver {
    fn resolve(&self, name: reqwest::dns::Name) -> reqwest::dns::Resolving {
        let slots = self.slots.clone();
        Box::pin(async move {
            if name.as_str().eq_ignore_ascii_case("localhost") {
                let addresses = vec![
                    SocketAddr::from((Ipv6Addr::LOCALHOST, 0)),
                    SocketAddr::from((Ipv4Addr::LOCALHOST, 0)),
                ];
                return Ok(Box::new(addresses.into_iter()) as reqwest::dns::Addrs);
            }
            let permit = slots.acquire_owned().await?;
            let addresses = tokio::task::spawn_blocking(move || {
                let _permit = permit;
                (name.as_str(), 0)
                    .to_socket_addrs()
                    .map(|values| values.take(64).collect::<Vec<_>>())
            })
            .await??;
            Ok(Box::new(addresses.into_iter()) as reqwest::dns::Addrs)
        })
    }
}

#[cfg(test)]
mod tests;
