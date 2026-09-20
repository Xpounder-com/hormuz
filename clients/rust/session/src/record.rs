use hormuz_client_core::{ClientError, ConnectionProfile, SessionState};
use hormuz_client_platform::SecretRecord;
use serde::{Deserialize, Serialize};
use std::fmt;
use time::{format_description::well_known::Rfc3339, OffsetDateTime};
use zeroize::Zeroizing;

pub(crate) const FOUNDATION_EPOCH: f64 = 978_307_200.0;

/// Existing Swift file-Keychain codec, not a new envelope. Dates are seconds
/// since 2001, not Unix seconds or the gateway's RFC3339 strings.
pub struct SessionRecord {
    pub(crate) profile: ConnectionProfile,
    pub(crate) access: Zeroizing<String>,
    pub(crate) refresh: Zeroizing<String>,
    pub(crate) access_expires: f64,
    pub(crate) session_expires: f64,
    pub(crate) state: SessionState,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
struct StoredWire {
    profile: Box<serde_json::value::RawValue>,
    access_token: Zeroizing<String>,
    refresh_token: Zeroizing<String>,
    access_expires_at: f64,
    session_expires_at: f64,
    state: SessionState,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct StoredOutput<'a> {
    profile: &'a ConnectionProfile,
    access_token: &'a str,
    refresh_token: &'a str,
    access_expires_at: f64,
    session_expires_at: f64,
    state: SessionState,
}

impl fmt::Debug for SessionRecord {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str("SessionRecord(<redacted>)")
    }
}

impl SessionRecord {
    pub fn from_secret(secret: &SecretRecord) -> Result<Self, ClientError> {
        let wire: StoredWire = serde_json::from_slice(secret.expose())
            .map_err(|_| ClientError::SecureStoreUnavailable)?;
        let value = Self {
            profile: ConnectionProfile::from_json(wire.profile.get().as_bytes())?,
            access: wire.access_token,
            refresh: wire.refresh_token,
            access_expires: wire.access_expires_at,
            session_expires: wire.session_expires_at,
            state: wire.state,
        };
        value.validate()?;
        Ok(value)
    }

    pub fn to_secret(&self) -> Result<SecretRecord, ClientError> {
        self.validate()?;
        SecretRecord::new(self.encode()?).map_err(|_| ClientError::SecureStoreUnavailable)
    }

    fn encode(&self) -> Result<Vec<u8>, ClientError> {
        serde_json::to_vec(&StoredOutput {
            profile: &self.profile,
            access_token: &self.access,
            refresh_token: &self.refresh,
            access_expires_at: self.access_expires,
            session_expires_at: self.session_expires,
            state: self.state,
        })
        .map_err(|_| ClientError::SecureStoreUnavailable)
    }

    fn validate(&self) -> Result<(), ClientError> {
        if !valid_token(&self.access, "hox_a_")
            || !valid_token(&self.refresh, "hox_r_")
            || !self.access_expires.is_finite()
            || !self.session_expires.is_finite()
            || self.access_expires > self.session_expires
        {
            return Err(ClientError::InvalidResponse);
        }
        Ok(())
    }

    pub fn profile(&self) -> &ConnectionProfile {
        &self.profile
    }
    pub fn state(&self) -> SessionState {
        self.state
    }
    pub fn session_expires_at(&self) -> f64 {
        self.session_expires + FOUNDATION_EPOCH
    }

    /// Both credential lengths are fixed. Reserve all states and more than the
    /// maximum JSON width of two finite f64 dates before server mutation.
    pub(crate) fn preflight(
        profile: &ConnectionProfile,
        maximum: usize,
    ) -> Result<(), ClientError> {
        let sample = Self {
            profile: profile.clone(),
            access: Zeroizing::new(format!("hox_a_{}", "a".repeat(43))),
            refresh: Zeroizing::new(format!("hox_r_{}", "r".repeat(43))),
            access_expires: 0.0,
            session_expires: 0.0,
            state: SessionState::RevocationPending,
        };
        let bytes = Zeroizing::new(sample.encode()?);
        if bytes.len().saturating_add(64) > maximum.min(32_767) {
            return Err(ClientError::SecureStoreUnavailable);
        }
        Ok(())
    }
}

pub(crate) fn valid_token(value: &str, prefix: &str) -> bool {
    value.len() == prefix.len() + 43
        && value.starts_with(prefix)
        && value[prefix.len()..]
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || b"_-".contains(&b))
}

#[derive(Deserialize)]
pub(crate) struct CredentialPair {
    access_token: Zeroizing<String>,
    refresh_token: Zeroizing<String>,
    token_type: String,
    access_expires_at: String,
    session_expires_at: String,
}

pub(crate) fn timestamp(raw: &str) -> Result<f64, ClientError> {
    let date = OffsetDateTime::parse(raw, &Rfc3339).map_err(|_| ClientError::InvalidResponse)?;
    Ok(date.unix_timestamp() as f64 + f64::from(date.nanosecond()) / 1_000_000_000.0)
}

impl CredentialPair {
    pub(crate) fn record(
        self,
        profile: &ConnectionProfile,
        now: f64,
    ) -> Result<SessionRecord, ClientError> {
        let access = timestamp(&self.access_expires_at)?;
        let session = timestamp(&self.session_expires_at)?;
        if !now.is_finite()
            || self.token_type != "Bearer"
            || access <= now
            || session <= now
            || access - now > 960.0
            || session - now > 43_260.0
        {
            return Err(ClientError::InvalidResponse);
        }
        let record = SessionRecord {
            profile: profile.clone(),
            access: self.access_token,
            refresh: self.refresh_token,
            access_expires: access - FOUNDATION_EPOCH,
            session_expires: session - FOUNDATION_EPOCH,
            state: SessionState::Active,
        };
        record.validate()?;
        Ok(record)
    }
}
