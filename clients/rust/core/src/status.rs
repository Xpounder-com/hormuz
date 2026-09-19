use crate::{ClientError, ConnectionProfile, PersonalUsage};
use serde::{Deserialize, Serialize};

#[derive(Clone, Copy, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub enum SessionState {
    Active,
    RefreshPending,
    RevocationPending,
}

/// A display projection only. No access/refresh credentials or refresh actions.
#[derive(Clone, Serialize)]
pub struct ConnectionStatus {
    profile: Option<ConnectionProfile>,
    session_state: Option<SessionState>,
    expires_at_epoch_seconds: Option<f64>,
}

impl ConnectionStatus {
    pub fn new(
        profile: Option<ConnectionProfile>,
        session_state: Option<SessionState>,
        expires_at_epoch_seconds: Option<f64>,
    ) -> Result<Self, ClientError> {
        if expires_at_epoch_seconds.is_some_and(|t| !t.is_finite() || t < 0.0)
            || (session_state.is_some() && profile.is_none())
            || (session_state.is_none() && expires_at_epoch_seconds.is_some())
        {
            return Err(ClientError::InvalidResponse);
        }
        Ok(Self {
            profile,
            session_state,
            expires_at_epoch_seconds,
        })
    }
    // Retained revocation/refresh records remain sessions, not usable credentials.
    pub fn has_session(&self) -> bool {
        self.session_state.is_some()
    }
    pub fn profile(&self) -> Option<&ConnectionProfile> {
        self.profile.as_ref()
    }
    pub fn session_state(&self) -> Option<SessionState> {
        self.session_state
    }
    pub fn expires_at_epoch_seconds(&self) -> Option<f64> {
        self.expires_at_epoch_seconds
    }
}

#[derive(Clone, Copy, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub enum ReadingStatus {
    Current,
    Stale,
    Offline,
    NeedsAuthentication,
}

impl ReadingStatus {
    pub fn is_stale(self) -> bool {
        self == Self::Stale
    }
    pub fn is_unavailable(self) -> bool {
        matches!(self, Self::Offline | Self::NeedsAuthentication)
    }
}

/// Missing readings stay absent. A retained reading always carries its original
/// check time. Scheduling, age thresholds and response ordering belong to #336/337.
#[derive(Clone, Serialize)]
pub struct UsageReading {
    status: ReadingStatus,
    usage: Option<PersonalUsage>,
    checked_at_epoch_seconds: Option<f64>,
}

impl UsageReading {
    pub fn new(
        status: ReadingStatus,
        usage: Option<PersonalUsage>,
        checked_at_epoch_seconds: Option<f64>,
    ) -> Result<Self, ClientError> {
        if usage.is_some() != checked_at_epoch_seconds.is_some()
            || (status == ReadingStatus::Current && usage.is_none())
            || checked_at_epoch_seconds.is_some_and(|t| !t.is_finite() || t < 0.0)
        {
            return Err(ClientError::InvalidResponse);
        }
        Ok(Self {
            status,
            usage,
            checked_at_epoch_seconds,
        })
    }
    pub fn status(&self) -> ReadingStatus {
        self.status
    }
    pub fn usage(&self) -> Option<&PersonalUsage> {
        self.usage.as_ref()
    }
    pub fn checked_at_epoch_seconds(&self) -> Option<f64> {
        self.checked_at_epoch_seconds
    }
}
