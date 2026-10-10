use super::*;
use std::io::{ErrorKind, Read, Write};
use std::net::TcpStream;
use std::sync::atomic::{AtomicUsize, Ordering};
use std::time::Instant;

fn profile(gateway: &str, client: &str) -> ConnectionProfile {
    ConnectionProfile::from_json(
        serde_json::json!({
            "id": "12345678-1234-1234-1234-123456789abc",
            "gateway": gateway,
            "organization": "org-a",
            "client": client,
            "model": "approved",
            "allowLoopbackHTTP": true,
            "setup": "custom"
        })
        .to_string()
        .as_bytes(),
    )
    .unwrap()
}
fn credential(calls: Arc<AtomicUsize>) -> Arc<dyn CredentialSource> {
    Arc::new(move || {
        calls.fetch_add(1, Ordering::SeqCst);
        Ok(Zeroizing::new(format!("hox_a_{}", "A".repeat(43))))
    })
}
fn call(relay: &LocalRelay, path: &str, body: &[u8], extra: &str, token: &str) -> String {
    let mut stream = TcpStream::connect_timeout(&relay.address(), Duration::from_secs(5)).unwrap();
    stream
        .set_read_timeout(Some(Duration::from_secs(5)))
        .unwrap();
    stream
        .set_write_timeout(Some(Duration::from_secs(5)))
        .unwrap();
    let header = format!(
        "POST {path} HTTP/1.1\r\nHost: 127.0.0.1:{}\r\nAuthorization: Bearer {token}\r\nContent-Length: {}\r\nContent-Type: application/json\r\n{extra}\r\n",
        relay.address().port(), body.len()
    );
    let request = [header.as_bytes(), body].concat();
    stream.write_all(&request).unwrap();
    read_response(&mut stream).unwrap()
}

fn read_response(mut input: impl Read) -> std::io::Result<String> {
    // Rejected requests deliberately leave the body unread. macOS may reset
    // that socket after sending a complete response, so fixed-length replies
    // must be checked by HTTP framing, not an additional read waiting for EOF.
    let mut headers = Vec::new();
    while !headers.ends_with(b"\r\n\r\n") {
        let mut byte = [0];
        input.read_exact(&mut byte)?;
        headers.push(byte[0]);
        assert!(headers.len() <= 8192);
    }
    let mut result = String::from_utf8(headers).unwrap();
    let length = result.lines().find_map(|line| {
        line.to_ascii_lowercase()
            .strip_prefix("content-length: ")
            .map(|value| value.parse::<usize>().unwrap())
    });
    if let Some(length) = length {
        assert!(length <= MAX_REQUEST_BYTES);
        let mut body = vec![0; length];
        input.read_exact(&mut body)?;
        result.push_str(std::str::from_utf8(&body).unwrap());
    } else {
        input.read_to_string(&mut result)?;
    }
    Ok(result)
}

#[test]
fn framed_test_replies_require_complete_bodies_not_an_extra_eof_read() {
    // A reset after the declared body is complete is not an HTTP truncation.
    // The same reset before the last declared byte must still fail the test.
    struct ResetAtEnd(std::io::Cursor<Vec<u8>>);
    impl Read for ResetAtEnd {
        fn read(&mut self, output: &mut [u8]) -> std::io::Result<usize> {
            if self.0.position() == self.0.get_ref().len() as u64 {
                Err(ErrorKind::ConnectionReset.into())
            } else {
                self.0.read(output)
            }
        }
    }

    let complete = b"HTTP/1.1 403 Forbidden\r\nContent-Length: 4\r\n\r\ndata";
    assert_eq!(
        read_response(ResetAtEnd(std::io::Cursor::new(complete.to_vec()))).unwrap(),
        std::str::from_utf8(complete).unwrap()
    );
    let truncated = complete[..complete.len() - 1].to_vec();
    assert_eq!(
        read_response(std::io::Cursor::new(truncated.clone()))
            .unwrap_err()
            .kind(),
        ErrorKind::UnexpectedEof
    );
    assert_eq!(
        read_response(ResetAtEnd(std::io::Cursor::new(truncated)))
            .unwrap_err()
            .kind(),
        ErrorKind::ConnectionReset
    );
    assert_eq!(
        read_response(std::io::Cursor::new(b"HTTP/1.1 403\r\n"))
            .unwrap_err()
            .kind(),
        ErrorKind::UnexpectedEof
    );
}

#[test]
fn rejects_bad_local_auth_host_routes_and_size_before_gateway_or_custody() {
    let calls = Arc::new(AtomicUsize::new(0));
    let relay = LocalRelay::start(
        &profile("http://127.0.0.1:9", "codex"),
        credential(calls.clone()),
        Optimization::Off,
    )
    .unwrap();
    let wrong = format!("hox_l_{}", "B".repeat(43));
    assert!(call(&relay, "/v1/responses", b"{}", "", &wrong).starts_with("HTTP/1.1 401"));
    assert!(
        call(&relay, "/v1/messages", b"{}", "", relay.local_credential())
            .starts_with("HTTP/1.1 404")
    );
    assert!(call(
        &relay,
        "/v1/responses",
        b"{}",
        "Origin: https://evil.example\r\n",
        relay.local_credential()
    )
    .starts_with("HTTP/1.1 403"));
    let mut stream = TcpStream::connect(relay.address()).unwrap();
    stream
        .set_read_timeout(Some(Duration::from_secs(5)))
        .unwrap();
    write!(stream, "POST /v1/responses HTTP/1.1\r\nHost: other.invalid\r\nAuthorization: Bearer {}\r\nContent-Length: 0\r\n\r\n", relay.local_credential()).unwrap();
    let text = read_response(&mut stream).unwrap();
    assert!(text.starts_with("HTTP/1.1 403"));
    assert_eq!(calls.load(Ordering::SeqCst), 0);
}

#[test]
fn work_id_header_requires_one_bounded_ascii_job_id() {
    let mut headers = hyper::HeaderMap::new();
    assert!(work_id_header(&headers, None).unwrap().is_none());
    for id in ["w".to_owned(), "Work-123._".to_owned(), "a".repeat(128)] {
        headers.insert(
            "x-hormuz-work-id",
            header::HeaderValue::from_str(&id).unwrap(),
        );
        assert_eq!(
            work_id_header(&headers, None).unwrap().unwrap().as_bytes(),
            id.as_bytes()
        );
    }
    for id in [
        "".to_owned(),
        "a".repeat(129),
        ".work".to_owned(),
        "_work".to_owned(),
        "-work".to_owned(),
        "work/id".to_owned(),
        "work:id".to_owned(),
        "work@id".to_owned(),
        "work id".to_owned(),
        "work,other".to_owned(),
        "work\tid".to_owned(),
    ] {
        headers.insert(
            "x-hormuz-work-id",
            header::HeaderValue::from_str(&id).unwrap(),
        );
        assert!(work_id_header(&headers, None).is_err());
    }
    headers.insert(
        "x-hormuz-work-id",
        header::HeaderValue::from_bytes(b"work\xff").unwrap(),
    );
    assert!(work_id_header(&headers, None).is_err());
    headers.insert(
        "x-hormuz-work-id",
        header::HeaderValue::from_static("work-123"),
    );
    headers.append(
        header::HeaderName::from_bytes(b"X-Hormuz-Work-Id").unwrap(),
        header::HeaderValue::from_static("work-123"),
    );
    assert!(work_id_header(&headers, None).is_err());
}

#[test]
fn bound_work_id_is_injected_or_exactly_matched_never_switched() {
    let expected = header::HeaderValue::from_static("Work-123._");
    let mut headers = hyper::HeaderMap::new();
    assert_eq!(
        work_id_header(&headers, Some(&expected)).unwrap(),
        Some(expected.clone())
    );
    headers.insert("x-hormuz-work-id", expected.clone());
    assert_eq!(
        work_id_header(&headers, Some(&expected)).unwrap(),
        Some(expected.clone())
    );
    for value in ["work-123._", "work-other", "", "work/invalid"] {
        headers.insert(
            "x-hormuz-work-id",
            header::HeaderValue::from_str(value).unwrap(),
        );
        assert!(work_id_header(&headers, Some(&expected)).is_err());
    }
    headers.insert("x-hormuz-work-id", expected.clone());
    headers.append("x-hormuz-work-id", expected.clone());
    assert!(work_id_header(&headers, Some(&expected)).is_err());
}

#[test]
fn invalid_expected_work_id_is_refused_before_relay_start() {
    for value in ["", "-work", "work/id", &"a".repeat(129)] {
        let credentials: Arc<dyn CredentialSource> =
            Arc::new(|| panic!("invalid work ID must not access credentials"));
        assert!(matches!(
            LocalRelay::start_with_work_id(
                &profile("http://127.0.0.1:9", "codex"),
                credentials,
                Optimization::Off,
                Some(value),
            ),
            Err(RelayError::InvalidConfiguration)
        ));
    }
}

type CapturedWorkRequests = Vec<(String, Vec<u8>)>;

struct WorkHeaderGateway {
    address: SocketAddr,
    stop: Arc<AtomicBool>,
    worker: Option<JoinHandle<CapturedWorkRequests>>,
}

impl WorkHeaderGateway {
    fn start(expected_requests: usize) -> Self {
        let listener = TcpListener::bind((Ipv4Addr::LOCALHOST, 0)).unwrap();
        listener.set_nonblocking(true).unwrap();
        let address = listener.local_addr().unwrap();
        let stop = Arc::new(AtomicBool::new(false));
        let stopping = stop.clone();
        let worker = thread::spawn(move || {
            let deadline = Instant::now() + Duration::from_secs(30);
            let mut captured = Vec::new();
            for _ in 0..expected_requests {
                let mut socket = loop {
                    if stopping.load(Ordering::SeqCst) {
                        return captured;
                    }
                    assert!(
                        Instant::now() < deadline,
                        "owned gateway admission timed out"
                    );
                    match listener.accept() {
                        Ok((socket, _)) => break socket,
                        Err(error) if error.kind() == ErrorKind::WouldBlock => {
                            thread::sleep(Duration::from_millis(5));
                        }
                        Err(error) => panic!("owned gateway admission failed: {error}"),
                    }
                };
                // Accepted sockets can inherit the listener's nonblocking mode.
                // Use bounded blocking I/O for this synthetic gateway fixture.
                socket.set_nonblocking(false).unwrap();
                socket
                    .set_read_timeout(Some(Duration::from_secs(5)))
                    .unwrap();
                socket
                    .set_write_timeout(Some(Duration::from_secs(5)))
                    .unwrap();
                let mut headers = Vec::new();
                while !headers.ends_with(b"\r\n\r\n") {
                    let mut byte = [0];
                    socket.read_exact(&mut byte).unwrap();
                    headers.push(byte[0]);
                    assert!(headers.len() <= 8192);
                }
                let headers = String::from_utf8(headers).unwrap();
                let length = headers
                    .lines()
                    .find_map(|line| {
                        line.to_ascii_lowercase()
                            .strip_prefix("content-length: ")
                            .and_then(|value| value.trim().parse::<usize>().ok())
                    })
                    .unwrap();
                assert!(length <= 1024);
                let mut body = vec![0; length];
                socket.read_exact(&mut body).unwrap();
                captured.push((headers, body));
                let reply = b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\n{}";
                socket.write_all(reply).unwrap();
            }
            captured
        });
        Self {
            address,
            stop,
            worker: Some(worker),
        }
    }

    fn finish(&mut self) -> CapturedWorkRequests {
        self.worker.take().unwrap().join().unwrap()
    }
}

impl Drop for WorkHeaderGateway {
    fn drop(&mut self) {
        self.stop.store(true, Ordering::SeqCst);
        if let Some(worker) = self.worker.take() {
            // Panic cleanup still stops the owned accept loop and reaps its
            // worker; accepted sockets have independent five-second deadlines.
            let _ = worker.join();
        }
    }
}

#[test]
fn relay_preserves_only_one_valid_work_id_without_forwarding_client_authority() {
    // Four serial requests, one owned gateway, one relay at a time. The
    // fixture stops and joins on both successful completion and panic unwind.
    let mut gateway = WorkHeaderGateway::start(4);
    let calls = Arc::new(AtomicUsize::new(0));
    let body = b"{\"input\":[]}";
    for (client, path) in [("codex", "/v1/responses"), ("claude-code", "/v1/messages")] {
        let relay = LocalRelay::start(
            &profile(&format!("http://{}", gateway.address), client),
            credential(calls.clone()),
            Optimization::Off,
        )
        .unwrap();
        for extra in ["X-Hormuz-Work-Id: Work-123._\r\n", ""] {
            let headers = [
                extra,
                concat!(
                    "X-Hormuz-Actor-Id: spoofed\r\n",
                    "X-Hormuz-Organization-Id: spoofed\r\n",
                    "OpenAI-Api-Key: client-provider-secret\r\n",
                    "Anthropic-Api-Key: client-provider-secret\r\n",
                    "X-Hormuz-Work-Attribution: tagged\r\n",
                ),
            ]
            .concat();
            let response = call(&relay, path, body, &headers, relay.local_credential());
            assert!(response.starts_with("HTTP/1.1 200"));
        }
        let address = relay.address();
        drop(relay);
        assert!(TcpStream::connect_timeout(&address, Duration::from_millis(100)).is_err());
    }
    let captured = gateway.finish();
    assert_eq!(captured.len(), 4);
    assert_eq!(calls.load(Ordering::SeqCst), 4);
    for (index, (headers, forwarded)) in captured.into_iter().enumerate() {
        assert_eq!(forwarded, body);
        let ids = headers
            .lines()
            .filter_map(|line| {
                let (name, value) = line.split_once(": ")?;
                name.eq_ignore_ascii_case("x-hormuz-work-id")
                    .then_some(value)
            })
            .collect::<Vec<_>>();
        assert_eq!(
            ids,
            if index % 2 == 0 {
                vec!["Work-123._"]
            } else {
                vec![]
            }
        );
        let headers = headers.to_ascii_lowercase();
        assert!(headers.contains("x-hormuz-work-attribution: tagged\r\n"));
        assert!(headers.contains("authorization: bearer hox_a_"));
        assert!(!headers.contains("hox_l_"));
        assert!(!headers.contains("client-provider-secret"));
        assert!(!headers.contains("x-api-key:"));
        assert!(!headers.contains("x-hormuz-actor-id:"));
        assert!(!headers.contains("x-hormuz-organization-id:"));
    }
    assert!(TcpStream::connect_timeout(&gateway.address, Duration::from_millis(100)).is_err());
}

#[test]
fn bound_relay_keeps_exactly_one_work_id_through_optimization() {
    let mut gateway = WorkHeaderGateway::start(4);
    let calls = Arc::new(AtomicUsize::new(0));
    let optimizations = Arc::new(AtomicUsize::new(0));
    for (client, path) in [("codex", "/v1/responses"), ("claude-code", "/v1/messages")] {
        let relay = LocalRelay::start_with_work_id(
            &profile(&format!("http://{}", gateway.address), client),
            credential(calls.clone()),
            Optimization::OnDemand(Arc::new(Change(optimizations.clone()))),
            Some("work-selected"),
        )
        .unwrap();
        for extra in ["", "X-Hormuz-Work-Id: work-selected\r\n"] {
            assert!(call(
                &relay,
                path,
                b"{\"input\":[1]}",
                extra,
                relay.local_credential()
            )
            .starts_with("HTTP/1.1 200"));
        }
        let address = relay.address();
        drop(relay);
        assert!(TcpStream::connect_timeout(&address, Duration::from_millis(100)).is_err());
    }
    let captured = gateway.finish();
    assert_eq!(captured.len(), 4);
    assert_eq!(calls.load(Ordering::SeqCst), 4);
    assert_eq!(optimizations.load(Ordering::SeqCst), 4);
    for (headers, body) in captured {
        assert_eq!(body, b"{\"input\":[]}");
        let ids: Vec<_> = headers
            .lines()
            .filter_map(|line| {
                let (name, value) = line.split_once(": ")?;
                name.eq_ignore_ascii_case("x-hormuz-work-id")
                    .then_some(value)
            })
            .collect();
        assert_eq!(ids, ["work-selected"]);
        assert!(headers
            .to_ascii_lowercase()
            .contains("x-hormuz-context-format: structural-v1"));
    }
    assert!(TcpStream::connect_timeout(&gateway.address, Duration::from_millis(100)).is_err());
}

#[test]
fn invalid_or_conflicting_work_ids_fail_before_optimizer_custody_or_egress() {
    // One owned listener can accept an unexpected request, making accidental
    // egress observable. Correct rejection stops it with zero accepted calls.
    let mut gateway = WorkHeaderGateway::start(1);
    let calls = Arc::new(AtomicUsize::new(0));
    let optimizations = Arc::new(AtomicUsize::new(0));
    for (client, path) in [("codex", "/v1/responses"), ("claude-code", "/v1/messages")] {
        for expected in [None, Some("work-selected")] {
            let relay = LocalRelay::start_with_work_id(
                &profile(&format!("http://{}", gateway.address), client),
                credential(calls.clone()),
                Optimization::OnDemand(Arc::new(CountOptimizationCalls(optimizations.clone()))),
                expected,
            )
            .unwrap();
            for extra in [
                "X-Hormuz-Work-Id: \r\n",
                "X-Hormuz-Work-Id: work/invalid\r\n",
                "X-Hormuz-Work-Id: work-selected\r\nx-hormuz-work-id: work-selected\r\n",
                "X-Hormuz-Work-Id: work-selected\r\nx-hormuz-work-id: work-other\r\n",
            ] {
                assert!(call(&relay, path, b"{}", extra, relay.local_credential())
                    .starts_with("HTTP/1.1 400"));
            }
            if expected.is_some() {
                assert!(call(
                    &relay,
                    path,
                    b"{}",
                    "X-Hormuz-Work-Id: work-other\r\n",
                    relay.local_credential()
                )
                .starts_with("HTTP/1.1 400"));
            }
            let address = relay.address();
            drop(relay);
            assert!(TcpStream::connect_timeout(&address, Duration::from_millis(100)).is_err());
        }
    }
    assert_eq!(calls.load(Ordering::SeqCst), 0);
    assert_eq!(optimizations.load(Ordering::SeqCst), 0);
    gateway.stop.store(true, Ordering::SeqCst);
    assert!(gateway.finish().is_empty());
    assert!(TcpStream::connect_timeout(&gateway.address, Duration::from_millis(100)).is_err());
}

#[test]
fn bound_optimizer_passthrough_keeps_original_bytes_and_selected_job_once() {
    let mut gateway = WorkHeaderGateway::start(2);
    let calls = Arc::new(AtomicUsize::new(0));
    let optimizations = Arc::new(AtomicUsize::new(0));
    let body = b"{ \"input\": [] }\n";
    for (client, path) in [("codex", "/v1/responses"), ("claude-code", "/v1/messages")] {
        let relay = LocalRelay::start_with_work_id(
            &profile(&format!("http://{}", gateway.address), client),
            credential(calls.clone()),
            Optimization::OnDemand(Arc::new(CountOptimizationCalls(optimizations.clone()))),
            Some("work-selected"),
        )
        .unwrap();
        assert!(call(&relay, path, body, "", relay.local_credential()).starts_with("HTTP/1.1 200"));
        drop(relay);
    }
    let captured = gateway.finish();
    assert_eq!(captured.len(), 2);
    assert_eq!(calls.load(Ordering::SeqCst), 2);
    assert_eq!(optimizations.load(Ordering::SeqCst), 2);
    for (headers, forwarded) in captured {
        assert_eq!(forwarded, body);
        let headers = headers.to_ascii_lowercase();
        assert_eq!(headers.matches("x-hormuz-work-id:").count(), 1);
        assert!(headers.contains("x-hormuz-work-id: work-selected\r\n"));
        assert!(!headers.contains("x-hormuz-context-format:"));
    }
    assert!(TcpStream::connect_timeout(&gateway.address, Duration::from_millis(100)).is_err());
}

#[test]
fn off_uses_governed_auth_and_streams_first_event_without_waiting_for_completion() {
    let gateway = TcpListener::bind((Ipv4Addr::LOCALHOST, 0)).unwrap();
    let address = gateway.local_addr().unwrap();
    let (first_sender, first_receiver) = mpsc::sync_channel(1);
    let (continue_sender, continue_receiver) = mpsc::sync_channel(1);
    let (request_sender, request_receiver) = mpsc::sync_channel(1);
    let upstream = thread::spawn(move || {
        let (mut socket, _) = gateway.accept().unwrap();
        socket
            .set_read_timeout(Some(Duration::from_secs(5)))
            .unwrap();
        let mut headers = Vec::new();
        while !headers.ends_with(b"\r\n\r\n") {
            let mut byte = [0];
            socket.read_exact(&mut byte).unwrap();
            headers.push(byte[0]);
            assert!(headers.len() < 8192);
        }
        let text = String::from_utf8(headers).unwrap();
        let len: usize = text
            .lines()
            .find_map(|line| {
                line.to_ascii_lowercase()
                    .strip_prefix("content-length: ")
                    .and_then(|value| value.trim().parse().ok())
            })
            .unwrap();
        let mut body = vec![0; len];
        socket.read_exact(&mut body).unwrap();
        request_sender.send((text, body)).unwrap();
        let first = b"data: first\n\n";
        let last = b"data: done\n\n";
        write!(socket, "HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nContent-Length: {}\r\nConnection: close\r\n\r\n", first.len() + last.len()).unwrap();
        socket.write_all(first).unwrap();
        first_sender.send(()).unwrap();
        continue_receiver
            .recv_timeout(Duration::from_secs(5))
            .unwrap();
        socket.write_all(last).unwrap();
    });
    let calls = Arc::new(AtomicUsize::new(0));
    let relay = LocalRelay::start(
        &profile(&format!("http://{address}"), "codex"),
        credential(calls.clone()),
        Optimization::Off,
    )
    .unwrap();
    let body = b"{ \"input\" : [] }\n";
    let mut client = TcpStream::connect(relay.address()).unwrap();
    client
        .set_read_timeout(Some(Duration::from_secs(5)))
        .unwrap();
    write!(client, "POST /v1/responses HTTP/1.1\r\nHost: 127.0.0.1:{}\r\nAuthorization: Bearer {}\r\nContent-Length: {}\r\nContent-Type: application/json\r\n\r\n", relay.address().port(), relay.local_credential(), body.len()).unwrap();
    client.write_all(body).unwrap();
    first_receiver.recv_timeout(Duration::from_secs(5)).unwrap();
    let mut response = Vec::new();
    while !response
        .windows(b"data: first".len())
        .any(|part| part == b"data: first")
    {
        let mut buffer = [0; 1024];
        let read = client.read(&mut buffer).unwrap();
        assert_ne!(read, 0);
        response.extend_from_slice(&buffer[..read]);
    }
    continue_sender.send(()).unwrap();
    client.read_to_end(&mut response).unwrap();
    assert!(response
        .windows(b"data: done".len())
        .any(|part| part == b"data: done"));
    assert!(response.starts_with(b"HTTP/1.1 200"));
    let (headers, forwarded) = request_receiver
        .recv_timeout(Duration::from_secs(5))
        .unwrap();
    assert_eq!(forwarded, body);
    assert!(
        headers.contains(&format!(
            "authorization: Bearer {}",
            "hox_a_".to_owned() + &"A".repeat(43)
        )) || headers
            .to_ascii_lowercase()
            .contains("authorization: bearer hox_a_")
    );
    assert!(!headers.contains(relay.local_credential()));
    assert_eq!(calls.load(Ordering::SeqCst), 1);
    upstream.join().unwrap();
}

struct Change(Arc<AtomicUsize>);
impl RequestOptimizer for Change {
    fn prepare(
        &self,
        _path: &str,
        original: &[u8],
        cancellation: &OptimizerCancellation,
    ) -> Option<Vec<u8>> {
        assert!(!cancellation.is_cancelled());
        self.0.fetch_add(1, Ordering::SeqCst);
        assert_eq!(original, b"{\"input\":[1]}");
        Some(b"{\"input\":[]}".to_vec())
    }
}

struct CountOptimizationCalls(Arc<AtomicUsize>);
impl RequestOptimizer for CountOptimizationCalls {
    fn prepare(
        &self,
        _path: &str,
        _original: &[u8],
        cancellation: &OptimizerCancellation,
    ) -> Option<Vec<u8>> {
        assert!(!cancellation.is_cancelled());
        self.0.fetch_add(1, Ordering::SeqCst);
        None
    }
}

#[test]
fn claude_token_count_requests_bypass_the_optimizer() {
    let calls = Arc::new(AtomicUsize::new(0));
    let relay = LocalRelay::start(
        &profile("http://127.0.0.1:9", "claude-code"),
        Arc::new(|| Err(RelayError::CredentialUnavailable)),
        Optimization::OnDemand(Arc::new(CountOptimizationCalls(calls.clone()))),
    )
    .unwrap();
    assert!(call(
        &relay,
        "/v1/messages/count_tokens",
        b"{}",
        "",
        relay.local_credential()
    )
    .starts_with("HTTP/1.1 503"));
    assert_eq!(calls.load(Ordering::SeqCst), 0);
}

#[test]
fn enabled_transform_runs_once_before_egress_and_marks_only_changed_bytes() {
    let gateway = TcpListener::bind((Ipv4Addr::LOCALHOST, 0)).unwrap();
    let address = gateway.local_addr().unwrap();
    let upstream = thread::spawn(move || {
        let (mut socket, _) = gateway.accept().unwrap();
        socket
            .set_read_timeout(Some(Duration::from_secs(5)))
            .unwrap();
        let mut headers = Vec::new();
        while !headers.ends_with(b"\r\n\r\n") {
            let mut byte = [0];
            socket.read_exact(&mut byte).unwrap();
            headers.push(byte[0]);
            assert!(headers.len() < 8192);
        }
        let text = String::from_utf8(headers).unwrap();
        let len: usize = text
            .lines()
            .find_map(|line| {
                line.to_ascii_lowercase()
                    .strip_prefix("content-length: ")
                    .and_then(|value| value.trim().parse().ok())
            })
            .unwrap();
        let mut body = vec![0; len];
        socket.read_exact(&mut body).unwrap();
        socket.write_all(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: 2\r\nConnection: close\r\n\r\n{}").unwrap();
        (text, body)
    });
    let count = Arc::new(AtomicUsize::new(0));
    let relay = LocalRelay::start(
        &profile(&format!("http://{address}"), "codex"),
        credential(Arc::new(AtomicUsize::new(0))),
        Optimization::OnDemand(Arc::new(Change(count.clone()))),
    )
    .unwrap();
    let response = call(
        &relay,
        "/v1/responses",
        b"{\"input\":[1]}",
        "",
        relay.local_credential(),
    );
    assert!(response.starts_with("HTTP/1.1 200"));
    assert!(
        response.ends_with("2\r\n{}\r\n0\r\n\r\n") || response.ends_with("\r\n\r\n{}"),
        "normal optimized response did not contain the complete gateway body: {response:?}"
    );
    let (headers, body) = upstream.join().unwrap();
    assert_eq!(body, b"{\"input\":[]}");
    assert!(headers
        .to_ascii_lowercase()
        .contains("x-hormuz-context-format: structural-v1"));
    assert_eq!(count.load(Ordering::SeqCst), 1);
}

struct BlockingOptimizer {
    started: Arc<AtomicUsize>,
    finished: Arc<AtomicUsize>,
}
impl RequestOptimizer for BlockingOptimizer {
    fn prepare(
        &self,
        _path: &str,
        _original: &[u8],
        cancellation: &OptimizerCancellation,
    ) -> Option<Vec<u8>> {
        self.started.fetch_add(1, Ordering::SeqCst);
        while !cancellation.is_cancelled() {
            thread::sleep(Duration::from_millis(1));
        }
        self.finished.fetch_add(1, Ordering::SeqCst);
        None
    }
}

struct BlockingCredentials {
    started: Arc<AtomicUsize>,
    finished: Arc<AtomicUsize>,
}
impl CredentialSource for BlockingCredentials {
    fn access_credential(&self) -> Result<Zeroizing<String>, RelayError> {
        panic!("request lookup must receive relay-owner cancellation");
    }

    fn access_credential_until(
        &self,
        cancellation: &RelayCancellation,
    ) -> Result<Zeroizing<String>, RelayError> {
        self.started.fetch_add(1, Ordering::SeqCst);
        while !cancellation.is_cancelled() {
            thread::sleep(Duration::from_millis(1));
        }
        self.finished.fetch_add(1, Ordering::SeqCst);
        Err(RelayError::CredentialUnavailable)
    }
}

#[test]
fn blocking_registry_rejects_work_after_shutdown() {
    let jobs = Arc::new(BlockingJobs::new());
    let calls = Arc::new(AtomicUsize::new(0));
    jobs.cancel();
    let optimizer = CountOptimizationCalls(calls.clone());
    assert!(jobs
        .spawn(move |cancellation| optimizer.prepare("/v1/responses", b"{}", cancellation))
        .is_none());
    assert_eq!(calls.load(Ordering::SeqCst), 0);
    assert_eq!(jobs.active_count(), 0);
}

#[test]
fn shutdown_cancels_running_and_prevents_queued_optimizer_prepare_without_egress() {
    let started = Arc::new(AtomicUsize::new(0));
    let finished = Arc::new(AtomicUsize::new(0));
    assert_shutdown_cancels_blocking_jobs(
        credential(Arc::new(AtomicUsize::new(0))),
        Optimization::OnDemand(Arc::new(BlockingOptimizer {
            started: started.clone(),
            finished: finished.clone(),
        })),
        started,
        finished,
    );
}

#[test]
fn shutdown_cancels_running_and_prevents_queued_credentials_without_egress() {
    let started = Arc::new(AtomicUsize::new(0));
    let finished = Arc::new(AtomicUsize::new(0));
    assert_shutdown_cancels_blocking_jobs(
        Arc::new(BlockingCredentials {
            started: started.clone(),
            finished: finished.clone(),
        }),
        Optimization::Off,
        started,
        finished,
    );
}

fn assert_shutdown_cancels_blocking_jobs(
    credentials: Arc<dyn CredentialSource>,
    optimization: Optimization,
    started: Arc<AtomicUsize>,
    finished: Arc<AtomicUsize>,
) {
    const REQUESTS: usize = 8;
    let gateway = TcpListener::bind((Ipv4Addr::LOCALHOST, 0)).unwrap();
    gateway.set_nonblocking(true).unwrap();
    let address = gateway.local_addr().unwrap();
    let relay = LocalRelay::start(
        &profile(&format!("http://{address}"), "codex"),
        credentials,
        optimization,
    )
    .unwrap();
    let relay_address = relay.address();
    let token = relay.local_credential().to_owned();
    let clients = (0..REQUESTS)
        .map(|_| {
            let token = token.clone();
            thread::spawn(move || {
                let mut stream = TcpStream::connect(relay_address).unwrap();
                stream
                    .set_read_timeout(Some(Duration::from_secs(5)))
                    .unwrap();
                write!(
                    stream,
                    "POST /v1/responses HTTP/1.1\r\nHost: 127.0.0.1:{}\r\nAuthorization: Bearer {token}\r\nContent-Length: 2\r\nContent-Type: application/json\r\n\r\n{{}}",
                    relay_address.port(),
                )
                .unwrap();
                let mut response = Vec::new();
                let _ = stream.read_to_end(&mut response);
            })
        })
        .collect::<Vec<_>>();

    let jobs = relay.blocking_jobs.clone();
    let deadline = Instant::now() + Duration::from_secs(5);
    while jobs.active_count() != REQUESTS {
        assert!(
            Instant::now() < deadline,
            "not all blocking jobs registered before shutdown: {}",
            jobs.active_count()
        );
        thread::sleep(Duration::from_millis(1));
    }
    while started.load(Ordering::SeqCst) != 2 {
        assert!(
            Instant::now() < deadline,
            "the two blocking-pool workers did not start"
        );
        thread::sleep(Duration::from_millis(1));
    }

    let shutdown_started = Instant::now();
    drop(relay);
    assert!(shutdown_started.elapsed() < Duration::from_secs(2));
    for client in clients {
        client.join().unwrap();
    }
    assert_eq!(started.load(Ordering::SeqCst), 2);
    assert_eq!(finished.load(Ordering::SeqCst), 2);
    assert_eq!(jobs.active_count(), 0);
    assert!(matches!(gateway.accept(), Err(error) if error.kind() == ErrorKind::WouldBlock));
}

#[test]
fn stopping_closes_the_connection_and_releases_relay_state() {
    let relay = LocalRelay::start(
        &profile("http://127.0.0.1:9", "claude-code"),
        credential(Arc::new(AtomicUsize::new(0))),
        Optimization::Off,
    )
    .unwrap();
    let mut connection = TcpStream::connect(relay.address()).unwrap();
    let state = relay.state_probe.upgrade().unwrap();
    let deadline = Instant::now() + Duration::from_secs(5);
    while Arc::strong_count(&state) < 3 {
        assert!(
            Instant::now() < deadline,
            "relay did not accept the connection"
        );
        thread::sleep(Duration::from_millis(1));
    }
    drop(state);
    let local_token = Arc::downgrade(&relay.token);
    drop(relay);
    // This connection belongs to the original listener even if its port is reused.
    connection
        .set_read_timeout(Some(Duration::from_secs(5)))
        .unwrap();
    let mut byte = [0_u8; 1];
    match connection.read(&mut byte) {
        Ok(0) => {}
        Err(error)
            if matches!(
                error.kind(),
                ErrorKind::ConnectionReset | ErrorKind::ConnectionAborted
            ) => {}
        result => panic!("relay connection remained open after shutdown: {result:?}"),
    }
    assert!(local_token.upgrade().is_none());
}

#[test]
fn oversized_body_and_unavailable_session_never_open_gateway_traffic() {
    let gateway = TcpListener::bind((Ipv4Addr::LOCALHOST, 0)).unwrap();
    gateway.set_nonblocking(true).unwrap();
    let calls = Arc::new(AtomicUsize::new(0));
    let address = gateway.local_addr().unwrap();
    let relay = LocalRelay::start(
        &profile(&format!("http://{address}"), "codex"),
        Arc::new({
            let calls = calls.clone();
            move || {
                calls.fetch_add(1, Ordering::SeqCst);
                Err(RelayError::CredentialUnavailable)
            }
        }),
        Optimization::Off,
    )
    .unwrap();
    let mut stream = TcpStream::connect(relay.address()).unwrap();
    stream
        .set_read_timeout(Some(Duration::from_secs(5)))
        .unwrap();
    write!(stream,
        "POST /v1/responses HTTP/1.1\r\nHost: 127.0.0.1:{}\r\nAuthorization: Bearer {}\r\nContent-Length: {}\r\n\r\n",
        relay.address().port(), relay.local_credential(), MAX_REQUEST_BYTES + 1,
    ).unwrap();
    let mut result = String::new();
    stream.read_to_string(&mut result).unwrap();
    assert!(result.starts_with("HTTP/1.1 413"));
    assert_eq!(calls.load(Ordering::SeqCst), 0);
    assert!(
        call(&relay, "/v1/responses", b"{}", "", relay.local_credential())
            .starts_with("HTTP/1.1 503")
    );
    assert_eq!(calls.load(Ordering::SeqCst), 1);
    assert!(gateway.accept().is_err());
}

#[test]
fn uncertain_gateway_outcome_is_not_replayed() {
    let gateway = TcpListener::bind((Ipv4Addr::LOCALHOST, 0)).unwrap();
    let address = gateway.local_addr().unwrap();
    let upstream = thread::spawn(move || {
        let (mut socket, _) = gateway.accept().unwrap();
        socket
            .set_read_timeout(Some(Duration::from_secs(5)))
            .unwrap();
        let mut request = [0; 2048];
        let received = socket.read(&mut request).unwrap();
        assert!(request[..received].starts_with(b"POST /v1/responses"));
        drop(socket); // A response might have been committed before the disconnect.
        gateway.set_nonblocking(true).unwrap();
        thread::sleep(Duration::from_millis(250));
        assert!(gateway.accept().is_err(), "the request was replayed");
    });
    let relay = LocalRelay::start(
        &profile(&format!("http://{address}"), "codex"),
        credential(Arc::new(AtomicUsize::new(0))),
        Optimization::Off,
    )
    .unwrap();
    assert!(
        call(&relay, "/v1/responses", b"{}", "", relay.local_credential())
            .starts_with("HTTP/1.1 502")
    );
    upstream.join().unwrap();
}

#[test]
fn cancelling_a_stream_releases_the_upstream_connection() {
    let gateway = TcpListener::bind((Ipv4Addr::LOCALHOST, 0)).unwrap();
    let address = gateway.local_addr().unwrap();
    let (released, wait) = mpsc::sync_channel(1);
    let upstream = thread::spawn(move || {
        let (mut socket, _) = gateway.accept().unwrap();
        socket
            .set_read_timeout(Some(Duration::from_millis(200)))
            .unwrap();
        let mut request = Vec::new();
        while !request.ends_with(b"\r\n\r\n") {
            let mut byte = [0];
            socket.read_exact(&mut byte).unwrap();
            request.push(byte[0]);
            assert!(request.len() < 8192);
        }
        let mut body = [0; 2];
        socket.read_exact(&mut body).unwrap();
        assert_eq!(&body, b"{}");
        socket.write_all(b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nContent-Length: 65536\r\n\r\ndata: first\n\n").unwrap();
        wait.recv_timeout(Duration::from_secs(5)).unwrap();
        let mut observed_close = false;
        for _ in 0..15 {
            if socket.write_all(b"data: more\n\n").is_err() {
                observed_close = true;
                break;
            }
            let mut byte = [0];
            match socket.read(&mut byte) {
                Ok(0) => {
                    observed_close = true;
                    break;
                }
                Err(error)
                    if matches!(
                        error.kind(),
                        io::ErrorKind::ConnectionReset
                            | io::ErrorKind::ConnectionAborted
                            | io::ErrorKind::BrokenPipe
                            | io::ErrorKind::NotConnected
                    ) =>
                {
                    observed_close = true;
                    break;
                }
                Err(error)
                    if matches!(
                        error.kind(),
                        io::ErrorKind::TimedOut | io::ErrorKind::WouldBlock
                    ) => {}
                result => panic!("unexpected gateway socket state: {result:?}"),
            }
        }
        assert!(
            observed_close,
            "cancelled stream kept the gateway socket open"
        );
    });
    let relay = LocalRelay::start(
        &profile(&format!("http://{address}"), "codex"),
        credential(Arc::new(AtomicUsize::new(0))),
        Optimization::Off,
    )
    .unwrap();
    let mut client = TcpStream::connect(relay.address()).unwrap();
    client
        .set_read_timeout(Some(Duration::from_secs(5)))
        .unwrap();
    write!(client,
        "POST /v1/responses HTTP/1.1\r\nHost: 127.0.0.1:{}\r\nAuthorization: Bearer {}\r\nContent-Length: 2\r\n\r\n{{}}",
        relay.address().port(), relay.local_credential(),
    ).unwrap();
    let mut response = Vec::new();
    while !response
        .windows(b"data: first".len())
        .any(|chunk| chunk == b"data: first")
    {
        let mut buffer = [0; 1024];
        let read = client.read(&mut buffer).unwrap();
        assert_ne!(read, 0);
        response.extend_from_slice(&buffer[..read]);
    }
    drop(client);
    released.send(()).unwrap();
    upstream.join().unwrap();
}
