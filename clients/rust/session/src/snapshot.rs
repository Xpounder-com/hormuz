//! Immutable display snapshots and a single coalesced change slot. No timers,
//! local accounting, callbacks or subscription worker are created here.
use hormuz_client_core::{
    ClientError, ConnectionProfile, GatewayIdentity, PersonalUsage, ReadingStatus, UsageReading,
};
use serde::{Deserialize, Serialize};
use std::sync::{Arc, Mutex};
use zeroize::Zeroizing;

pub(crate) fn personal_usage(bytes: &[u8]) -> Result<PersonalUsage, ClientError> {
    // V1 is personal by contract and omits scope. Reject a conflicting extension.
    fn current_actor() -> String {
        "current_actor".into()
    }
    #[derive(Deserialize)]
    struct Scope {
        #[serde(default = "current_actor")]
        scope: String,
    }
    let usage = PersonalUsage::from_json(bytes)?;
    let scope: Scope = serde_json::from_slice(bytes).map_err(|_| ClientError::InvalidResponse)?;
    if scope.scope != "current_actor" {
        return Err(ClientError::InvalidResponse);
    }
    Ok(usage)
}

#[derive(Clone, Serialize, PartialEq)]
pub struct UsageSnapshot {
    scope: &'static str,
    identity: Option<GatewayIdentity>,
    reading: UsageReading,
}
impl UsageSnapshot {
    fn missing(status: ReadingStatus) -> Self {
        Self {
            scope: "current_actor",
            identity: None,
            reading: UsageReading::new(status, None, None).expect("valid missing reading"),
        }
    }
    pub fn identity(&self) -> Option<&GatewayIdentity> {
        self.identity.as_ref()
    }
    pub fn reading(&self) -> &UsageReading {
        &self.reading
    }
    pub fn scope(&self) -> &'static str {
        self.scope
    }
    /// The sum can exceed i64::MAX. Preserve exact arithmetic for native views.
    pub fn total_tokens(&self) -> Option<u64> {
        self.reading
            .usage()
            .map(|u| u.input_tokens() as u64 + u.output_tokens() as u64)
    }
}

pub(crate) struct Ticket {
    connection: Arc<()>,
    attempt: Arc<()>,
}
struct State {
    profile: Option<ConnectionProfile>,
    credential: Option<Zeroizing<String>>,
    identity_binding: Option<GatewayIdentity>,
    rejected_identity: bool,
    connection: Arc<()>,
    attempt: Arc<()>,
    snapshot: Arc<UsageSnapshot>,
    change: Option<Arc<UsageSnapshot>>,
}
impl State {
    fn matches(&self, ticket: &Ticket) -> bool {
        Arc::ptr_eq(&self.connection, &ticket.connection)
            && Arc::ptr_eq(&self.attempt, &ticket.attempt)
    }
    fn publish(&mut self, value: UsageSnapshot) {
        if *self.snapshot != value {
            self.snapshot = Arc::new(value);
            // A bounded one-slot queue coalesces changes when the UI is hidden.
            self.change = Some(self.snapshot.clone());
        }
    }
}
pub(crate) struct Snapshots(Mutex<State>);
impl Default for Snapshots {
    fn default() -> Self {
        let snapshot = Arc::new(UsageSnapshot::missing(ReadingStatus::Offline));
        Self(Mutex::new(State {
            profile: None,
            credential: None,
            identity_binding: None,
            rejected_identity: false,
            connection: Arc::new(()),
            attempt: Arc::new(()),
            change: Some(snapshot.clone()),
            snapshot,
        }))
    }
}
impl Snapshots {
    pub fn snapshot(&self) -> Arc<UsageSnapshot> {
        self.0.lock().unwrap().snapshot.clone()
    }
    pub fn take_change(&self) -> Option<Arc<UsageSnapshot>> {
        self.0.lock().unwrap().change.take()
    }
    pub fn begin(&self, profile: &ConnectionProfile) -> Ticket {
        let mut state = self.0.lock().unwrap();
        if state.profile.as_ref() != Some(profile) {
            state.connection = Arc::new(());
            state.profile = Some(profile.clone());
            state.credential = None;
            state.identity_binding = None;
            state.rejected_identity = false;
            state.publish(UsageSnapshot::missing(ReadingStatus::Offline));
        }
        state.attempt = Arc::new(());
        Ticket {
            connection: state.connection.clone(),
            attempt: state.attempt.clone(),
        }
    }
    pub fn invalidate(&self, status: ReadingStatus) {
        let mut state = self.0.lock().unwrap();
        state.connection = Arc::new(());
        state.credential = None;
        state.identity_binding = None;
        state.rejected_identity = false;
        state.publish(UsageSnapshot::missing(status));
    }
    pub fn current(&self, ticket: &Ticket) -> bool {
        self.0.lock().unwrap().matches(ticket)
    }
    pub fn bind_credential(&self, profile: &ConnectionProfile, credential: &str) {
        let mut state = self.0.lock().unwrap();
        if state.profile.as_ref() == Some(profile)
            && state.credential.as_deref().map(|s| s.as_str()) != Some(credential)
        {
            // An external helper may have signed in a different person under
            // this same profile. Drop cached data before any fallible request.
            state.credential = Some(Zeroizing::new(credential.to_owned()));
            state.identity_binding = None;
            state.rejected_identity = false;
            state.publish(UsageSnapshot::missing(ReadingStatus::Offline));
        }
    }
    pub fn rotate_credential(&self, old: &str, replacement: &str) {
        let mut state = self.0.lock().unwrap();
        if state.credential.as_deref().map(|s| s.as_str()) == Some(old) {
            state.credential = Some(Zeroizing::new(replacement.to_owned()));
        }
    }
    pub fn verify_identity(
        &self,
        ticket: &Ticket,
        identity: &GatewayIdentity,
    ) -> Result<(), ClientError> {
        let mut state = self.0.lock().unwrap();
        if !state.matches(ticket) {
            return Err(ClientError::ConfigurationChanged);
        }
        if state.rejected_identity
            || state
                .identity_binding
                .as_ref()
                .is_some_and(|old| !old.same_session(identity))
        {
            state.rejected_identity = true;
            state.publish(UsageSnapshot::missing(ReadingStatus::NeedsAuthentication));
            return Err(ClientError::IdentityMismatch);
        }
        state.identity_binding = Some(identity.clone());
        Ok(())
    }
    pub fn success(
        &self,
        ticket: &Ticket,
        identity: GatewayIdentity,
        usage: PersonalUsage,
        checked: f64,
    ) -> Result<(), ClientError> {
        let reading = UsageReading::new(ReadingStatus::Current, Some(usage), Some(checked))?;
        let mut state = self.0.lock().unwrap();
        if state.matches(ticket) {
            state.publish(UsageSnapshot {
                scope: "current_actor",
                identity: Some(identity),
                reading,
            });
        }
        Ok(())
    }
    pub fn failure(&self, ticket: &Ticket, error: ClientError) {
        let mut state = self.0.lock().unwrap();
        if !state.matches(ticket) {
            return;
        }
        if matches!(
            error,
            ClientError::LoginRequired
                | ClientError::LoginRejected
                | ClientError::LogoutPending
                | ClientError::RefreshInterrupted
                | ClientError::IdentityMismatch
                | ClientError::InvalidProfile
                | ClientError::ConfigurationChanged
                | ClientError::SecureStoreUnavailable
                | ClientError::StorageUnavailable
                | ClientError::UnsafeStorage
        ) {
            state.publish(UsageSnapshot::missing(ReadingStatus::NeedsAuthentication));
            return;
        }
        let old = &state.snapshot;
        let status = if matches!(
            error,
            ClientError::InvalidResponse
                | ClientError::ResponseTooLarge
                | ClientError::UnexpectedRedirect
        ) {
            if old.reading.usage().is_some() {
                ReadingStatus::Stale
            } else {
                ReadingStatus::Offline
            }
        } else {
            ReadingStatus::Offline
        };
        let value = UsageSnapshot {
            scope: "current_actor",
            identity: old.identity.clone(),
            reading: UsageReading::new(
                status,
                old.reading.usage().cloned(),
                old.reading.checked_at_epoch_seconds(),
            )
            .expect("retained valid reading"),
        };
        state.publish(value);
    }
}

#[cfg(test)]
mod tests;
