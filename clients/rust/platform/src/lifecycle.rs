//! Worker-side helper ownership and explicit panel/exit policy for #339.
//!
//! This module installs no login item, process adapter, IPC listener or native
//! event source. A shell owns those integrations and passes only operational
//! events here. It must keep accepted client leases until those clients exit.

use crate::{
    ApplicationInstance, LifecycleEvent, PlatformError, ProcessSupervisor, SupervisedProcess,
};
use std::fmt;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::Arc;
use std::time::Duration;

pub const MAX_ACTIVE_CLIENTS: usize = 1024;
pub const MAX_HELPER_ARGUMENT_BYTES: usize = 16_384;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum LifecycleError {
    InvalidCommand,
    InvalidPolicy,
    ShuttingDown,
    Suspended,
    HelperUnavailable,
    TooManyClients,
    Platform(PlatformError),
}
impl fmt::Display for LifecycleError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(match self {
            Self::InvalidCommand => "The helper command is invalid or exceeds its bound.",
            Self::InvalidPolicy => "The helper restart policy is invalid.",
            Self::ShuttingDown => "The application is shutting down.",
            Self::Suspended => "New local clients are paused.",
            Self::HelperUnavailable => "The local helper is unavailable.",
            Self::TooManyClients => "The local client limit has been reached.",
            Self::Platform(_) => "The helper operation could not be completed.",
        })
    }
}
impl std::error::Error for LifecycleError {}
impl From<PlatformError> for LifecycleError {
    fn from(error: PlatformError) -> Self {
        Self::Platform(error)
    }
}
pub type Result<T> = std::result::Result<T, LifecycleError>;

/// A bounded, redacted command supplied by the native shell. This validation
/// is structural only. The ProcessSupervisor must independently enforce signed
/// executable identity, allowed arguments and an explicit environment policy.
/// Never pass credentials or request/response content as command arguments.
pub struct HelperCommand {
    executable: PathBuf,
    arguments: Vec<String>,
}
impl HelperCommand {
    pub fn new(executable: &Path, arguments: Vec<String>) -> Result<Self> {
        let path = executable.as_os_str().as_encoded_bytes();
        if !executable.is_absolute()
            || path.len() > 4096
            || path.contains(&0)
            || arguments.len() > 64
            || arguments.iter().any(|a| a.len() > 4096 || a.contains('\0'))
            || arguments.iter().map(String::len).sum::<usize>() > MAX_HELPER_ARGUMENT_BYTES
        {
            return Err(LifecycleError::InvalidCommand);
        }
        Ok(Self {
            executable: executable.to_owned(),
            arguments,
        })
    }
}
impl fmt::Debug for HelperCommand {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str("HelperCommand(<redacted>)")
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct RestartPolicy {
    maximum_attempts: u32,
    initial_delay: Duration,
    maximum_delay: Duration,
}
impl Default for RestartPolicy {
    fn default() -> Self {
        Self {
            maximum_attempts: 3,
            initial_delay: Duration::from_secs(1),
            maximum_delay: Duration::from_secs(30),
        }
    }
}
impl RestartPolicy {
    /// The attempt budget includes the initial launch. Successful launches do
    /// not reset it: repeated quick crashes must eventually stop. A new idle
    /// demand cycle or explicit retry starts a new budget.
    pub fn new(
        maximum_attempts: u32,
        initial_delay: Duration,
        maximum_delay: Duration,
    ) -> Result<Self> {
        if !(1..=16).contains(&maximum_attempts)
            || initial_delay < Duration::from_millis(100)
            || initial_delay > maximum_delay
            || maximum_delay > Duration::from_secs(300)
        {
            return Err(LifecycleError::InvalidPolicy);
        }
        Ok(Self {
            maximum_attempts,
            initial_delay,
            maximum_delay,
        })
    }
    fn delay(&self, attempts: u32) -> Duration {
        self.initial_delay
            .saturating_mul(1 << attempts.saturating_sub(1).min(15))
            .min(self.maximum_delay)
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum PanelIntent {
    Reopen,
    Hide,
}
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum ShutdownReason {
    Quit,
    Update,
}
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Phase {
    Running,
    Draining(ShutdownReason),
    ReadyToExit(ShutdownReason),
}
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum HelperStatus {
    Stopped,
    /// The child handle is retained, including after uncertain native errors.
    /// This is not a health, authentication or relay-readiness assertion.
    Owned,
    WaitingToRetry,
    RetryExhausted,
}

/// Displayable operational metadata only. There are no paths, command values,
/// identities, credentials or request/response contents in this snapshot.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct LifecycleSnapshot {
    pub phase: Phase,
    pub helper: HelperStatus,
    pub panel_visible: bool,
    pub suspended: bool,
    pub active_clients: usize,
    pub launch_attempts: u32,
    pub retry_at: Option<Duration>,
}

/// One accepted client's lifetime. Not cloneable; dropping it releases exactly
/// one count. Transfer it with the actual client owner, never a view or panel.
pub struct ClientLease(Arc<AtomicUsize>);
impl Drop for ClientLease {
    fn drop(&mut self) {
        self.0.fetch_sub(1, Ordering::AcqRel);
    }
}
impl fmt::Debug for ClientLease {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str("ClientLease(<operational>)")
    }
}

/// A single, synchronous worker owns this controller. Native calls belong on a
/// bounded worker, never the UI thread. The application lease is held through
/// helper cleanup. No method can create a second child while one is owned,
/// including after an uncertain liveness result or a failed termination.
pub struct HelperLifecycle<S: ProcessSupervisor> {
    supervisor: S,
    command: HelperCommand,
    child: Option<S::Child>,
    // Tests exercise portable policy without claiming an unsupported OS lock.
    _instance: Option<ApplicationInstance>,
    policy: RestartPolicy,
    phase: Phase,
    force_quit: bool,
    clients: Arc<AtomicUsize>,
    required: bool,
    panel_visible: bool,
    sleeping: bool,
    locked: bool,
    attempts: u32,
    retry_at: Option<Duration>,
    last_now: Duration,
}
impl<S: ProcessSupervisor> HelperLifecycle<S> {
    pub fn new(
        instance: ApplicationInstance,
        supervisor: S,
        command: HelperCommand,
        policy: RestartPolicy,
    ) -> Self {
        Self::initialize(Some(instance), supervisor, command, policy)
    }

    fn initialize(
        instance: Option<ApplicationInstance>,
        supervisor: S,
        command: HelperCommand,
        policy: RestartPolicy,
    ) -> Self {
        Self {
            supervisor,
            command,
            child: None,
            _instance: instance,
            policy,
            phase: Phase::Running,
            force_quit: false,
            clients: Arc::new(AtomicUsize::new(0)),
            required: false,
            panel_visible: false,
            sleeping: false,
            locked: false,
            attempts: 0,
            retry_at: None,
            last_now: Duration::ZERO,
        }
    }

    pub fn snapshot(&self) -> LifecycleSnapshot {
        LifecycleSnapshot {
            phase: self.phase,
            helper: if self.child.is_some() {
                HelperStatus::Owned
            } else if self.attempts >= self.policy.maximum_attempts && self.demanded() {
                HelperStatus::RetryExhausted
            } else if self.retry_at.is_some() {
                HelperStatus::WaitingToRetry
            } else {
                HelperStatus::Stopped
            },
            panel_visible: self.panel_visible,
            suspended: self.sleeping || self.locked,
            active_clients: self.clients.load(Ordering::Acquire),
            launch_attempts: self.attempts,
            retry_at: self.retry_at,
        }
    }

    pub fn close_panel(&mut self) -> PanelIntent {
        self.panel_visible = false;
        PanelIntent::Hide
    }

    pub fn reopen(&mut self) -> Option<PanelIntent> {
        if matches!(self.phase, Phase::ReadyToExit(_)) {
            return None;
        }
        self.panel_visible = true;
        Some(PanelIntent::Reopen)
    }

    pub fn lifecycle_event(&mut self, event: LifecycleEvent) {
        match event {
            LifecycleEvent::Sleep => self.sleeping = true,
            LifecycleEvent::Wake => self.sleeping = false,
            LifecycleEvent::SessionLocked => self.locked = true,
            LifecycleEvent::SessionUnlocked => self.locked = false,
            LifecycleEvent::Quit => self.begin_shutdown(ShutdownReason::Quit),
            LifecycleEvent::NetworkChanged => (),
        }
    }

    pub fn require_helper(&mut self, required: bool) -> Result<()> {
        if self.phase != Phase::Running {
            return Err(LifecycleError::ShuttingDown);
        }
        self.required = required;
        Ok(())
    }

    /// Reserve a client only after the shell has checked helper readiness. A
    /// process handle alone does not prove relay readiness or authentication.
    pub fn admit_client(&mut self) -> Result<ClientLease> {
        if self.phase != Phase::Running {
            return Err(LifecycleError::ShuttingDown);
        }
        if self.sleeping || self.locked {
            return Err(LifecycleError::Suspended);
        }
        if self.child.is_none() {
            return Err(LifecycleError::HelperUnavailable);
        }
        if self.clients.load(Ordering::Acquire) >= MAX_ACTIVE_CLIENTS {
            return Err(LifecycleError::TooManyClients);
        }
        self.clients.fetch_add(1, Ordering::AcqRel);
        Ok(ClientLease(self.clients.clone()))
    }

    /// Stops admitting clients immediately. Updates always drain; they cannot
    /// become ready while a client lease or an uncertain helper remains.
    pub fn begin_shutdown(&mut self, reason: ShutdownReason) {
        if self.phase == Phase::Running
            || (self.phase == Phase::Draining(ShutdownReason::Update)
                && reason == ShutdownReason::Quit)
        {
            self.phase = Phase::Draining(reason);
        }
    }

    /// Explicit user intent to stop the owned helper despite active clients.
    /// Pending updates become quit; this can never authorize a forced update.
    pub fn force_quit(&mut self) {
        if !matches!(self.phase, Phase::ReadyToExit(_)) {
            self.phase = Phase::Draining(ShutdownReason::Quit);
            self.force_quit = true;
        }
    }

    /// Explicit retry after the shell displays helper failure. Sleep, reopen,
    /// network-change events and successful short launches never reset budget.
    pub fn retry_helper(&mut self) -> Result<()> {
        if self.phase != Phase::Running {
            return Err(LifecycleError::ShuttingDown);
        }
        if self.child.is_some() {
            return Err(LifecycleError::HelperUnavailable);
        }
        self.attempts = 0;
        self.retry_at = None;
        Ok(())
    }

    fn demanded(&self) -> bool {
        self.required || self.clients.load(Ordering::Acquire) > 0
    }

    fn delay_retry(&mut self, now: Duration) {
        self.retry_at = (self.attempts < self.policy.maximum_attempts)
            .then(|| now.saturating_add(self.policy.delay(self.attempts)));
    }

    fn stop_helper(&mut self) -> Result<()> {
        if let Some(child) = &mut self.child {
            // A failed stop leaves the exact owned handle in place. No new
            // child or ready-to-exit result can hide this uncertain outcome.
            child.terminate_and_wait()?;
            self.child = None;
        }
        Ok(())
    }

    /// Reconcile once on a bounded worker, using monotonic elapsed time. The
    /// shell owns timer scheduling; no timer or polling thread is created here.
    /// An error preserves ownership and can be retried by a later worker turn.
    pub fn tick(&mut self, now: Duration) -> Result<LifecycleSnapshot> {
        if now < self.last_now && self.retry_at.is_some() {
            self.delay_retry(now);
        }
        self.last_now = now;
        match self.phase {
            Phase::ReadyToExit(_) => return Ok(self.snapshot()),
            Phase::Draining(reason) => {
                if self.force_quit || self.clients.load(Ordering::Acquire) == 0 {
                    self.stop_helper()?;
                    self.required = false;
                    self.retry_at = None;
                    self.phase = Phase::ReadyToExit(reason);
                    self.panel_visible = false;
                }
                return Ok(self.snapshot());
            }
            Phase::Running => (),
        }
        if !self.demanded() {
            self.stop_helper()?;
            self.attempts = 0;
            self.retry_at = None;
            return Ok(self.snapshot());
        }
        if let Some(child) = &mut self.child {
            if !child.is_running()? {
                self.child = None;
                self.delay_retry(now);
            }
        }
        if self.child.is_none()
            && !self.sleeping
            && !self.locked
            && self.attempts < self.policy.maximum_attempts
            && self.retry_at.is_none_or(|due| now >= due)
        {
            self.attempts += 1;
            match self
                .supervisor
                .launch(&self.command.executable, &self.command.arguments)
            {
                Ok(child) => {
                    self.child = Some(child);
                    self.retry_at = None;
                }
                Err(error) => {
                    self.delay_retry(now);
                    return Err(error.into());
                }
            }
        }
        Ok(self.snapshot())
    }
}
impl<S: ProcessSupervisor> fmt::Debug for HelperLifecycle<S> {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_tuple("HelperLifecycle")
            .field(&self.snapshot())
            .finish()
    }
}
impl<S: ProcessSupervisor> Drop for HelperLifecycle<S> {
    fn drop(&mut self) {
        if let Some(mut child) = self.child.take() {
            // An adapter must supply non-detaching Drop/parent-death cleanup
            // even when this attempt fails. Drop the child before releasing
            // application ownership, never transfer an untracked PID.
            let _ = child.terminate_and_wait();
        }
    }
}

#[cfg(test)]
mod tests;
