//! Native command entry point for a supervised launch. No credential or model
//! content is printed, passed on the process command line, or persisted here.
#[cfg(target_os = "linux")]
use hormuz_client_core::ConnectionProfile;
#[cfg(target_os = "linux")]
use hormuz_client_platform::{CredentialStore, RefreshCoordinator};
use hormuz_client_platform::{NativeCredentialStore, PrivateDirectory};
use hormuz_client_relay::{run_client, CredentialSource, Optimization, RelayError};
#[cfg(any(target_os = "macos", target_os = "linux", windows))]
use hormuz_client_relay::{OptimizerCancellation, RequestOptimizer};
#[cfg(target_os = "linux")]
use hormuz_client_session::{Clock, SessionTransport};
use hormuz_client_session::{NativeTransport, Operation, SessionController, SystemClock};
#[cfg(any(target_os = "macos", target_os = "linux", windows))]
use serde::Deserialize;
use std::ffi::OsStr;
#[cfg(target_os = "linux")]
use std::ffi::OsString;
#[cfg(target_os = "linux")]
use std::path::Path;
use std::path::PathBuf;
#[cfg(any(target_os = "macos", target_os = "linux", windows))]
use std::process::Command;
use std::process::ExitCode;
use std::sync::Arc;
#[cfg(any(target_os = "macos", target_os = "linux", windows))]
use std::time::Duration;
use zeroize::Zeroizing;

#[cfg(any(target_os = "macos", target_os = "linux", windows))]
const TRANSFORM_BUDGET: Duration = Duration::from_secs(30);
#[cfg(any(target_os = "macos", target_os = "linux", windows))]
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
    let LaunchArguments { key, root, .. } = arguments()?;
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
    let LaunchArguments {
        key,
        root,
        optimizer_python,
    } = arguments()?;
    let optimizer_root = root.clone();
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
        |profile| linux_optimization(profile, &optimizer_root, optimizer_python.as_deref()),
        run_client,
    )
}

/// The optimizer is constructed only after the supervised-launch preflight and
/// credential check. Absence of the explicit interpreter keeps the streaming
/// Off path even when an old private preference happens to be enabled.
#[cfg(target_os = "linux")]
fn linux_optimization(
    profile: &ConnectionProfile,
    root: &Path,
    python: Option<&Path>,
) -> Result<Optimization, RelayError> {
    let Some(python) = python else {
        return Ok(Optimization::Off);
    };
    validate_optimizer_python_executable(python)?;
    let directory = PrivateDirectory::open(root).map_err(|_| RelayError::InvalidConfiguration)?;
    Ok(Optimization::OnDemand(Arc::new(PythonOptimizer {
        directory,
        key: profile.key().to_owned(),
        client: profile.client().as_str().to_owned(),
        gateway: profile.gateway().to_owned(),
        python: python.to_owned(),
        state_root: root.to_owned(),
    })))
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
    configure_optimization: impl FnOnce(&ConnectionProfile) -> Result<Optimization, RelayError>,
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
    let optimization = configure_optimization(&profile)?;
    launch(&profile, token_source, optimization)
}

struct LaunchArguments {
    key: String,
    root: PathBuf,
    #[cfg(target_os = "linux")]
    optimizer_python: Option<PathBuf>,
}

fn arguments() -> Result<LaunchArguments, RelayError> {
    parse_arguments(std::env::args_os().skip(1))
}

fn parse_arguments<I>(arguments: I) -> Result<LaunchArguments, RelayError>
where
    I: IntoIterator<Item = std::ffi::OsString>,
{
    let mut args = arguments.into_iter();
    let mut profile = None;
    let mut directory = None;
    #[cfg(target_os = "linux")]
    let mut optimizer_python = None;
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
        } else if cfg!(target_os = "linux") && flag == "--optimizer-python" {
            #[cfg(target_os = "linux")]
            {
                if optimizer_python.is_some() {
                    return Err(RelayError::InvalidConfiguration);
                }
                let path = PathBuf::from(value);
                validate_optimizer_python_path(&path)?;
                optimizer_python = Some(path);
            }
            #[cfg(not(target_os = "linux"))]
            return Err(RelayError::InvalidConfiguration);
        } else {
            return Err(RelayError::InvalidConfiguration);
        }
    }
    Ok(LaunchArguments {
        key: profile.ok_or(RelayError::InvalidConfiguration)?,
        root: directory.ok_or(RelayError::InvalidConfiguration)?,
        #[cfg(target_os = "linux")]
        optimizer_python,
    })
}

#[cfg(target_os = "linux")]
fn validate_optimizer_python_path(path: &Path) -> Result<(), RelayError> {
    if !path.is_absolute() || path.as_os_str().as_encoded_bytes().len() > 4096 {
        return Err(RelayError::InvalidConfiguration);
    }
    Ok(())
}

/// Inspect the executable only after the verified user-service and session
/// checks, since an explicit path could itself reside below private state.
#[cfg(target_os = "linux")]
fn validate_optimizer_python_executable(path: &Path) -> Result<(), RelayError> {
    use rustix::fs::{accessat, Access, AtFlags, CWD};
    validate_optimizer_python_path(path)?;
    let metadata = path
        .metadata()
        .map_err(|_| RelayError::InvalidConfiguration)?;
    if !metadata.is_file() || accessat(CWD, path, Access::EXEC_OK, AtFlags::EACCESS).is_err() {
        return Err(RelayError::InvalidConfiguration);
    }
    Ok(())
}

#[cfg(any(target_os = "macos", target_os = "linux", windows))]
struct PythonOptimizer {
    directory: PrivateDirectory,
    key: String,
    client: String,
    gateway: String,
    #[cfg(target_os = "linux")]
    python: PathBuf,
    #[cfg(target_os = "linux")]
    state_root: PathBuf,
}

#[cfg(any(target_os = "macos", target_os = "linux"))]
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
        #[cfg(target_os = "macos")]
        let mut command = Command::new("python3");
        #[cfg(target_os = "linux")]
        let mut command = Command::new(&self.python);
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
        #[cfg(target_os = "linux")]
        command.env("HORMUZ_CLIENT_STATE_DIRECTORY", &self.state_root);
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
#[cfg(any(target_os = "macos", target_os = "linux", windows))]
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
            | "SSL_CERT_DIR"
            | "HORMUZ_CONTEXT_TOKENIZER_CACHE"
    )
}

#[cfg(any(target_os = "macos", target_os = "linux", windows))]
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Preference {
    enabled: bool,
    schema_version: u32,
}
#[cfg(any(target_os = "macos", target_os = "linux", windows))]
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
#[cfg(any(target_os = "macos", target_os = "linux", windows))]
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
        assert!(python_environment(OsStr::new("SSL_CERT_FILE")));
        assert!(python_environment(OsStr::new("SSL_CERT_DIR")));
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
    use std::os::unix::fs::PermissionsExt;
    use std::sync::atomic::{AtomicUsize, Ordering};
    use std::thread;
    use std::time::Duration;

    const KEY: &str = "12345678-1234-1234-1234-123456789abc";
    const NOW: f64 = 1_780_000_000.0;
    const FOUNDATION_EPOCH: f64 = 978_307_200.0;

    fn fake_interpreter(path: &Path, body: &str) {
        std::fs::write(path, format!("#!/bin/sh\n{body}\n")).unwrap();
        let mut permissions = std::fs::metadata(path).unwrap().permissions();
        permissions.set_mode(0o700);
        std::fs::set_permissions(path, permissions).unwrap();
    }

    #[test]
    fn linux_optimizer_requires_one_explicit_absolute_executable() {
        assert!(python_environment(OsStr::new(
            "HORMUZ_CONTEXT_TOKENIZER_CACHE"
        )));
        assert!(python_environment(OsStr::new("SSL_CERT_FILE")));
        assert!(python_environment(OsStr::new("SSL_CERT_DIR")));
        for blocked in ["OPENAI_API_KEY", "ANTHROPIC_API_KEY", "PYTHONPATH"] {
            assert!(!python_environment(OsStr::new(blocked)));
        }
        assert!(!python_environment(OsStr::new(
            "HORMUZ_CLIENT_STATE_DIRECTORY"
        )));
        let temporary = tempfile::tempdir().unwrap();
        let interpreter = temporary.path().join("python");
        fake_interpreter(&interpreter, "exit 0");
        let parse = |parts: Vec<OsString>| parse_arguments(parts);
        let base = || {
            vec![
                OsString::from("--profile"),
                OsString::from(KEY),
                OsString::from("--state-directory"),
                OsString::from("/synthetic/private"),
            ]
        };
        assert!(parse(base()).unwrap().optimizer_python.is_none());
        let mut opted_in = base();
        opted_in.extend([
            OsString::from("--optimizer-python"),
            interpreter.clone().into(),
        ]);
        assert_eq!(
            parse(opted_in.clone()).unwrap().optimizer_python,
            Some(interpreter.clone())
        );
        opted_in.extend([
            OsString::from("--optimizer-python"),
            interpreter.clone().into(),
        ]);
        assert!(matches!(
            parse(opted_in),
            Err(RelayError::InvalidConfiguration)
        ));
        let mut args = base();
        args.extend([
            OsString::from("--optimizer-python"),
            OsString::from("python"),
        ]);
        assert!(matches!(parse(args), Err(RelayError::InvalidConfiguration)));
        let requested = profile("http://127.0.0.1:9", "approved");
        for invalid in [
            temporary.path().join("missing"),
            temporary.path().to_owned(),
        ] {
            let mut args = base();
            args.extend([OsString::from("--optimizer-python"), invalid.clone().into()]);
            assert_eq!(parse(args).unwrap().optimizer_python, Some(invalid.clone()));
            assert!(matches!(
                linux_optimization(
                    &requested,
                    &temporary.path().join("private"),
                    Some(&invalid)
                ),
                Err(RelayError::InvalidConfiguration)
            ));
        }
        // Owner permissions take precedence over group/other bits for an
        // ordinary user. Root may execute with either group/other bit set,
        // matching the effective-user access check in production.
        for mode in [0o600, 0o001, 0o010] {
            let mut permissions = std::fs::metadata(&interpreter).unwrap().permissions();
            permissions.set_mode(mode);
            std::fs::set_permissions(&interpreter, permissions).unwrap();
            let mut args = base();
            args.extend([
                OsString::from("--optimizer-python"),
                interpreter.clone().into(),
            ]);
            assert_eq!(
                parse(args).unwrap().optimizer_python,
                Some(interpreter.clone())
            );
            let executable = mode != 0o600 && unsafe { libc::geteuid() } == 0;
            assert_eq!(
                validate_optimizer_python_executable(&interpreter).is_ok(),
                executable
            );
            if !executable {
                assert!(matches!(
                    linux_optimization(
                        &requested,
                        &temporary.path().join("private"),
                        Some(&interpreter)
                    ),
                    Err(RelayError::InvalidConfiguration)
                ));
            }
        }
    }

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
            |_| {
                panic!("preflight failure must precede optimizer selection");
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
                |_| {
                    panic!("unavailable session must precede optimizer selection");
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
            |_| Ok(Optimization::Off),
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

    #[test]
    fn opt_in_linux_helper_transforms_only_with_enabled_private_preference() {
        for (
            interpreter_selected,
            preference_enabled,
            helper_succeeds,
            expected_body,
            helper_runs,
        ) in [
            (true, true, true, b"{\"input\":[2]}".as_slice(), true),
            (true, false, true, b"{\"input\":[1]}".as_slice(), false),
            (false, true, true, b"{\"input\":[1]}".as_slice(), false),
            (true, true, false, b"{\"input\":[1]}".as_slice(), true),
        ] {
            let temporary = tempfile::tempdir().unwrap();
            let root = temporary.path().join("private");
            let directory = PrivateDirectory::open(&root).unwrap();
            directory
                .try_lock()
                .unwrap()
                .write(
                    &format!("context-optimization-{KEY}.json"),
                    if preference_enabled {
                        b"{\"enabled\":true,\"schema_version\":1}"
                    } else {
                        b"{\"enabled\":false,\"schema_version\":1}"
                    },
                    None,
                )
                .unwrap();
            let helper = temporary.path().join("synthetic-python");
            let marker = temporary.path().join("helper-ran");
            fake_interpreter(
                &helper,
                &format!(
                    "[ \"$HORMUZ_CLIENT_STATE_DIRECTORY\" = '{}' ] && \
                     [ \"$#\" -eq 7 ] && [ \"$1\" = -I ] && [ \"$2\" = -m ] && \
                     [ \"$3\" = hormuz.context_relay_bridge ] && [ \"$4\" = --client ] && \
                     [ \"$5\" = codex ] && [ \"$6\" = --path ] && \
                     [ \"$7\" = /v1/responses ] || exit 9\n\
                     : > '{}'\ncat >/dev/null\n{}",
                    root.display(),
                    marker.display(),
                    if helper_succeeds {
                        "printf '\\001{\"input\":[2]}'"
                    } else {
                        "exit 7"
                    }
                ),
            );
            let gateway = TcpListener::bind((Ipv4Addr::LOCALHOST, 0)).unwrap();
            gateway.set_nonblocking(true).unwrap();
            let gateway_address = gateway.local_addr().unwrap();
            let upstream = thread::spawn(move || {
                let deadline = std::time::Instant::now() + Duration::from_secs(5);
                let (mut socket, _) = loop {
                    match gateway.accept() {
                        Ok(connection) => break connection,
                        Err(error) if error.kind() == std::io::ErrorKind::WouldBlock => {
                            assert!(std::time::Instant::now() < deadline);
                            thread::sleep(Duration::from_millis(10));
                        }
                        Err(error) => panic!("synthetic gateway failed: {error}"),
                    }
                };
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
                let header_text = String::from_utf8(headers).unwrap();
                let length: usize = header_text
                    .lines()
                    .find_map(|line| {
                        line.to_ascii_lowercase()
                            .strip_prefix("content-length: ")
                            .and_then(|value| value.trim().parse().ok())
                    })
                    .unwrap();
                let mut body = vec![0; length];
                socket.read_exact(&mut body).unwrap();
                socket
                    .write_all(
                        b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\nOK",
                    )
                    .unwrap();
                drop(socket);
                let no_replay_until = std::time::Instant::now() + Duration::from_millis(150);
                while std::time::Instant::now() < no_replay_until {
                    match gateway.accept() {
                        Ok(_) => panic!("opt-in relay repeated a synthetic POST"),
                        Err(error) if error.kind() == std::io::ErrorKind::WouldBlock => {
                            thread::sleep(Duration::from_millis(10));
                        }
                        Err(error) => panic!("synthetic gateway failed: {error}"),
                    }
                }
                (header_text, body)
            });
            let requested = profile(&format!("http://{gateway_address}"), "approved");
            let result = execute_linux_with(
                KEY,
                &root,
                || Ok(()),
                |_| {
                    Ok(controller(
                        Some(&requested),
                        Some(record(&requested)),
                        false,
                        Arc::new(AtomicUsize::new(0)),
                        Arc::new(AtomicUsize::new(0)),
                        Arc::new(AtomicUsize::new(0)),
                    ))
                },
                |profile| {
                    linux_optimization(
                        profile,
                        &root,
                        interpreter_selected.then_some(helper.as_path()),
                    )
                },
                |profile, credentials, optimization| {
                    assert_eq!(
                        matches!(&optimization, Optimization::OnDemand(_)),
                        interpreter_selected
                    );
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
                    Ok(0)
                },
            );
            assert_eq!(result, Ok(0));
            let (headers, body) = upstream.join().unwrap();
            assert_eq!(body, expected_body);
            assert_eq!(marker.exists(), helper_runs);
            assert_eq!(
                headers
                    .to_ascii_lowercase()
                    .contains("x-hormuz-context-format: structural-v1"),
                helper_runs && helper_succeeds
            );
        }
    }

    #[test]
    fn relay_shutdown_cancels_blocked_opt_in_helper_before_gateway_egress() {
        let temporary = tempfile::tempdir().unwrap();
        let root = temporary.path().join("private");
        PrivateDirectory::open(&root)
            .unwrap()
            .try_lock()
            .unwrap()
            .write(
                &format!("context-optimization-{KEY}.json"),
                b"{\"enabled\":true,\"schema_version\":1}",
                None,
            )
            .unwrap();
        let helper = temporary.path().join("synthetic-python");
        let marker = temporary.path().join("helper-pid");
        fake_interpreter(
            &helper,
            &format!(
                "printf '%s' \"$$\" > '{}'\nexec /bin/sleep 10",
                marker.display()
            ),
        );
        let gateway = TcpListener::bind((Ipv4Addr::LOCALHOST, 0)).unwrap();
        gateway.set_nonblocking(true).unwrap();
        let requested = profile(
            &format!("http://{}", gateway.local_addr().unwrap()),
            "approved",
        );
        let optimization = linux_optimization(&requested, &root, Some(&helper)).unwrap();
        let credentials = Arc::new(|| Ok(Zeroizing::new(format!("hox_a_{}", "A".repeat(43)))))
            as Arc<dyn CredentialSource>;
        let relay =
            hormuz_client_relay::LocalRelay::start(&requested, credentials, optimization).unwrap();
        let mut client = TcpStream::connect(relay.address()).unwrap();
        write!(client, "POST /v1/responses HTTP/1.1\r\nHost: 127.0.0.1:{}\r\nAuthorization: Bearer {}\r\nContent-Length: 13\r\nContent-Type: application/json\r\n\r\n{{\"input\":[1]}}", relay.address().port(), relay.local_credential()).unwrap();
        let deadline = std::time::Instant::now() + Duration::from_secs(5);
        let helper_pid = loop {
            if let Some(pid) = std::fs::read_to_string(&marker)
                .ok()
                .and_then(|text| text.parse::<u32>().ok())
            {
                break pid;
            }
            assert!(
                std::time::Instant::now() < deadline,
                "fake helper did not start"
            );
            thread::sleep(Duration::from_millis(10));
        };
        let started = std::time::Instant::now();
        drop(relay);
        assert!(started.elapsed() < Duration::from_secs(2));
        assert!(!PathBuf::from(format!("/proc/{helper_pid}")).exists());
        assert_eq!(
            gateway.accept().unwrap_err().kind(),
            std::io::ErrorKind::WouldBlock
        );
        drop(client);
    }
}
