//! Lossless structural-v1 optimizer. This crate is not a shipping default.
//! Resources are loaded only when the caller explicitly constructs Tokenizers.
#![forbid(unsafe_code)]

pub mod bridge;
mod formats;
pub mod json;
mod request;
mod tokenizers;

pub use formats::{compact_text, restore_text};
pub use request::{derive_selections, optimize_request};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::collections::BTreeMap;
pub use tokenizers::{validate_resources, Tokenizers, RESOURCE_FILES, RESOURCE_VERSION};

pub const TRANSFORM_VERSION: &str = "structural-v1";
pub const MAX_BLOCK_BYTES: usize = 64 * 1024;
pub const MAX_REQUEST_BYTES: usize = 1024 * 1024;
pub const MAX_ROWS: usize = 4096;
pub const MAX_COLUMNS: usize = 64;
pub const MAX_DEPTH: usize = 32;
pub const MAX_NODES: usize = 50_000;
pub const MAX_SELECTIONS: usize = 64;
pub const MIN_BLOCK_BYTES: usize = 256;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Deserialize, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum Format {
    JsonTable,
    LineRuns,
    SearchLines,
    PathList,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Deserialize, Serialize)]
#[serde(rename_all = "lowercase")]
pub enum Protocol {
    Chat,
    Responses,
    Anthropic,
}

#[derive(Debug, Clone, PartialEq, Eq, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct Selection {
    pub result_id: String,
    pub format: Format,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Error {
    InvalidJson,
    LimitExceeded,
    MalformedEnvelope,
    InvalidSelection,
    ResourcesUnavailable,
}
impl std::fmt::Display for Error {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter.write_str(match self {
            Self::InvalidJson => "invalid_json",
            Self::LimitExceeded => "limit_exceeded",
            Self::MalformedEnvelope => "malformed_compaction_envelope",
            Self::InvalidSelection => "invalid_selection",
            Self::ResourcesUnavailable => "resources_unavailable",
        })
    }
}
impl std::error::Error for Error {}

pub type Counts = BTreeMap<String, usize>;
pub trait TokenCounter {
    fn count(&self, text: &str) -> Result<Counts, Error>;
}

#[derive(Debug, Clone, Serialize)]
pub struct Outcome {
    pub payload: Value,
    pub changed: bool,
    pub reason: &'static str,
    pub changed_blocks: usize,
    pub before_bytes: Option<usize>,
    pub after_bytes: Option<usize>,
    pub before_tokens: Counts,
    pub after_tokens: Counts,
    pub transform_version: &'static str,
}
