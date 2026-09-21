#![cfg(target_os = "linux")]

//! Host-only proof for the production Linux command and credential adapter.
//! It must run as the disposable `hormuzrelaytest` account, whose systemd user
//! manager and Secret Service collection contain synthetic data only.

use hormuz_client_core::ConnectionProfile;
use hormuz_client_platform::{
    CredentialStore, NativeCredentialStore, PrivateDirectory, SecretRecord,
};
use std::fs::{self, File};
use std::io::{Read, Write};
use std::net::{Ipv4Addr, SocketAddr, TcpListener, TcpStream};
use std::os::unix::fs::{FileTypeExt, MetadataExt, PermissionsExt};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::thread;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

const OPT_IN: &str = "HORMUZ_RELAY_ISOLATED_SECRET_SERVICE_TEST";
const FIXTURE_USER: &str = "hormuzrelaytest";
const PROFILE_KEY: &str = "12345678-1234-1234-1234-123456789abc";
const BODY: &[u8] = b"{\"messages\":[]}";
const FOUNDATION_EPOCH: f64 = 978_307_200.0;
// Two service checks, several 10-second Secret Service calls, and the client
// version probe may all complete within their individual production budgets.
const HOST_STARTUP_BUDGET: Duration = Duration::from_secs(120);

fn user_systemctl(arguments: &[&str]) -> std::process::Output {
    let uid = unsafe { libc::getuid() };
    let runtime = format!("/run/user/{uid}");
    Command::new("/usr/bin/systemctl")
        .env_clear()
        .env("LC_ALL", "C")
        .env("XDG_RUNTIME_DIR", &runtime)
        .env(
            "DBUS_SESSION_BUS_ADDRESS",
            format!("unix:path={runtime}/bus"),
        )
        .arg("--user")
        .args(arguments)
        .output()
        .unwrap()
}

fn require_isolated_user() {
    let name = Command::new("/usr/bin/id").arg("-un").output().unwrap();
    assert!(name.status.success());
    assert_eq!(String::from_utf8(name.stdout).unwrap().trim(), FIXTURE_USER);
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
    assert!(user_systemctl(&["show-environment"]).status.success());
}

fn fake_client(path: &Path) {
    fs::write(
        path,
        r##"#!/usr/bin/python3
import http.client
import os
import sys
from urllib.parse import urlsplit

if "--version" in sys.argv:
    print("claude 2.1.233")
    raise SystemExit(0)

origin = urlsplit(os.environ["ANTHROPIC_BASE_URL"])
if origin.hostname != "127.0.0.1" or not origin.port:
    raise SystemExit("relay did not bind loopback")
sentinels = {
    "OPENAI_API_KEY": "synthetic-openai-must-not-reach-client",
    "ANTHROPIC_API_KEY": "synthetic-anthropic-must-not-reach-client",
    "CLAUDE_CODE_OAUTH_TOKEN": "synthetic-oauth-must-not-reach-client",
}
for name, sentinel in sentinels.items():
    if os.environ.get(name) == sentinel:
        raise SystemExit("provider credential reached fake client: " + name)
if "OPENAI_API_KEY" in os.environ or "CLAUDE_CODE_OAUTH_TOKEN" in os.environ:
    raise SystemExit("unrelated provider credential name reached fake client")
if os.environ.get("ANTHROPIC_API_KEY") != "":
    raise SystemExit("Claude API key was not replaced with an empty value")
connection = http.client.HTTPConnection(origin.hostname, origin.port, timeout=5)
body = b'{"messages":[]}'
connection.request("POST", "/v1/messages", body, {
    "Authorization": "Bearer " + os.environ["ANTHROPIC_AUTH_TOKEN"],
    "Content-Type": "application/json",
})
response = connection.getresponse()
if response.status != 200 or response.read() != b"OK":
    raise SystemExit("relay returned unexpected response")
connection.close()
with open("relay-port", "w", encoding="ascii") as report:
    report.write(str(origin.port))
"##,
    )
    .unwrap();
    let mut permissions = fs::metadata(path).unwrap().permissions();
    permissions.set_mode(0o700);
    fs::set_permissions(path, permissions).unwrap();
}

fn fake_gateway(listener: TcpListener) -> thread::JoinHandle<()> {
    thread::spawn(move || {
        listener.set_nonblocking(true).unwrap();
        let deadline = Instant::now() + HOST_STARTUP_BUDGET;
        let mut stream = loop {
            match listener.accept() {
                Ok((stream, _)) => break stream,
                Err(error) if error.kind() == std::io::ErrorKind::WouldBlock => {
                    assert!(
                        Instant::now() < deadline,
                        "relay did not contact fake gateway"
                    );
                    thread::sleep(Duration::from_millis(10));
                }
                Err(error) => panic!("fake gateway accept failed: {error}"),
            }
        };
        stream
            .set_read_timeout(Some(Duration::from_secs(5)))
            .unwrap();
        let mut headers = Vec::new();
        while !headers.ends_with(b"\r\n\r\n") {
            let mut byte = [0];
            stream.read_exact(&mut byte).unwrap();
            headers.push(byte[0]);
            assert!(headers.len() < 8192);
        }
        let text = String::from_utf8(headers).unwrap();
        assert!(text.starts_with("POST /v1/messages HTTP/1.1\r\n"));
        let authorization: Vec<_> = text
            .lines()
            .filter_map(|line| {
                let (name, value) = line.split_once(':')?;
                name.eq_ignore_ascii_case("authorization")
                    .then_some(value.trim())
            })
            .collect();
        assert_eq!(authorization.len(), 1);
        let mut parts = authorization[0].split_whitespace();
        assert!(parts.next().unwrap().eq_ignore_ascii_case("bearer"));
        assert_eq!(parts.next().unwrap(), format!("hox_a_{}", "A".repeat(43)));
        assert!(parts.next().is_none());
        let length: usize = text
            .lines()
            .find_map(|line| {
                line.to_ascii_lowercase()
                    .strip_prefix("content-length: ")
                    .and_then(|value| value.trim().parse().ok())
            })
            .unwrap();
        assert_eq!(length, BODY.len());
        let mut body = vec![0; length];
        stream.read_exact(&mut body).unwrap();
        assert_eq!(body, BODY);
        stream
            .write_all(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\nOK")
            .unwrap();
        // A second upstream connection would violate the one-request fixture.
        let until = Instant::now() + Duration::from_millis(250);
        while Instant::now() < until {
            match listener.accept() {
                Ok(_) => panic!("relay replayed the synthetic request"),
                Err(error) if error.kind() == std::io::ErrorKind::WouldBlock => {
                    thread::sleep(Duration::from_millis(10));
                }
                Err(error) => panic!("fake gateway accept failed: {error}"),
            }
        }
    })
}

struct SecretGuard<'a>(&'a NativeCredentialStore);
impl Drop for SecretGuard<'_> {
    fn drop(&mut self) {
        let _ = self.0.delete();
    }
}

struct UnitGuard {
    unit: String,
    wrapper: Child,
}
impl Drop for UnitGuard {
    fn drop(&mut self) {
        let _ = user_systemctl(&["stop", &self.unit]);
        let _ = self.wrapper.kill();
        let _ = self.wrapper.wait();
    }
}

fn wait_wrapper(guard: &mut UnitGuard, log: &Path) {
    let deadline = Instant::now() + HOST_STARTUP_BUDGET;
    loop {
        if let Some(status) = guard.wrapper.try_wait().unwrap() {
            let bounded_log = fs::read(log).unwrap_or_default();
            let bounded_log = &bounded_log[..bounded_log.len().min(4096)];
            assert!(
                status.success(),
                "synthetic relay exited {status}; log={}",
                String::from_utf8_lossy(bounded_log)
            );
            return;
        }
        assert!(Instant::now() < deadline, "synthetic relay did not exit");
        thread::sleep(Duration::from_millis(20));
    }
}

#[test]
#[ignore = "requires isolated Secret Service user manager"]
fn isolated_secret_service_launch_forwards_once_and_tears_down() {
    if std::env::var(OPT_IN).as_deref() != Ok("1") {
        eprintln!("host-only Secret Service launch test skipped: isolated user opt-in missing");
        return;
    }
    require_isolated_user();
    let store = NativeCredentialStore::default();
    assert!(
        store.load().unwrap().is_none(),
        "disposable fixture user must have no existing Hormuz credential"
    );
    let _secret_guard = SecretGuard(&store);
    let root = tempfile::tempdir().unwrap();
    let bin = root.path().join("bin");
    fs::create_dir(&bin).unwrap();
    let client = bin.join("claude");
    fake_client(&client);
    let gateway = TcpListener::bind((Ipv4Addr::LOCALHOST, 0)).unwrap();
    let gateway_address = gateway.local_addr().unwrap();
    let gateway = fake_gateway(gateway);
    let profile = ConnectionProfile::from_json(
        serde_json::json!({
            "id": PROFILE_KEY,
            "gateway": format!("http://{gateway_address}"),
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
    let directory = root.path().join("state");
    let private = PrivateDirectory::open(&directory).unwrap();
    private
        .try_lock()
        .unwrap()
        .write("profile.json", &serde_json::to_vec(&profile).unwrap(), None)
        .unwrap();
    let now = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap()
        .as_secs_f64();
    let record = serde_json::to_vec(&serde_json::json!({
        "profile": profile,
        "accessToken": format!("hox_a_{}", "A".repeat(43)),
        "refreshToken": format!("hox_r_{}", "R".repeat(43)),
        "accessExpiresAt": now - FOUNDATION_EPOCH + 600.0,
        "sessionExpiresAt": now - FOUNDATION_EPOCH + 3600.0,
        "state": "active"
    }))
    .unwrap();
    store.save(&SecretRecord::new(record).unwrap()).unwrap();

    let nonce = now.to_bits();
    let token = format!("secret-test-{}-{nonce}", std::process::id());
    let unit = format!("hormuz-relay-{token}.service");
    let wrapper = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("run-in-user-service.sh");
    let log_path = root.path().join("service.log");
    let log = File::create(&log_path).unwrap();
    let report = root.path().join("relay-port");
    let path =
        std::env::join_paths([bin.as_path(), Path::new("/usr/bin"), Path::new("/bin")]).unwrap();
    let child = Command::new(wrapper)
        .arg(token)
        .arg(env!("CARGO_BIN_EXE_hormuz-client-relay"))
        .args(["--profile", PROFILE_KEY, "--state-directory"])
        .arg(&directory)
        .current_dir(root.path())
        .env("PATH", path)
        .env("OPENAI_API_KEY", "synthetic-openai-must-not-reach-client")
        .env(
            "ANTHROPIC_API_KEY",
            "synthetic-anthropic-must-not-reach-client",
        )
        .env(
            "CLAUDE_CODE_OAUTH_TOKEN",
            "synthetic-oauth-must-not-reach-client",
        )
        .env("DBUS_SESSION_BUS_ADDRESS", "unix:path=/tmp/spoofed-bus")
        .env("XDG_RUNTIME_DIR", root.path())
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::from(log))
        .spawn()
        .unwrap();
    let mut guard = UnitGuard {
        unit,
        wrapper: child,
    };
    wait_wrapper(&mut guard, &log_path);
    gateway.join().unwrap();
    let port: u16 = fs::read_to_string(report).unwrap().parse().unwrap();
    let relay_address = SocketAddr::from((Ipv4Addr::LOCALHOST, port));
    let deadline = Instant::now() + Duration::from_secs(3);
    while TcpStream::connect_timeout(&relay_address, Duration::from_millis(100)).is_ok() {
        assert!(
            Instant::now() < deadline,
            "relay listener survived client exit"
        );
        thread::sleep(Duration::from_millis(10));
    }
    let status = user_systemctl(&["show", &guard.unit, "--property=ActiveState"]);
    if status.status.success() {
        assert_eq!(
            String::from_utf8_lossy(&status.stdout).trim(),
            "ActiveState=inactive"
        );
    } else {
        // --collect may unload the completed unit before this query.
        assert!(
            String::from_utf8_lossy(&status.stderr).contains("could not be found"),
            "could not verify service teardown: {}",
            String::from_utf8_lossy(&status.stderr)
        );
    }
    store.delete().unwrap();
    assert!(store.load().unwrap().is_none());
}
