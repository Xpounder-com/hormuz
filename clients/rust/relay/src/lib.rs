//! On-demand governed client launch and relay. Constructing a plan opens no
//! socket, reads no credential and starts no idle worker.
#![cfg_attr(not(any(target_os = "linux", windows)), forbid(unsafe_code))]
#![cfg_attr(any(target_os = "linux", windows), deny(unsafe_code))]

mod launch;
#[cfg(target_os = "linux")]
mod linux_service;
// Linux's parent-death signal requires one pre-exec syscall. Keep the unsafe
// fork-side hook in this reviewed module; the rest of the crate remains safe.
#[cfg(target_os = "linux")]
#[allow(unsafe_code)]
mod process_scope_linux;
// The Windows Job Object calls are confined to this module.
#[cfg(windows)]
#[allow(unsafe_code)]
mod process_scope;
mod relay;

pub use launch::{discover_supported_client, run_client};
pub use relay::{
    CredentialSource, LocalRelay, Optimization, OptimizerCancellation, RequestOptimizer,
};

use std::fmt;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum RelayError {
    UnsupportedClient,
    InvalidConfiguration,
    LocalAuthentication,
    CredentialUnavailable,
    TooManyClients,
    RelayUnavailable,
    ClientLaunchFailed,
    NativeSupervisionUnavailable,
    ClientExitedUnsuccessfully,
}
impl fmt::Display for RelayError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(match self {
            Self::UnsupportedClient => "The installed AI client version is unsupported.",
            Self::InvalidConfiguration => "The client launch configuration is invalid.",
            Self::LocalAuthentication => "The local relay credential was rejected.",
            Self::CredentialUnavailable => "The gateway session credential is unavailable.",
            Self::TooManyClients => "The local relay is at capacity.",
            Self::RelayUnavailable => "The local relay is unavailable.",
            Self::ClientLaunchFailed => "The AI client could not be launched.",
            Self::NativeSupervisionUnavailable => {
                "A verified on-demand user service is required before launching an AI client."
            }
            Self::ClientExitedUnsuccessfully => "The AI client exited unsuccessfully.",
        })
    }
}
impl std::error::Error for RelayError {}
