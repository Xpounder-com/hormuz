// Native filesystem implementations are deliberately selected explicitly.
#[cfg(target_os = "macos")]
#[path = "private_macos.rs"]
mod implementation;
#[cfg(windows)]
#[path = "private_windows.rs"]
mod implementation;
#[cfg(not(any(target_os = "macos", windows)))]
mod implementation {
    use crate::{PlatformError, Result};
    use std::path::Path;
    pub struct PrivateDirectory;
    pub struct PrivateTransaction;
    impl PrivateDirectory {
        pub fn open(_root: &Path) -> Result<Self> {
            Err(PlatformError::Unsupported)
        }
        pub fn try_lock(&self) -> Result<PrivateTransaction> {
            Err(PlatformError::Unsupported)
        }
    }
    impl PrivateTransaction {
        pub fn read(&self, _name: &str) -> Result<Option<Vec<u8>>> {
            Err(PlatformError::Unsupported)
        }
        pub fn write(&self, _name: &str, _bytes: &[u8], _expected: Option<&[u8]>) -> Result<()> {
            Err(PlatformError::Unsupported)
        }
    }
}
pub use implementation::{PrivateDirectory, PrivateTransaction};
pub const MAX_PRIVATE_FILE_BYTES: usize = 1_048_576;

#[cfg(any(target_os = "macos", windows))]
fn validate_name(name: &str) -> crate::Result<()> {
    if name.is_empty()
        || name.len() > 128
        || name == "."
        || name == ".."
        || name.to_ascii_lowercase().starts_with(".write-")
        || name.eq_ignore_ascii_case("connection.lock")
        || name.ends_with(['.', ' '])
        || !name
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || b"._-".contains(&byte))
    {
        return Err(crate::PlatformError::UnsafeStorage);
    }
    // Reject Windows device names on every implemented platform.
    let stem = name.split('.').next().unwrap_or("").to_ascii_uppercase();
    if [
        "CON", "PRN", "AUX", "NUL", "COM1", "COM2", "COM3", "COM4", "COM5", "COM6", "COM7", "COM8",
        "COM9", "LPT1", "LPT2", "LPT3", "LPT4", "LPT5", "LPT6", "LPT7", "LPT8", "LPT9",
    ]
    .contains(&stem.as_str())
    {
        return Err(crate::PlatformError::UnsafeStorage);
    }
    Ok(())
}
