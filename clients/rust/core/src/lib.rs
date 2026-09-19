//! Initial native-client contract slice for issue #330.
//!
//! These are validated, credential-free projections of the existing gateway v1
//! responses, matching the Mac client. They are not server-schema validators.
//! No network runtime, secure store, helper process or tokenizer is initialized.

#![forbid(unsafe_code)]

use serde::de::{self, Visitor};
use serde::{Deserialize, Deserializer, Serialize};
use std::fmt;

/// Matches the existing Mac transport bound; transport must also bound reads.
pub const MAX_RESPONSE_BYTES: usize = 128 * 1024;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum ClientError {
    InvalidResponse,
    IdentityMismatch,
    ResponseTooLarge,
}

impl fmt::Display for ClientError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(match self {
            Self::InvalidResponse => {
                "The gateway returned an invalid response. No credential was displayed."
            }
            Self::IdentityMismatch => {
                "The returned identity does not match this team's client connection."
            }
            Self::ResponseTooLarge => "The gateway response exceeded the client safety limit.",
        })
    }
}

impl std::error::Error for ClientError {}

#[derive(Clone, Copy, Debug, Deserialize, Serialize, PartialEq, Eq)]
pub enum AIClient {
    #[serde(rename = "codex")]
    Codex,
    #[serde(rename = "claude-code")]
    ClaudeCode,
}

impl AIClient {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Codex => "codex",
            Self::ClaudeCode => "claude-code",
        }
    }
}

// Swift JSONDecoder accepts exactly integral JSON numbers (including 1.0)
// for Int. Keep the signed 64-bit boundary used by supported native platforms.
fn swift_integer<'de, D: Deserializer<'de>>(decoder: D) -> Result<i64, D::Error> {
    struct Integer;
    impl Visitor<'_> for Integer {
        type Value = i64;
        fn expecting(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
            f.write_str("a signed 64-bit integral JSON number")
        }
        fn visit_i64<E: de::Error>(self, value: i64) -> Result<i64, E> {
            Ok(value)
        }
        fn visit_u64<E: de::Error>(self, value: u64) -> Result<i64, E> {
            i64::try_from(value).map_err(|_| E::custom("integer out of range"))
        }
        fn visit_f64<E: de::Error>(self, value: f64) -> Result<i64, E> {
            if value.is_finite()
                && value.fract() == 0.0
                && value >= i64::MIN as f64
                && value < 9_223_372_036_854_775_808.0
            {
                Ok(value as i64)
            } else {
                Err(E::custom("integer out of range or fractional"))
            }
        }
    }
    decoder.deserialize_any(Integer)
}

fn decode<T: serde::de::DeserializeOwned>(bytes: &[u8]) -> Result<T, ClientError> {
    if bytes.len() > MAX_RESPONSE_BYTES {
        return Err(ClientError::ResponseTooLarge);
    }
    // Do not carry serde errors or input values into a public diagnostic.
    serde_json::from_slice(bytes).map_err(|_| ClientError::InvalidResponse)
}

/// A read-only client projection. Unknown gateway fields are ignored just as
/// they are by the existing Swift decoder; they cannot enter the output model.
#[derive(Clone, Serialize, PartialEq)]
pub struct PersonalUsage {
    schema_id: String,
    schema_version: i64,
    month: String,
    requests: i64,
    denied_requests: i64,
    rate_limited_requests: i64,
    input_tokens: i64,
    output_tokens: i64,
    cost_usd: f64,
    cost_basis: String,
    coverage: String,
    redactions: i64,
}

#[derive(Deserialize)]
struct UsageWire {
    schema_id: String,
    #[serde(deserialize_with = "swift_integer")]
    schema_version: i64,
    month: String,
    #[serde(deserialize_with = "swift_integer")]
    requests: i64,
    #[serde(deserialize_with = "swift_integer")]
    denied_requests: i64,
    #[serde(deserialize_with = "swift_integer")]
    rate_limited_requests: i64,
    #[serde(deserialize_with = "swift_integer")]
    input_tokens: i64,
    #[serde(deserialize_with = "swift_integer")]
    output_tokens: i64,
    cost_usd: f64,
    cost_basis: String,
    coverage: String,
    #[serde(deserialize_with = "swift_integer")]
    redactions: i64,
}

impl PersonalUsage {
    pub fn from_json(bytes: &[u8]) -> Result<Self, ClientError> {
        let value: UsageWire = decode(bytes)?;
        if value.schema_id != "hormuz.gateway-usage-summary"
            || value.schema_version != 1
            || value.month != "current"
            || [
                value.requests,
                value.denied_requests,
                value.rate_limited_requests,
                value.input_tokens,
                value.output_tokens,
                value.redactions,
            ]
            .iter()
            .any(|n| *n < 0)
            || !value.cost_usd.is_finite()
            || value.cost_usd < 0.0
            || value.cost_basis != "configured_rate_card_estimate"
            || value.coverage != "gateway_captured_requests_only"
        {
            return Err(ClientError::InvalidResponse);
        }
        Ok(Self {
            schema_id: value.schema_id,
            schema_version: value.schema_version,
            month: value.month,
            requests: value.requests,
            denied_requests: value.denied_requests,
            rate_limited_requests: value.rate_limited_requests,
            input_tokens: value.input_tokens,
            output_tokens: value.output_tokens,
            cost_usd: value.cost_usd,
            cost_basis: value.cost_basis,
            coverage: value.coverage,
            redactions: value.redactions,
        })
    }

    pub fn requests(&self) -> i64 {
        self.requests
    }
    pub fn input_tokens(&self) -> i64 {
        self.input_tokens
    }
    pub fn output_tokens(&self) -> i64 {
        self.output_tokens
    }
    /// Existing display estimate only. Not a decimal accounting or billing value.
    pub fn cost_usd(&self) -> f64 {
        self.cost_usd
    }
    pub fn cost_basis(&self) -> &str {
        &self.cost_basis
    }
    pub fn coverage(&self) -> &str {
        &self.coverage
    }
}

#[derive(Clone, Serialize, PartialEq, Eq)]
pub struct GatewayIdentity {
    schema_id: String,
    schema_version: i64,
    actor_id: String,
    actor_name: String,
    team_id: String,
    team_name: String,
    organization_id: String,
    identity_type: String,
    allowed_clients: Vec<String>,
    authentication_source: String,
}

#[derive(Deserialize)]
struct IdentityWire {
    schema_id: String,
    #[serde(deserialize_with = "swift_integer")]
    schema_version: i64,
    actor_id: String,
    actor_name: String,
    team_id: String,
    team_name: String,
    organization_id: String,
    identity_type: String,
    allowed_clients: Vec<String>,
    authentication_source: String,
}

impl GatewayIdentity {
    /// Expected scope comes from a validated saved profile, never this response.
    pub fn from_json(
        bytes: &[u8],
        organization: &str,
        client: AIClient,
    ) -> Result<Self, ClientError> {
        let value: IdentityWire = decode(bytes)?;
        if value.schema_id != "hormuz.gateway-identity"
            || value.schema_version != 1
            || value.organization_id != organization
            || !value
                .allowed_clients
                .iter()
                .any(|allowed| allowed == client.as_str())
            || value.identity_type != "human"
            || !value.authentication_source.starts_with("session:")
            || value.actor_id.is_empty()
            || value.actor_name.is_empty()
            || value.actor_name.len() > 1024
            || value.team_name.len() > 1024
        {
            return Err(ClientError::IdentityMismatch);
        }
        Ok(Self {
            schema_id: value.schema_id,
            schema_version: value.schema_version,
            actor_id: value.actor_id,
            actor_name: value.actor_name,
            team_id: value.team_id,
            team_name: value.team_name,
            organization_id: value.organization_id,
            identity_type: value.identity_type,
            allowed_clients: value.allowed_clients,
            authentication_source: value.authentication_source,
        })
    }

    pub fn actor_id(&self) -> &str {
        &self.actor_id
    }
    pub fn actor_name(&self) -> &str {
        &self.actor_name
    }
    pub fn organization_id(&self) -> &str {
        &self.organization_id
    }
}
