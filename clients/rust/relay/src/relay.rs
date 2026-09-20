use crate::{launch::valid_local_credential, RelayError};
use base64::{engine::general_purpose::URL_SAFE_NO_PAD, Engine};
use bytes::Bytes;
use futures_util::stream;
use hormuz_client_core::{AIClient, ConnectionProfile};
use http_body_util::{combinators::UnsyncBoxBody, BodyExt, Full, StreamBody};
use hyper::{
    body::Frame, body::Incoming, header, server::conn::http1, service::service_fn, Method, Request,
    Response, StatusCode,
};
use hyper_util::rt::TokioIo;
use std::{
    convert::Infallible,
    io,
    net::{Ipv4Addr, SocketAddr, TcpListener},
    sync::{mpsc, Arc},
    thread::{self, JoinHandle},
    time::Duration,
};
use subtle::ConstantTimeEq;
use tokio::{
    sync::{oneshot, Semaphore},
    task::JoinSet,
};
use zeroize::Zeroizing;

const MAX_REQUEST_BYTES: usize = 25 * 1024 * 1024;
const MAX_OPTIMIZER_BYTES: usize = 1024 * 1024;
const MAX_CONNECTIONS: usize = 16;
const CONNECTION_BUDGET: Duration = Duration::from_secs(900);
type ResponseBody = UnsyncBoxBody<Bytes, io::Error>;

/// Called for each authenticated request. The session controller, rather than
/// a client environment variable or helper command, remains authoritative.
pub trait CredentialSource: Send + Sync + 'static {
    fn access_credential(&self) -> Result<Zeroizing<String>, RelayError>;
}
impl<F> CredentialSource for F
where
    F: Fn() -> Result<Zeroizing<String>, RelayError> + Send + Sync + 'static,
{
    fn access_credential(&self) -> Result<Zeroizing<String>, RelayError> {
        self()
    }
}

/// Optional pre-egress optimization of a request of at most one MiB. A failed
/// transform returns None and forwards the exact original bytes once.
pub trait RequestOptimizer: Send + Sync + 'static {
    fn prepare(&self, path: &str, original: &[u8]) -> Option<Vec<u8>>;
}
pub enum Optimization {
    Off,
    OnDemand(Arc<dyn RequestOptimizer>),
}

struct State {
    gateway: String,
    client: AIClient,
    local_token: Arc<Zeroizing<String>>,
    credentials: Arc<dyn CredentialSource>,
    optimization: Optimization,
}

/// The listener/runtime exist only while a launched client owns this value.
pub struct LocalRelay {
    address: SocketAddr,
    token: Arc<Zeroizing<String>>,
    shutdown: Option<oneshot::Sender<()>>,
    worker: Option<JoinHandle<()>>,
}
impl std::fmt::Debug for LocalRelay {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str("LocalRelay(<redacted>)")
    }
}
impl LocalRelay {
    pub fn start(
        profile: &ConnectionProfile,
        credentials: Arc<dyn CredentialSource>,
        optimization: Optimization,
    ) -> Result<Self, RelayError> {
        let listener = TcpListener::bind((Ipv4Addr::LOCALHOST, 0))
            .map_err(|_| RelayError::RelayUnavailable)?;
        listener
            .set_nonblocking(true)
            .map_err(|_| RelayError::RelayUnavailable)?;
        let address = listener
            .local_addr()
            .map_err(|_| RelayError::RelayUnavailable)?;
        let mut random = Zeroizing::new([0_u8; 32]);
        getrandom::fill(random.as_mut()).map_err(|_| RelayError::RelayUnavailable)?;
        let token = Arc::new(Zeroizing::new(format!(
            "hox_l_{}",
            URL_SAFE_NO_PAD.encode(random.as_ref())
        )));
        debug_assert!(valid_local_credential(&token));
        let state = Arc::new(State {
            gateway: profile.gateway().to_owned(),
            client: profile.client(),
            local_token: token.clone(),
            credentials,
            optimization,
        });
        let (shutdown, stopped) = oneshot::channel();
        let (ready, accepted) = mpsc::sync_channel(1);
        let worker = thread::Builder::new()
            .name("hormuz-local-relay".into())
            .spawn(move || run(listener, state, stopped, ready))
            .map_err(|_| RelayError::RelayUnavailable)?;
        if !matches!(accepted.recv_timeout(Duration::from_secs(5)), Ok(Ok(()))) {
            let _ = shutdown.send(());
            let _ = worker.join();
            return Err(RelayError::RelayUnavailable);
        }
        Ok(Self {
            address,
            token,
            shutdown: Some(shutdown),
            worker: Some(worker),
        })
    }
    pub fn address(&self) -> SocketAddr {
        self.address
    }
    pub fn origin(&self) -> String {
        format!("http://127.0.0.1:{}", self.address.port())
    }
    /// Never include this credential in a log, process argument, or evidence.
    pub fn local_credential(&self) -> &str {
        &self.token
    }
}
impl Drop for LocalRelay {
    fn drop(&mut self) {
        if let Some(sender) = self.shutdown.take() {
            let _ = sender.send(());
        }
        if let Some(worker) = self.worker.take() {
            let _ = worker.join();
        }
    }
}

fn run(
    listener: TcpListener,
    state: Arc<State>,
    mut stopped: oneshot::Receiver<()>,
    ready: mpsc::SyncSender<Result<(), RelayError>>,
) {
    let Ok(runtime) = tokio::runtime::Builder::new_multi_thread()
        .worker_threads(2)
        .max_blocking_threads(2)
        .enable_all()
        .build()
    else {
        let _ = ready.send(Err(RelayError::RelayUnavailable));
        return;
    };
    runtime.block_on(async move {
        let Ok(listener) = tokio::net::TcpListener::from_std(listener) else {
            let _ = ready.send(Err(RelayError::RelayUnavailable));
            return;
        };
        let client = match reqwest::Client::builder()
            .tls_backend_native()
            .tls_version_min(reqwest::tls::Version::TLS_1_2)
            .redirect(reqwest::redirect::Policy::none())
            .retry(reqwest::retry::never())
            .no_proxy()
            .referer(false)
            .http1_only()
            .no_gzip()
            .no_brotli()
            .no_deflate()
            .no_zstd()
            .connect_timeout(Duration::from_secs(5))
            .read_timeout(Duration::from_secs(60))
            .pool_max_idle_per_host(2)
            .build()
        {
            Ok(value) => value,
            Err(_) => {
                let _ = ready.send(Err(RelayError::RelayUnavailable));
                return;
            }
        };
        let admission = Arc::new(Semaphore::new(MAX_CONNECTIONS));
        let _ = ready.send(Ok(()));
        let mut tasks = JoinSet::new();
        loop {
            tokio::select! {
                _ = &mut stopped => break,
                accepted = listener.accept() => {
                    let Ok((socket, peer)) = accepted else { break; };
                    if !peer.ip().is_loopback() { continue; }
                    let Ok(permit) = admission.clone().try_acquire_owned() else { continue; };
                    let state = state.clone();
                    let client = client.clone();
                    tasks.spawn(async move {
                        let local = socket.local_addr().ok();
                        let service = service_fn(move |request| {
                            relay_request(request, state.clone(), client.clone(), local)
                        });
                        let mut server = http1::Builder::new();
                        server.keep_alive(false);
                        let _ = tokio::time::timeout(
                            CONNECTION_BUDGET,
                            server.serve_connection(TokioIo::new(socket), service),
                        ).await;
                        drop(permit);
                    });
                }
            }
        }
        tasks.abort_all();
        while tasks.join_next().await.is_some() {}
    });
    // A cancelled connection can have a blocking credential lookup or Python
    // transform in flight. Give the latter its full 30-second kill/reap budget
    // before the owner reports that its helper has stopped.
    runtime.shutdown_timeout(Duration::from_secs(35));
}

async fn relay_request(
    request: Request<Incoming>,
    state: Arc<State>,
    client: reqwest::Client,
    local: Option<SocketAddr>,
) -> Result<Response<ResponseBody>, Infallible> {
    Ok(exchange(request, state, client, local).await)
}

async fn exchange(
    request: Request<Incoming>,
    state: Arc<State>,
    client: reqwest::Client,
    local: Option<SocketAddr>,
) -> Response<ResponseBody> {
    let Some(local) = local else {
        return error(StatusCode::SERVICE_UNAVAILABLE, "local_unavailable");
    };
    let hosts = request.headers().get_all(header::HOST);
    if hosts.iter().count() != 1
        || !hosts
            .iter()
            .next()
            .and_then(|v| v.to_str().ok())
            .is_some_and(|v| {
                v == format!("127.0.0.1:{}", local.port())
                    || v == format!("localhost:{}", local.port())
            })
    {
        return error(StatusCode::FORBIDDEN, "local_origin_rejected");
    }
    let origins = request.headers().get_all(header::ORIGIN);
    if origins.iter().count() > 1
        || origins.iter().next().is_some_and(|v| {
            !v.to_str().ok().is_some_and(|v| {
                v == format!("http://127.0.0.1:{}", local.port())
                    || v == format!("http://localhost:{}", local.port())
            })
        })
    {
        return error(StatusCode::FORBIDDEN, "local_origin_rejected");
    }
    if request.method() != Method::POST {
        return error(StatusCode::METHOD_NOT_ALLOWED, "local_method_not_allowed");
    }
    let uri = request.uri();
    if uri.scheme().is_some() || uri.authority().is_some() {
        return error(StatusCode::NOT_FOUND, "local_route_not_found");
    }
    let path = uri.path().to_owned();
    let allowed = match state.client {
        AIClient::Codex => matches!(path.as_str(), "/v1/responses" | "/v1/responses/compact"),
        AIClient::ClaudeCode => {
            matches!(path.as_str(), "/v1/messages" | "/v1/messages/count_tokens")
        }
    };
    let path_query = uri
        .path_and_query()
        .map(|value| value.as_str())
        .unwrap_or(&path)
        .to_owned();
    if !allowed || path_query.len() > 1024 {
        return error(StatusCode::NOT_FOUND, "local_route_not_found");
    }
    if !authenticated(request.headers(), &state.local_token) {
        return error(StatusCode::UNAUTHORIZED, "local_authentication_failed");
    }
    if request.headers().contains_key(header::TRANSFER_ENCODING) {
        return error(
            StatusCode::BAD_REQUEST,
            "local_transfer_encoding_unsupported",
        );
    }
    let lengths = request.headers().get_all(header::CONTENT_LENGTH);
    if lengths.iter().count() != 1 {
        return error(StatusCode::LENGTH_REQUIRED, "local_content_length_required");
    }
    let Some(length) = lengths
        .iter()
        .next()
        .and_then(|value| value.to_str().ok())
        .and_then(|value| value.parse::<usize>().ok())
    else {
        return error(StatusCode::BAD_REQUEST, "local_content_length_invalid");
    };
    if length > MAX_REQUEST_BYTES {
        return error(StatusCode::PAYLOAD_TOO_LARGE, "local_request_too_large");
    }
    let original_headers = request.headers().clone();
    let mut body = request.into_body();
    let mut optimized = false;
    let mut upstream_length = length;
    let upstream_body = if length <= MAX_OPTIMIZER_BYTES
        && matches!(&state.optimization, Optimization::OnDemand(_))
    {
        let mut collected = Vec::with_capacity(length);
        while let Some(frame) = body.frame().await {
            let frame = match frame {
                Ok(value) => value,
                Err(_) => return error(StatusCode::BAD_REQUEST, "local_body_invalid"),
            };
            if let Ok(data) = frame.into_data() {
                if data.len() > length.saturating_sub(collected.len()) {
                    return error(StatusCode::BAD_REQUEST, "local_body_invalid");
                }
                collected.extend_from_slice(&data);
            }
        }
        if collected.len() != length {
            return error(StatusCode::BAD_REQUEST, "local_body_invalid");
        }
        if let Optimization::OnDemand(optimizer) = &state.optimization {
            let input = collected.clone();
            let optimizer = optimizer.clone();
            let path = path.to_owned();
            if let Ok(Some(changed)) =
                tokio::task::spawn_blocking(move || optimizer.prepare(&path, &input)).await
            {
                if changed != collected && changed.len() <= MAX_OPTIMIZER_BYTES {
                    collected = changed;
                    optimized = true;
                }
            }
        }
        upstream_length = collected.len();
        reqwest::Body::from(collected)
    } else {
        let stream = stream::try_unfold((body, 0usize), move |(mut body, seen)| async move {
            loop {
                match body.frame().await {
                    Some(Ok(frame)) => {
                        if let Ok(data) = frame.into_data() {
                            let total = seen.checked_add(data.len()).ok_or_else(|| {
                                io::Error::new(io::ErrorKind::InvalidData, "local body invalid")
                            })?;
                            if total > MAX_REQUEST_BYTES || total > length {
                                return Err(io::Error::new(
                                    io::ErrorKind::InvalidData,
                                    "local body invalid",
                                ));
                            }
                            return Ok(Some((data, (body, total))));
                        }
                    }
                    Some(Err(_)) => {
                        return Err(io::Error::new(
                            io::ErrorKind::InvalidData,
                            "local body invalid",
                        ))
                    }
                    None if seen != length => {
                        return Err(io::Error::new(
                            io::ErrorKind::UnexpectedEof,
                            "local body incomplete",
                        ))
                    }
                    None => return Ok(None),
                }
            }
        });
        reqwest::Body::wrap_stream(stream)
    };
    let credentials = state.credentials.clone();
    let credential =
        match tokio::task::spawn_blocking(move || credentials.access_credential()).await {
            Ok(Ok(value)) if valid_access_credential(&value) => value,
            _ => {
                return error(
                    StatusCode::SERVICE_UNAVAILABLE,
                    "gateway_credential_unavailable",
                )
            }
        };
    let url = format!("{}{}", state.gateway, path_query);
    let Ok(url) = reqwest::Url::parse(&url) else {
        return error(StatusCode::BAD_GATEWAY, "gateway_unavailable");
    };
    let mut authorization =
        match reqwest::header::HeaderValue::from_str(&format!("Bearer {}", credential.as_str())) {
            Ok(value) => value,
            Err(_) => {
                return error(
                    StatusCode::SERVICE_UNAVAILABLE,
                    "gateway_credential_unavailable",
                )
            }
        };
    authorization.set_sensitive(true);
    let mut upstream = client
        .post(url.clone())
        .header(header::AUTHORIZATION, authorization)
        .header(header::CACHE_CONTROL, "no-store")
        .header(header::CONTENT_LENGTH, upstream_length.to_string())
        .body(upstream_body);
    for name in [
        header::CONTENT_TYPE,
        header::ACCEPT,
        header::USER_AGENT,
        header::HeaderName::from_static("openai-beta"),
        header::HeaderName::from_static("anthropic-version"),
        header::HeaderName::from_static("anthropic-beta"),
        header::HeaderName::from_static("x-hormuz-work-attribution"),
    ] {
        if let Some(value) = original_headers.get(&name) {
            upstream = upstream.header(name, value.clone());
        }
    }
    if optimized {
        upstream = upstream.header("X-Hormuz-Context-Format", "structural-v1");
    }
    let response = match upstream.send().await {
        Ok(value) if value.url() == &url && !value.status().is_redirection() => value,
        _ => return error(StatusCode::BAD_GATEWAY, "gateway_unavailable"),
    };
    let mut result = Response::builder().status(response.status());
    for (name, value) in response.headers() {
        if !hop_header(name) && name != header::SET_COOKIE && name != header::CONTENT_LENGTH {
            result = result.header(name, value);
        }
    }
    let stream = stream::try_unfold(response, |mut response| async move {
        match response.chunk().await {
            Ok(Some(chunk)) => Ok(Some((Frame::data(chunk), response))),
            Ok(None) => Ok(None),
            Err(_) => Err(io::Error::new(
                io::ErrorKind::BrokenPipe,
                "gateway stream ended",
            )),
        }
    });
    let body = StreamBody::new(stream).boxed_unsync();
    result
        .header(header::CONNECTION, "close")
        .body(body)
        .unwrap_or_else(|_| error(StatusCode::BAD_GATEWAY, "gateway_unavailable"))
}

fn authenticated(headers: &hyper::HeaderMap, expected: &str) -> bool {
    let authorizations = headers.get_all(header::AUTHORIZATION);
    let keys = headers.get_all("x-api-key");
    if authorizations.iter().count() > 1 || keys.iter().count() > 1 {
        return false;
    }
    let authorization = authorizations
        .iter()
        .next()
        .and_then(|value| value.to_str().ok())
        .and_then(|value| {
            value
                .get(0..7)
                .filter(|prefix| prefix.eq_ignore_ascii_case("bearer "))
                .and_then(|_| value.get(7..))
        });
    let key = keys.iter().next().and_then(|value| value.to_str().ok());
    let value = match (authorization, key) {
        (Some(value), None) | (None, Some(value)) => value.trim(),
        _ => return false,
    };
    value.len() == expected.len() && value.as_bytes().ct_eq(expected.as_bytes()).into()
}
fn valid_access_credential(value: &str) -> bool {
    value.len() == 49
        && value.starts_with("hox_a_")
        && value[6..]
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || b"_-".contains(&byte))
}
fn hop_header(name: &header::HeaderName) -> bool {
    matches!(
        name.as_str(),
        "connection"
            | "keep-alive"
            | "proxy-authenticate"
            | "proxy-authorization"
            | "te"
            | "trailer"
            | "transfer-encoding"
            | "upgrade"
    )
}
fn error(status: StatusCode, code: &'static str) -> Response<ResponseBody> {
    let content = format!("{{\"error\":{{\"code\":\"{code}\"}}}}");
    Response::builder()
        .status(status)
        .header(header::CONTENT_TYPE, "application/json")
        .header(header::CONTENT_LENGTH, content.len())
        .header(header::CACHE_CONTROL, "no-store")
        .header(header::CONNECTION, "close")
        .body(
            Full::new(Bytes::from(content))
                .map_err(|never: Infallible| match never {})
                .boxed_unsync(),
        )
        .expect("static error response")
}

#[cfg(test)]
mod tests;
