//! Native command entry point for a supervised launch. No credential or model
//! content is printed, passed on the process command line, or persisted here.
#[cfg(target_os = "linux")]
use hormuz_client_core::ConnectionProfile;
#[cfg(target_os = "linux")]
use hormuz_client_platform::{CredentialStore, RefreshCoordinator};
use hormuz_client_platform::{NativeCredentialStore, PrivateDirectory};
use hormuz_client_relay::{run_client, CredentialSource, Optimization, RelayError};
#[cfg(any(target_os = "macos", windows))]
use hormuz_client_relay::{OptimizerCancellation, RequestOptimizer};
#[cfg(target_os = "linux")]
use hormuz_client_session::{Clock, SessionTransport};
use hormuz_client_session::{NativeTransport, Operation, SessionController, SystemClock};
#[cfg(any(target_os = "macos", windows))]
use serde::Deserialize;
use std::ffi::OsStr;
#[cfg(target_os = "linux")]
use std::ffi::OsString;
#[cfg(target_os = "linux")]
use std::path::Path;
use std::path::PathBuf;
#[cfg(any(target_os = "macos", windows))]
use std::process::Command;
use std::process::ExitCode;
use std::sync::Arc;
#[cfg(any(target_os = "macos", windows))]
use std::time::Duration;
use zeroize::Zeroizing;

#[cfg(any(target_os = "macos", windows))]
const TRANSFORM_BUDGET: Duration = Duration::from_secs(30);
#[cfg(any(target_os = "macos", windows))]
const MAX_TRANSFORM_BYTES: u64 = 1024 * 1024;

pub fn main() -> ExitCode {
    match execute() {
        Ok(code) => ExitCode::from(u8::try_from(code).unwrap_or(1)),
        Err(error) => {
            eprintln!("relay error: {error}");
            ExitCode::FAILURE
        }
    }
}

#[cfg(any(target_os = "macos", windows))]
fn execute() -> Result<i32, RelayError> {
    let (key, root) = arguments()?;
    let directory = PrivateDirectory::open(&root).map_err(|_| RelayError::InvalidConfiguration)?;
    let controller = Arc::new(SessionController::new(
        directory.clone(),
        NativeCredentialStore::default(),
        NativeTransport,
        SystemClock::default(),
    ));
    let status = controller
        .status(&Operation::default())
        .map_err(|_| RelayError::CredentialUnavailable)?;
    let profile = status
        .profile()
        .filter(|profile| profile.key() == key)
        .cloned()
        .ok_or(RelayError::InvalidConfiguration)?;
    if !status.has_session() {
        return Err(RelayError::CredentialUnavailable);
    }
    // Fail before launching an AI client when custody is locked or the session
    // is pending/expired. The shared controller owns any required refresh intent.
    controller
        .access_credential(&profile, false, &Operation::default())
        .map_err(|_| RelayError::CredentialUnavailable)?;
    let token_source = Arc::new(move || {
        let credential = controller
            .access_credential(&profile, false, &Operation::default())
            .map_err(|_| RelayError::CredentialUnavailable)?;
        Ok(Zeroizing::new(credential.expose().to_owned()))
    }) as Arc<dyn CredentialSource>;
    let optimizer = Arc::new(PythonOptimizer {
        directory,
        key,
        client: status
            .profile()
            .ok_or(RelayError::InvalidConfiguration)?
            .client()
            .as_str()
            .to_owned(),
        gateway: status
            .profile()
            .ok_or(RelayError::InvalidConfiguration)?
            .gateway()
            .to_owned(),
    });
    let status_profile = status.profile().ok_or(RelayError::InvalidConfiguration)?;
    run_client(
        status_profile,
        token_source,
        Optimization::OnDemand(optimizer),
    )
}

#[cfg(target_os = "linux")]
fn execute() -> Result<i32, RelayError> {
    if let Some(token) = linux_stop_token_from(std::env::args_os().skip(1)) {
        hormuz_client_relay::stop_linux_user_service(&token?)?;
        return Ok(0);
    }
    let (key, root) = arguments()?;
    execute_linux_with(
        &key,
        &root,
        hormuz_client_relay::require_linux_user_service,
        |root| {
            let directory =
                PrivateDirectory::open(root).map_err(|_| RelayError::InvalidConfiguration)?;
            Ok(SessionController::new(
                directory,
                NativeCredentialStore::default(),
                NativeTransport,
                SystemClock::default(),
            ))
        },
        run_client,
    )
}

/// Parse the Linux control operation before any private-state or custody work.
/// The service layer validates the exact token and canonical same-UID bus.
#[cfg(target_os = "linux")]
fn linux_stop_token_from<I>(arguments: I) -> Option<Result<String, RelayError>>
where
    I: IntoIterator<Item = OsString>,
{
    let mut arguments = arguments.into_iter();
    if arguments.next()?.as_os_str() != OsStr::new("stop") {
        return None;
    }
    if arguments.next().as_deref() != Some(OsStr::new("--unit-token")) {
        return Some(Err(RelayError::InvalidConfiguration));
    }
    let token = match arguments.next() {
        Some(token) => token
            .into_string()
            .map_err(|_| RelayError::InvalidConfiguration),
        None => Err(RelayError::InvalidConfiguration),
    };
    if arguments.next().is_some() {
        return Some(Err(RelayError::InvalidConfiguration));
    }
    Some(token)
}

/// The Linux preflight runs before opening the private directory or touching
/// Secret Service. The later client-discovery check protects the intervening
/// launch interval. Tests inject each boundary without live credentials.
#[cfg(target_os = "linux")]
fn execute_linux_with<C, S, T, K, Preflight, Open, Launch>(
    key: &str,
    root: &Path,
    preflight: Preflight,
    open: Open,
    launch: Launch,
) -> Result<i32, RelayError>
where
    C: RefreshCoordinator + Send + Sync + 'static,
    S: CredentialStore + Send + Sync + 'static,
    T: SessionTransport + Send + Sync + 'static,
    K: Clock + Send + Sync + 'static,
    Preflight: FnOnce() -> Result<(), RelayError>,
    Open: FnOnce(&Path) -> Result<SessionController<C, S, T, K>, RelayError>,
    Launch: FnOnce(
        &ConnectionProfile,
        Arc<dyn CredentialSource>,
        Optimization,
    ) -> Result<i32, RelayError>,
{
    preflight()?;
    let controller = Arc::new(open(root)?);
    let status = controller
        .status(&Operation::default())
        .map_err(|_| RelayError::CredentialUnavailable)?;
    let profile = status
        .profile()
        .filter(|profile| profile.key() == key)
        .cloned()
        .ok_or(RelayError::InvalidConfiguration)?;
    if !status.has_session() {
        return Err(RelayError::CredentialUnavailable);
    }
    // Reject a missing, locked, pending, expired or mismatched session before
    // the first listener or supported-client version probe can start.
    controller
        .access_credential(&profile, false, &Operation::default())
        .map_err(|_| RelayError::CredentialUnavailable)?;
    let credential_profile = profile.clone();
    let token_source = Arc::new(move || {
        let credential = controller
            .access_credential(&credential_profile, false, &Operation::default())
            .map_err(|_| RelayError::CredentialUnavailable)?;
        Ok(Zeroizing::new(credential.expose().to_owned()))
    }) as Arc<dyn CredentialSource>;
    launch(&profile, token_source, Optimization::Off)
}

fn arguments() -> Result<(String, PathBuf), RelayError> {
    let mut args = std::env::args_os().skip(1);
    let mut profile = None;
    let mut directory = None;
    while let Some(flag) = args.next() {
        let value = args.next().ok_or(RelayError::InvalidConfiguration)?;
        if flag == "--profile" && profile.is_none() {
            let value = value
                .into_string()
                .map_err(|_| RelayError::InvalidConfiguration)?;
            if value.len() != 36
                || !value.bytes().enumerate().all(|(index, byte)| {
                    if matches!(index, 8 | 13 | 18 | 23) {
                        byte == b'-'
                    } else {
                        byte.is_ascii_hexdigit()
                    }
                })
            {
                return Err(RelayError::InvalidConfiguration);
            }
            profile = Some(value.to_ascii_lowercase());
        } else if flag == "--state-directory" && directory.is_none() {
            let value = PathBuf::from(value);
            if !value.is_absolute() || value.as_os_str().as_encoded_bytes().len() > 4096 {
                return Err(RelayError::InvalidConfiguration);
            }
            directory = Some(value);
        } else {
            return Err(RelayError::InvalidConfiguration);
        }
    }
    Ok((
        profile.ok_or(RelayError::InvalidConfiguration)?,
        directory.ok_or(RelayError::InvalidConfiguration)?,
    ))
}

#[cfg(any(target_os = "macos", windows))]
struct PythonOptimizer {
    directory: PrivateDirectory,
    key: String,
    client: String,
    gateway: String,
}

#[cfg(target_os = "macos")]
impl RequestOptimizer for PythonOptimizer {
    fn prepare(
        &self,
        path: &str,
        original: &[u8],
        cancellation: &OptimizerCancellation,
    ) -> Option<Vec<u8>> {
        if cancellation.is_cancelled() || !enabled(&self.directory, &self.key) {
            return None;
        }
        let mut command = Command::new("python3");
        command
            .arg("-I")
            .arg("-m")
            .arg("hormuz.context_relay_bridge")
            .arg("--client")
            .arg(&self.client)
            .arg("--path")
            .arg(path)
            .env_clear()
            .envs(std::env::vars_os().filter(|(name, _)| python_environment(name)));
        let output = Zeroizing::new(crate::helper_exchange::run(
            &mut command,
            self.gateway.as_bytes(),
            original,
            TRANSFORM_BUDGET,
            MAX_TRANSFORM_BYTES as usize,
            cancellation,
        )?);
        match output.first() {
            Some(1) if output.len() > 1 => Some(output[1..].to_vec()),
            _ => None,
        }
    }
}

#[cfg(windows)]
impl RequestOptimizer for PythonOptimizer {
    fn prepare(
        &self,
        path: &str,
        original: &[u8],
        cancellation: &OptimizerCancellation,
    ) -> Option<Vec<u8>> {
        if cancellation.is_cancelled() || !enabled(&self.directory, &self.key) {
            return None;
        }
        let mut command = Command::new("python.exe");
        command
            .arg("-I")
            .arg("-m")
            .arg("hormuz.context_relay_bridge")
            .arg("--client")
            .arg(&self.client)
            .arg("--path")
            .arg(path)
            .env_clear()
            .envs(std::env::vars_os().filter(|(name, _)| python_environment(name)));
        let output = Zeroizing::new(crate::helper_exchange_windows::run(
            &mut command,
            self.gateway.as_bytes(),
            original,
            TRANSFORM_BUDGET,
            MAX_TRANSFORM_BYTES as usize,
            cancellation,
        )?);
        match output.first() {
            Some(0) if output.len() == 1 => None,
            Some(1) if output.len() > 1 => Some(output[1..].to_vec()),
            _ => None,
        }
    }
}

/// The Python helper receives only the model body over stdin. An inherited
/// provider token or Python import override is not needed for this transform.
#[cfg(any(target_os = "macos", windows))]
fn python_environment(name: &OsStr) -> bool {
    let name = name.to_string_lossy().to_ascii_uppercase();
    matches!(
        name.as_str(),
        "PATH"
            | "HOME"
            | "USERPROFILE"
            | "APPDATA"
            | "LOCALAPPDATA"
            | "SYSTEMROOT"
            | "SYSTEMDRIVE"
            | "TEMP"
            | "TMP"
            | "TMPDIR"
            | "LANG"
            | "LC_ALL"
            | "SSL_CERT_FILE"
            | "HORMUZ_CONTEXT_TOKENIZER_CACHE"
    )
}

#[cfg(any(target_os = "macos", windows))]
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Preference {
    enabled: bool,
    schema_version: u32,
}
#[cfg(any(target_os = "macos", windows))]
fn enabled(directory: &PrivateDirectory, key: &str) -> bool {
    let name = format!("context-optimization-{key}.json");
    let Ok(guard) = directory.try_lock() else {
        return false;
    };
    let Ok(Some(bytes)) = guard.read(&name) else {
        return false;
    };
    preference_enabled(&bytes)
}
#[cfg(any(target_os = "macos", windows))]
fn preference_enabled(bytes: &[u8]) -> bool {
    if bytes.len() > 4096 {
        return false;
    }
    let Ok(preference) = serde_json::from_slice::<Preference>(bytes) else {
        return false;
    };
    preference.schema_version == 1
        && bytes
            == format!(
                "{{\"enabled\":{},\"schema_version\":1}}",
                preference.enabled
            )
            .as_bytes()
        && preference.enabled
}

#[cfg(all(test, any(target_os = "macos", windows)))]
mod tests {
    use super::*;
    #[test]
    fn preferences_are_canonical_and_fail_to_off() {
        assert!(preference_enabled(
            b"{\"enabled\":true,\"schema_version\":1}"
        ));
        for value in [
            b"{\"enabled\":false,\"schema_version\":1}".as_slice(),
            b"{\"schema_version\":1,\"enabled\":true}".as_slice(),
            b"{\"enabled\":true,\"enabled\":true,\"schema_version\":1}".as_slice(),
            b"{\"enabled\":true,\"schema_version\":2}".as_slice(),
        ] {
            assert!(!preference_enabled(value));
        }
    }

    #[test]
    fn helper_preserves_only_approved_runtime_configuration() {
        assert!(python_environment(OsStr::new(
            "HORMUZ_CONTEXT_TOKENIZER_CACHE"
        )));
        assert!(!python_environment(OsStr::new("OPENAI_API_KEY")));
        assert!(!python_environment(OsStr::new("PYTHONPATH")));
    }
}

#[cfg(all(test, target_os = "linux"))]
mod linux_tests {
    use super::*;
    use hormuz_client_platform::{
        PlatformError, PrivateFiles, Result as PlatformResult, SecretRecord,
    };
    use hormuz_client_session::Reply;
    use hormuz_client_transport::{ErrorKind, RequestOutcome, TransportError};
    use std::io::{Read, Write};
    use std::net::{Ipv4Addr, TcpListener, TcpStream};
    use std::sync::atomic::{AtomicUsize, Ordering};
    use std::thread;
    use std::time::Duration;

    const KEY: &str = "12345678-1234-1234-1234-123456789abc";
    const NOW: f64 = 1_780_000_000.0;
    const FOUNDATION_EPOCH: f64 = 978_307_200.0;

    #[test]
    fn stop_command_is_exact_and_separate_from_launch_arguments() {
        let parse = |parts: &[&str]| linux_stop_token_from(parts.iter().map(OsString::from));
        assert_eq!(
            parse(&["stop", "--unit-token", "test-42"]),
            Some(Ok("test-42".to_owned()))
        );
        assert_eq!(parse(&["--profile", KEY]), None);
        for parts in [
            &["stop"][..],
            &["stop", "--unit-token"],
            &["stop", "--profile", KEY],
            &["stop", "--unit-token", "test", "--state-directory", "/tmp"],
        ] {
            assert_eq!(parse(parts), Some(Err(RelayError::InvalidConfiguration)));
        }
    }

    #[derive(Clone)]
    struct FakeCoordinator {
        profile: Option<Vec<u8>>,
        reads: Arc<AtomicUsize>,
    }

    struct FakeGuard(FakeCoordinator);

    impl PrivateFiles for FakeGuard {
        fn read(&self, name: &str) -> PlatformResult<Option<Vec<u8>>> {
            assert_eq!(name, "profile.json");
            self.0.reads.fetch_add(1, Ordering::SeqCst);
            Ok(self.0.profile.clone())
        }

        fn write(
            &self,
            _name: &str,
            _bytes: &[u8],
            _expected: Option<&[u8]>,
        ) -> PlatformResult<()> {
            Err(PlatformError::UnsafeStorage)
        }
    }

    impl RefreshCoordinator for FakeCoordinator {
        type Guard = FakeGuard;

        fn try_acquire(&self) -> PlatformResult<Self::Guard> {
            Ok(FakeGuard(self.clone()))
        }
    }

    #[derive(Clone)]
    struct FakeStore {
        bytes: Option<Vec<u8>>,
        locked: bool,
        loads: Arc<AtomicUsize>,
    }

    impl CredentialStore for FakeStore {
        fn load(&self) -> PlatformResult<Option<SecretRecord>> {
            self.loads.fetch_add(1, Ordering::SeqCst);
            if self.locked {
                return Err(PlatformError::SecureStoreUnavailable);
            }
            self.bytes
                .as_ref()
                .map(|bytes| SecretRecord::new(bytes.clone()))
                .transpose()
        }

        fn save(&self, _record: &SecretRecord) -> PlatformResult<()> {
            Err(PlatformError::SecureStoreUnavailable)
        }

        fn delete(&self) -> PlatformResult<()> {
            Err(PlatformError::SecureStoreUnavailable)
        }

        fn maximum_record_bytes(&self) -> usize {
            32_767
        }
    }

    #[derive(Clone)]
    struct FakeTransport(Arc<AtomicUsize>);

    impl SessionTransport for FakeTransport {
        fn request(
            &self,
            _profile: &ConnectionProfile,
            _path: &str,
            _body: Option<&[u8]>,
            _access: Option<&str>,
            _operation: &Operation,
        ) -> Result<Reply, TransportError> {
            self.0.fetch_add(1, Ordering::SeqCst);
            Err(TransportError {
                kind: ErrorKind::Offline,
                outcome: RequestOutcome::NotSent,
            })
        }
    }

    #[derive(Clone, Copy)]
    struct FakeClock;

    impl Clock for FakeClock {
        fn now(&self) -> f64 {
            NOW
        }

        fn elapsed(&self) -> Duration {
            Duration::ZERO
        }

        fn sleep(&self, _duration: Duration) {
            panic!("synthetic session unexpectedly waited for a lock or refresh");
        }
    }

    fn profile(gateway: &str, model: &str) -> ConnectionProfile {
        ConnectionProfile::from_json(
            serde_json::json!({
                "id": KEY,
                "gateway": gateway,
                "organization": "org-a",
                "client": "codex",
                "model": model,
                "allowLoopbackHTTP": true,
                "setup": "custom"
            })
            .to_string()
            .as_bytes(),
        )
        .unwrap()
    }

    fn record(profile: &ConnectionProfile) -> Vec<u8> {
        serde_json::to_vec(&serde_json::json!({
            "profile": profile,
            "accessToken": format!("hox_a_{}", "A".repeat(43)),
            "refreshToken": format!("hox_r_{}", "R".repeat(43)),
            "accessExpiresAt": NOW - FOUNDATION_EPOCH + 600.0,
            "sessionExpiresAt": NOW - FOUNDATION_EPOCH + 43_200.0,
            "state": "active"
        }))
        .unwrap()
    }

    fn controller(
        private_profile: Option<&ConnectionProfile>,
        store_bytes: Option<Vec<u8>>,
        locked: bool,
        reads: Arc<AtomicUsize>,
        loads: Arc<AtomicUsize>,
        transport_calls: Arc<AtomicUsize>,
    ) -> SessionController<FakeCoordinator, FakeStore, FakeTransport, FakeClock> {
        SessionController::new(
            FakeCoordinator {
                profile: private_profile.map(|profile| serde_json::to_vec(profile).unwrap()),
                reads,
            },
            FakeStore {
                bytes: store_bytes,
                locked,
                loads,
            },
            FakeTransport(transport_calls),
            FakeClock,
        )
    }

    #[test]
    fn failed_preflight_opens_no_private_state_or_custody_and_starts_no_client_or_gateway() {
        let opened = Arc::new(AtomicUsize::new(0));
        let launched = Arc::new(AtomicUsize::new(0));
        let reads = Arc::new(AtomicUsize::new(0));
        let loads = Arc::new(AtomicUsize::new(0));
        let transport_calls = Arc::new(AtomicUsize::new(0));
        let result = execute_linux_with(
            KEY,
            Path::new("/synthetic/private"),
            || Err(RelayError::NativeSupervisionUnavailable),
            |_| {
                opened.fetch_add(1, Ordering::SeqCst);
                Ok(controller(
                    None,
                    None,
                    false,
                    reads.clone(),
                    loads.clone(),
                    transport_calls.clone(),
                ))
            },
            |_, _, _| {
                launched.fetch_add(1, Ordering::SeqCst);
                Ok(0)
            },
        );
        assert_eq!(result, Err(RelayError::NativeSupervisionUnavailable));
        assert_eq!(opened.load(Ordering::SeqCst), 0);
        assert_eq!(reads.load(Ordering::SeqCst), 0);
        assert_eq!(loads.load(Ordering::SeqCst), 0);
        assert_eq!(launched.load(Ordering::SeqCst), 0);
        assert_eq!(transport_calls.load(Ordering::SeqCst), 0);
    }

    #[test]
    fn missing_locked_and_mismatched_sessions_never_open_a_listener() {
        let requested = profile("http://127.0.0.1:9", "approved");
        let mismatched = profile("http://127.0.0.1:9", "different");
        for (private_profile, store_bytes, locked, expected) in [
            (
                Some(requested.clone()),
                None,
                false,
                RelayError::CredentialUnavailable,
            ),
            (
                Some(requested.clone()),
                Some(record(&requested)),
                true,
                RelayError::CredentialUnavailable,
            ),
            (
                Some(mismatched),
                Some(record(&requested)),
                false,
                RelayError::CredentialUnavailable,
            ),
        ] {
            let launched = Arc::new(AtomicUsize::new(0));
            let loads = Arc::new(AtomicUsize::new(0));
            let transport_calls = Arc::new(AtomicUsize::new(0));
            let result = execute_linux_with(
                KEY,
                Path::new("/synthetic/private"),
                || Ok(()),
                |_| {
                    Ok(controller(
                        private_profile.as_ref(),
                        store_bytes,
                        locked,
                        Arc::new(AtomicUsize::new(0)),
                        loads.clone(),
                        transport_calls.clone(),
                    ))
                },
                |_, _, _| {
                    launched.fetch_add(1, Ordering::SeqCst);
                    Ok(0)
                },
            );
            assert_eq!(result, Err(expected));
            assert_eq!(launched.load(Ordering::SeqCst), 0);
            assert_eq!(transport_calls.load(Ordering::SeqCst), 0);
            assert!(loads.load(Ordering::SeqCst) >= 1);
        }
    }

    #[test]
    fn valid_synthetic_session_uses_governed_off_relay_once() {
        let gateway = TcpListener::bind((Ipv4Addr::LOCALHOST, 0)).unwrap();
        let gateway_address = gateway.local_addr().unwrap();
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
            let length: usize = text
                .lines()
                .find_map(|line| {
                    line.to_ascii_lowercase()
                        .strip_prefix("content-length: ")
                        .and_then(|value| value.trim().parse().ok())
                })
                .unwrap();
            let mut body = vec![0; length];
            socket.read_exact(&mut body).unwrap();
            assert_eq!(body, b"{\"input\":[1]}");
            assert!(text
                .to_ascii_lowercase()
                .contains(&format!("authorization: bearer hox_a_{}", "a".repeat(43))));
            socket
                .write_all(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\nOK")
                .unwrap();
        });
        let requested = profile(&format!("http://{gateway_address}"), "approved");
        let reads = Arc::new(AtomicUsize::new(0));
        let loads = Arc::new(AtomicUsize::new(0));
        let transport_calls = Arc::new(AtomicUsize::new(0));
        let result = execute_linux_with(
            KEY,
            Path::new("/synthetic/private"),
            || Ok(()),
            |_| {
                Ok(controller(
                    Some(&requested),
                    Some(record(&requested)),
                    false,
                    reads.clone(),
                    loads.clone(),
                    transport_calls.clone(),
                ))
            },
            |profile, credentials, optimization| {
                assert!(matches!(optimization, Optimization::Off));
                let relay =
                    hormuz_client_relay::LocalRelay::start(profile, credentials, optimization)?;
                let mut client = TcpStream::connect(relay.address()).unwrap();
                client
                    .set_read_timeout(Some(Duration::from_secs(5)))
                    .unwrap();
                write!(client, "POST /v1/responses HTTP/1.1\r\nHost: 127.0.0.1:{}\r\nAuthorization: Bearer {}\r\nContent-Length: 13\r\nContent-Type: application/json\r\n\r\n{{\"input\":[1]}}", relay.address().port(), relay.local_credential()).unwrap();
                let mut response = Vec::new();
                client.read_to_end(&mut response).unwrap();
                assert!(response.starts_with(b"HTTP/1.1 200"));
                let body_start = response
                    .windows(4)
                    .position(|part| part == b"\r\n\r\n")
                    .unwrap()
                    + 4;
                assert!(response[body_start..].windows(2).any(|part| part == b"OK"));
                Ok(7)
            },
        );
        assert_eq!(result, Ok(7));
        assert_eq!(transport_calls.load(Ordering::SeqCst), 0);
        assert!(reads.load(Ordering::SeqCst) >= 1);
        assert!(loads.load(Ordering::SeqCst) >= 2);
        upstream.join().unwrap();
    }
}
