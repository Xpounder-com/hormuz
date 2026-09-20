use super::*;
use std::sync::Mutex;

#[derive(Default)]
struct Calls {
    launches: usize,
    stops: usize,
    handles: usize,
    alive: bool,
    fail_launch: bool,
    fail_probe: bool,
    fail_stop: bool,
    fallback_drops: usize,
}
#[derive(Clone)]
struct FakeSupervisor(Arc<Mutex<Calls>>);
struct FakeChild(Arc<Mutex<Calls>>);
impl ProcessSupervisor for FakeSupervisor {
    type Child = FakeChild;
    fn launch(&self, _executable: &Path, _arguments: &[String]) -> crate::Result<FakeChild> {
        let mut state = self.0.lock().unwrap();
        state.launches += 1;
        assert_eq!(state.handles, 0, "a retained child forbids another launch");
        if state.fail_launch {
            return Err(PlatformError::Unavailable);
        }
        state.alive = true;
        state.handles = 1;
        Ok(FakeChild(self.0.clone()))
    }
}
impl SupervisedProcess for FakeChild {
    fn is_running(&mut self) -> crate::Result<bool> {
        let state = self.0.lock().unwrap();
        if state.fail_probe {
            Err(PlatformError::Unavailable)
        } else {
            Ok(state.alive)
        }
    }
    fn terminate_and_wait(&mut self) -> crate::Result<()> {
        let mut state = self.0.lock().unwrap();
        state.stops += 1;
        if state.fail_stop {
            return Err(PlatformError::Unavailable);
        }
        state.alive = false;
        Ok(())
    }
}
impl Drop for FakeChild {
    fn drop(&mut self) {
        let mut state = self.0.lock().unwrap();
        state.handles -= 1;
        if state.alive {
            state.fallback_drops += 1;
            state.alive = false;
        }
    }
}

fn executable() -> PathBuf {
    // No executable is launched. Use an absolute native path on every test OS.
    std::env::current_dir()
        .unwrap()
        .join("synthetic-helper-marker")
}
fn command() -> HelperCommand {
    HelperCommand::new(&executable(), vec!["--synthetic-operational-marker".into()]).unwrap()
}
fn controller() -> (HelperLifecycle<FakeSupervisor>, Arc<Mutex<Calls>>) {
    let state = Arc::new(Mutex::new(Calls::default()));
    (
        HelperLifecycle::initialize(
            None,
            FakeSupervisor(state.clone()),
            command(),
            RestartPolicy::default(),
        ),
        state,
    )
}
fn seconds(now: u64) -> Duration {
    Duration::from_secs(now)
}

#[test]
fn panel_close_reopen_and_network_events_do_not_launch_or_stop_helpers() {
    let (mut lifecycle, state) = controller();
    assert_eq!(lifecycle.reopen(), Some(PanelIntent::Reopen));
    assert_eq!(lifecycle.close_panel(), PanelIntent::Hide);
    lifecycle.lifecycle_event(LifecycleEvent::NetworkChanged);
    assert_eq!(
        lifecycle.tick(seconds(0)).unwrap().helper,
        HelperStatus::Stopped
    );
    assert_eq!(state.lock().unwrap().launches, 0);
    lifecycle.require_helper(true).unwrap();
    lifecycle.tick(seconds(0)).unwrap();
    let client = lifecycle.admit_client().unwrap();
    for now in 1..10 {
        assert_eq!(lifecycle.close_panel(), PanelIntent::Hide);
        lifecycle.reopen();
        lifecycle.tick(seconds(now)).unwrap();
    }
    assert_eq!(state.lock().unwrap().launches, 1);
    assert_eq!(state.lock().unwrap().stops, 0);
    assert_eq!(lifecycle.snapshot().active_clients, 1);
    drop(client);
}

#[test]
fn demand_removal_preserves_active_clients_and_stops_after_last_lease() {
    let (mut lifecycle, state) = controller();
    lifecycle.require_helper(true).unwrap();
    lifecycle.tick(seconds(0)).unwrap();
    let a = lifecycle.admit_client().unwrap();
    let b = lifecycle.admit_client().unwrap();
    lifecycle.require_helper(false).unwrap();
    lifecycle.tick(seconds(1)).unwrap();
    drop(a);
    lifecycle.tick(seconds(2)).unwrap();
    assert_eq!(state.lock().unwrap().stops, 0);
    drop(b);
    assert_eq!(
        lifecycle.tick(seconds(3)).unwrap().helper,
        HelperStatus::Stopped
    );
    assert_eq!(state.lock().unwrap().stops, 1);
    assert_eq!(lifecycle.snapshot().launch_attempts, 0);
}

#[test]
fn update_drains_clients_and_waits_for_confirmed_helper_cleanup() {
    let (mut lifecycle, state) = controller();
    lifecycle.require_helper(true).unwrap();
    lifecycle.tick(seconds(0)).unwrap();
    let client = lifecycle.admit_client().unwrap();
    lifecycle.begin_shutdown(ShutdownReason::Update);
    assert!(matches!(
        lifecycle.admit_client(),
        Err(LifecycleError::ShuttingDown)
    ));
    assert_eq!(
        lifecycle.require_helper(true),
        Err(LifecycleError::ShuttingDown)
    );
    assert_eq!(lifecycle.retry_helper(), Err(LifecycleError::ShuttingDown));
    assert_eq!(
        lifecycle.tick(seconds(1)).unwrap().phase,
        Phase::Draining(ShutdownReason::Update)
    );
    assert_eq!(state.lock().unwrap().stops, 0);
    drop(client);
    state.lock().unwrap().fail_stop = true;
    assert_eq!(
        lifecycle.tick(seconds(2)),
        Err(LifecycleError::Platform(PlatformError::Unavailable))
    );
    assert_eq!(
        lifecycle.snapshot().phase,
        Phase::Draining(ShutdownReason::Update)
    );
    assert_eq!(lifecycle.snapshot().helper, HelperStatus::Owned);
    assert_eq!(state.lock().unwrap().handles, 1);
    state.lock().unwrap().fail_stop = false;
    assert_eq!(
        lifecycle.tick(seconds(3)).unwrap().phase,
        Phase::ReadyToExit(ShutdownReason::Update)
    );
    assert_eq!(state.lock().unwrap().handles, 0);
    assert_eq!(lifecycle.reopen(), None);
    lifecycle.lifecycle_event(LifecycleEvent::Wake);
    lifecycle.tick(seconds(4)).unwrap();
    assert_eq!(state.lock().unwrap().launches, 1);
}

#[test]
fn forced_quit_is_explicit_and_never_authorizes_a_forced_update() {
    let (mut lifecycle, state) = controller();
    lifecycle.require_helper(true).unwrap();
    lifecycle.tick(seconds(0)).unwrap();
    let client = lifecycle.admit_client().unwrap();
    lifecycle.begin_shutdown(ShutdownReason::Update);
    lifecycle.force_quit();
    let snapshot = lifecycle.tick(seconds(1)).unwrap();
    assert_eq!(snapshot.phase, Phase::ReadyToExit(ShutdownReason::Quit));
    assert_eq!(snapshot.active_clients, 1);
    assert_eq!(state.lock().unwrap().stops, 1);
    drop(client);
    assert_eq!(lifecycle.snapshot().active_clients, 0);
}

#[test]
fn quit_event_drains_and_never_restarts_a_crashed_helper() {
    let (mut lifecycle, state) = controller();
    lifecycle.require_helper(true).unwrap();
    lifecycle.tick(seconds(0)).unwrap();
    let client = lifecycle.admit_client().unwrap();
    lifecycle.lifecycle_event(LifecycleEvent::Quit);
    state.lock().unwrap().alive = false;
    assert_eq!(
        lifecycle.tick(seconds(1)).unwrap().phase,
        Phase::Draining(ShutdownReason::Quit)
    );
    lifecycle.tick(seconds(100)).unwrap();
    assert_eq!(state.lock().unwrap().launches, 1);
    drop(client);
    assert_eq!(
        lifecycle.tick(seconds(101)).unwrap().phase,
        Phase::ReadyToExit(ShutdownReason::Quit)
    );
}

#[test]
fn quit_cancels_pending_update_and_stops_even_when_liveness_probe_fails() {
    let (mut lifecycle, state) = controller();
    lifecycle.require_helper(true).unwrap();
    lifecycle.tick(seconds(0)).unwrap();
    lifecycle.begin_shutdown(ShutdownReason::Update);
    lifecycle.lifecycle_event(LifecycleEvent::Quit);
    state.lock().unwrap().fail_probe = true;
    assert_eq!(
        lifecycle.tick(seconds(1)).unwrap().phase,
        Phase::ReadyToExit(ShutdownReason::Quit)
    );
    assert_eq!(state.lock().unwrap().stops, 1);
    assert_eq!(state.lock().unwrap().handles, 0);
}

#[test]
fn sleep_and_lock_are_independent_and_keep_one_owned_helper() {
    let (mut lifecycle, state) = controller();
    lifecycle.require_helper(true).unwrap();
    lifecycle.lifecycle_event(LifecycleEvent::Sleep);
    lifecycle.lifecycle_event(LifecycleEvent::SessionLocked);
    lifecycle.tick(seconds(0)).unwrap();
    assert_eq!(state.lock().unwrap().launches, 0);
    lifecycle.lifecycle_event(LifecycleEvent::Wake);
    lifecycle.tick(seconds(1)).unwrap();
    assert!(matches!(
        lifecycle.admit_client(),
        Err(LifecycleError::Suspended)
    ));
    lifecycle.lifecycle_event(LifecycleEvent::SessionUnlocked);
    lifecycle.tick(seconds(2)).unwrap();
    let client = lifecycle.admit_client().unwrap();
    lifecycle.lifecycle_event(LifecycleEvent::Sleep);
    lifecycle.tick(seconds(3)).unwrap();
    assert_eq!(state.lock().unwrap().stops, 0);
    lifecycle.lifecycle_event(LifecycleEvent::Wake);
    lifecycle.tick(seconds(4)).unwrap();
    assert_eq!(state.lock().unwrap().launches, 1);
    assert_eq!(lifecycle.snapshot().active_clients, 1);
    drop(client);
}

#[test]
fn bounded_crash_recovery_waits_after_sleep_and_does_not_reset_on_reopen() {
    let (mut lifecycle, state) = controller();
    lifecycle.require_helper(true).unwrap();
    lifecycle.tick(seconds(0)).unwrap();
    state.lock().unwrap().alive = false;
    assert_eq!(
        lifecycle.tick(seconds(1)).unwrap().retry_at,
        Some(seconds(2))
    );
    lifecycle.lifecycle_event(LifecycleEvent::Sleep);
    lifecycle.tick(seconds(50)).unwrap();
    assert_eq!(state.lock().unwrap().launches, 1);
    lifecycle.lifecycle_event(LifecycleEvent::Wake);
    lifecycle.tick(seconds(51)).unwrap();
    state.lock().unwrap().alive = false;
    assert_eq!(
        lifecycle.tick(seconds(52)).unwrap().retry_at,
        Some(seconds(54))
    );
    lifecycle.tick(seconds(53)).unwrap();
    assert_eq!(state.lock().unwrap().launches, 2);
    lifecycle.tick(seconds(54)).unwrap();
    state.lock().unwrap().alive = false;
    assert_eq!(
        lifecycle.tick(seconds(55)).unwrap().helper,
        HelperStatus::RetryExhausted
    );
    for event in [
        LifecycleEvent::Wake,
        LifecycleEvent::SessionUnlocked,
        LifecycleEvent::NetworkChanged,
    ] {
        lifecycle.lifecycle_event(event);
        lifecycle.reopen();
        lifecycle.tick(seconds(100)).unwrap();
    }
    assert_eq!(state.lock().unwrap().launches, 3);
    lifecycle.retry_helper().unwrap();
    lifecycle.tick(seconds(101)).unwrap();
    assert_eq!(state.lock().unwrap().launches, 4);
}

#[test]
fn failed_launch_uses_budget_and_backward_time_rebases_backoff() {
    let (mut lifecycle, state) = controller();
    state.lock().unwrap().fail_launch = true;
    lifecycle.require_helper(true).unwrap();
    assert!(lifecycle.tick(seconds(10)).is_err());
    assert_eq!(lifecycle.snapshot().retry_at, Some(seconds(11)));
    assert_eq!(
        lifecycle.tick(seconds(2)).unwrap().retry_at,
        Some(seconds(3))
    );
    assert_eq!(state.lock().unwrap().launches, 1);
    assert!(lifecycle.tick(seconds(3)).is_err());
    assert_eq!(lifecycle.snapshot().retry_at, Some(seconds(5)));
    assert!(lifecycle.tick(seconds(5)).is_err());
    assert_eq!(lifecycle.snapshot().helper, HelperStatus::RetryExhausted);
    lifecycle.tick(seconds(100)).unwrap();
    assert_eq!(state.lock().unwrap().launches, 3);
    assert_eq!(state.lock().unwrap().handles, 0);
}

#[test]
fn uncertain_liveness_and_failed_stop_retain_handle_and_prevent_duplicates() {
    let (mut lifecycle, state) = controller();
    lifecycle.require_helper(true).unwrap();
    lifecycle.tick(seconds(0)).unwrap();
    state.lock().unwrap().fail_probe = true;
    assert!(lifecycle.tick(seconds(1)).is_err());
    assert_eq!(
        lifecycle.retry_helper(),
        Err(LifecycleError::HelperUnavailable)
    );
    assert_eq!(state.lock().unwrap().launches, 1);
    state.lock().unwrap().fail_probe = false;
    state.lock().unwrap().fail_stop = true;
    lifecycle.require_helper(false).unwrap();
    assert!(lifecycle.tick(seconds(2)).is_err());
    lifecycle.require_helper(true).unwrap();
    lifecycle.tick(seconds(3)).unwrap();
    assert_eq!(state.lock().unwrap().launches, 1);
    assert_eq!(state.lock().unwrap().handles, 1);
}

#[test]
fn client_leases_are_bounded_and_concurrent_release_does_not_underflow() {
    let (mut lifecycle, _) = controller();
    assert!(matches!(
        lifecycle.admit_client(),
        Err(LifecycleError::HelperUnavailable)
    ));
    lifecycle.require_helper(true).unwrap();
    lifecycle.tick(seconds(0)).unwrap();
    let mut clients = Vec::new();
    for _ in 0..MAX_ACTIVE_CLIENTS {
        clients.push(lifecycle.admit_client().unwrap());
    }
    assert!(matches!(
        lifecycle.admit_client(),
        Err(LifecycleError::TooManyClients)
    ));
    lifecycle.begin_shutdown(ShutdownReason::Quit);
    let mut workers = Vec::new();
    while !clients.is_empty() {
        let batch: Vec<_> = clients.drain(..clients.len().min(128)).collect();
        workers.push(std::thread::spawn(move || drop(batch)));
    }
    for worker in workers {
        worker.join().unwrap();
    }
    assert_eq!(lifecycle.snapshot().active_clients, 0);
    assert_eq!(
        lifecycle.tick(seconds(1)).unwrap().phase,
        Phase::ReadyToExit(ShutdownReason::Quit)
    );
}

#[test]
fn drop_attempts_cleanup_then_uses_the_adapters_non_detaching_fallback() {
    let (mut lifecycle, state) = controller();
    lifecycle.require_helper(true).unwrap();
    lifecycle.tick(seconds(0)).unwrap();
    state.lock().unwrap().fail_stop = true;
    drop(lifecycle);
    assert_eq!(state.lock().unwrap().stops, 1);
    assert_eq!(state.lock().unwrap().handles, 0);
    assert_eq!(state.lock().unwrap().fallback_drops, 1);
    // This is a fake-adapter contract test. Actual process-death containment
    // requires the native shell's process group/job object acceptance.
}

#[cfg(any(target_os = "macos", windows))]
#[test]
fn owner_holds_instance_lease_until_cleanup_and_allows_session_refresh() {
    let temporary = tempfile::tempdir().unwrap();
    let directory = crate::PrivateDirectory::open(&temporary.path().join("private")).unwrap();
    let instance = directory.try_claim_instance().unwrap();
    let state = Arc::new(Mutex::new(Calls::default()));
    let mut lifecycle = HelperLifecycle::new(
        instance,
        FakeSupervisor(state.clone()),
        command(),
        RestartPolicy::default(),
    );
    lifecycle.require_helper(true).unwrap();
    lifecycle.tick(seconds(0)).unwrap();
    assert!(matches!(
        directory.try_claim_instance(),
        Err(PlatformError::Busy)
    ));
    let refresh = directory.try_lock().unwrap();
    refresh
        .write("synthetic-profile", b"operational", None)
        .unwrap();
    drop(refresh);
    lifecycle.begin_shutdown(ShutdownReason::Update);
    state.lock().unwrap().fail_stop = true;
    assert!(lifecycle.tick(seconds(1)).is_err());
    assert!(matches!(
        directory.try_claim_instance(),
        Err(PlatformError::Busy)
    ));
    drop(lifecycle);
    assert_eq!(state.lock().unwrap().handles, 0);
    directory.try_claim_instance().unwrap();
}

#[test]
fn malformed_oversized_commands_and_invalid_policies_are_rejected_without_content() {
    for args in [
        vec!["bad\0value".into()],
        vec!["x".repeat(4097)],
        vec!["x".into(); 65],
        vec!["x".repeat(4096); 5],
    ] {
        assert!(matches!(
            HelperCommand::new(&executable(), args),
            Err(LifecycleError::InvalidCommand)
        ));
    }
    assert!(matches!(
        HelperCommand::new(Path::new("relative"), vec![]),
        Err(LifecycleError::InvalidCommand)
    ));
    assert!(matches!(
        HelperCommand::new(&executable().join("bad\0path"), vec![]),
        Err(LifecycleError::InvalidCommand)
    ));
    HelperCommand::new(&executable(), vec!["x".repeat(4096); 4]).unwrap();
    for (attempts, initial, maximum) in [(0, 1, 2), (17, 1, 2), (1, 0, 2), (1, 2, 1), (1, 1, 301)] {
        assert_eq!(
            RestartPolicy::new(attempts, seconds(initial), seconds(maximum)),
            Err(LifecycleError::InvalidPolicy)
        );
    }
    let (lifecycle, _) = controller();
    let printed = format!(
        "{:?} {lifecycle:?} {:?}",
        command(),
        LifecycleError::InvalidCommand
    );
    assert!(!printed.contains("synthetic-helper-marker"));
    assert!(!printed.contains("synthetic-operational-marker"));
}
