use super::*;
use futures_executor::block_on;
use std::io::{BufRead, BufReader, Read, Write};
use std::net::{TcpListener, TcpStream};
use std::sync::atomic::{AtomicBool, AtomicUsize};
use std::sync::MutexGuard;
use std::thread::{self, JoinHandle};

// Synthetic local fixtures only. Never use a saved profile, OS credential,
// externally trusted test root, real hostname or provider in this module.
const TOKEN: &str = "hox_a_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
static SERIAL: Mutex<()> = Mutex::new(());

fn wait_for(mut predicate: impl FnMut() -> bool) {
    let end = Instant::now() + Duration::from_secs(5);
    while !predicate() {
        assert!(Instant::now() < end, "bounded local test wait expired");
        thread::sleep(Duration::from_millis(5));
    }
}

fn serial() -> MutexGuard<'static, ()> {
    let guard = SERIAL.lock().unwrap_or_else(|e| e.into_inner());
    wait_for(|| shared().unwrap().pending.available_permits() == MAX_PENDING_REQUESTS);
    guard
}

fn profile(origin: &str) -> ConnectionProfile {
    ConnectionProfile::from_json(
        &serde_json::to_vec(&serde_json::json!({
            "id": "11111111-1111-4111-8111-111111111111", "gateway": origin,
            "organization": "synthetic", "client": "codex", "model": "test-model",
            "allowLoopbackHTTP": origin.starts_with("http://"), "setup": "custom"
        }))
        .unwrap(),
    )
    .unwrap()
}

#[derive(Default)]
struct Observation {
    method: String,
    path: String,
    authorization: Option<String>,
    cookie: bool,
    body: Vec<u8>,
    identity_encoding: bool,
    no_store: bool,
}

enum Action {
    Json(u16, Vec<u8>, Vec<(&'static str, &'static str)>),
    Raw(Vec<u8>),
    HoldHeaders,
    HoldBody,
    Disconnect,
}

fn json(body: &[u8]) -> Action {
    Action::Json(200, body.to_vec(), Vec::new())
}

struct Gateway {
    origin: String,
    requests: Arc<AtomicUsize>,
    connections: Arc<AtomicUsize>,
    stop: Arc<AtomicBool>,
    worker: Option<JoinHandle<()>>,
}

impl Gateway {
    fn new(handler: impl Fn(Observation, usize) -> Action + Send + Sync + 'static) -> Self {
        let listener = TcpListener::bind((Ipv4Addr::LOCALHOST, 0)).unwrap();
        listener.set_nonblocking(true).unwrap();
        let origin = format!("http://{}", listener.local_addr().unwrap());
        let requests = Arc::new(AtomicUsize::new(0));
        let connections = Arc::new(AtomicUsize::new(0));
        let stop = Arc::new(AtomicBool::new(false));
        let (count, connected, stopped) = (requests.clone(), connections.clone(), stop.clone());
        let handler = Arc::new(handler);
        let worker = thread::spawn(move || {
            let mut children = Vec::new();
            while !stopped.load(Ordering::SeqCst) {
                match listener.accept() {
                    Ok((stream, _)) => {
                        connected.fetch_add(1, Ordering::SeqCst);
                        let (count, stopped, handler) =
                            (count.clone(), stopped.clone(), handler.clone());
                        children.push(thread::spawn(move || {
                            serve(stream, count, stopped, handler)
                        }));
                    }
                    Err(error) if error.kind() == std::io::ErrorKind::WouldBlock => {
                        thread::sleep(Duration::from_millis(2))
                    }
                    Err(_) => break,
                }
            }
            for child in children {
                child.join().unwrap();
            }
        });
        Self {
            origin,
            requests,
            connections,
            stop,
            worker: Some(worker),
        }
    }

    fn profile(&self) -> ConnectionProfile {
        profile(&self.origin)
    }
}

impl Drop for Gateway {
    fn drop(&mut self) {
        self.stop.store(true, Ordering::SeqCst);
        if let Some(worker) = self.worker.take() {
            worker.join().unwrap();
        }
    }
}

fn serve(
    stream: TcpStream,
    count: Arc<AtomicUsize>,
    stop: Arc<AtomicBool>,
    handler: Arc<dyn Fn(Observation, usize) -> Action + Send + Sync>,
) {
    stream.set_nonblocking(false).unwrap();
    stream
        .set_read_timeout(Some(Duration::from_millis(100)))
        .unwrap();
    stream
        .set_write_timeout(Some(Duration::from_secs(2)))
        .unwrap();
    let mut reader = BufReader::new(stream);
    while !stop.load(Ordering::SeqCst) {
        let mut line = String::new();
        match reader.read_line(&mut line) {
            Ok(0) => break,
            Ok(_) => (),
            Err(error)
                if matches!(
                    error.kind(),
                    std::io::ErrorKind::WouldBlock | std::io::ErrorKind::TimedOut
                ) =>
            {
                continue
            }
            Err(_) => break,
        }
        let mut request = Observation::default();
        let parts: Vec<_> = line.split_whitespace().collect();
        if parts.len() != 3 {
            break;
        }
        request.method = parts[0].to_owned();
        request.path = parts[1].to_owned();
        let mut length = 0;
        loop {
            line.clear();
            if reader.read_line(&mut line).is_err() {
                return;
            }
            if line == "\r\n" {
                break;
            }
            let Some((key, value)) = line.split_once(':') else {
                return;
            };
            match key.to_ascii_lowercase().as_str() {
                "content-length" => length = value.trim().parse::<usize>().unwrap(),
                "authorization" => request.authorization = Some(value.trim().to_owned()),
                "cookie" => request.cookie = true,
                "accept-encoding" => request.identity_encoding = value.trim() == "identity",
                "cache-control" => request.no_store = value.trim() == "no-store",
                _ => (),
            }
        }
        assert!(length <= MAX_REQUEST_BYTES);
        request.body.resize(length, 0);
        if reader.read_exact(&mut request.body).is_err() {
            break;
        }
        let index = count.fetch_add(1, Ordering::SeqCst);
        match handler(request, index) {
            Action::Json(status, body, headers) => {
                let mut response = format!("HTTP/1.1 {status} Synthetic\r\nContent-Type: application/json\r\nContent-Length: {}\r\n", body.len());
                for (key, value) in headers {
                    response.push_str(&format!("{key}: {value}\r\n"));
                }
                response.push_str("\r\n");
                if reader.get_mut().write_all(response.as_bytes()).is_err()
                    || reader.get_mut().write_all(&body).is_err()
                {
                    break;
                }
            }
            Action::Raw(bytes) => {
                let _ = reader.get_mut().write_all(&bytes);
                break;
            }
            Action::HoldHeaders => {
                while !stop.load(Ordering::SeqCst) {
                    thread::sleep(Duration::from_millis(5));
                }
                break;
            }
            Action::HoldBody => {
                let _ = reader.get_mut().write_all(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: 2\r\n\r\n{");
                while !stop.load(Ordering::SeqCst) {
                    thread::sleep(Duration::from_millis(5));
                }
                break;
            }
            Action::Disconnect => break,
        }
    }
}

#[test]
fn validated_paths_credentials_and_body_limits_never_send_invalid_input() {
    let _guard = serial();
    let gateway = Gateway::new(|_, _| json(b"{}"));
    let transport = GatewayTransport;
    for path in [
        "https://example.invalid/v1/gateway/whoami",
        "//example.invalid/v1/x",
        "/v1/../x",
        "/v1/%2e%2e/x",
        "/v1/x?credential=synthetic",
        "/v1/x#fragment",
        "/v1/x\\y",
        "/v1//x",
        "/v1/",
        "/v1/x\r\ny",
        "/v1/x%2Fy",
    ] {
        let error = transport
            .request(&gateway.profile(), path, None, None)
            .err()
            .unwrap();
        assert_eq!(
            error,
            TransportError::before_send(ErrorKind::InvalidRequest)
        );
    }
    for token in [
        "synthetic",
        "hox_a_",
        "hox_r_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        "hox_a_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\n",
    ] {
        assert_eq!(
            transport
                .request(&gateway.profile(), "/v1/gateway/whoami", None, Some(token))
                .err()
                .unwrap()
                .kind,
            ErrorKind::InvalidRequest
        );
    }
    assert_eq!(
        transport
            .request(
                &gateway.profile(),
                "/v1/auth/enrollments",
                Some(&vec![0; MAX_REQUEST_BYTES + 1]),
                None
            )
            .err()
            .unwrap()
            .kind,
        ErrorKind::RequestTooLarge
    );
    assert_eq!(gateway.connections.load(Ordering::SeqCst), 0);
}

#[test]
fn reuses_connection_without_retaining_cookies_or_authorization() {
    let _guard = serial();
    let gateway = Gateway::new(|request, index| {
        assert_eq!(request.path, "/v1/gateway/whoami");
        assert_eq!(request.method, if index == 0 { "POST" } else { "GET" });
        assert!(request.identity_encoding && request.no_store && !request.cookie);
        if index == 0 {
            assert_eq!(
                request.authorization.as_deref(),
                Some(format!("Bearer {TOKEN}").as_str())
            );
            assert_eq!(request.body, b"{\"synthetic\":true}");
        } else {
            assert!(request.authorization.is_none() && request.body.is_empty());
        }
        Action::Json(
            200,
            b"{\"private\":\"synthetic-secret\"}".to_vec(),
            vec![("Set-Cookie", "fixture=synthetic; Path=/")],
        )
    });
    let first = block_on(
        GatewayTransport
            .request(
                &gateway.profile(),
                "/v1/gateway/whoami",
                Some(b"{\"synthetic\":true}"),
                Some(TOKEN),
            )
            .unwrap(),
    )
    .unwrap();
    assert!(!format!("{first:?}").contains("synthetic-secret"));
    first.require_success().unwrap();
    let second = block_on(
        GatewayTransport
            .request(&gateway.profile(), "/v1/gateway/whoami", None, None)
            .unwrap(),
    )
    .unwrap();
    assert_eq!(second.status(), 200);
    assert_eq!(gateway.requests.load(Ordering::SeqCst), 2);
    assert_eq!(gateway.connections.load(Ordering::SeqCst), 1);
}

#[test]
fn redirects_never_reach_the_target_or_forward_credentials() {
    let _guard = serial();
    let target = Gateway::new(|_, _| panic!("redirect was followed"));
    let location = target.origin.clone() + "/v1/gateway/whoami";
    for status in [301, 302, 303, 307, 308] {
        let value = location.clone();
        let gateway = Gateway::new(move |_, _| {
            Action::Raw(
                format!(
                    "HTTP/1.1 {status} Redirect\r\nLocation: {value}\r\nContent-Length: 0\r\n\r\n"
                )
                .into_bytes(),
            )
        });
        let error = block_on(
            GatewayTransport
                .request(
                    &gateway.profile(),
                    "/v1/auth/refresh",
                    Some(b"{}"),
                    Some(TOKEN),
                )
                .unwrap(),
        )
        .unwrap_err();
        assert_eq!(error.kind, ErrorKind::Redirect);
        assert_eq!(gateway.requests.load(Ordering::SeqCst), 1);
    }
    assert_eq!(target.connections.load(Ordering::SeqCst), 0);
}

#[test]
fn preserves_http_status_but_never_exposes_server_diagnostics() {
    let _guard = serial();
    for status in [401, 409, 429, 500, 503] {
        let gateway = Gateway::new(move |_, _| {
            Action::Json(
                status,
                b"{\"error\":\"synthetic-secret\"}".to_vec(),
                Vec::new(),
            )
        });
        let reply = block_on(
            GatewayTransport
                .request(
                    &gateway.profile(),
                    "/v1/auth/enrollments",
                    Some(b"{}"),
                    None,
                )
                .unwrap(),
        )
        .unwrap();
        assert_eq!(reply.status(), status);
        let error = reply.require_success().unwrap_err();
        assert_eq!(error.kind, ErrorKind::HttpStatus(status));
        assert_eq!(error.outcome, RequestOutcome::ResponseReceived);
        assert!(!format!("{error:?} {error}").contains("synthetic-secret"));
        assert_eq!(gateway.requests.load(Ordering::SeqCst), 1);
    }
}

#[test]
fn rejects_malformed_mime_encoding_truncation_and_json() {
    let _guard = serial();
    let cases: Vec<Vec<u8>> = vec![
        b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\nContent-Length: 2\r\n\r\n{}".to_vec(),
        b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\n{}".to_vec(),
        b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Encoding: gzip\r\nContent-Length: 2\r\n\r\n{}".to_vec(),
        b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: 20\r\n\r\n{}".to_vec(),
        b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: 3\r\n\r\n{xx".to_vec(),
        b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: 5\r\n\r\n{} {}".to_vec(),
        b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: 1\r\n\r\n\xff".to_vec(),
    ];
    for raw in cases {
        let gateway = Gateway::new(move |_, _| Action::Raw(raw.clone()));
        let error = block_on(
            GatewayTransport
                .request(&gateway.profile(), "/v1/gateway/usage", None, None)
                .unwrap(),
        )
        .unwrap_err();
        assert_eq!(error.kind, ErrorKind::InvalidResponse);
    }
}

#[test]
fn enforces_declared_and_streamed_response_limits_with_exact_boundary() {
    let _guard = serial();
    let gateway = Gateway::new(|_, _| {
        Action::Raw(
            format!(
                "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\n\r\n",
                MAX_RESPONSE_BYTES + 1
            )
            .into_bytes(),
        )
    });
    assert_eq!(
        block_on(
            GatewayTransport
                .request(&gateway.profile(), "/v1/gateway/usage", None, None)
                .unwrap()
        )
        .unwrap_err()
        .kind,
        ErrorKind::ResponseTooLarge
    );
    for length in [MAX_RESPONSE_BYTES, MAX_RESPONSE_BYTES + 1] {
        let gateway = Gateway::new(move |_, _| {
            let mut raw = b"HTTP/1.1 200 OK\r\nContent-Type: APPLICATION/JSON; charset=utf-8\r\nTransfer-Encoding: chunked\r\n\r\n".to_vec();
            raw.extend(format!("{length:x}\r\n\"").as_bytes());
            raw.extend(std::iter::repeat_n(b'a', length - 2));
            raw.extend(b"\"\r\n0\r\n\r\n");
            Action::Raw(raw)
        });
        let result = block_on(
            GatewayTransport
                .request(&gateway.profile(), "/v1/gateway/usage", None, None)
                .unwrap(),
        );
        if length == MAX_RESPONSE_BYTES {
            assert_eq!(result.unwrap().body().len(), length);
        } else {
            assert_eq!(result.unwrap_err().kind, ErrorKind::ResponseTooLarge);
        }
    }
}

#[test]
fn timeout_covers_headers_and_body_and_does_not_retry() {
    let _guard = serial();
    for body in [false, true] {
        let gateway = Gateway::new(move |_, _| {
            if body {
                Action::HoldBody
            } else {
                Action::HoldHeaders
            }
        });
        let start = Instant::now();
        let error = block_on(
            GatewayTransport
                .request_with_timeout(
                    &gateway.profile(),
                    "/v1/auth/refresh",
                    Some(b"{}"),
                    None,
                    Duration::from_millis(150),
                )
                .unwrap(),
        )
        .unwrap_err();
        assert_eq!(error.kind, ErrorKind::Timeout);
        assert_eq!(error.outcome, RequestOutcome::Unconfirmed);
        assert!(start.elapsed() < Duration::from_secs(3));
        assert_eq!(gateway.requests.load(Ordering::SeqCst), 1);
    }
}

#[test]
fn cancellation_and_deadline_while_queued_do_not_dispatch() {
    let _guard = serial();
    let gateway = Gateway::new(|_, _| Action::HoldHeaders);
    let first = GatewayTransport
        .request(&gateway.profile(), "/v1/gateway/usage", None, None)
        .unwrap();
    let second = GatewayTransport
        .request(&gateway.profile(), "/v1/gateway/usage", None, None)
        .unwrap();
    wait_for(|| gateway.requests.load(Ordering::SeqCst) == 2);
    let queued = GatewayTransport
        .request(&gateway.profile(), "/v1/auth/refresh", Some(b"{}"), None)
        .unwrap();
    queued.cancellation().cancel();
    assert_eq!(
        block_on(queued).unwrap_err(),
        TransportError::before_send(ErrorKind::Cancelled)
    );
    let deadline = GatewayTransport
        .request_with_timeout(
            &gateway.profile(),
            "/v1/auth/refresh",
            Some(b"{}"),
            None,
            Duration::from_millis(80),
        )
        .unwrap();
    assert_eq!(
        block_on(deadline).unwrap_err(),
        TransportError::before_send(ErrorKind::Timeout)
    );
    first.cancellation().cancel();
    assert_eq!(
        block_on(first).unwrap_err(),
        TransportError::unconfirmed(ErrorKind::Cancelled)
    );
    drop(second);
    wait_for(|| shared().unwrap().active.available_permits() == MAX_ACTIVE_REQUESTS);
    assert_eq!(gateway.requests.load(Ordering::SeqCst), 2);
}

#[test]
fn queue_and_unconsumed_completed_replies_are_bounded() {
    let _guard = serial();
    for completes in [false, true] {
        let gateway = Gateway::new(move |_, _| {
            if completes {
                json(b"{}")
            } else {
                Action::HoldHeaders
            }
        });
        let mut tasks = Vec::new();
        for _ in 0..MAX_PENDING_REQUESTS {
            tasks.push(
                GatewayTransport
                    .request(&gateway.profile(), "/v1/gateway/usage", None, None)
                    .unwrap(),
            );
        }
        if completes {
            wait_for(|| gateway.requests.load(Ordering::SeqCst) == MAX_PENDING_REQUESTS);
        } else {
            wait_for(|| gateway.requests.load(Ordering::SeqCst) == MAX_ACTIVE_REQUESTS);
        }
        assert_eq!(
            GatewayTransport
                .request(&gateway.profile(), "/v1/gateway/usage", None, None)
                .err()
                .unwrap(),
            TransportError::before_send(ErrorKind::Busy)
        );
        drop(tasks);
        wait_for(|| shared().unwrap().pending.available_permits() == MAX_PENDING_REQUESTS);
        assert_eq!(
            shared().unwrap().active.available_permits(),
            MAX_ACTIVE_REQUESTS
        );
    }
}

#[test]
fn disconnect_after_model_post_is_unconfirmed_and_never_replayed() {
    let _guard = serial();
    let gateway = Gateway::new(|request, _| {
        assert_eq!(request.method, "POST");
        assert_eq!(request.path, "/v1/responses");
        assert_eq!(request.body, b"{\"input\":\"synthetic\"}");
        Action::Disconnect
    });
    let error = block_on(
        GatewayTransport
            .request(
                &gateway.profile(),
                "/v1/responses",
                Some(b"{\"input\":\"synthetic\"}"),
                Some(TOKEN),
            )
            .unwrap(),
    )
    .unwrap_err();
    assert_eq!(error.outcome, RequestOutcome::Unconfirmed);
    assert_eq!(gateway.requests.load(Ordering::SeqCst), 1);
    assert_eq!(gateway.connections.load(Ordering::SeqCst), 1);
}

#[test]
fn cancellation_during_body_download_releases_capacity_without_retry() {
    let _guard = serial();
    let gateway = Gateway::new(|_, _| Action::HoldBody);
    let task = GatewayTransport
        .request(&gateway.profile(), "/v1/gateway/usage", None, None)
        .unwrap();
    wait_for(|| gateway.requests.load(Ordering::SeqCst) == 1);
    let cancellation = task.cancellation();
    // Await on another executor so cancellation must wake an already-pending
    // consumer, not merely be noticed by its first poll.
    let (polled, pending) = std::sync::mpsc::channel();
    let consumer = thread::spawn(move || {
        let mut task = Box::pin(task);
        let mut polled = Some(polled);
        block_on(std::future::poll_fn(|cx| {
            let result = task.as_mut().poll(cx);
            if result.is_pending() {
                if let Some(sender) = polled.take() {
                    sender.send(()).unwrap();
                }
            }
            result
        }))
    });
    pending.recv_timeout(Duration::from_secs(5)).unwrap();
    cancellation.cancel();
    assert_eq!(
        consumer.join().unwrap().unwrap_err(),
        TransportError::unconfirmed(ErrorKind::Cancelled)
    );
    wait_for(|| shared().unwrap().pending.available_permits() == MAX_PENDING_REQUESTS);
    assert_eq!(gateway.requests.load(Ordering::SeqCst), 1);
    assert_eq!(
        shared().unwrap().active.available_permits(),
        MAX_ACTIVE_REQUESTS
    );
}

#[test]
fn shared_gateway_and_raw_number_fixtures_survive_transport_unchanged() {
    use hormuz_client_core::{AIClient, GatewayIdentity, PersonalUsage};
    let _guard = serial();
    let fixture: serde_json::Value = serde_json::from_str(include_str!(
        "../../../../tests/fixtures/native_client/v1/gateway.json"
    ))
    .unwrap();
    let cases = fixture["cases"].as_array().unwrap().clone();
    let replies: Vec<Vec<u8>> = cases
        .iter()
        .map(|case| serde_json::to_vec(&case["response"]).unwrap())
        .collect();
    let served = replies.clone();
    let gateway = Gateway::new(move |_, index| json(&served[index]));
    for (index, case) in cases.iter().enumerate() {
        let path = if case["kind"] == "identity" {
            "/v1/gateway/whoami"
        } else {
            "/v1/gateway/usage"
        };
        let reply = block_on(
            GatewayTransport
                .request(&gateway.profile(), path, None, None)
                .unwrap(),
        )
        .unwrap();
        assert_eq!(reply.body(), replies[index]);
        let valid = if case["kind"] == "identity" {
            GatewayIdentity::from_json(
                reply.body(),
                fixture["organization"].as_str().unwrap(),
                AIClient::Codex,
            )
            .is_ok()
        } else {
            PersonalUsage::from_json(reply.body()).is_ok()
        };
        assert_eq!(
            valid,
            case["client_valid"].as_bool().unwrap(),
            "{}",
            case["id"]
        );
    }
    let fixture: serde_json::Value = serde_json::from_str(include_str!(
        "../../../../tests/fixtures/native_client/v1/raw-numbers.json"
    ))
    .unwrap();
    let cases = fixture["cases"].as_array().unwrap();
    let replies: Vec<_> = cases
        .iter()
        .map(|case| case["response_json"].as_str().unwrap().as_bytes().to_vec())
        .collect();
    let served = replies.clone();
    let gateway = Gateway::new(move |_, index| json(&served[index]));
    for (index, case) in cases.iter().enumerate() {
        let reply = block_on(
            GatewayTransport
                .request(&gateway.profile(), "/v1/gateway/usage", None, None)
                .unwrap(),
        )
        .unwrap();
        assert_eq!(reply.body(), replies[index]);
        let expected = case["expected_requests"]
            .as_str()
            .map(|value| value.parse::<i64>().unwrap());
        assert_eq!(
            PersonalUsage::from_json(reply.body())
                .ok()
                .map(|usage| usage.requests()),
            expected
        );
    }
}

#[test]
fn connection_refusal_and_error_formatting_are_safe() {
    let _guard = serial();
    let listener = TcpListener::bind((Ipv4Addr::LOCALHOST, 0)).unwrap();
    let origin = format!("http://{}", listener.local_addr().unwrap());
    drop(listener);
    let error = block_on(
        GatewayTransport
            .request(&profile(&origin), "/v1/gateway/usage", None, Some(TOKEN))
            .unwrap(),
    )
    .unwrap_err();
    assert_eq!(error.kind, ErrorKind::Offline);
    let text = format!("{error:?} {error}");
    assert!(!text.contains(&origin) && !text.contains(TOKEN));
}

#[test]
fn resolver_pins_localhost_and_limits_queued_resolution_without_external_dns() {
    use reqwest::dns::Resolve;
    let _guard = serial();
    let shared = shared().unwrap();
    let addresses: Vec<_> = block_on(shared.resolver.resolve("localhost".parse().unwrap()))
        .unwrap()
        .collect();
    assert_eq!(addresses.len(), 2);
    assert!(addresses.iter().all(|value| value.ip().is_loopback()));
    let held = shared
        .resolver
        .slots
        .clone()
        .try_acquire_many_owned(2)
        .unwrap();
    let resolver = shared.resolver.clone();
    let result = block_on(shared.runtime.spawn(async move {
        tokio::time::timeout(
            Duration::from_millis(50),
            resolver.resolve("synthetic.invalid".parse().unwrap()),
        )
        .await
    }))
    .unwrap();
    assert!(result.is_err());
    // The lookup never acquired a slot and therefore never reached OS DNS.
    drop(held);
    assert_eq!(shared.resolver.slots.available_permits(), 2);
}

struct TlsGateway {
    port: u16,
    certificate: reqwest::Certificate,
    worker: Option<JoinHandle<()>>,
}

impl TlsGateway {
    fn new(expired: bool) -> Self {
        let mut ca = rcgen::CertificateParams::new(Vec::new()).unwrap();
        ca.is_ca = rcgen::IsCa::Ca(rcgen::BasicConstraints::Unconstrained);
        ca.distinguished_name
            .push(rcgen::DnType::CommonName, "Synthetic Hormuz test root");
        ca.key_usages = vec![
            rcgen::KeyUsagePurpose::KeyCertSign,
            rcgen::KeyUsagePurpose::DigitalSignature,
        ];
        let ca_key = rcgen::KeyPair::generate().unwrap();
        let ca_cert = ca.self_signed(&ca_key).unwrap();
        let certificate = reqwest::Certificate::from_der(ca_cert.der()).unwrap();
        let issuer = rcgen::Issuer::new(ca, ca_key);
        let mut leaf = rcgen::CertificateParams::new(vec!["localhost".into()]).unwrap();
        leaf.distinguished_name
            .push(rcgen::DnType::CommonName, "localhost");
        leaf.extended_key_usages = vec![rcgen::ExtendedKeyUsagePurpose::ServerAuth];
        leaf.key_usages = vec![rcgen::KeyUsagePurpose::DigitalSignature];
        leaf.not_before = time::OffsetDateTime::now_utc() - time::Duration::days(1);
        leaf.not_after = time::OffsetDateTime::now_utc()
            + if expired {
                time::Duration::hours(-1)
            } else {
                time::Duration::days(7)
            };
        let signing_key = rcgen::KeyPair::generate().unwrap();
        let cert = leaf.signed_by(&signing_key, &issuer).unwrap();
        let config = rustls::ServerConfig::builder_with_provider(Arc::new(
            rustls::crypto::ring::default_provider(),
        ))
        .with_safe_default_protocol_versions()
        .unwrap()
        .with_no_client_auth()
        .with_single_cert(
            vec![cert.der().clone()],
            rustls::pki_types::PrivateKeyDer::Pkcs8(signing_key.serialize_der().into()),
        )
        .unwrap();
        let listener = TcpListener::bind((Ipv4Addr::LOCALHOST, 0)).unwrap();
        listener.set_nonblocking(true).unwrap();
        let port = listener.local_addr().unwrap().port();
        let worker = thread::spawn(move || {
            let deadline = Instant::now() + Duration::from_secs(5);
            let stream = loop {
                if let Ok((stream, _)) = listener.accept() {
                    break stream;
                }
                if Instant::now() >= deadline {
                    return;
                }
                thread::sleep(Duration::from_millis(2));
            };
            // macOS inherits the listener's nonblocking flag on accept.
            stream.set_nonblocking(false).unwrap();
            stream
                .set_read_timeout(Some(Duration::from_secs(2)))
                .unwrap();
            stream
                .set_write_timeout(Some(Duration::from_secs(2)))
                .unwrap();
            let connection = rustls::ServerConnection::new(Arc::new(config)).unwrap();
            let mut stream = rustls::StreamOwned::new(connection, stream);
            let mut bytes = Vec::new();
            let mut byte = [0_u8];
            while bytes.len() < 16_384 && !bytes.ends_with(b"\r\n\r\n") {
                if stream.read_exact(&mut byte).is_err() {
                    return;
                }
                bytes.push(byte[0]);
            }
            let _ = stream.write_all(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: 2\r\nConnection: close\r\n\r\n{}");
            let _ = stream.flush();
            stream.conn.send_close_notify();
            let _ = stream.flush();
        });
        Self {
            port,
            certificate,
            worker: Some(worker),
        }
    }
}

impl Drop for TlsGateway {
    fn drop(&mut self) {
        if let Some(worker) = self.worker.take() {
            worker.join().unwrap();
        }
    }
}

#[test]
fn tls_verifies_certificate_trust_and_hostname() {
    let _guard = serial();
    let gateway = TlsGateway::new(false);
    let profile = profile(&format!("https://localhost:{}", gateway.port));
    assert_eq!(
        block_on(
            GatewayTransport
                .request(&profile, "/v1/gateway/usage", None, None)
                .unwrap()
        )
        .unwrap_err()
        .kind,
        ErrorKind::Tls
    );
    for (hostname, expired) in [
        ("localhost", false),
        ("127.0.0.1", false),
        ("localhost", true),
    ] {
        let gateway = TlsGateway::new(expired);
        let profile = super::tests::profile(&format!("https://{hostname}:{}", gateway.port));
        // Test-only trust injection into a single client, never the OS store.
        // The public transport exposes no trust override or verification bypass.
        let shared = shared().unwrap();
        let client = client_builder(shared.resolver.clone())
            .tls_certs_merge([gateway.certificate.clone()])
            .build()
            .unwrap();
        let request = validate_request(&profile, "/v1/gateway/usage", None, None).unwrap();
        let response = block_on(shared.runtime.spawn(exchange(client, request, None))).unwrap();
        if hostname == "localhost" && !expired {
            assert_eq!(response.unwrap().status(), 200);
        } else {
            assert_eq!(response.unwrap_err().kind, ErrorKind::Tls);
        }
    }
}
