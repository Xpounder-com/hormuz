use super::*;
use hormuz_client_platform::LifecycleEvent;

type Controller = SessionController<Coordinator, Store, Transport, FakeClock>;
fn seconds(value: u64) -> Duration {
    Duration::from_secs(value)
}
fn success() -> Vec<Step> {
    vec![
        step("/v1/gateway/whoami", 200, identity()),
        step("/v1/gateway/usage", 200, usage_reply()),
    ]
}
fn setup(steps: Vec<Step>) -> (Harness, Controller) {
    let h = Harness::new(steps);
    let mut record = record();
    record.access_expires = NOW - FOUNDATION_EPOCH + 20_000.0;
    h.seed(&record);
    let c = h.controller();
    c.set_dashboard_profile(Some(profile()));
    (h, c)
}
fn run(c: &Controller) -> Result<(), ClientError> {
    c.run_dashboard_refresh(c.take_dashboard_refresh().expect("one due job"))
}

#[test]
fn one_summary_detail_cadence_and_no_hidden_or_duplicate_polls() {
    let (h, c) = setup(
        [success(), success(), success()]
            .into_iter()
            .flatten()
            .collect(),
    );
    assert_eq!(c.next_dashboard_wakeup(), None);
    assert!(c.take_dashboard_refresh().is_none());
    c.set_dashboard_visibility(DashboardVisibility::Summary);
    assert_eq!(c.next_dashboard_wakeup(), Some(Duration::ZERO));
    let job = c.take_dashboard_refresh().unwrap();
    for _ in 0..100 {
        c.set_dashboard_visibility(DashboardVisibility::Summary);
        assert!(c.take_dashboard_refresh().is_none());
        assert_eq!(c.next_dashboard_wakeup(), None);
    }
    c.run_dashboard_refresh(job).unwrap();
    assert_eq!(c.next_dashboard_wakeup(), Some(seconds(45)));
    h.clock.sleep(seconds(44));
    assert!(c.take_dashboard_refresh().is_none());
    h.clock.sleep(seconds(1));
    run(&c).unwrap();
    c.set_dashboard_visibility(DashboardVisibility::Detail);
    assert_eq!(c.next_dashboard_wakeup(), Some(seconds(7)));
    h.clock.sleep(seconds(7));
    run(&c).unwrap();
    c.set_dashboard_visibility(DashboardVisibility::Hidden);
    h.clock.sleep(seconds(3600));
    assert_eq!(c.next_dashboard_wakeup(), None);
    assert!(c.take_dashboard_refresh().is_none());
    assert_eq!(h.transport.calls.lock().unwrap().len(), 6);
    h.done();
}

#[test]
fn reopen_only_when_stale_and_age_preserves_last_success_and_change_coalescing() {
    let (h, c) = setup([success(), success()].into_iter().flatten().collect());
    c.set_dashboard_visibility(DashboardVisibility::Summary);
    run(&c).unwrap();
    let prior = c.snapshot();
    c.take_snapshot_change();
    c.set_dashboard_visibility(DashboardVisibility::Hidden);
    h.clock.sleep(seconds(50));
    c.set_dashboard_visibility(DashboardVisibility::Summary);
    assert!(c.take_dashboard_refresh().is_none());
    assert_eq!(c.next_dashboard_wakeup(), Some(seconds(10))); // age alarm before poll
    h.clock.sleep(seconds(10));
    assert!(c.take_dashboard_refresh().is_none());
    assert_eq!(c.snapshot().reading().status(), ReadingStatus::Stale);
    assert_eq!(c.snapshot().total_tokens(), prior.total_tokens());
    assert_eq!(c.snapshot().reading().checked_at_epoch_seconds(), Some(NOW));
    assert!(c.take_snapshot_change().is_some());
    for _ in 0..100 {
        assert_eq!(c.next_dashboard_wakeup(), Some(seconds(35)));
        assert!(c.take_snapshot_change().is_none());
    }
    c.set_dashboard_visibility(DashboardVisibility::Hidden);
    c.set_dashboard_visibility(DashboardVisibility::Summary);
    assert_eq!(c.next_dashboard_wakeup(), Some(Duration::ZERO));
    run(&c).unwrap();
    assert_eq!(c.snapshot().reading().status(), ReadingStatus::Current);
    assert_eq!(
        c.snapshot().reading().checked_at_epoch_seconds(),
        Some(NOW + 60.0)
    );
    assert_eq!(prior.reading().status(), ReadingStatus::Current); // immutable old holder
    h.done();
}

#[test]
fn overlapping_lock_sleep_events_disarm_and_resume_without_catchup_bursts() {
    let (h, c) = setup([success(), success()].into_iter().flatten().collect());
    c.set_dashboard_visibility(DashboardVisibility::Detail);
    run(&c).unwrap();
    c.dashboard_lifecycle(LifecycleEvent::SessionLocked);
    c.dashboard_lifecycle(LifecycleEvent::Sleep);
    h.clock.sleep(seconds(200));
    c.local_request_completed();
    c.dashboard_lifecycle(LifecycleEvent::NetworkChanged);
    assert_eq!(c.next_dashboard_wakeup(), None);
    c.dashboard_lifecycle(LifecycleEvent::Wake);
    assert_eq!(c.next_dashboard_wakeup(), None); // still locked
    c.dashboard_lifecycle(LifecycleEvent::SessionUnlocked);
    run(&c).unwrap();
    assert_eq!(c.next_dashboard_wakeup(), Some(seconds(7)));
    for _ in 0..100 {
        c.dashboard_lifecycle(LifecycleEvent::Wake);
        c.dashboard_lifecycle(LifecycleEvent::SessionUnlocked);
        assert!(c.take_dashboard_refresh().is_none());
        assert_eq!(c.next_dashboard_wakeup(), Some(seconds(7)));
    }
    c.dashboard_lifecycle(LifecycleEvent::Quit);
    c.dashboard_lifecycle(LifecycleEvent::Wake);
    c.set_dashboard_visibility(DashboardVisibility::Summary);
    assert_eq!(c.next_dashboard_wakeup(), None);
    h.done();
}

#[test]
fn fresh_resume_waits_and_queued_work_does_not_start_when_suspended() {
    let (h, c) = setup(success());
    c.set_dashboard_visibility(DashboardVisibility::Summary);
    let queued = c.take_dashboard_refresh().unwrap();
    c.dashboard_lifecycle(LifecycleEvent::SessionLocked);
    assert!(c.run_dashboard_refresh(queued).is_err());
    assert!(h.transport.calls.lock().unwrap().is_empty());
    c.dashboard_lifecycle(LifecycleEvent::SessionUnlocked);
    h.clock.sleep(seconds(5));
    run(&c).unwrap();
    c.dashboard_lifecycle(LifecycleEvent::Sleep);
    h.clock.sleep(seconds(5));
    c.dashboard_lifecycle(LifecycleEvent::Wake);
    assert!(c.take_dashboard_refresh().is_none());
    assert_eq!(c.next_dashboard_wakeup(), Some(seconds(45)));
    h.done();
}

#[test]
fn local_completion_bursts_debounce_with_a_maximum_delay_and_no_local_accounting() {
    let (h, c) = setup(
        [success(), success(), success()]
            .into_iter()
            .flatten()
            .collect(),
    );
    c.set_dashboard_visibility(DashboardVisibility::Summary);
    run(&c).unwrap();
    let prior = c.snapshot();
    c.take_snapshot_change();
    for _ in 0..10 {
        for _ in 0..100 {
            c.local_request_completed();
        }
        assert!(c.take_dashboard_refresh().is_none());
        assert!(Arc::ptr_eq(&prior, &c.snapshot()));
        assert!(c.take_snapshot_change().is_none());
        h.clock.sleep(seconds(1));
    }
    let job = c.take_dashboard_refresh().unwrap(); // cannot starve beyond 10s
    c.local_request_completed(); // completion during a refresh is not lost
    c.run_dashboard_refresh(job).unwrap();
    assert_eq!(c.next_dashboard_wakeup(), Some(seconds(2)));
    h.clock.sleep(seconds(2));
    run(&c).unwrap();
    assert_eq!(c.snapshot().total_tokens(), prior.total_tokens());
    assert_eq!(c.next_dashboard_wakeup(), Some(seconds(45)));
    h.done();
}

#[test]
fn offline_backoff_is_capped_and_not_bypassed_by_reopens_or_relay_completions() {
    let mut steps = Vec::new();
    for _ in 0..9 {
        steps.push(offline("/v1/gateway/whoami"));
    }
    steps.extend(success());
    let (h, c) = setup(steps);
    c.set_dashboard_visibility(DashboardVisibility::Summary);
    for delay in [5, 10, 20, 40, 80, 160, 300, 300, 300] {
        assert_eq!(run(&c), Err(ClientError::GatewayUnavailable));
        assert_eq!(c.next_dashboard_wakeup(), Some(seconds(delay)));
        c.local_request_completed();
        c.set_dashboard_visibility(DashboardVisibility::Hidden);
        c.set_dashboard_visibility(DashboardVisibility::Detail);
        assert_eq!(c.next_dashboard_wakeup(), Some(seconds(delay)));
        h.clock.sleep(seconds(delay - 1));
        assert!(c.take_dashboard_refresh().is_none());
        h.clock.sleep(seconds(1));
    }
    run(&c).unwrap();
    assert_eq!(c.next_dashboard_wakeup(), Some(seconds(7)));
    h.done();
}

#[test]
fn network_hints_coalesce_retry_without_requesting_again_for_a_current_reading() {
    let (h, c) = setup(
        [vec![offline("/v1/gateway/whoami")], success()]
            .into_iter()
            .flatten()
            .collect(),
    );
    c.set_dashboard_visibility(DashboardVisibility::Summary);
    assert!(run(&c).is_err());
    c.dashboard_lifecycle(LifecycleEvent::NetworkChanged);
    assert_eq!(c.next_dashboard_wakeup(), Some(seconds(2)));
    h.clock.sleep(seconds(1));
    for _ in 0..100 {
        c.dashboard_lifecycle(LifecycleEvent::NetworkChanged);
        assert_eq!(c.next_dashboard_wakeup(), Some(seconds(1)));
    }
    h.clock.sleep(seconds(1));
    run(&c).unwrap();
    for _ in 0..100 {
        c.dashboard_lifecycle(LifecycleEvent::NetworkChanged);
        assert_eq!(c.next_dashboard_wakeup(), Some(seconds(45)));
        assert!(c.take_dashboard_refresh().is_none());
    }
    h.done();
}

#[test]
fn clock_jumps_expire_once_without_advancing_success_or_changing_retry_deadlines() {
    for jump in [-3600.0, 3600.0, f64::NAN, f64::INFINITY] {
        let (h, c) = setup(success());
        c.set_dashboard_visibility(DashboardVisibility::Summary);
        run(&c).unwrap();
        c.take_snapshot_change();
        *h.clock.1.lock().unwrap() = jump;
        assert_eq!(c.next_dashboard_wakeup(), Some(seconds(45)));
        assert_eq!(c.snapshot().reading().status(), ReadingStatus::Stale);
        assert_eq!(c.snapshot().reading().checked_at_epoch_seconds(), Some(NOW));
        assert!(c.take_snapshot_change().is_some());
        *h.clock.1.lock().unwrap() = 0.0;
        assert_eq!(c.next_dashboard_wakeup(), Some(seconds(45)));
        assert!(c.take_snapshot_change().is_none());
        assert_eq!(c.snapshot().reading().status(), ReadingStatus::Stale);
        h.done();
    }
    let (h, c) = setup(vec![offline("/v1/gateway/whoami")]);
    c.set_dashboard_visibility(DashboardVisibility::Summary);
    assert!(run(&c).is_err());
    *h.clock.1.lock().unwrap() = 1e100;
    assert_eq!(c.next_dashboard_wakeup(), Some(seconds(5)));
    h.done();
}

#[test]
fn monotonic_age_survives_wall_clock_freeze_and_rollback_has_a_bounded_deadline() {
    let (h, c) = setup(success());
    c.set_dashboard_visibility(DashboardVisibility::Summary);
    run(&c).unwrap();
    c.set_dashboard_visibility(DashboardVisibility::Hidden);
    h.clock.sleep(seconds(60));
    *h.clock.1.lock().unwrap() = -60.0;
    c.set_dashboard_visibility(DashboardVisibility::Summary);
    assert_eq!(c.next_dashboard_wakeup(), Some(Duration::ZERO));
    assert_eq!(c.snapshot().reading().status(), ReadingStatus::Stale);
    *h.clock.0.lock().unwrap() = Duration::ZERO;
    assert_eq!(c.next_dashboard_wakeup(), Some(seconds(45)));
    assert!(c.take_dashboard_refresh().is_none());
    h.done();
}

#[test]
fn authentication_blocks_automatic_work_until_explicit_session_recovery() {
    let (h, c) = setup(
        [vec![step("/v1/gateway/whoami", 401, json!({}))], success()]
            .into_iter()
            .flatten()
            .collect(),
    );
    c.set_dashboard_visibility(DashboardVisibility::Summary);
    assert_eq!(run(&c), Err(ClientError::LoginRequired));
    h.clock.sleep(seconds(100));
    c.dashboard_lifecycle(LifecycleEvent::NetworkChanged);
    c.local_request_completed();
    c.set_dashboard_visibility(DashboardVisibility::Hidden);
    c.set_dashboard_visibility(DashboardVisibility::Summary);
    assert_eq!(c.next_dashboard_wakeup(), None);
    c.set_dashboard_profile(Some(profile()));
    run(&c).unwrap();
    assert_eq!(c.next_dashboard_wakeup(), Some(seconds(45)));
    h.done();
}

#[test]
fn obsolete_queued_jobs_cannot_start_after_profile_change_or_signout() {
    let (h, c) = setup(vec![revoked()]);
    c.set_dashboard_visibility(DashboardVisibility::Summary);
    let old = c.take_dashboard_refresh().unwrap();
    let mut value = fixtures()["record"]["profile"].clone();
    value["id"] = json!("00000000-0000-0000-0000-000000000337");
    c.set_dashboard_profile(Some(
        ConnectionProfile::from_json(&serde_json::to_vec(&value).unwrap()).unwrap(),
    ));
    assert!(c.take_dashboard_refresh().is_none()); // old slot is still owned
    assert_eq!(
        c.run_dashboard_refresh(old),
        Err(ClientError::ConfigurationChanged)
    );
    assert!(h.transport.calls.lock().unwrap().is_empty());
    c.set_dashboard_profile(Some(profile()));
    let queued = c.take_dashboard_refresh().unwrap();
    c.sign_out(&Operation::default()).unwrap();
    assert!(c.run_dashboard_refresh(queued).is_err());
    assert_eq!(c.next_dashboard_wakeup(), None);
    assert!(c.snapshot().identity().is_none());
    assert_eq!(h.transport.calls.lock().unwrap().len(), 1);
    h.done();
}

#[test]
fn explicit_cancellation_and_dropped_or_foreign_jobs_release_only_their_slot() {
    let (h, c) = setup(success());
    c.set_dashboard_visibility(DashboardVisibility::Summary);
    let job = c.take_dashboard_refresh().unwrap();
    job.cancellation().cancel();
    assert!(c.run_dashboard_refresh(job).is_err());
    assert!(h.transport.calls.lock().unwrap().is_empty());
    h.clock.sleep(seconds(5));
    let job = c.take_dashboard_refresh().unwrap();
    let foreign = h.controller();
    assert_eq!(
        foreign.run_dashboard_refresh(job),
        Err(ClientError::ConfigurationChanged)
    );
    assert_eq!(c.next_dashboard_wakeup(), Some(seconds(10)));
    h.clock.sleep(seconds(10));
    drop(c.take_dashboard_refresh().unwrap());
    assert_eq!(c.next_dashboard_wakeup(), Some(seconds(20)));
    h.clock.sleep(seconds(20));
    run(&c).unwrap();
    h.done();
}

#[test]
fn policy_is_tunable_but_cannot_create_zero_or_unbounded_intervals() {
    for values in [
        [0, 7, 60, 2, 5, 300],
        [7, 8, 60, 2, 5, 300],
        [45, 7, u64::MAX, 2, 5, 300],
        [45, 7, 60, 2, 300, 5],
    ] {
        assert!(RefreshPolicy::new(
            values[0], values[1], values[2], values[3], values[4], values[5]
        )
        .is_err());
    }
    let (h, c) = setup(success());
    c.set_refresh_policy(RefreshPolicy::new(30, 5, 90, 1, 10, 600).unwrap());
    c.set_dashboard_visibility(DashboardVisibility::Summary);
    run(&c).unwrap();
    assert_eq!(c.next_dashboard_wakeup(), Some(seconds(30)));
    c.set_dashboard_visibility(DashboardVisibility::Detail);
    assert_eq!(c.next_dashboard_wakeup(), Some(seconds(5)));
    h.done();
}

#[test]
fn hiding_locking_and_sleeping_during_persisted_refresh_allow_bounded_commit() {
    struct PausedRefresh {
        inner: Transport,
        entered: std::sync::mpsc::SyncSender<()>,
        resume: Mutex<std::sync::mpsc::Receiver<()>>,
    }
    impl SessionTransport for PausedRefresh {
        fn request(
            &self,
            profile: &ConnectionProfile,
            path: &str,
            body: Option<&[u8]>,
            access: Option<&str>,
            operation: &Operation,
        ) -> Result<Reply, TransportError> {
            if path == "/v1/auth/refresh" {
                assert_eq!(self.inner.store.state(), Some(SessionState::RefreshPending));
                self.entered.send(()).unwrap();
                self.resume
                    .lock()
                    .unwrap()
                    .recv_timeout(seconds(5))
                    .unwrap();
                assert!(!operation.is_cancelled());
            }
            self.inner.request(profile, path, body, access, operation)
        }
    }
    let h = Harness::new(
        [
            vec![step("/v1/auth/refresh", 200, pair("b", NOW))],
            success(),
        ]
        .into_iter()
        .flatten()
        .collect(),
    );
    let mut old = record();
    old.access_expires = NOW - FOUNDATION_EPOCH + 30.0;
    old.session_expires = NOW - FOUNDATION_EPOCH + 43_200.0;
    h.seed(&old);
    let (entered_tx, entered_rx) = std::sync::mpsc::sync_channel(1);
    let (resume_tx, resume_rx) = std::sync::mpsc::sync_channel(1);
    let c = SessionController::new(
        h.coordinator.clone(),
        h.store.clone(),
        PausedRefresh {
            inner: h.transport.clone(),
            entered: entered_tx,
            resume: Mutex::new(resume_rx),
        },
        h.clock.clone(),
    );
    c.set_dashboard_profile(Some(profile()));
    c.set_dashboard_visibility(DashboardVisibility::Summary);
    let job = c.take_dashboard_refresh().unwrap();
    let cancellation = job.cancellation();
    std::thread::scope(|scope| {
        let worker = scope.spawn(|| c.run_dashboard_refresh(job));
        entered_rx.recv_timeout(seconds(5)).unwrap();
        c.set_dashboard_visibility(DashboardVisibility::Hidden);
        c.dashboard_lifecycle(LifecycleEvent::SessionLocked);
        c.dashboard_lifecycle(LifecycleEvent::Sleep);
        c.local_request_completed();
        assert_eq!(c.next_dashboard_wakeup(), None);
        assert!(c.take_dashboard_refresh().is_none());
        assert!(!cancellation.is_cancelled());
        resume_tx.send(()).unwrap();
        worker.join().unwrap().unwrap();
    });
    assert_eq!(h.store.state(), Some(SessionState::Active));
    assert_eq!(c.snapshot().reading().status(), ReadingStatus::Current);
    assert_eq!(c.next_dashboard_wakeup(), None);
    h.done();
}

#[test]
fn cancellation_after_dashboard_refresh_intent_disarms_until_session_recovery() {
    let mut refresh = step("/v1/auth/refresh", 200, pair("b", NOW));
    refresh.cancel = true;
    let h = Harness::new(vec![refresh, revoked()]);
    let mut old = record();
    old.access_expires = NOW - FOUNDATION_EPOCH + 30.0;
    old.session_expires = NOW - FOUNDATION_EPOCH + 43_200.0;
    h.seed(&old);
    let c = h.controller();
    c.set_dashboard_profile(Some(profile()));
    c.set_dashboard_visibility(DashboardVisibility::Summary);
    assert_eq!(run(&c), Err(ClientError::GatewayUnavailable));
    assert_eq!(h.store.state(), Some(SessionState::RefreshPending));
    assert_eq!(
        c.snapshot().reading().status(),
        ReadingStatus::NeedsAuthentication
    );
    assert_eq!(c.next_dashboard_wakeup(), None);
    c.dashboard_lifecycle(LifecycleEvent::NetworkChanged);
    c.local_request_completed();
    h.clock.sleep(seconds(300));
    assert!(c.take_dashboard_refresh().is_none());
    h.done();
}

#[test]
fn dashboard_cancellation_and_power_events_leave_an_active_ai_request_running() {
    use std::io::{Read, Write};
    use std::net::TcpListener;
    let listener = TcpListener::bind("127.0.0.1:0").unwrap();
    let mut wire = fixtures()["record"]["profile"].clone();
    wire["gateway"] = json!(format!("http://{}", listener.local_addr().unwrap()));
    wire["allowLoopbackHTTP"] = json!(true);
    let relay_profile = ConnectionProfile::from_json(&serde_json::to_vec(&wire).unwrap()).unwrap();
    let (entered_tx, entered_rx) = std::sync::mpsc::sync_channel(1);
    let (resume_tx, resume_rx) = std::sync::mpsc::sync_channel(1);
    let server = std::thread::spawn(move || {
        let (mut stream, _) = listener.accept().unwrap();
        stream.set_read_timeout(Some(seconds(5))).unwrap();
        let mut request = Vec::new();
        let mut bytes = [0u8; 4096];
        while !request.windows(4).any(|s| s == b"\r\n\r\n") {
            let count = stream.read(&mut bytes).unwrap();
            assert_ne!(count, 0);
            request.extend_from_slice(&bytes[..count]);
        }
        assert!(request.starts_with(b"GET /v1/responses "));
        entered_tx.send(()).unwrap();
        resume_rx.recv_timeout(seconds(5)).unwrap();
        stream.write_all(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: 2\r\nConnection: close\r\n\r\n{}").unwrap();
    });
    let (h, c) = setup(Vec::new());
    c.set_dashboard_visibility(DashboardVisibility::Summary);
    let job = c.take_dashboard_refresh().unwrap();
    let dashboard_cancel = job.cancellation();
    let relay_operation = Operation::default();
    std::thread::scope(|scope| {
        let relay = scope.spawn(|| {
            NativeTransport.request(
                &relay_profile,
                "/v1/responses",
                None,
                None,
                &relay_operation,
            )
        });
        entered_rx.recv_timeout(seconds(5)).unwrap();
        c.set_dashboard_visibility(DashboardVisibility::Hidden);
        c.dashboard_lifecycle(LifecycleEvent::SessionLocked);
        c.dashboard_lifecycle(LifecycleEvent::Sleep);
        dashboard_cancel.cancel();
        assert!(c.run_dashboard_refresh(job).is_err());
        assert!(!relay_operation.is_cancelled());
        assert!(!relay.is_finished());
        resume_tx.send(()).unwrap();
        assert_eq!(relay.join().unwrap().unwrap().status, 200);
    });
    server.join().unwrap();
    h.done();
}
