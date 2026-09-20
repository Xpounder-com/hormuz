use super::*;
use std::io::{Read, Write};
use std::net::TcpStream;
use std::sync::atomic::{AtomicUsize, Ordering};

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
    let mut stream = TcpStream::connect(relay.address()).unwrap();
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
    stream.write_all(header.as_bytes()).unwrap();
    stream.write_all(body).unwrap();
    let mut result = String::new();
    stream.read_to_string(&mut result).unwrap();
    result
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
    let mut text = String::new();
    stream.read_to_string(&mut text).unwrap();
    assert!(text.starts_with("HTTP/1.1 403"));
    assert_eq!(calls.load(Ordering::SeqCst), 0);
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
    fn prepare(&self, _path: &str, original: &[u8]) -> Option<Vec<u8>> {
        self.0.fetch_add(1, Ordering::SeqCst);
        assert_eq!(original, b"{\"input\":[1]}");
        Some(b"{\"input\":[]}".to_vec())
    }
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
    assert!(call(
        &relay,
        "/v1/responses",
        b"{\"input\":[1]}",
        "",
        relay.local_credential()
    )
    .starts_with("HTTP/1.1 200"));
    let (headers, body) = upstream.join().unwrap();
    assert_eq!(body, b"{\"input\":[]}");
    assert!(headers
        .to_ascii_lowercase()
        .contains("x-hormuz-context-format: structural-v1"));
    assert_eq!(count.load(Ordering::SeqCst), 1);
}

#[test]
fn stopping_owns_the_listener_and_closes_its_port() {
    let relay = LocalRelay::start(
        &profile("http://127.0.0.1:9", "claude-code"),
        credential(Arc::new(AtomicUsize::new(0))),
        Optimization::Off,
    )
    .unwrap();
    let address = relay.address();
    drop(relay);
    assert!(TcpStream::connect_timeout(&address, Duration::from_millis(200)).is_err());
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
