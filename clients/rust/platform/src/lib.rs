//! OS custody and storage primitives for #333. No session/network policy.
//!
//! All calls are synchronous and belong on a worker, never a native UI thread.
//! Linux native adapters remain in #343. Browser/process/event interfaces are
//! implemented by their owning shell/lifecycle work, not initialized here.

#![deny(unsafe_op_in_unsafe_fn)]

use std::fmt;
use std::path::Path;
use zeroize::Zeroizing;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum PlatformError {
    Unavailable,
    UnsafeStorage,
    Busy,
    Changed,
    TooLarge,
    SecureStoreUnavailable,
    Unsupported,
}

impl fmt::Display for PlatformError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(match self {
            Self::Unavailable => "Local storage is unavailable.",
            Self::UnsafeStorage => "Local storage did not pass its safety checks.",
            Self::Busy => "Another client operation holds the required local lock.",
            Self::Changed => "Local configuration changed before it could be saved.",
            Self::TooLarge => "The record exceeds the native storage limit.",
            Self::SecureStoreUnavailable => "The secure credential store is unavailable.",
            Self::Unsupported => "This platform adapter is not implemented.",
        })
    }
}
impl std::error::Error for PlatformError {}
pub type Result<T> = std::result::Result<T, PlatformError>;

/// Opaque secret bytes: never serialized or printed by this library. Session
/// schema validation belongs to #335. Only our owned allocation is zeroized;
/// OS APIs and the caller may own independent copies.
pub struct SecretRecord(Zeroizing<Vec<u8>>);

impl SecretRecord {
    pub fn new(bytes: Vec<u8>) -> Result<Self> {
        let bytes = Zeroizing::new(bytes);
        if bytes.is_empty() || bytes.len() > 32_767 {
            return Err(PlatformError::TooLarge);
        }
        Ok(Self(bytes))
    }
    pub fn expose(&self) -> &[u8] {
        &self.0
    }
}
impl fmt::Debug for SecretRecord {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str("SecretRecord(<redacted>)")
    }
}

/// The session controller holds its coordination guard throughout a complete
/// read/pending-write/network/commit transaction. It must never replay an
/// ambiguous refresh. These adapters do not make that session decision.
pub trait CredentialStore {
    fn load(&self) -> Result<Option<SecretRecord>>;
    fn save(&self, record: &SecretRecord) -> Result<()>;
    fn delete(&self) -> Result<()>;
    fn maximum_record_bytes(&self) -> usize;
}

/// Typed boundary for a shell to open an already validated authentication URL.
/// No command strings, shell expansion, or secret-bearing diagnostic messages.
pub trait BrowserOpener {
    fn open_authentication_url(&self, url: &str) -> Result<()>;
}

/// A uniquely owned child, never an arbitrary PID. Native implementations must
/// bound all calls, reap a confirmed exit, and provide non-detaching Drop and
/// parent-death cleanup. A failed stop keeps ownership with the caller. Native
/// process groups/Windows job objects and executable trust remain shell work.
pub trait SupervisedProcess {
    /// False means the child has exited and has been reaped.
    fn is_running(&mut self) -> Result<bool>;
    /// Ok means the owned child has exited and has been reaped.
    fn terminate_and_wait(&mut self) -> Result<()>;
}

/// Launch policy, binary identity and environment allowlists belong to #339/#341.
/// Err must leave no untracked child. A successful child has the lifetime
/// guarantees in SupervisedProcess, including when its owner crashes.
pub trait ProcessSupervisor {
    type Child: SupervisedProcess;
    fn launch(&self, executable: &Path, arguments: &[String]) -> Result<Self::Child>;
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum LifecycleEvent {
    Sleep,
    Wake,
    NetworkChanged,
    SessionLocked,
    SessionUnlocked,
    Quit,
}

pub trait LifecycleEvents {
    /// A shell owns the bounded event queue and coalescing policy (#337/#339).
    fn next_event(&mut self) -> Option<LifecycleEvent>;
}

#[cfg(target_os = "macos")]
mod macos;
#[cfg(target_os = "macos")]
pub use macos::NativeCredentialStore;
#[cfg(windows)]
mod windows;
#[cfg(windows)]
pub use windows::NativeCredentialStore;

mod private;
/// Native application ownership can only be acquired through a private
/// directory. It cannot be forged, including on unsupported platforms.
///
/// ```compile_fail
/// use hormuz_client_platform::ApplicationInstance;
/// let instance = ApplicationInstance {};
/// ```
pub use private::ApplicationInstance;
pub use private::{PrivateDirectory, PrivateTransaction, MAX_PRIVATE_FILE_BYTES};

pub mod lifecycle;

pub trait PrivateFiles {
    fn read(&self, name: &str) -> Result<Option<Vec<u8>>>;
    fn write(&self, name: &str, bytes: &[u8], expected: Option<&[u8]>) -> Result<()>;
}
impl PrivateFiles for PrivateTransaction {
    fn read(&self, name: &str) -> Result<Option<Vec<u8>>> {
        PrivateTransaction::read(self, name)
    }
    fn write(&self, name: &str, bytes: &[u8], expected: Option<&[u8]>) -> Result<()> {
        PrivateTransaction::write(self, name, bytes, expected)
    }
}

pub trait RefreshCoordinator {
    type Guard: PrivateFiles;
    fn try_acquire(&self) -> Result<Self::Guard>;
}
impl RefreshCoordinator for PrivateDirectory {
    type Guard = PrivateTransaction;
    fn try_acquire(&self) -> Result<Self::Guard> {
        self.try_lock()
    }
}

#[cfg(test)]
mod tests;

#[cfg(all(test, any(target_os = "macos", windows)))]
mod instance_tests;
