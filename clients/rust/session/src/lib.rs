//! Session transactions for native workers, not the UI thread. No resident
//! worker, scheduler, browser process, or network runtime is created here.
#![forbid(unsafe_code)]

mod io;
mod record;
mod snapshot;
use base64::{engine::general_purpose::URL_SAFE_NO_PAD, Engine};
use hormuz_client_core::{
    ClientError, ConnectionProfile, ConnectionStatus, GatewayIdentity, ReadingStatus, SessionState,
};
use hormuz_client_platform::{
    BrowserOpener, CredentialStore, PlatformError, PrivateFiles, RefreshCoordinator,
};
pub use io::{Clock, NativeTransport, Operation, Reply, SessionTransport, SystemClock};
pub use record::SessionRecord;
use record::{timestamp, CredentialPair, FOUNDATION_EPOCH};
use serde::Deserialize;
pub use snapshot::UsageSnapshot;
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::time::Duration;
use zeroize::Zeroizing;

const LOCK_BUDGET: Duration = Duration::from_secs(10);
const POLL_BUDGET: Duration = Duration::from_secs(900);
const TICK: Duration = Duration::from_millis(50);

/// Redacted explicit credential handoff for a supervised helper. Never a display
/// snapshot. Previously issued copies cannot be recalled by local sign-out.
pub struct AccessCredential(Zeroizing<String>);
impl AccessCredential {
    pub fn expose(&self) -> &str {
        &self.0
    }
}
impl std::fmt::Debug for AccessCredential {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str("AccessCredential(<redacted>)")
    }
}

pub struct SessionController<C, S, T = NativeTransport, K = SystemClock> {
    coordinator: C,
    store: S,
    transport: T,
    clock: K,
    // Reject fresh local credential requests as soon as sign-out is requested,
    // including when lock acquisition or durable persistence subsequently fails.
    disabled: AtomicBool,
    generation: AtomicU64,
    snapshots: snapshot::Snapshots,
}

impl<C: RefreshCoordinator, S: CredentialStore, T: SessionTransport, K: Clock>
    SessionController<C, S, T, K>
{
    pub fn new(coordinator: C, store: S, transport: T, clock: K) -> Self {
        Self {
            coordinator,
            store,
            transport,
            clock,
            disabled: AtomicBool::new(false),
            generation: AtomicU64::new(0),
            snapshots: snapshot::Snapshots::default(),
        }
    }

    fn lock(&self, operation: &Operation) -> Result<C::Guard, ClientError> {
        let start = self.clock.elapsed();
        loop {
            operation.check()?;
            match self.coordinator.try_acquire() {
                Ok(guard) => return Ok(guard),
                Err(PlatformError::Busy)
                    if self.clock.elapsed().saturating_sub(start) < LOCK_BUDGET =>
                {
                    self.clock.sleep(TICK)
                }
                Err(error) => return Err(platform_error(error)),
            }
        }
    }
    fn load(&self) -> Result<Option<SessionRecord>, ClientError> {
        self.store
            .load()
            .map_err(platform_error)?
            .as_ref()
            .map(SessionRecord::from_secret)
            .transpose()
    }
    fn save(&self, record: &SessionRecord) -> Result<(), ClientError> {
        let bytes = record.to_secret()?;
        if bytes.expose().len() > self.store.maximum_record_bytes() {
            return Err(ClientError::SecureStoreUnavailable);
        }
        self.store.save(&bytes).map_err(platform_error)
    }

    pub fn status(&self, operation: &Operation) -> Result<ConnectionStatus, ClientError> {
        let guard = self.lock(operation)?;
        if let Some(record) = self.load()? {
            return ConnectionStatus::new(
                Some(record.profile.clone()),
                Some(record.state),
                Some(record.session_expires_at()),
            );
        }
        let profile = guard
            .read("profile.json")
            .map_err(platform_error)?
            .as_deref()
            .map(ConnectionProfile::from_json)
            .transpose()?;
        ConnectionStatus::new(profile, None, None)
    }

    pub fn sign_in(
        &self,
        profile: &ConnectionProfile,
        browser: &impl BrowserOpener,
        operation: &Operation,
    ) -> Result<(), ClientError> {
        let generation = self.generation.load(Ordering::SeqCst);
        let check = || {
            operation.check()?;
            if self.generation.load(Ordering::SeqCst) != generation {
                Err(ClientError::LogoutPending)
            } else {
                Ok(())
            }
        };
        let guard = self.lock(operation)?;
        check()?;
        if self.load()?.is_some() {
            return Err(ClientError::AlreadySignedIn);
        }
        self.snapshots
            .invalidate(ReadingStatus::NeedsAuthentication);
        SessionRecord::preflight(profile, self.store.maximum_record_bytes())?;
        let prior = guard.read("profile.json").map_err(platform_error)?;
        let profile_bytes = serde_json::to_vec(profile).map_err(|_| ClientError::InvalidProfile)?;
        guard
            .write("profile.json", &profile_bytes, prior.as_deref())
            .map_err(platform_error)?;
        let mut random = Zeroizing::new([0u8; 48]);
        getrandom::fill(random.as_mut()).map_err(|_| ClientError::SecureStoreUnavailable)?;
        let secret = Zeroizing::new(URL_SAFE_NO_PAD.encode(random.as_ref()));
        let mut fields = vec![
            ("client", profile.client().as_str()),
            ("organization_id", profile.organization()),
            ("enrollment_secret", &secret),
        ];
        if let Some(issuer) = profile.issuer() {
            fields.push(("issuer", issuer));
        }
        let reply = self.post(profile, "/v1/auth/enrollments", &fields, operation)?;
        if reply.status != 201 {
            return Err(ClientError::LoginRejected);
        }
        #[derive(Deserialize)]
        struct Enrollment {
            enrollment_id: String,
            login_url: String,
            expires_at: String,
            poll_interval_seconds: u64,
        }
        let enrollment: Enrollment = reply.decode()?;
        let expires = timestamp(&enrollment.expires_at)?;
        let remaining = expires - self.clock.now();
        if enrollment.enrollment_id.len() != 32
            || !enrollment
                .enrollment_id
                .bytes()
                .all(|b| b.is_ascii_alphanumeric() || b"_-".contains(&b))
            || !(1..=10).contains(&enrollment.poll_interval_seconds)
            || !remaining.is_finite()
            || remaining <= 0.0
            || enrollment.login_url
                != format!(
                    "{}/v1/auth/login?enrollment={}",
                    profile.gateway(),
                    enrollment.enrollment_id
                )
        {
            return Err(ClientError::InvalidResponse);
        }
        let start = self.clock.elapsed();
        let budget = Duration::from_secs_f64(remaining.min(POLL_BUDGET.as_secs_f64()));
        check()?;
        browser
            .open_authentication_url(&enrollment.login_url)
            .map_err(platform_error)?;
        let path = format!("/v1/auth/enrollments/{}/redeem", enrollment.enrollment_id);
        while self.clock.elapsed().saturating_sub(start) < budget {
            check()?;
            let reply = self.post(profile, &path, &[("enrollment_secret", &secret)], operation)?;
            if reply.status == 200 {
                let mut record = reply
                    .decode::<CredentialPair>()?
                    .record(profile, self.clock.now())?;
                // Keep a non-usable intent before verification/commit. This also
                // makes a crash during identity validation recoverable by logout.
                record.state = SessionState::RevocationPending;
                if let Err(error) = self.save(&record) {
                    self.revoke_unsaved(&record);
                    return Err(error);
                }
                let result = (|| {
                    check()?;
                    self.identity(&record, operation)?;
                    check()?;
                    record.state = SessionState::Active;
                    self.save(&record)?;
                    check()?;
                    Ok(())
                })();
                if let Err(error) = result {
                    record.state = SessionState::RevocationPending;
                    if self.save(&record).is_err() {
                        let _ = self.store.delete();
                    }
                    if self.revoke_unsaved(&record) {
                        let _ = self.store.delete();
                    }
                    self.disabled.store(true, Ordering::SeqCst);
                    return Err(error);
                }
                self.disabled.store(false, Ordering::SeqCst);
                if let Err(error) = check() {
                    self.disabled.store(true, Ordering::SeqCst);
                    record.state = SessionState::RevocationPending;
                    if self.save(&record).is_err() {
                        let _ = self.store.delete();
                    }
                    self.revoke_unsaved(&record);
                    return Err(error);
                }
                return Ok(());
            }
            if reply.status != 409 {
                return Err(ClientError::LoginRejected);
            }
            let sleep_start = self.clock.elapsed();
            let pause = Duration::from_secs(enrollment.poll_interval_seconds);
            while self.clock.elapsed().saturating_sub(sleep_start) < pause
                && self.clock.elapsed().saturating_sub(start) < budget
            {
                check()?;
                self.clock.sleep(TICK);
            }
        }
        Err(ClientError::LoginTimedOut)
    }

    pub fn access_credential(
        &self,
        profile: &ConnectionProfile,
        force_refresh: bool,
        operation: &Operation,
    ) -> Result<AccessCredential, ClientError> {
        let result = (|| {
            let guard = self.lock(operation)?;
            let record = self.credential(&guard, profile, force_refresh, operation)?;
            self.check_enabled(operation)?;
            Ok(AccessCredential(record.access))
        })();
        if result
            .as_ref()
            .is_err_and(|error| *error != ClientError::ProfileBusy)
        {
            self.snapshots
                .invalidate(ReadingStatus::NeedsAuthentication);
        }
        result
    }

    fn check_enabled(&self, operation: &Operation) -> Result<(), ClientError> {
        if self.disabled.load(Ordering::SeqCst) {
            return Err(ClientError::LogoutPending);
        }
        operation.check()
    }

    fn credential(
        &self,
        guard: &C::Guard,
        profile: &ConnectionProfile,
        force: bool,
        operation: &Operation,
    ) -> Result<SessionRecord, ClientError> {
        self.check_enabled(operation)?;
        let stored_profile = guard
            .read("profile.json")
            .map_err(platform_error)?
            .ok_or(ClientError::LoginRequired)?;
        let saved_profile = ConnectionProfile::from_json(&stored_profile)?;
        let mut record = self.load()?.ok_or(ClientError::LoginRequired)?;
        if &saved_profile != profile || record.profile != *profile {
            return Err(ClientError::IdentityMismatch);
        }
        self.snapshots.bind_credential(profile, &record.refresh);
        match record.state {
            SessionState::RevocationPending => return Err(ClientError::LogoutPending),
            SessionState::RefreshPending => return Err(ClientError::RefreshInterrupted),
            SessionState::Active => (),
        }
        let now = self.clock.now() - FOUNDATION_EPOCH;
        if !now.is_finite() || record.session_expires <= now {
            return Err(ClientError::LoginRequired);
        }
        if !force && record.access_expires - now > 60.0 {
            return Ok(record);
        }
        SessionRecord::preflight(profile, self.store.maximum_record_bytes())?;
        self.check_enabled(operation)?;
        record.state = SessionState::RefreshPending;
        self.save(&record)?;
        // All failures, including NotSent, preserve the intent. No hidden replay.
        let reply = self
            .post(
                profile,
                "/v1/auth/refresh",
                &[("refresh_token", &record.refresh)],
                operation,
            )
            .map_err(|_| ClientError::RefreshInterrupted)?;
        if reply.status != 200 {
            return Err(ClientError::RefreshInterrupted);
        }
        let updated = reply
            .decode::<CredentialPair>()?
            .record(profile, self.clock.now())?;
        if updated.session_expires != record.session_expires
            || updated.access == record.access
            || updated.refresh == record.refresh
        {
            self.revoke_unsaved(&updated);
            return Err(ClientError::InvalidResponse);
        }
        if let Err(error) = self
            .check_enabled(operation)
            .and_then(|_| self.save(&updated))
        {
            self.revoke_unsaved(&updated);
            return Err(error);
        }
        // Cancellation concurrent with commit must not hand out credentials.
        if let Err(error) = self.check_enabled(operation) {
            let mut suspended = updated;
            suspended.state = SessionState::RevocationPending;
            if self.save(&suspended).is_err() {
                let _ = self.store.delete();
            }
            self.revoke_unsaved(&suspended);
            return Err(error);
        }
        self.snapshots
            .rotate_credential(&record.refresh, &updated.refresh);
        Ok(updated)
    }

    pub fn sign_out(&self, operation: &Operation) -> Result<(), ClientError> {
        self.generation.fetch_add(1, Ordering::SeqCst);
        self.disabled.store(true, Ordering::SeqCst);
        self.snapshots
            .invalidate(ReadingStatus::NeedsAuthentication);
        let _guard = self.lock(operation)?;
        let Some(mut record) = self.load()? else {
            return Ok(());
        };
        record.state = SessionState::RevocationPending;
        self.save(&record)?;
        if !self.revoke(&record, operation) {
            return Err(ClientError::LogoutPending);
        }
        self.store.delete().map_err(platform_error)
    }

    pub fn snapshot(&self) -> std::sync::Arc<UsageSnapshot> {
        self.snapshots.snapshot()
    }

    /// Single-consumer, bounded change delivery. Native UI adapters drain this
    /// after worker completion or local sign-out; equivalent states emit nothing.
    pub fn take_snapshot_change(&self) -> Option<std::sync::Arc<UsageSnapshot>> {
        self.snapshots.take_change()
    }

    /// A relay completion can call this same refresh entry point. No local usage
    /// is added and no scheduler or background polling loop is started here.
    pub fn refresh_snapshot(
        &self,
        profile: &ConnectionProfile,
        operation: &Operation,
    ) -> Result<(), ClientError> {
        let ticket = self.snapshots.begin(profile);
        let result = (|| {
            let guard = self.lock(operation)?;
            if !self.snapshots.current(&ticket) {
                return Err(ClientError::ConfigurationChanged);
            }
            let record = self.credential(&guard, profile, false, operation)?;
            let identity = self.identity(&record, operation)?;
            self.snapshots.verify_identity(&ticket, &identity)?;
            self.check_enabled(operation)?;
            let reply = self
                .transport
                .request(
                    profile,
                    "/v1/gateway/usage",
                    None,
                    Some(&record.access),
                    operation,
                )
                .map_err(|_| ClientError::GatewayUnavailable)?;
            if reply.status == 401 {
                return Err(ClientError::LoginRequired);
            }
            if reply.status != 200 {
                return Err(ClientError::GatewayUnavailable);
            }
            let usage = snapshot::personal_usage(reply.body())?;
            self.check_enabled(operation)?;
            // Configuration can be edited outside the cooperating lock. Re-read
            // before publishing so even that change cannot relabel this result.
            let bytes = guard
                .read("profile.json")
                .map_err(platform_error)?
                .ok_or(ClientError::LoginRequired)?;
            if ConnectionProfile::from_json(&bytes)? != *profile {
                return Err(ClientError::IdentityMismatch);
            }
            self.snapshots
                .success(&ticket, identity, usage, self.clock.now())
        })();
        if let Err(error) = result {
            self.snapshots.failure(&ticket, error);
        }
        result
    }

    fn identity(
        &self,
        record: &SessionRecord,
        operation: &Operation,
    ) -> Result<GatewayIdentity, ClientError> {
        let reply = self
            .transport
            .request(
                &record.profile,
                "/v1/gateway/whoami",
                None,
                Some(&record.access),
                operation,
            )
            .map_err(transport_error)?;
        if reply.status == 401 {
            return Err(ClientError::LoginRequired);
        }
        if reply.status != 200 {
            return Err(ClientError::GatewayUnavailable);
        }
        GatewayIdentity::from_json(
            reply.body(),
            record.profile.organization(),
            record.profile.client(),
        )
    }

    fn post(
        &self,
        profile: &ConnectionProfile,
        path: &str,
        fields: &[(&str, &str)],
        operation: &Operation,
    ) -> Result<Reply, ClientError> {
        operation.check()?;
        let map: std::collections::BTreeMap<_, _> = fields.iter().copied().collect();
        let body =
            Zeroizing::new(serde_json::to_vec(&map).map_err(|_| ClientError::InvalidResponse)?);
        self.transport
            .request(profile, path, Some(&body), None, operation)
            .map_err(transport_error)
    }
    fn revoke(&self, record: &SessionRecord, operation: &Operation) -> bool {
        #[derive(Deserialize)]
        struct Revoked {
            revoked: bool,
        }
        self.post(
            &record.profile,
            "/v1/auth/logout",
            &[("credential", &record.refresh)],
            operation,
        )
        .is_ok_and(|r| r.status == 200 && r.decode::<Revoked>().is_ok_and(|v| v.revoked))
    }
    fn revoke_unsaved(&self, record: &SessionRecord) -> bool {
        // Independent uncancelled request, bounded by the transport's 15s total
        // timeout. Worker ownership makes cleanup survive UI cancellation.
        self.revoke(record, &Operation::default())
    }
}

fn platform_error(error: PlatformError) -> ClientError {
    match error {
        PlatformError::Busy => ClientError::ProfileBusy,
        PlatformError::UnsafeStorage => ClientError::UnsafeStorage,
        PlatformError::Changed => ClientError::ConfigurationChanged,
        PlatformError::Unavailable => ClientError::StorageUnavailable,
        _ => ClientError::SecureStoreUnavailable,
    }
}

fn transport_error(error: hormuz_client_transport::TransportError) -> ClientError {
    use hormuz_client_transport::ErrorKind;
    match error.kind {
        ErrorKind::Redirect => ClientError::UnexpectedRedirect,
        ErrorKind::ResponseTooLarge => ClientError::ResponseTooLarge,
        ErrorKind::InvalidResponse | ErrorKind::InvalidRequest => ClientError::InvalidResponse,
        ErrorKind::RequestTooLarge => ClientError::InvalidArguments,
        _ => ClientError::GatewayUnavailable,
    }
}

#[cfg(test)]
mod tests;
