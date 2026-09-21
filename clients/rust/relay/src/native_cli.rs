//! Native command entry point for a supervised launch. No credential or model
//! content is printed, passed on the process command line, or persisted here.
use hormuz_client_platform::{NativeCredentialStore, PrivateDirectory};
use hormuz_client_relay::{
    run_client, CredentialSource, Optimization, OptimizerCancellation, RelayError, RequestOptimizer,
};
use hormuz_client_session::{NativeTransport, Operation, SessionController, SystemClock};
use serde::Deserialize;
use std::ffi::OsStr;
use std::path::PathBuf;
use std::process::{Command, ExitCode};
use std::sync::Arc;
use std::time::Duration;
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

    #[test]
    fn helper_preserves_only_approved_runtime_configuration() {
        assert!(python_environment(OsStr::new(
            "HORMUZ_CONTEXT_TOKENIZER_CACHE"
        )));
        assert!(!python_environment(OsStr::new("OPENAI_API_KEY")));
        assert!(!python_environment(OsStr::new("PYTHONPATH")));
    }
}
