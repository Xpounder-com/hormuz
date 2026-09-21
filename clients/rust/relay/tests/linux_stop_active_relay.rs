#![cfg(target_os = "linux")]

//! Opt-in host proof for stopping one active synthetic relay service. No
//! Secret Service record, installed AI client, or provider is involved.

use hormuz_client_core::ConnectionProfile;
use hormuz_client_relay::{require_linux_user_service, LocalRelay, Optimization};
use std::fs::{self, File};
use std::io::{ErrorKind, Read, Write};
use std::net::{Ipv4Addr, SocketAddr, TcpListener, TcpStream};
use std::os::unix::fs::{FileTypeExt, MetadataExt};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::Arc;
use std::thread;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};
use zeroize::Zeroizing;

const OPT_IN: &str = "HORMUZ_RELAY_ISOLATED_USER_SERVICE_TEST";
const STAGE: &str = "HORMUZ_RELAY_STOP_TEST_STAGE";
const ROOT: &str = "HORMUZ_RELAY_STOP_TEST_ROOT";
const GATEWAY: &str = "HORMUZ_RELAY_STOP_TEST_GATEWAY";
const TEST_NAME: &str = "stop_closes_active_relay_without_replaying_post";
const BODY: &[u8] = b"{\"messages\":[]}";

fn canonical_systemctl(arguments: &[&str]) -> std::process::Output {
    // The fixture's cleanup is independent of spoofed caller bus variables.
    let uid = unsafe { libc::getuid() };
    let runtime = format!("/run/user/{uid}");
    Command::new("/usr/bin/timeout")
        .env_clear()
        .env("LC_ALL", "C")
        .env("XDG_RUNTIME_DIR", &runtime)
        .env(
            "DBUS_SESSION_BUS_ADDRESS",
            format!("unix:path={runtime}/bus"),
        )
        .args(["5s", "/usr/bin/systemctl", "--user"])
        .args(arguments)
        .output()
        .unwrap()
}

fn require_disposable_user() {
    let name = Command::new("/usr/bin/id").arg("-un").output().unwrap();
    assert!(name.status.success());
    assert_eq!(
        String::from_utf8(name.stdout).unwrap().trim(),
        "hormuzrelaytest"
    );
    let uid = unsafe { libc::getuid() };
    assert_ne!(uid, 0);
    let home = PathBuf::from(std::env::var_os("HOME").unwrap());
    assert_eq!(home, Path::new("/tmp/hormuz-relay-test-home"));
    assert_eq!(fs::metadata(home).unwrap().uid(), uid);
    let runtime = PathBuf::from(format!("/run/user/{uid}"));
    let directory = fs::symlink_metadata(&runtime).unwrap();
    assert!(directory.is_dir() && !directory.file_type().is_symlink());
    assert_eq!(directory.uid(), uid);
    assert_eq!(directory.mode() & 0o7777, 0o700);
    let bus = fs::symlink_metadata(runtime.join("bus")).unwrap();
    assert!(bus.file_type().is_socket());
    assert_eq!(bus.uid(), uid);
    assert!(canonical_systemctl(&["show-environment"]).status.success());
}

fn service_stage(root: &Path, gateway: SocketAddr) {
    require_linux_user_service().unwrap();
    let profile = ConnectionProfile::from_json(
        serde_json::json!({
            "id": "12345678-1234-1234-1234-123456789abc",
            "gateway": format!("http://{gateway}"),
            "organization": "synthetic-org",
            "client": "claude-code",
            "model": "synthetic-model",
            "allowLoopbackHTTP": true,
            "setup": "custom"
        })
        .to_string()
        .as_bytes(),
    )
    .unwrap();
    let credentials = Arc::new(|| Ok(Zeroizing::new(format!("hox_a_{}", "A".repeat(43)))));
    let relay = LocalRelay::start(&profile, credentials, Optimization::Off).unwrap();
    let address = relay.address();
    let membership = fs::read_to_string("/proc/self/cgroup").unwrap();
    let group = membership
        .lines()
        .find_map(|line| line.strip_prefix("0::"))
        .unwrap();
    fs::write(
        root.join("ready.tmp"),
        format!("{} {group}", address.port()),
    )
    .unwrap();
    fs::rename(root.join("ready.tmp"), root.join("ready")).unwrap();
    let mut client = TcpStream::connect(address).unwrap();
    client
        .write_all(
            format!(
                "POST /v1/messages HTTP/1.1\r\nHost: 127.0.0.1:{}\r\nAuthorization: Bearer {}\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n",
                address.port(),
                relay.local_credential(),
                BODY.len()
            )
            .as_bytes(),
        )
        .unwrap();
    client.write_all(BODY).unwrap();
    // The parent stops the owning service after its fake gateway has accepted
    // this POST. This bound catches a fixture that fails to stop the service.
    client
        .set_read_timeout(Some(Duration::from_secs(30)))
        .unwrap();
    let mut one = [0];
    let _ = client.read(&mut one);
}

struct UnitGuard {
    unit: String,
    wrapper: Child,
}
impl Drop for UnitGuard {
    fn drop(&mut self) {
        let _ = canonical_systemctl(&["stop", &self.unit]);
        let _ = self.wrapper.kill();
        let _ = self.wrapper.wait();
    }
}

fn wait_ready(path: &Path, guard: &mut UnitGuard, log: &Path) -> (SocketAddr, String) {
    let deadline = Instant::now() + Duration::from_secs(10);
    loop {
        if let Ok(value) = fs::read_to_string(path) {
            let mut parts = value.split_whitespace();
            let port: u16 = parts.next().unwrap().parse().unwrap();
            let group = parts.next().unwrap().to_owned();
            assert!(parts.next().is_none());
            return (SocketAddr::from((Ipv4Addr::LOCALHOST, port)), group);
        }
        if let Some(status) = guard.wrapper.try_wait().unwrap() {
            let mut bounded_log = Vec::new();
            if let Ok(file) = File::open(log) {
                let _ = file.take(4096).read_to_end(&mut bounded_log);
            }
            panic!(
                "synthetic service exited {status}; log={}",
                String::from_utf8_lossy(&bounded_log)
            );
        }
        assert!(Instant::now() < deadline, "synthetic service did not start");
        thread::sleep(Duration::from_millis(10));
    }
}

fn read_exact_deadline(stream: &mut TcpStream, bytes: &mut [u8], deadline: Instant) {
    let mut offset = 0;
    while offset < bytes.len() {
        let remaining = deadline.saturating_duration_since(Instant::now());
        assert!(!remaining.is_zero(), "fake gateway request timed out");
        stream.set_read_timeout(Some(remaining)).unwrap();
        let count = stream.read(&mut bytes[offset..]).unwrap();
        assert_ne!(count, 0, "relay closed upstream before request completion");
        offset += count;
    }
}

#[test]
#[ignore = "requires disposable systemd user manager"]
fn stop_closes_active_relay_without_replaying_post() {
    if std::env::var(STAGE).as_deref() == Ok("service") {
        let root = PathBuf::from(std::env::var(ROOT).unwrap());
        let gateway = std::env::var(GATEWAY).unwrap().parse().unwrap();
        service_stage(&root, gateway);
        return;
    }
    assert_eq!(
        std::env::var(OPT_IN).as_deref(),
        Ok("1"),
        "explicit host test requires {OPT_IN}=1"
    );
    require_disposable_user();
    let root = tempfile::tempdir().unwrap();
    let gateway = TcpListener::bind((Ipv4Addr::LOCALHOST, 0)).unwrap();
    gateway.set_nonblocking(true).unwrap();
    let gateway_address = gateway.local_addr().unwrap();
    let nonce = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap()
        .as_nanos();
    let token = format!("stop-test-{}-{nonce}", std::process::id());
    let unit = format!("hormuz-relay-{token}.service");
    let wrapper = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("run-in-user-service.sh");
    let log_path = root.path().join("service.log");
    let log = File::create(&log_path).unwrap();
    let fixture = std::env::current_exe().unwrap();
    let child = Command::new(wrapper)
        .arg(&token)
        .arg("/usr/bin/env")
        .arg(format!("{STAGE}=service"))
        .arg(format!("{ROOT}={}", root.path().display()))
        .arg(format!("{GATEWAY}={gateway_address}"))
        .arg(fixture)
        .args(["--ignored", "--exact", TEST_NAME, "--nocapture"])
        .current_dir(root.path())
        .env("PATH", "/usr/bin:/bin")
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::from(log))
        .spawn()
        .unwrap();
    let mut guard = UnitGuard {
        unit,
        wrapper: child,
    };
    let (relay_address, group) = wait_ready(&root.path().join("ready"), &mut guard, &log_path);
    assert!(group.ends_with(&format!("/{}", guard.unit)));
    let deadline = Instant::now() + Duration::from_secs(10);
    let mut upstream = loop {
        match gateway.accept() {
            Ok((stream, _)) => break stream,
            Err(error) if error.kind() == ErrorKind::WouldBlock => {
                assert!(Instant::now() < deadline, "fake gateway saw no POST");
                thread::sleep(Duration::from_millis(10));
            }
            Err(error) => panic!("fake gateway accept failed: {error}"),
        }
    };
    let mut headers = Vec::new();
    while !headers.ends_with(b"\r\n\r\n") {
        let mut byte = [0];
        read_exact_deadline(&mut upstream, &mut byte, deadline);
        headers.push(byte[0]);
        assert!(headers.len() <= 8192);
    }
    let headers = String::from_utf8(headers).unwrap();
    assert!(headers.starts_with("POST /v1/messages HTTP/1.1\r\n"));
    assert!(
        !headers
            .lines()
            .filter_map(|line| line.split_once(':'))
            .any(|(name, _)| name.eq_ignore_ascii_case("x-hormuz-context-format")),
        "Optimization::Off declared a structural context format"
    );
    let authorization: Vec<_> = headers
        .lines()
        .filter_map(|line| {
            let (name, value) = line.split_once(':')?;
            name.eq_ignore_ascii_case("authorization")
                .then_some(value.trim())
        })
        .collect();
    assert_eq!(authorization.len(), 1);
    assert_eq!(authorization[0], format!("Bearer hox_a_{}", "A".repeat(43)));
    let length: usize = headers
        .lines()
        .find_map(|line| {
            line.to_ascii_lowercase()
                .strip_prefix("content-length: ")
                .and_then(|value| value.trim().parse().ok())
        })
        .unwrap();
    assert_eq!(length, BODY.len());
    let mut body = vec![0; length];
    read_exact_deadline(&mut upstream, &mut body, deadline);
    assert_eq!(body, BODY);

    let stop = || {
        Command::new(env!("CARGO_BIN_EXE_hormuz-client-relay"))
            .args(["stop", "--unit-token", &token])
            .env("DBUS_SESSION_BUS_ADDRESS", "unix:path=/tmp/spoofed-bus")
            .env("XDG_RUNTIME_DIR", root.path())
            .output()
            .unwrap()
    };
    let stopped = stop();
    assert!(
        stopped.status.success(),
        "stop command failed: {}",
        String::from_utf8_lossy(&stopped.stderr)
    );
    upstream
        .set_read_timeout(Some(Duration::from_secs(2)))
        .unwrap();
    let mut byte = [0];
    assert_eq!(
        upstream.read(&mut byte).unwrap(),
        0,
        "upstream socket remained open"
    );
    let events = Path::new("/sys/fs/cgroup")
        .join(group.trim_start_matches('/'))
        .join("cgroup.events");
    let empty = match fs::read_to_string(events) {
        Ok(value) => value.lines().any(|line| line == "populated 0"),
        Err(error) if error.kind() == ErrorKind::NotFound => true,
        Err(error) => panic!("could not inspect service cgroup: {error}"),
    };
    assert!(empty, "stopped unit retained processes");
    let wrapper_deadline = Instant::now() + Duration::from_secs(3);
    while guard.wrapper.try_wait().unwrap().is_none() {
        assert!(
            Instant::now() < wrapper_deadline,
            "systemd-run wrapper survived stop"
        );
        thread::sleep(Duration::from_millis(10));
    }
    let error = TcpStream::connect_timeout(&relay_address, Duration::from_millis(250))
        .expect_err("stopped relay listener remained open");
    assert_eq!(error.kind(), ErrorKind::ConnectionRefused);
    let stopped_again = stop();
    assert!(
        stopped_again.status.success(),
        "stopping the already-collected unit failed: {}",
        String::from_utf8_lossy(&stopped_again.stderr)
    );
    // Keep the fake gateway listening after the first uncertain POST. A new
    // connection would reveal an automatic replay; one short bounded window
    // is enough because the stopped service and its cgroup are already gone.
    let no_replay_until = Instant::now() + Duration::from_millis(300);
    while Instant::now() < no_replay_until {
        match gateway.accept() {
            Ok(_) => panic!("relay replayed an uncertain POST after stop"),
            Err(error) if error.kind() == ErrorKind::WouldBlock => {
                thread::sleep(Duration::from_millis(10));
            }
            Err(error) => panic!("fake gateway accept failed: {error}"),
        }
    }
}
