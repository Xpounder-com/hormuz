//! One owned worker, one pending command and one coalesced UI notification.
//! Credentials and network responses stay inside the shared session controller.
use hormuz_client_core::{ClientError, ConnectionProfile, ConnectionStatus, SessionState};
use hormuz_client_platform::{BrowserOpener, CredentialStore, LifecycleEvent, RefreshCoordinator};
use hormuz_client_session::{
    Clock, DashboardVisibility, Operation, SessionController, SessionTransport, UsageSnapshot,
};
use std::sync::{Arc, Condvar, Mutex};
use std::thread::{self, JoinHandle};

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Phase {
    Checking,
    Ready,
    SigningIn,
    SigningOut,
    Failed(ClientError),
}
impl Phase {
    pub fn busy(self) -> bool {
        matches!(self, Self::Checking | Self::SigningIn | Self::SigningOut)
    }
}
#[derive(Clone)]
pub struct View {
    pub phase: Phase,
    pub connection: Option<ConnectionStatus>,
    pub snapshot: Arc<UsageSnapshot>,
}
impl View {
    pub fn has_session(&self) -> bool {
        self.connection
            .as_ref()
            .is_some_and(ConnectionStatus::has_session)
    }
}
enum Command {
    Restore,
    SignIn(ConnectionProfile),
    SignOut,
}
struct State {
    command: Option<Command>,
    operation: Option<Operation>,
    epoch: u64,
    quitting: bool,
    notified: bool,
    view: View,
}
struct Shared {
    state: Mutex<State>,
    wake: Condvar,
    notify: Box<dyn Fn() -> bool + Send + Sync>,
}
impl Shared {
    // Called only with the state mutex held. The native callback is exclusively
    // a nonblocking PostMessage; it cannot access this mutex or GUI allocations.
    fn publish(&self, state: &mut State) {
        if !state.quitting && !state.notified {
            state.notified = (self.notify)();
        }
    }
}

pub struct Connection<C, S, T, K, B> {
    controller: Arc<SessionController<C, S, T, K>>,
    shared: Arc<Shared>,
    worker: Option<JoinHandle<()>>,
    _browser: std::marker::PhantomData<B>,
}
impl<C, S, T, K, B> Connection<C, S, T, K, B>
where
    C: RefreshCoordinator + Send + Sync + 'static,
    S: CredentialStore + Send + Sync + 'static,
    T: SessionTransport + Send + Sync + 'static,
    K: Clock + Send + Sync + 'static,
    B: BrowserOpener + Send + 'static,
{
    pub fn start(
        controller: SessionController<C, S, T, K>,
        browser: B,
        notify: impl Fn() -> bool + Send + Sync + 'static,
    ) -> std::io::Result<Self> {
        // Remain hidden and locked until the native shell has registered its
        // session notifications and established the initial desktop state.
        controller.dashboard_lifecycle(LifecycleEvent::SessionLocked);
        let shared = Arc::new(Shared {
            state: Mutex::new(State {
                command: Some(Command::Restore),
                operation: None,
                epoch: 0,
                quitting: false,
                notified: false,
                view: View {
                    phase: Phase::Checking,
                    connection: None,
                    snapshot: controller.snapshot(),
                },
            }),
            wake: Condvar::new(),
            notify: Box::new(notify),
        });
        let controller = Arc::new(controller);
        let service = controller.clone();
        let events = shared.clone();
        let worker = thread::Builder::new()
            .name("hormuz-connection".into())
            .spawn(move || {
                loop {
                    let mut state = events.state.lock().unwrap();
                    if state.quitting {
                        break;
                    }
                    let epoch = state.epoch;
                    if let Some(command) = state.command.take() {
                        let operation = Operation::default();
                        state.operation = Some(operation.clone());
                        drop(state);
                        let outcome = match command {
                            Command::Restore => {
                                service.dashboard_lifecycle(LifecycleEvent::NetworkChanged);
                                Ok(())
                            }
                            Command::SignIn(profile) => {
                                service.sign_in(&profile, &browser, &operation)
                            }
                            Command::SignOut => service.sign_out(&operation),
                        };
                        let status = service.status(&operation);
                        let mut state = events.state.lock().unwrap();
                        state.operation = None;
                        // A requested sign-out/quit invalidates earlier completion,
                        // even if its network response crossed cancellation.
                        if state.epoch != epoch || state.quitting {
                            continue;
                        }
                        let mut error = outcome.err();
                        match status {
                            Ok(status) => {
                                let active = status.session_state() == Some(SessionState::Active);
                                if error.is_none() {
                                    error = match status.session_state() {
                                        Some(SessionState::RefreshPending) => {
                                            Some(ClientError::RefreshInterrupted)
                                        }
                                        Some(SessionState::RevocationPending) => {
                                            Some(ClientError::LogoutPending)
                                        }
                                        _ => None,
                                    };
                                }
                                service.set_dashboard_profile(if active && error.is_none() {
                                    status.profile().cloned()
                                } else {
                                    None
                                });
                                state.view.connection = Some(status);
                            }
                            Err(status_error) => {
                                service.set_dashboard_profile(None);
                                state.view.connection = None;
                                error = Some(status_error);
                            }
                        }
                        state.view.phase = error.map_or(Phase::Ready, Phase::Failed);
                        state.view.snapshot = service.snapshot();
                        events.publish(&mut state);
                        continue;
                    }
                    if let Some(job) = service.take_dashboard_refresh() {
                        state.operation = Some(job.cancellation());
                        drop(state);
                        let result = service.run_dashboard_refresh(job);
                        let mut state = events.state.lock().unwrap();
                        state.operation = None;
                        if state.epoch == epoch && !state.quitting {
                            state.view.phase = result.err().map_or(Phase::Ready, Phase::Failed);
                            state.view.snapshot = service.snapshot();
                            events.publish(&mut state);
                        }
                        continue;
                    }
                    let delay = service.next_dashboard_wakeup();
                    if let Some(snapshot) = service.take_snapshot_change() {
                        state.view.snapshot = snapshot;
                        events.publish(&mut state);
                    }
                    // Native events update the scheduler while holding this same
                    // mutex, preventing a lost wake between planning and waiting.
                    if let Some(delay) = delay {
                        drop(events.wake.wait_timeout(state, delay).unwrap());
                    } else {
                        drop(events.wake.wait(state).unwrap());
                    }
                }
            })?;
        Ok(Self {
            controller,
            shared,
            worker: Some(worker),
            _browser: std::marker::PhantomData,
        })
    }
    pub fn view(&self) -> View {
        let mut state = self.shared.state.lock().unwrap();
        state.notified = false;
        state.view.clone()
    }
    pub fn sign_in(&self, profile: ConnectionProfile) -> bool {
        let mut state = self.shared.state.lock().unwrap();
        if state.quitting
            || state.view.phase.busy()
            || state.command.is_some()
            || state.view.has_session()
        {
            return false;
        }
        state.epoch += 1;
        state.command = Some(Command::SignIn(profile));
        state.view.phase = Phase::SigningIn;
        self.shared.publish(&mut state);
        self.shared.wake.notify_one();
        true
    }
    pub fn sign_out(&self) {
        let mut state = self.shared.state.lock().unwrap();
        if state.quitting || state.view.phase == Phase::SigningOut {
            return;
        }
        state.epoch += 1;
        if let Some(operation) = &state.operation {
            operation.cancel();
        }
        self.controller.set_dashboard_profile(None);
        state.view.snapshot = self.controller.snapshot();
        state.view.phase = Phase::SigningOut;
        state.command = Some(Command::SignOut);
        self.shared.publish(&mut state);
        self.shared.wake.notify_one();
    }
    pub fn retry(&self) -> bool {
        let mut state = self.shared.state.lock().unwrap();
        if state.quitting || state.view.phase.busy() || state.command.is_some() {
            return false;
        }
        // Restore reads custody first. It never retries an ambiguous refresh or
        // revocation as if it were an active credential.
        state.epoch += 1;
        state.command = Some(Command::Restore);
        state.view.phase = Phase::Checking;
        self.shared.publish(&mut state);
        self.shared.wake.notify_one();
        true
    }
    pub fn visibility(&self, visibility: DashboardVisibility) {
        let _state = self.shared.state.lock().unwrap();
        self.controller.set_dashboard_visibility(visibility);
        self.shared.wake.notify_one();
    }
    pub fn lifecycle(&self, event: LifecycleEvent) {
        let _state = self.shared.state.lock().unwrap();
        self.controller.dashboard_lifecycle(event);
        self.shared.wake.notify_one();
    }
}
impl<C, S, T, K, B> Drop for Connection<C, S, T, K, B> {
    fn drop(&mut self) {
        {
            let mut state = self.shared.state.lock().unwrap();
            state.quitting = true;
            state.epoch += 1;
            if let Some(operation) = &state.operation {
                operation.cancel();
            }
            self.shared.wake.notify_one();
        }
        // The shared transport bounds requests and drains cancellation. Join
        // before destroying the controller; no detached credential worker.
        if let Some(worker) = self.worker.take() {
            let _ = worker.join();
        }
    }
}

#[cfg(test)]
#[path = "connection_tests.rs"]
mod tests;

/// Credential-free native view boundary. Type erasure lets Windows tests drive
/// the identical controls with an isolated store/transport, without a test CLI
/// or an override for the production credential target.
#[cfg(windows)]
pub trait DesktopConnection {
    fn view(&self) -> View;
    fn sign_in(&self, profile: ConnectionProfile) -> bool;
    fn sign_out(&self);
    fn retry(&self) -> bool;
    fn visibility(&self, visibility: DashboardVisibility);
    fn lifecycle(&self, event: LifecycleEvent);
}
#[cfg(windows)]
impl<C, S, T, K, B> DesktopConnection for Connection<C, S, T, K, B>
where
    C: RefreshCoordinator + Send + Sync + 'static,
    S: CredentialStore + Send + Sync + 'static,
    T: SessionTransport + Send + Sync + 'static,
    K: Clock + Send + Sync + 'static,
    B: BrowserOpener + Send + 'static,
{
    fn view(&self) -> View {
        self.view()
    }
    fn sign_in(&self, profile: ConnectionProfile) -> bool {
        self.sign_in(profile)
    }
    fn sign_out(&self) {
        self.sign_out()
    }
    fn retry(&self) -> bool {
        self.retry()
    }
    fn visibility(&self, visibility: DashboardVisibility) {
        self.visibility(visibility)
    }
    fn lifecycle(&self, event: LifecycleEvent) {
        self.lifecycle(event)
    }
}
