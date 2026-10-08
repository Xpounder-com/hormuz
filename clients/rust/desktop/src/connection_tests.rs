include!("connection_test_support.rs");
use crate::presentation;
use hormuz_client_core::ClientError;

#[test]
fn sign_in_usage_logout_and_reconnect_use_the_real_controller() {
    let store = Store::default();
    let transport = Transport::default();
    let connection = start(store.clone(), transport.clone(), TestClock::default());
    sign_in(&connection);
    assert_eq!(
        transport.usage.load(Ordering::SeqCst),
        0,
        "hidden/initially locked must not poll"
    );
    open(&connection);
    let view = current(&connection);
    assert_eq!(view.snapshot.scope(), "current_actor");
    assert!(presentation::labels(&view)[2].starts_with("Your requests:"));
    assert!(!presentation::labels(&view).join(" ").contains("hox_"));
    connection.sign_out();
    assert!(connection.view().snapshot.reading().usage().is_none());
    wait(&connection, |v| v.phase == Phase::Ready && !v.has_session());
    assert!(store.load().unwrap().is_none());
    assert!(connection.sign_in(profile()));
    current(&connection);
}
#[test]
fn locked_store_blocks_network_and_recovers_only_on_explicit_retry() {
    let store = Store::default();
    store.locked.store(true, Ordering::SeqCst);
    let transport = Transport::default();
    let connection = start(store.clone(), transport.clone(), TestClock::default());
    open(&connection);
    wait(&connection, |v| {
        v.phase == Phase::Failed(ClientError::SecureStoreUnavailable)
    });
    assert_eq!(transport.requests.load(Ordering::SeqCst), 0);
    store.locked.store(false, Ordering::SeqCst);
    assert!(connection.retry());
    sign_in(&connection);
    current(&connection);
}
#[test]
fn offline_retains_success_time_and_unauthorized_clears_values() {
    let transport = Transport::default();
    let clock = TestClock::default();
    let connection = start(Store::default(), transport.clone(), clock.clone());
    sign_in(&connection);
    open(&connection);
    let first = current(&connection);
    let checked = first.snapshot.reading().checked_at_epoch_seconds();
    transport.mode.store(1, Ordering::SeqCst);
    clock.1.store(10, Ordering::SeqCst);
    connection.lifecycle(LifecycleEvent::NetworkChanged);
    let offline = wait(&connection, |v| {
        v.snapshot.reading().status() == ReadingStatus::Offline
    });
    assert!(offline.snapshot.reading().usage().is_some());
    assert_eq!(
        offline.snapshot.reading().checked_at_epoch_seconds(),
        checked
    );
    assert!(presentation::labels(&offline)[0].contains("Offline"));
    transport.mode.store(2, Ordering::SeqCst);
    clock.1.store(30, Ordering::SeqCst);
    connection.lifecycle(LifecycleEvent::NetworkChanged);
    let expired = wait(&connection, |v| {
        v.snapshot.reading().status() == ReadingStatus::NeedsAuthentication
    });
    assert!(expired.snapshot.reading().usage().is_none());
    assert!(presentation::labels(&expired)[2].ends_with('—'));
}
#[test]
fn sign_out_cancels_inflight_usage_and_rejects_its_late_response() {
    let transport = Transport::default();
    transport.mode.store(3, Ordering::SeqCst);
    let connection = start(Store::default(), transport.clone(), TestClock::default());
    sign_in(&connection);
    open(&connection);
    wait(&connection, |_| transport.entered.load(Ordering::SeqCst));
    connection.sign_out();
    let view = wait(&connection, |v| v.phase == Phase::Ready && !v.has_session());
    assert!(transport.cancelled.load(Ordering::SeqCst));
    assert!(view.snapshot.reading().usage().is_none());
}
#[test]
fn cancel_pending_sign_in_and_quit_join_the_owned_worker() {
    let store = Store::default();
    let transport = Transport::default();
    transport.mode.store(4, Ordering::SeqCst);
    let connection = start(store.clone(), transport.clone(), TestClock::default());
    wait(&connection, |v| v.phase == Phase::Ready);
    assert!(connection.sign_in(profile()));
    wait(&connection, |_| transport.entered.load(Ordering::SeqCst));
    assert!(
        !connection.sign_in(profile()),
        "never queue another enrollment"
    );
    connection.sign_out();
    wait(&connection, |v| v.phase == Phase::Ready && !v.has_session());
    assert!(store.load().unwrap().is_none());
    transport.entered.store(false, Ordering::SeqCst);
    assert!(connection.sign_in(profile()));
    wait(&connection, |_| transport.entered.load(Ordering::SeqCst));
    let start = Instant::now();
    drop(connection);
    assert!(start.elapsed() < Duration::from_secs(2));
    assert!(store.load().unwrap().is_none());
}
#[test]
fn hidden_locked_and_sleeping_do_not_poll_or_duplicate_workers() {
    let transport = Transport::default();
    let clock = TestClock::default();
    let connection = start(Store::default(), transport.clone(), clock.clone());
    sign_in(&connection);
    open(&connection);
    current(&connection);
    connection.visibility(DashboardVisibility::Hidden);
    connection.lifecycle(LifecycleEvent::Sleep);
    connection.lifecycle(LifecycleEvent::SessionLocked);
    let count = transport.requests.load(Ordering::SeqCst);
    clock.1.store(90, Ordering::SeqCst);
    for _ in 0..100 {
        connection.lifecycle(LifecycleEvent::NetworkChanged);
    }
    thread::sleep(Duration::from_millis(40));
    assert_eq!(transport.requests.load(Ordering::SeqCst), count);
    connection.lifecycle(LifecycleEvent::Wake);
    connection.visibility(DashboardVisibility::Detail);
    thread::sleep(Duration::from_millis(40));
    assert_eq!(transport.requests.load(Ordering::SeqCst), count);
    connection.lifecycle(LifecycleEvent::SessionUnlocked);
    wait(&connection, |v| {
        v.snapshot
            .reading()
            .checked_at_epoch_seconds()
            .is_some_and(|t| t > SystemClock::default().now() + 80.0)
    });
    assert_eq!(transport.requests.load(Ordering::SeqCst), count + 2);
}
#[test]
fn wrong_scope_and_invalid_setup_never_become_usage() {
    for gateway in [
        "http://127.0.0.1",
        "https://user:password@example.test",
        "file:///tmp/example",
    ] {
        assert!(presentation::profile(gateway, "org-a", "model", "", "codex").is_err());
    }
    let transport = Transport::default();
    transport.mode.store(5, Ordering::SeqCst);
    let connection = start(Store::default(), transport, TestClock::default());
    sign_in(&connection);
    open(&connection);
    let failed = wait(&connection, |v| {
        v.phase == Phase::Failed(ClientError::InvalidResponse)
    });
    assert!(failed.snapshot.reading().usage().is_none());
}

#[test]
fn failed_logout_keeps_local_use_disabled_until_revocation_retry() {
    let store = Store::default();
    let transport = Transport::default();
    let connection = start(store.clone(), transport.clone(), TestClock::default());
    sign_in(&connection);
    open(&connection);
    current(&connection);
    transport.mode.store(1, Ordering::SeqCst);
    connection.sign_out();
    let failed = wait(&connection, |v| {
        v.phase == Phase::Failed(ClientError::LogoutPending)
    });
    assert!(failed.has_session());
    assert!(failed.snapshot.reading().usage().is_none());
    assert!(!connection.sign_in(profile()));
    assert!(connection.retry());
    wait(&connection, |v| {
        v.phase == Phase::Failed(ClientError::LogoutPending)
    });
    transport.mode.store(0, Ordering::SeqCst);
    connection.sign_out();
    wait(&connection, |v| v.phase == Phase::Ready && !v.has_session());
    assert!(store.load().unwrap().is_none());
}
#[test]
fn session_expiry_and_store_lock_clear_previously_visible_usage() {
    for locked in [false, true] {
        let store = Store::default();
        let transport = Transport::default();
        let clock = TestClock::default();
        let connection = start(store.clone(), transport.clone(), clock.clone());
        sign_in(&connection);
        open(&connection);
        current(&connection);
        if locked {
            store.locked.store(true, Ordering::SeqCst);
        }
        clock
            .1
            .store(if locked { 10 } else { 50000 }, Ordering::SeqCst);
        connection.lifecycle(LifecycleEvent::NetworkChanged);
        let failed = wait(&connection, |v| {
            v.snapshot.reading().status() == ReadingStatus::NeedsAuthentication
        });
        assert!(failed.snapshot.reading().usage().is_none());
        assert_eq!(
            failed.phase,
            Phase::Failed(if locked {
                ClientError::SecureStoreUnavailable
            } else {
                ClientError::LoginRequired
            })
        );
    }
}

#[test]
fn immediately_quitting_after_sign_out_still_drains_revocation() {
    let store = Store::default();
    let connection = start(store.clone(), Transport::default(), TestClock::default());
    sign_in(&connection);
    connection.sign_out();
    drop(connection);
    assert!(
        store.load().unwrap().is_none(),
        "accepted sign-out was discarded by quit"
    );
}
