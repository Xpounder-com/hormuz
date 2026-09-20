//! One event-driven dashboard scheduler per session controller. The shell owns
//! one replaceable alarm and a bounded worker; this module creates neither.
use crate::{snapshot, Clock, Operation, SessionController, SessionTransport};
use hormuz_client_core::{ClientError, ConnectionProfile, ReadingStatus};
use hormuz_client_platform::{CredentialStore, LifecycleEvent, RefreshCoordinator};
use std::sync::{Arc, Mutex};
use std::time::Duration;

#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub enum DashboardVisibility {
    #[default]
    Hidden,
    Summary,
    Detail,
}

/// Initial measurement hypotheses, not measured power/performance guarantees.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct RefreshPolicy {
    summary: Duration,
    detail: Duration,
    stale_after: Duration,
    debounce: Duration,
    retry_initial: Duration,
    retry_maximum: Duration,
}
impl Default for RefreshPolicy {
    fn default() -> Self {
        Self::new(45, 7, 60, 2, 5, 300).expect("valid default policy")
    }
}
impl RefreshPolicy {
    /// Seconds, each in 1..=86400. Reject zero/overflowing or inverted intervals
    /// rather than allowing a tight polling loop through configuration.
    pub fn new(
        summary: u64,
        detail: u64,
        stale_after: u64,
        debounce: u64,
        retry_initial: u64,
        retry_maximum: u64,
    ) -> Result<Self, ClientError> {
        if [
            summary,
            detail,
            stale_after,
            debounce,
            retry_initial,
            retry_maximum,
        ]
        .iter()
        .any(|v| !(1..=86_400).contains(v))
            || detail > summary
            || retry_initial > retry_maximum
        {
            return Err(ClientError::InvalidArguments);
        }
        Ok(Self {
            summary: Duration::from_secs(summary),
            detail: Duration::from_secs(detail),
            stale_after: Duration::from_secs(stale_after),
            debounce: Duration::from_secs(debounce),
            retry_initial: Duration::from_secs(retry_initial),
            retry_maximum: Duration::from_secs(retry_maximum),
        })
    }
}

struct Flight {
    id: Arc<()>,
    generation: Arc<()>,
    abandoned: bool,
}
struct Debounce {
    first: Duration,
    last: Duration,
}
#[derive(Default)]
pub(crate) struct RefreshState {
    policy: RefreshPolicy,
    profile: Option<ConnectionProfile>,
    generation: Arc<()>,
    visibility: DashboardVisibility,
    sleeping: bool,
    locked: bool,
    quit: bool,
    authentication_blocked: bool,
    reopened: bool,
    network_changed: bool,
    regular: Option<Duration>,
    retry: Option<Duration>,
    failures: u32,
    dirty: Option<Debounce>,
    flight: Option<Flight>,
    last_now: Duration,
}
impl RefreshState {
    fn enabled(&self) -> bool {
        self.profile.is_some()
            && self.visibility != DashboardVisibility::Hidden
            && !self.sleeping
            && !self.locked
            && !self.quit
            && !self.authentication_blocked
    }
    fn interval(&self) -> Duration {
        match self.visibility {
            DashboardVisibility::Detail => self.policy.detail,
            _ => self.policy.summary,
        }
    }
    fn observe_time(&mut self, now: Duration) {
        if now < self.last_now {
            // A native monotonic clock should not go backward. Rebase fake or
            // replaced clocks without a burst or an effectively infinite wait.
            self.regular = Some(now.saturating_add(self.interval()));
            self.retry = self
                .retry
                .map(|_| now.saturating_add(self.policy.retry_initial));
            if self.dirty.is_some() {
                self.dirty = Some(Debounce {
                    first: now,
                    last: now,
                });
            }
        }
        self.last_now = now;
    }
    fn reconcile_dropped(&mut self, now: Duration) {
        if let Some(id) = self
            .flight
            .as_ref()
            .filter(|f| f.abandoned)
            .map(|f| f.id.clone())
        {
            // Drop has no clock or I/O. Start the delay at the next observed
            // event, never at the possibly long-past dispatch time.
            self.finish(&id, now, Err(ClientError::GatewayUnavailable), false);
        }
    }
    fn due(&mut self, now: Duration, fresh: bool) -> Duration {
        if self.reopened {
            self.regular = Some(if fresh {
                now.saturating_add(self.interval())
            } else {
                now
            });
            self.reopened = false;
        }
        if self.network_changed {
            // Coalesce a burst of connectivity hints into one bounded probe.
            // A current reading needs no extra request just for an OS hint.
            if !fresh {
                let probe = now.saturating_add(self.policy.debounce);
                self.regular = Some(self.regular.map_or(probe, |old| old.min(probe)));
                self.retry = self.retry.map(|old| old.min(probe));
            }
            self.network_changed = false;
        }
        let dirty = self.dirty.as_ref().map(|d| {
            d.last.saturating_add(self.policy.debounce).min(
                d.first
                    .saturating_add(self.policy.debounce.saturating_mul(5)),
            )
        });
        let due = match (self.regular, dirty) {
            (Some(a), Some(b)) => a.min(b),
            (Some(a), None) | (None, Some(a)) => a,
            (None, None) => self.retry.unwrap_or(now),
        };
        // Relay completions and view changes must not defeat offline backoff.
        self.retry.map_or(due, |retry| due.max(retry))
    }
    fn finish(
        &mut self,
        id: &Arc<()>,
        now: Duration,
        result: Result<(), ClientError>,
        needs_authentication: bool,
    ) {
        let Some(flight) = self.flight.as_ref().filter(|f| Arc::ptr_eq(&f.id, id)) else {
            return;
        };
        let current = Arc::ptr_eq(&flight.generation, &self.generation);
        self.flight = None;
        if !current {
            return;
        }
        self.observe_time(now);
        self.regular = None;
        match result {
            Ok(()) => {
                self.failures = 0;
                self.retry = None;
                self.regular = Some(now.saturating_add(self.interval()));
            }
            Err(_) if needs_authentication => {
                self.authentication_blocked = true;
                self.retry = None;
                self.dirty = None;
            }
            Err(_) => {
                let delay = self
                    .policy
                    .retry_initial
                    .saturating_mul(1 << self.failures.min(17))
                    .min(self.policy.retry_maximum);
                self.failures = self.failures.saturating_add(1);
                self.retry = Some(now.saturating_add(delay));
            }
        }
    }
}

/// Move to a bounded worker, then pass to `run_dashboard_refresh`. Not cloneable:
/// one dispatch owns one operation. Keep only its cancellation handle on the UI.
#[must_use = "run on a bounded worker or drop to release the scheduling slot"]
pub struct DashboardRefresh {
    scheduler: Arc<Mutex<RefreshState>>,
    id: Arc<()>,
    profile: ConnectionProfile,
    ticket: snapshot::Ticket,
    operation: Operation,
}
impl DashboardRefresh {
    /// Explicit cancellation only. Hide/lock/sleep never cancel a transaction
    /// that may have persisted a credential-refresh intent. Never give this
    /// handle to relay traffic, or give a relay handle to dashboard work.
    pub fn cancellation(&self) -> Operation {
        self.operation.clone()
    }
}
impl Drop for DashboardRefresh {
    fn drop(&mut self) {
        let mut state = self.scheduler.lock().unwrap();
        if let Some(flight) = state
            .flight
            .as_mut()
            .filter(|f| Arc::ptr_eq(&f.id, &self.id))
        {
            flight.abandoned = true;
        }
    }
}

impl<C: RefreshCoordinator, S: CredentialStore, T: SessionTransport, K: Clock>
    SessionController<C, S, T, K>
{
    /// Activate after restoring/verifying a session on a worker; use None before
    /// sign-out or profile replacement. This does no custody or network I/O.
    /// An explicit same-profile activation also resumes after authentication
    /// recovery. Visibility and lifecycle events never do that automatically.
    pub fn set_dashboard_profile(&self, profile: Option<ConnectionProfile>) {
        let mut state = self.refresh.lock().unwrap();
        if state.profile != profile
            || state.authentication_blocked
            || self.snapshot().reading().status() == ReadingStatus::NeedsAuthentication
        {
            self.snapshots.invalidate(if profile.is_some() {
                ReadingStatus::Offline
            } else {
                ReadingStatus::NeedsAuthentication
            });
            state.generation = Arc::new(());
            state.profile = profile;
            state.authentication_blocked = false;
            state.reopened = true;
            state.regular = None;
            state.retry = None;
            state.failures = 0;
            state.dirty = None;
            state.network_changed = false;
        }
    }

    pub fn set_refresh_policy(&self, policy: RefreshPolicy) {
        let mut state = self.refresh.lock().unwrap();
        if state.policy != policy {
            state.policy = policy;
            state.reopened = true;
        }
    }

    pub fn set_dashboard_visibility(&self, visibility: DashboardVisibility) {
        let mut state = self.refresh.lock().unwrap();
        if state.visibility != visibility {
            state.visibility = visibility;
            state.reopened = true;
        }
    }

    pub fn dashboard_lifecycle(&self, event: LifecycleEvent) {
        let mut state = self.refresh.lock().unwrap();
        match event {
            LifecycleEvent::Sleep => state.sleeping = true,
            LifecycleEvent::SessionLocked => state.locked = true,
            LifecycleEvent::Wake => {
                if state.sleeping {
                    state.reopened = true;
                }
                state.sleeping = false;
            }
            LifecycleEvent::SessionUnlocked => {
                if state.locked {
                    state.reopened = true;
                }
                state.locked = false;
            }
            LifecycleEvent::NetworkChanged => state.network_changed = true,
            LifecycleEvent::Quit => state.quit = true,
        }
    }

    /// Content-free signal only. Coalesce completions; never add local usage.
    /// Hidden/locked/sleeping dashboards retain one dirty bit and do no polling.
    pub fn local_request_completed(&self) {
        let mut state = self.refresh.lock().unwrap();
        let now = self.clock.elapsed();
        state.observe_time(now);
        state.reconcile_dropped(now);
        if state.profile.is_some() && !state.quit && !state.authentication_blocked {
            match &mut state.dirty {
                Some(dirty) => dirty.last = now,
                None => {
                    state.dirty = Some(Debounce {
                        first: now,
                        last: now,
                    })
                }
            }
        }
    }

    /// Replace the shell's single alarm with this relative delay. None means
    /// disarm it, not poll this function in a loop. Call after each native event
    /// and worker completion, and drain the coalesced snapshot change slot.
    /// This can publish one age-based stale transition without starting work.
    pub fn next_dashboard_wakeup(&self) -> Option<Duration> {
        let mut state = self.refresh.lock().unwrap();
        let now = self.clock.elapsed();
        self.dashboard_plan(&mut state, now)
            .map(|due| due.saturating_sub(now))
    }

    fn dashboard_plan(&self, state: &mut RefreshState, now: Duration) -> Option<Duration> {
        state.observe_time(now);
        state.reconcile_dropped(now);
        if !state.enabled() {
            return None;
        }
        let remaining = self.snapshots.age(&self.clock, state.policy.stale_after);
        let age_due = remaining.map(|v| now.saturating_add(v));
        if state.flight.is_some() {
            return age_due;
        }
        if self.snapshot().reading().status() == ReadingStatus::NeedsAuthentication {
            state.authentication_blocked = true;
            return None;
        }
        let due = state.due(now, remaining.is_some());
        Some(age_due.map_or(due, |age| due.min(age)))
    }

    /// Take at most one due job. Repeated view events cannot queue duplicates.
    /// Run the returned job off the UI thread; do not make a worker per view.
    pub fn take_dashboard_refresh(&self) -> Option<DashboardRefresh> {
        let mut state = self.refresh.lock().unwrap();
        let now = self.clock.elapsed();
        self.dashboard_plan(&mut state, now)?;
        if state.flight.is_some()
            || state.due(
                now,
                self.snapshot().reading().status() == ReadingStatus::Current,
            ) > now
        {
            return None;
        }
        let profile = state.profile.as_ref()?.clone();
        let id = Arc::new(());
        state.flight = Some(Flight {
            id: id.clone(),
            generation: state.generation.clone(),
            abandoned: false,
        });
        state.dirty = None;
        Some(DashboardRefresh {
            scheduler: self.refresh.clone(),
            id,
            ticket: self.snapshots.begin(&profile),
            profile,
            operation: Operation::default(),
        })
    }

    /// Synchronous worker operation. Always deliver completion to the shell so
    /// it can drain snapshot changes and replace/disarm its one alarm.
    pub fn run_dashboard_refresh(&self, job: DashboardRefresh) -> Result<(), ClientError> {
        if !Arc::ptr_eq(&self.refresh, &job.scheduler) {
            return Err(ClientError::ConfigurationChanged);
        }
        // A queued job that has not entered its transaction may be suppressed.
        // Once started, visibility/power events never cancel it mid-refresh.
        let enabled = self.refresh.lock().unwrap().enabled();
        let result = if enabled {
            self.refresh_snapshot_with_ticket(&job.profile, &job.operation, &job.ticket)
        } else {
            Err(ClientError::GatewayUnavailable)
        };
        let mut state = self.refresh.lock().unwrap();
        let needs_authentication =
            self.snapshot().reading().status() == ReadingStatus::NeedsAuthentication;
        state.finish(&job.id, self.clock.elapsed(), result, needs_authentication);
        drop(state);
        result
    }
}
