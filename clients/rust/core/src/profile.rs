use crate::{AIClient, ClientError, MAX_RESPONSE_BYTES};
use serde::{Deserialize, Serialize};
use unicode_general_category::{get_general_category, GeneralCategory};

#[derive(Clone, Copy, Debug, Default, Deserialize, Serialize, PartialEq, Eq)]
pub enum GatewaySetup {
    #[serde(rename = "openai-pilot")]
    OpenAIPilot,
    #[default]
    #[serde(rename = "custom")]
    Custom,
}

/// Validated, non-secret profile. Unknown input members are never retained.
/// The existing Mac persistence format is characterized, not migrated here.
#[derive(Clone, Serialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct ConnectionProfile {
    id: String,
    gateway: String,
    organization: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    issuer: Option<String>,
    client: AIClient,
    model: String,
    #[serde(rename = "allowLoopbackHTTP")]
    allow_loopback_http: bool,
    setup: GatewaySetup,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
struct ProfileWire {
    id: String,
    gateway: String,
    organization: String,
    issuer: Option<String>,
    client: AIClient,
    model: String,
    #[serde(rename = "allowLoopbackHTTP")]
    allow_loopback_http: bool,
    // Only a missing setup is legacy custom. Null/unknown values are invalid.
    #[serde(default)]
    setup: GatewaySetup,
}

impl ConnectionProfile {
    pub fn from_json(bytes: &[u8]) -> Result<Self, ClientError> {
        if bytes.len() > MAX_RESPONSE_BYTES {
            return Err(ClientError::ResponseTooLarge);
        }
        let wire: ProfileWire =
            serde_json::from_slice(bytes).map_err(|_| ClientError::InvalidProfile)?;
        let gateway = normalize_gateway(&wire.gateway, wire.allow_loopback_http)?;
        let uuid_valid = wire.id.len() == 36
            && wire.id.bytes().enumerate().all(|(i, b)| {
                if matches!(i, 8 | 13 | 18 | 23) {
                    b == b'-'
                } else {
                    b.is_ascii_hexdigit()
                }
            });
        let model_valid = (1..=128).contains(&wire.model.len())
            && wire.model.as_bytes()[0].is_ascii_alphanumeric()
            && wire
                .model
                .bytes()
                .all(|b| b.is_ascii_alphanumeric() || b"._:-".contains(&b));
        if !uuid_valid
            || wire.organization.is_empty()
            || !safe_text(&wire.organization, 200)
            || !model_valid
            || wire.issuer.as_ref().is_some_and(|s| !safe_text(s, 2048))
            || (wire.setup == GatewaySetup::OpenAIPilot
                && (wire.client != AIClient::Codex
                    || !matches!(wire.model.as_str(), "openai-primary" | "openai-secondary")
                    || wire.allow_loopback_http
                    || !gateway.starts_with("https://")))
        {
            return Err(ClientError::InvalidProfile);
        }
        Ok(Self {
            id: wire.id.to_ascii_uppercase(),
            gateway,
            organization: wire.organization,
            issuer: wire.issuer.filter(|s| !s.is_empty()),
            client: wire.client,
            model: wire.model,
            allow_loopback_http: wire.allow_loopback_http,
            setup: wire.setup,
        })
    }

    pub fn key(&self) -> String {
        self.id.to_ascii_lowercase()
    }
    pub fn gateway(&self) -> &str {
        &self.gateway
    }
    pub fn organization(&self) -> &str {
        &self.organization
    }
    pub fn issuer(&self) -> Option<&str> {
        self.issuer.as_deref()
    }
    pub fn client(&self) -> AIClient {
        self.client
    }
    pub fn model(&self) -> &str {
        &self.model
    }
    pub fn allow_loopback_http(&self) -> bool {
        self.allow_loopback_http
    }
    pub fn setup(&self) -> GatewaySetup {
        self.setup
    }
}

fn safe_text(value: &str, maximum: usize) -> bool {
    value.len() <= maximum
        && value.trim() == value
        && !value.chars().any(|c| {
            matches!(
                get_general_category(c),
                GeneralCategory::Control | GeneralCategory::Format
            )
        })
}

/// Only HTTP(S) origins, with an explicit opt-in for literal loopback HTTP.
/// Reject parser rewrites of the host; callers can enter canonical ASCII/IDNA
/// names. This deliberately narrows Foundation's permissive host parsing.
pub fn normalize_gateway(value: &str, allow_loopback_http: bool) -> Result<String, ClientError> {
    let invalid = ClientError::InvalidGateway;
    if !safe_text(value, 2048) || value.contains(['\\', '?', '#']) {
        return Err(invalid);
    }
    let (scheme, rest) = value.split_once("://").ok_or(invalid)?;
    let scheme = scheme.to_ascii_lowercase();
    if !matches!(scheme.as_str(), "https" | "http") {
        return Err(invalid);
    }
    let (authority, path) = rest.split_once('/').unwrap_or((rest, ""));
    if authority.is_empty() || authority.contains('@') || !path.is_empty() {
        return Err(invalid);
    }
    let (host, port) = if authority.starts_with('[') {
        let end = authority.find(']').ok_or(invalid)?;
        let tail = &authority[end + 1..];
        (
            &authority[..=end],
            if tail.is_empty() {
                None
            } else {
                Some(tail.strip_prefix(':').ok_or(invalid)?)
            },
        )
    } else {
        authority
            .rsplit_once(':')
            .map_or((authority, None), |(h, p)| (h, Some(p)))
    };
    if let Some(port) = port {
        if port.is_empty()
            || !port.bytes().all(|b| b.is_ascii_digit())
            || port.parse::<u16>().ok().filter(|p| *p > 0).is_none()
        {
            return Err(invalid);
        }
    }
    let host = host.to_ascii_lowercase();
    if scheme == "http"
        && (!allow_loopback_http || !matches!(host.as_str(), "127.0.0.1" | "localhost" | "[::1]"))
    {
        return Err(ClientError::InsecureGateway);
    }
    let parsed = url::Url::parse(value).map_err(|_| invalid)?;
    if !host.is_ascii() || parsed.host_str() != Some(host.as_str()) {
        return Err(invalid);
    }
    // Keep an explicit default port, matching Foundation. URL's serializer
    // would remove it. No credentials, query, fragment or path can get here.
    Ok(format!(
        "{scheme}://{host}{}",
        port.map_or(String::new(), |p| format!(":{p}"))
    ))
}
