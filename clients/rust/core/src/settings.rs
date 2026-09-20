use crate::ClientError;
use serde::{Deserialize, Serialize};

pub const MAX_CONTEXT_SETTINGS_BYTES: usize = 4096;

/// Only the existing two canonical byte strings are valid settings files.
/// This rejects duplicates, escaped keys, extra keys, whitespace, coercions and
/// unsupported versions exactly as the existing Swift/Python readers do.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub struct ContextOptimizationPreference {
    enabled: bool,
}

impl ContextOptimizationPreference {
    pub fn new(enabled: bool) -> Self {
        Self { enabled }
    }
    pub fn enabled(self) -> bool {
        self.enabled
    }
    pub fn from_file_bytes(bytes: Option<&[u8]>) -> Result<Self, ClientError> {
        match bytes {
            None | Some(b"{\"enabled\":false,\"schema_version\":1}") => Ok(Self::default()),
            Some(b"{\"enabled\":true,\"schema_version\":1}") => Ok(Self::new(true)),
            Some(_) => Err(ClientError::ContextSettingsInvalid),
        }
    }
    pub fn to_file_bytes(self) -> &'static [u8] {
        if self.enabled {
            b"{\"enabled\":true,\"schema_version\":1}"
        } else {
            b"{\"enabled\":false,\"schema_version\":1}"
        }
    }
}

#[derive(Clone, Copy, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum ContextOptimizationStatus {
    Off,
    Ready,
    ResourcesUnavailable,
    UnsupportedClient,
    UnsupportedHistory,
    GatewayIncompatible,
    SettingsInvalid,
}

impl ContextOptimizationStatus {
    pub fn label(self) -> &'static str {
        match self {
            Self::Off => "Off",
            Self::Ready => "Ready for eligible tool results",
            Self::ResourcesUnavailable => "Tokenizer resources need setup",
            Self::UnsupportedClient => "This client version is not supported",
            Self::UnsupportedHistory => "Current history will pass through unchanged",
            Self::GatewayIncompatible => "Gateway upgrade required",
            Self::SettingsInvalid => "Local setting could not be verified",
        }
    }
}
