//! Initial native-client contract slice for issue #330.
//!
//! These are validated, credential-free projections of the existing gateway v1
//! responses, matching the Mac client. They are not server-schema validators.
//! No network runtime, secure store, helper process or tokenizer is initialized.

#![forbid(unsafe_code)]

use serde::de;
use serde::{Deserialize, Deserializer, Serialize};

/// Matches the existing Mac transport bound; transport must also bound reads.
pub const MAX_RESPONSE_BYTES: usize = 128 * 1024;

mod error;
mod profile;
mod settings;
mod status;

pub use error::ClientError;
pub use profile::{normalize_gateway, ConnectionProfile, GatewaySetup};
pub use settings::{
    ContextOptimizationPreference, ContextOptimizationStatus, MAX_CONTEXT_SETTINGS_BYTES,
};
pub use status::{ConnectionStatus, ReadingStatus, SessionState, UsageReading};

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

// Preserve integral decimal/exponent values without passing through f64.
// A display count must never be silently rounded. The gateway emits integers;
// integral decimal spellings support the existing client's compatibility case.
fn exact_integer<'de, D: Deserializer<'de>>(decoder: D) -> Result<i64, D::Error> {
    let raw = Box::<serde_json::value::RawValue>::deserialize(decoder)?;
    let literal = raw.get();
    // RawValue validates JSON syntax but permits all JSON types. Check the
    // primitive before interpreting its digits; objects/strings/bools are not numbers.
    if !literal
        .as_bytes()
        .first()
        .is_some_and(|byte| byte.is_ascii_digit() || *byte == b'-')
    {
        return Err(de::Error::custom("expected an integral JSON number"));
    }
    parse_exact_integer(literal)
        .ok_or_else(|| de::Error::custom("integer out of range or fractional"))
}

fn parse_exact_integer(value: &str) -> Option<i64> {
    let (negative, unsigned) = match value.strip_prefix('-') {
        Some(unsigned) => (true, unsigned),
        None => (false, value),
    };
    let (mantissa, exponent) = unsigned.split_once(['e', 'E']).unwrap_or((unsigned, "0"));
    let fraction_len = mantissa.split_once('.').map_or(0, |(_, tail)| tail.len());
    let digits: String = mantissa.chars().filter(|c| *c != '.').collect();
    let digits = digits.trim_start_matches('0');
    if digits.is_empty() {
        return Some(0);
    }
    // Lexical validity is enforced by serde_json. Limit arithmetic and work even
    // for extremely long exponent strings inside the bounded response body.
    let scale = exponent
        .parse::<i64>()
        .ok()?
        .checked_sub(fraction_len as i64)?;
    let (digits, padding) = if scale < 0 {
        let trim = usize::try_from(scale.checked_neg()?).ok()?;
        if trim > digits.len()
            || !digits.as_bytes()[digits.len() - trim..]
                .iter()
                .all(|c| *c == b'0')
        {
            return None;
        }
        (&digits[..digits.len() - trim], 0)
    } else {
        (digits, usize::try_from(scale).ok()?)
    };
    if digits.len().checked_add(padding)? > 19 {
        return None;
    }
    digits
        .bytes()
        .chain(std::iter::repeat_n(b'0', padding))
        .try_fold(0_i64, |acc, digit| {
            let acc = acc.checked_mul(10)?;
            let digit = i64::from(digit - b'0');
            if negative {
                acc.checked_sub(digit)
            } else {
                acc.checked_add(digit)
            }
        })
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
    #[serde(deserialize_with = "exact_integer")]
    schema_version: i64,
    month: String,
    #[serde(deserialize_with = "exact_integer")]
    requests: i64,
    #[serde(deserialize_with = "exact_integer")]
    denied_requests: i64,
    #[serde(deserialize_with = "exact_integer")]
    rate_limited_requests: i64,
    #[serde(deserialize_with = "exact_integer")]
    input_tokens: i64,
    #[serde(deserialize_with = "exact_integer")]
    output_tokens: i64,
    cost_usd: f64,
    cost_basis: String,
    coverage: String,
    #[serde(deserialize_with = "exact_integer")]
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
    #[serde(deserialize_with = "exact_integer")]
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

    /// Names and display labels may change; a retained snapshot cannot cross
    /// the authenticated actor, organization, team or session boundary.
    pub fn same_session(&self, other: &Self) -> bool {
        self.actor_id == other.actor_id
            && self.organization_id == other.organization_id
            && self.team_id == other.team_id
            && self.authentication_source == other.authentication_source
    }
}
