#[cfg(windows)]
use crate::process_scope::OwnedClient;
use crate::{CredentialSource, LocalRelay, Optimization, RelayError};
use hormuz_client_core::{AIClient, ConnectionProfile};
use std::ffi::{OsStr, OsString};
use std::io::{Read, Seek, SeekFrom};
use std::net::SocketAddr;
use std::path::{Path, PathBuf};
#[cfg(not(windows))]
use std::process::Child;
use std::process::{Command, Stdio};
use std::sync::Arc;
use std::thread;
use std::time::{Duration, Instant};

const VERSION_BUDGET: Duration = Duration::from_secs(15);
const MAX_VERSION_OUTPUT: u64 = 4096;

/// Only the client versions already qualified by the Python reference are
/// admitted. Never fall through to an unverified executable on PATH.
pub fn discover_supported_client(client: AIClient) -> Result<PathBuf, RelayError> {
    let path = std::env::var_os("PATH").ok_or(RelayError::UnsupportedClient)?;
    discover_in_path(client, &path)
}

fn discover_in_path(client: AIClient, path: &OsStr) -> Result<PathBuf, RelayError> {
    let (command, version) = match client {
        AIClient::Codex => ("codex", "0.147.0"),
        AIClient::ClaudeCode => ("claude", "2.1.233"),
    };
    // npm installs supported Windows CLIs as .cmd shims; executable-only
    // discovery would silently make both clients unavailable there.
    let suffixes: &[&str] = if cfg!(windows) {
        &[".exe", ".cmd", ".bat"]
    } else {
        &[""]
    };
    let executable = std::env::split_paths(path)
        .flat_map(|directory| {
            suffixes
                .iter()
                .map(move |suffix| directory.join(format!("{command}{suffix}")))
        })
        .find(|candidate| candidate.is_file())
        .ok_or(RelayError::UnsupportedClient)?;
    let executable = executable
        .canonicalize()
        .map_err(|_| RelayError::UnsupportedClient)?;
    if !executable.is_absolute() {
        return Err(RelayError::UnsupportedClient);
    }
    let output = bounded_version_output(&executable)?;
    let text = std::str::from_utf8(&output).map_err(|_| RelayError::UnsupportedClient)?;
    if first_version(text) != Some(version) {
        return Err(RelayError::UnsupportedClient);
    }
    Ok(executable)
}

// Match the first semantic version, as the existing Python launcher does.
// A later occurrence of a supported version must not mask the installed one.
fn first_version(text: &str) -> Option<&str> {
    let bytes = text.as_bytes();
    for start in 0..bytes.len() {
        if !bytes[start].is_ascii_digit() || start > 0 && bytes[start - 1].is_ascii_digit() {
            continue;
        }
        let mut end = start;
        for group in 0..3 {
            let digits = end;
            while end < bytes.len() && bytes[end].is_ascii_digit() {
                end += 1;
            }
            if end == digits {
                break;
            }
            if group < 2 {
                if bytes.get(end) != Some(&b'.') {
                    break;
                }
                end += 1;
            } else if !bytes.get(end).is_some_and(u8::is_ascii_digit) {
                return text.get(start..end);
            }
        }
    }
    None
}

fn bounded_version_output(executable: &Path) -> Result<Vec<u8>, RelayError> {
    // Files avoid an unbounded pipe join if a version command exits after a
    // descendant inherits stdout or stderr. They carry only version text.
    let mut stdout = tempfile::tempfile().map_err(|_| RelayError::UnsupportedClient)?;
    let mut stderr = tempfile::tempfile().map_err(|_| RelayError::UnsupportedClient)?;
    let mut command = Command::new(executable);
    command
        .arg("--version")
        .env_clear()
        .envs(std::env::vars_os().filter(|(name, _)| !blocked(name)))
        .stdin(Stdio::null())
        .stdout(Stdio::from(
            stdout
                .try_clone()
                .map_err(|_| RelayError::UnsupportedClient)?,
        ))
        .stderr(Stdio::from(
            stderr
                .try_clone()
                .map_err(|_| RelayError::UnsupportedClient)?,
        ));
    #[cfg(windows)]
    let mut child = OwnedClient::spawn(&mut command).map_err(|_| RelayError::UnsupportedClient)?;
    #[cfg(target_os = "linux")]
    let mut child = OwnedClient(Some(
        crate::process_scope_linux::spawn(&mut command)
            .map_err(|_| RelayError::UnsupportedClient)?,
    ));
    #[cfg(all(not(windows), not(target_os = "linux")))]
    let mut child = OwnedClient(Some(
        command.spawn().map_err(|_| RelayError::UnsupportedClient)?,
    ));
    let deadline = Instant::now() + VERSION_BUDGET;
    let status = loop {
        #[cfg(windows)]
        let next = child
            .try_wait_status()
            .map_err(|_| RelayError::UnsupportedClient)?;
        #[cfg(not(windows))]
        let next = child
            .0
            .as_mut()
            .unwrap()
            .try_wait()
            .map_err(|_| RelayError::UnsupportedClient)?;
        if let Some(status) = next {
            #[cfg(not(windows))]
            {
                child.0 = None;
            }
            break status;
        }
        if Instant::now() >= deadline {
            drop(child);
            return Err(RelayError::UnsupportedClient);
        }
        thread::sleep(Duration::from_millis(10));
    };
    stdout
        .seek(SeekFrom::Start(0))
        .map_err(|_| RelayError::UnsupportedClient)?;
    stderr
        .seek(SeekFrom::Start(0))
        .map_err(|_| RelayError::UnsupportedClient)?;
    let mut output = Vec::new();
    stdout
        .take(MAX_VERSION_OUTPUT + 1)
        .read_to_end(&mut output)
        .map_err(|_| RelayError::UnsupportedClient)?;
    output.push(b' ');
    stderr
        .take(MAX_VERSION_OUTPUT + 1)
        .read_to_end(&mut output)
        .map_err(|_| RelayError::UnsupportedClient)?;
    if !status.success() || output.len() > MAX_VERSION_OUTPUT as usize {
        return Err(RelayError::UnsupportedClient);
    }
    Ok(output)
}

pub(crate) struct LaunchPlan {
    executable: PathBuf,
    args: Vec<OsString>,
    environment: Vec<(OsString, OsString)>,
}
impl std::fmt::Debug for LaunchPlan {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str("LaunchPlan(<redacted>)")
    }
}
impl LaunchPlan {
    pub(crate) fn new(
        profile: &ConnectionProfile,
        executable: PathBuf,
        relay_address: SocketAddr,
        local_credential: &str,
    ) -> Result<Self, RelayError> {
        if !executable.is_absolute()
            || !executable.is_file()
            || !relay_address.ip().is_loopback()
            || !valid_local_credential(local_credential)
        {
            return Err(RelayError::InvalidConfiguration);
        }
        let origin = format!("http://127.0.0.1:{}", relay_address.port());
        let mut environment: Vec<(OsString, OsString)> = std::env::vars_os()
            .filter(|(name, _)| !blocked(name))
            .collect();
        // Client tool subprocesses may need the user's proxy settings, but
        // the short-lived relay credential must never be sent to a proxy.
        for name in if cfg!(windows) {
            &["NO_PROXY"][..]
        } else {
            &["NO_PROXY", "no_proxy"][..]
        } {
            let mut value = std::env::var_os(name).unwrap_or_default();
            if !value.is_empty() {
                value.push(",");
            }
            value.push("127.0.0.1,localhost");
            environment.retain(|(existing, _)| {
                if cfg!(windows) {
                    !existing.to_string_lossy().eq_ignore_ascii_case(name)
                } else {
                    existing != OsStr::new(name)
                }
            });
            environment.push((name.into(), value));
        }
        let args = match profile.client() {
            AIClient::Codex => {
                environment.push(("HORMUZ_LOCAL_RELAY_TOKEN".into(), local_credential.into()));
                let provider = format!(
                    "{{name=\"Hormuz\",base_url={},wire_api=\"responses\",requires_openai_auth=false,env_key=\"HORMUZ_LOCAL_RELAY_TOKEN\"}}",
                    serde_json::to_string(&(origin + "/v1")).map_err(|_| RelayError::InvalidConfiguration)?
                );
                vec![
                    "-c".into(),
                    "model_provider=\"hormuz_context_relay\"".into(),
                    "-c".into(),
                    format!("model_providers.hormuz_context_relay={provider}").into(),
                    "-c".into(),
                    format!(
                        "model={}",
                        serde_json::to_string(profile.model())
                            .map_err(|_| RelayError::InvalidConfiguration)?
                    )
                    .into(),
                ]
            }
            AIClient::ClaudeCode => {
                for (name, value) in [
                    ("ANTHROPIC_BASE_URL", origin.as_str()),
                    ("ANTHROPIC_API_KEY", ""),
                    ("ANTHROPIC_AUTH_TOKEN", local_credential),
                    ("ANTHROPIC_MODEL", profile.model()),
                    ("ANTHROPIC_DEFAULT_OPUS_MODEL", profile.model()),
                    ("ANTHROPIC_DEFAULT_SONNET_MODEL", profile.model()),
                    ("ANTHROPIC_DEFAULT_HAIKU_MODEL", profile.model()),
                ] {
                    environment.push((name.into(), value.into()));
                }
                vec!["--model".into(), profile.model().into()]
            }
        };
        Ok(Self {
            executable,
            args,
            environment,
        })
    }

    /// Child environment and arguments exist only in this process and the
    /// launched client. No user-level AI client config/auth file is modified.
    pub(crate) fn spawn(self) -> Result<OwnedClient, RelayError> {
        let mut command = Command::new(&self.executable);
        command.args(&self.args).env_clear().envs(self.environment);
        #[cfg(windows)]
        {
            OwnedClient::spawn(&mut command).map_err(|_| RelayError::ClientLaunchFailed)
        }
        #[cfg(not(windows))]
        {
            #[cfg(target_os = "linux")]
            let child = crate::process_scope_linux::spawn(&mut command)
                .map_err(|_| RelayError::ClientLaunchFailed)?;
            #[cfg(not(target_os = "linux"))]
            let child = command
                .spawn()
                .map_err(|_| RelayError::ClientLaunchFailed)?;
            Ok(OwnedClient(Some(child)))
        }
    }
}

fn blocked(name: &OsStr) -> bool {
    let name = name.to_string_lossy().to_ascii_uppercase();
    matches!(
        name.as_str(),
        "OPENAI_API_KEY"
            | "OPENAI_BASE_URL"
            | "OPENAI_ORGANIZATION"
            | "OPENAI_PROJECT"
            | "CODEX_API_KEY"
            | "ANTHROPIC_API_KEY"
            | "ANTHROPIC_AUTH_TOKEN"
            | "ANTHROPIC_BASE_URL"
            | "ANTHROPIC_CUSTOM_HEADERS"
            | "CLAUDE_CODE_OAUTH_TOKEN"
            | "CLAUDE_CODE_USE_BEDROCK"
            | "CLAUDE_CODE_USE_VERTEX"
            | "CLAUDE_CODE_USE_FOUNDRY"
            | "HORMUZ_LOCAL_RELAY_TOKEN"
    )
}
pub(crate) fn valid_local_credential(value: &str) -> bool {
    value.len() == 49
        && value.starts_with("hox_l_")
        && value[6..]
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || b"_-".contains(&byte))
}

/// Owns and reaps the direct client on Unix. The dedicated launcher holds
/// this handle through client exit; closing the panel does not drop it.
#[cfg(not(windows))]
pub(crate) struct OwnedClient(Option<Child>);
#[cfg(not(windows))]
impl OwnedClient {
    pub(crate) fn wait(&mut self) -> Result<i32, RelayError> {
        let status = self
            .0
            .as_mut()
            .ok_or(RelayError::ClientLaunchFailed)?
            .wait()
            .map_err(|_| RelayError::ClientLaunchFailed)?;
        self.0 = None;
        Ok(status.code().unwrap_or(1))
    }
}
#[cfg(not(windows))]
impl Drop for OwnedClient {
    fn drop(&mut self) {
        if let Some(mut child) = self.0.take() {
            let _ = child.kill();
            let _ = child.wait();
        }
    }
}

/// Discover the qualified client before opening a listener; own the listener
/// and direct child together until the client exits. No relay remains idle.
pub fn run_client(
    profile: &ConnectionProfile,
    credentials: Arc<dyn CredentialSource>,
    optimization: Optimization,
) -> Result<i32, RelayError> {
    let executable = discover_supported_client(profile.client())?;
    run_with_executable(profile, credentials, optimization, executable)
}

fn run_with_executable(
    profile: &ConnectionProfile,
    credentials: Arc<dyn CredentialSource>,
    optimization: Optimization,
    executable: PathBuf,
) -> Result<i32, RelayError> {
    let relay = LocalRelay::start(profile, credentials, optimization)?;
    let plan = LaunchPlan::new(
        profile,
        executable,
        relay.address(),
        relay.local_credential(),
    )?;
    let mut client = plan.spawn()?;
    #[cfg(windows)]
    {
        let status = client
            .wait_status()
            .map_err(|_| RelayError::ClientLaunchFailed)?;
        Ok(status.code().unwrap_or(1))
    }
    #[cfg(not(windows))]
    {
        client.wait()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn pins_match_the_reference_client_versions() {
        let reference = include_str!("../../../../hormuz/client_versions.py");
        assert!(reference.contains("SUPPORTED_CODEX_VERSION = \"0.147.0\""));
        assert!(reference.contains("SUPPORTED_CLAUDE_CODE_VERSION = \"2.1.233\""));
    }
    #[test]
    fn local_credential_shape_is_exact() {
        assert!(valid_local_credential(&format!("hox_l_{}", "A".repeat(43))));
        assert!(!valid_local_credential(&format!(
            "hox_a_{}",
            "A".repeat(43)
        )));
        assert!(!valid_local_credential(&format!(
            "hox_l_{}!",
            "A".repeat(42)
        )));
    }

    #[cfg(unix)]
    fn fake_client(path: &Path, source: &str) {
        use std::os::unix::fs::PermissionsExt;
        std::fs::write(path, format!("#!/bin/sh\n{source}\n")).unwrap();
        let mut permissions = std::fs::metadata(path).unwrap().permissions();
        permissions.set_mode(0o700);
        std::fs::set_permissions(path, permissions).unwrap();
    }

    #[cfg(unix)]
    #[test]
    fn discovery_rejects_a_different_installed_client_version() {
        let temporary = tempfile::tempdir().unwrap();
        let executable = temporary.path().join("codex");
        fake_client(&executable, "printf 'codex 0.146.0\\n'");
        assert_eq!(
            discover_in_path(AIClient::Codex, temporary.path().as_os_str()).unwrap_err(),
            RelayError::UnsupportedClient
        );
        fake_client(&executable, "printf 'codex 0.146.0 (protocol 0.147.0)\\n'");
        assert_eq!(
            discover_in_path(AIClient::Codex, temporary.path().as_os_str()).unwrap_err(),
            RelayError::UnsupportedClient
        );
        fake_client(
            &executable,
            &format!("printf 'codex 0.147.0{}'", "x".repeat(4096)),
        );
        assert_eq!(
            discover_in_path(AIClient::Codex, temporary.path().as_os_str()).unwrap_err(),
            RelayError::UnsupportedClient
        );
        fake_client(&executable, "printf 'codex 0.147.0\\n'");
        assert_eq!(
            discover_in_path(AIClient::Codex, temporary.path().as_os_str()).unwrap(),
            executable.canonicalize().unwrap()
        );
    }

    #[cfg(windows)]
    #[test]
    fn discovery_accepts_a_qualified_windows_cmd_shim() {
        let temporary = tempfile::tempdir().unwrap();
        let executable = temporary.path().join("codex.cmd");
        std::fs::write(&executable, b"@echo off\r\necho codex 0.147.0\r\n").unwrap();
        assert_eq!(
            discover_in_path(AIClient::Codex, temporary.path().as_os_str()).unwrap(),
            executable.canonicalize().unwrap()
        );
    }

    #[cfg(unix)]
    #[test]
    fn launched_client_owns_relay_until_exit_without_inheriting_provider_auth() {
        use std::{fs, net::TcpStream};
        let temporary = tempfile::tempdir().unwrap();
        let executable = temporary.path().join("claude");
        let address_file = temporary.path().join("address");
        let release_file = temporary.path().join("release");
        let quote =
            |path: &Path| format!("'{}'", path.display().to_string().replace('\'', "'\\''"));
        fake_client(
            &executable,
            &format!(
                "test -z \"${{OPENAI_API_KEY-}}\" || exit 41\n\
                 test -z \"${{ANTHROPIC_API_KEY-}}\" || exit 42\n\
                 test -n \"${{ANTHROPIC_AUTH_TOKEN-}}\" || exit 43\n\
                 case \"$NO_PROXY\" in *127.0.0.1,localhost*) ;; *) exit 44 ;; esac\n\
                 printf '%s' \"$ANTHROPIC_BASE_URL\" > {}\n\
                 count=0\n\
                 while [ ! -f {} ] && [ \"$count\" -lt 50 ]; do sleep 0.1; count=$((count + 1)); done\n\
                 exit 7",
                quote(&address_file), quote(&release_file)
            ),
        );
        let profile = ConnectionProfile::from_json(
            br#"{"id":"12345678-1234-1234-1234-123456789abc","gateway":"http://127.0.0.1:9","organization":"org-a","client":"claude-code","model":"approved","allowLoopbackHTTP":true,"setup":"custom"}"#,
        ).unwrap();
        let credential: Arc<dyn CredentialSource> =
            Arc::new(|| Ok(zeroize::Zeroizing::new(format!("hox_a_{}", "A".repeat(43)))));
        let handle = thread::spawn(move || {
            run_with_executable(&profile, credential, Optimization::Off, executable)
        });
        let deadline = Instant::now() + Duration::from_secs(5);
        while !address_file.exists() && Instant::now() < deadline {
            thread::sleep(Duration::from_millis(10));
        }
        let origin = fs::read_to_string(&address_file).unwrap();
        let address: SocketAddr = origin.strip_prefix("http://").unwrap().parse().unwrap();
        assert!(TcpStream::connect_timeout(&address, Duration::from_millis(250)).is_ok());
        fs::write(&release_file, b"").unwrap();
        assert_eq!(handle.join().unwrap().unwrap(), 7);
        assert!(TcpStream::connect_timeout(&address, Duration::from_millis(250)).is_err());
    }
}
