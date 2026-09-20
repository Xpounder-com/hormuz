//! Native command entry point for a supervised launch. No credential or model
//! content is printed, passed on the process command line, or persisted here.
use hormuz_client_platform::{NativeCredentialStore, PrivateDirectory};
use hormuz_client_relay::{
    run_client, CredentialSource, Optimization, RelayError, RequestOptimizer,
};
use hormuz_client_session::{NativeTransport, Operation, SessionController, SystemClock};
use serde::Deserialize;
use std::ffi::OsStr;
use std::io::{Read, Write};
use std::path::PathBuf;
use std::process::{Child, Command, ExitCode, Stdio};
use std::sync::Arc;
use std::thread;
use std::time::{Duration, Instant};
use zeroize::Zeroizing;

const TRANSFORM_BUDGET: Duration = Duration::from_secs(30);
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

struct PythonOptimizer {
    directory: PrivateDirectory,
    key: String,
    client: String,
    gateway: String,
}
impl RequestOptimizer for PythonOptimizer {
    fn prepare(&self, path: &str, original: &[u8]) -> Option<Vec<u8>> {
        if !enabled(&self.directory, &self.key) {
            return None;
        }
        if original.len() > MAX_TRANSFORM_BYTES as usize {
            return None;
        }
        let program = if cfg!(windows) {
            "python.exe"
        } else {
            "python3"
        };
        let mut child = OwnedTransform(Some(
            Command::new(program)
                .arg("-I")
                .arg("-m")
                .arg("hormuz.context_relay_bridge")
                .arg("--client")
                .arg(&self.client)
                .arg("--path")
                .arg(path)
                .env_clear()
                .envs(std::env::vars_os().filter(|(name, _)| python_environment(name)))
                .stdin(Stdio::piped())
                .stdout(Stdio::piped())
                .stderr(Stdio::null())
                .spawn()
                .ok()?,
        ));
        let mut stdin = child.0.as_mut()?.stdin.take()?;
        let stdout = child.0.as_mut()?.stdout.take()?;
        let body = Zeroizing::new(original.to_vec());
        let gateway = Zeroizing::new(self.gateway.as_bytes().to_vec());
        let length = u16::try_from(gateway.len()).ok()?.to_be_bytes();
        let writer = thread::spawn(move || {
            stdin.write_all(&length)?;
            stdin.write_all(&gateway)?;
            stdin.write_all(&body)
        });
        let reader = thread::spawn(move || {
            let mut output = Vec::new();
            stdout
                .take(MAX_TRANSFORM_BYTES + 2)
                .read_to_end(&mut output)
                .map(|_| output)
        });
        let deadline = Instant::now() + TRANSFORM_BUDGET;
        let status = loop {
            match child.0.as_mut()?.try_wait() {
                Ok(Some(status)) => {
                    child.0 = None;
                    break status;
                }
                Ok(None) if Instant::now() < deadline => thread::sleep(Duration::from_millis(10)),
                _ => {
                    drop(child);
                    let _ = writer.join();
                    let _ = reader.join();
                    return None;
                }
            }
        };
        let wrote = writer.join().ok()?.is_ok();
        let output = reader.join().ok()?.ok()?;
        if !status.success()
            || !wrote
            || output.is_empty()
            || output.len() > MAX_TRANSFORM_BYTES as usize + 1
        {
            return None;
        }
        match output[0] {
            0 if output.len() == 1 => None,
            1 if output.len() > 1 => Some(output[1..].to_vec()),
            _ => None,
        }
    }
}

/// The Python helper receives only the model body over stdin. An inherited
/// provider token or Python import override is not needed for this transform.
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
    )
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Preference {
    enabled: bool,
    schema_version: u32,
}
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

struct OwnedTransform(Option<Child>);
impl Drop for OwnedTransform {
    fn drop(&mut self) {
        if let Some(mut child) = self.0.take() {
            let _ = child.kill();
            let _ = child.wait();
        }
    }
}

#[cfg(test)]
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
}
