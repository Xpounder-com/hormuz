use super::*;
use crate::presentation;
use hormuz_client_core::ReadingStatus;
use hormuz_client_platform::{PlatformError, PrivateFiles, SecretRecord};
use hormuz_client_session::{Reply, SystemClock};
use hormuz_client_transport::{ErrorKind, RequestOutcome, TransportError};
use serde_json::{json, Value};
use std::collections::BTreeMap;
use std::sync::atomic::{AtomicBool, AtomicU64, AtomicUsize, Ordering};
use std::time::{Duration, Instant};

fn fixture() -> Value {
    serde_json::from_str(include_str!(
        "../../../../tests/fixtures/native_client/v1/snapshots.json"
    ))
    .unwrap()
}
fn profile() -> ConnectionProfile {
    ConnectionProfile::from_json(&serde_json::to_vec(&fixture()["profile"]).unwrap()).unwrap()
}
#[derive(Clone, Default)]
struct Store {
    bytes: Arc<Mutex<Option<Vec<u8>>>>,
    locked: Arc<AtomicBool>,
}
impl CredentialStore for Store {
    fn load(&self) -> hormuz_client_platform::Result<Option<SecretRecord>> {
        if self.locked.load(Ordering::SeqCst) {
            return Err(PlatformError::SecureStoreUnavailable);
        }
        self.bytes
            .lock()
            .unwrap()
            .as_ref()
            .map(|b| SecretRecord::new(b.clone()))
            .transpose()
    }
    fn save(&self, record: &SecretRecord) -> hormuz_client_platform::Result<()> {
        if self.locked.load(Ordering::SeqCst) {
            return Err(PlatformError::SecureStoreUnavailable);
        }
        *self.bytes.lock().unwrap() = Some(record.expose().to_vec());
        Ok(())
    }
    fn delete(&self) -> hormuz_client_platform::Result<()> {
        *self.bytes.lock().unwrap() = None;
        Ok(())
    }
    fn maximum_record_bytes(&self) -> usize {
        2560
    }
}
#[derive(Clone, Default)]
struct Coordinator(Arc<Mutex<BTreeMap<String, Vec<u8>>>>, Arc<AtomicBool>);
struct Guard(Coordinator);
impl Drop for Guard {
    fn drop(&mut self) {
        self.0 .1.store(false, Ordering::SeqCst);
    }
}
impl RefreshCoordinator for Coordinator {
    type Guard = Guard;
    fn try_acquire(&self) -> hormuz_client_platform::Result<Guard> {
        if self.1.swap(true, Ordering::SeqCst) {
            Err(PlatformError::Busy)
        } else {
            Ok(Guard(self.clone()))
        }
    }
}
impl PrivateFiles for Guard {
    fn read(&self, name: &str) -> hormuz_client_platform::Result<Option<Vec<u8>>> {
        Ok(self.0 .0.lock().unwrap().get(name).cloned())
    }
    fn write(
        &self,
        name: &str,
        bytes: &[u8],
        expected: Option<&[u8]>,
    ) -> hormuz_client_platform::Result<()> {
        let mut files = self.0 .0.lock().unwrap();
        if files.get(name).map(Vec::as_slice) != expected {
            return Err(PlatformError::Changed);
        }
        files.insert(name.into(), bytes.to_vec());
        Ok(())
    }
}
#[derive(Clone)]
struct TestClock(Arc<SystemClock>, Arc<AtomicU64>);
impl Default for TestClock {
    fn default() -> Self {
        Self(
            Arc::new(SystemClock::default()),
            Arc::new(AtomicU64::new(0)),
        )
    }
}
impl Clock for TestClock {
    fn now(&self) -> f64 {
        self.0.now() + self.1.load(Ordering::SeqCst) as f64
    }
    fn elapsed(&self) -> Duration {
        self.0.elapsed() + Duration::from_secs(self.1.load(Ordering::SeqCst))
    }
    fn sleep(&self, duration: Duration) {
        thread::sleep(duration);
    }
}
#[derive(Clone, Default)]
struct Transport {
    mode: Arc<AtomicUsize>, // 1 offline, 2 unauthorized, 3 blocked usage, 4 enrollment pending, 5 wrong scope
    requests: Arc<AtomicUsize>,
    usage: Arc<AtomicUsize>,
    entered: Arc<AtomicBool>,
    cancelled: Arc<AtomicBool>,
}
fn unavailable() -> TransportError {
    TransportError {
        kind: ErrorKind::Cancelled,
        outcome: RequestOutcome::ResponseReceived,
    }
}
impl SessionTransport for Transport {
    fn request(
        &self,
        profile: &ConnectionProfile,
        path: &str,
        _body: Option<&[u8]>,
        _access: Option<&str>,
        operation: &Operation,
    ) -> Result<Reply, TransportError> {
        self.requests.fetch_add(1, Ordering::SeqCst);
        let mode = self.mode.load(Ordering::SeqCst);
        if mode == 1 {
            return Err(unavailable());
        }
        let iso = |offset| {
            time::OffsetDateTime::from_unix_timestamp(SystemClock::default().now() as i64 + offset)
                .unwrap()
                .format(&time::format_description::well_known::Rfc3339)
                .unwrap()
        };
        let (status, body) = match path {
            "/v1/auth/enrollments" => (
                201,
                json!({"enrollment_id":"a".repeat(32),"login_url":format!("{}/v1/auth/login?enrollment={}",profile.gateway(),"a".repeat(32)),"expires_at":iso(600),"poll_interval_seconds":1}),
            ),
            p if p.ends_with("/redeem") => {
                if mode == 4 {
                    self.entered.store(true, Ordering::SeqCst);
                    (409, json!({}))
                } else {
                    (
                        200,
                        json!({"access_token":format!("hox_a_{}","a".repeat(43)),"refresh_token":format!("hox_r_{}","a".repeat(43)),"token_type":"Bearer","access_expires_at":iso(600),"session_expires_at":iso(43200)}),
                    )
                }
            }
            "/v1/gateway/whoami" => {
                if mode == 2 {
                    (401, json!({}))
                } else {
                    (200, fixture()["identity"].clone())
                }
            }
            "/v1/gateway/usage" => {
                self.usage.fetch_add(1, Ordering::SeqCst);
                if mode == 3 {
                    self.entered.store(true, Ordering::SeqCst);
                    let deadline = Instant::now() + Duration::from_secs(3);
                    while !operation.is_cancelled() && Instant::now() < deadline {
                        thread::sleep(Duration::from_millis(1));
                    }
                    assert!(operation.is_cancelled(), "owned worker was not cancelled");
                    self.cancelled.store(true, Ordering::SeqCst);
                    // Deliberately deliver a late successful response after cancellation.
                }
                let mut usage = fixture()["usage"].clone();
                if mode == 5 {
                    usage["scope"] = json!("organization");
                }
                (200, usage)
            }
            "/v1/auth/logout" => (200, json!({"revoked":true})),
            _ => panic!("unexpected synthetic endpoint"),
        };
        Ok(Reply::new(status, serde_json::to_vec(&body).unwrap()).unwrap())
    }
}
#[derive(Default)]
struct Browser(Arc<AtomicUsize>);
impl BrowserOpener for Browser {
    fn open_authentication_url(&self, url: &str) -> hormuz_client_platform::Result<()> {
        assert!(url.starts_with("https://gateway.example.test/v1/auth/login?enrollment="));
        self.0.fetch_add(1, Ordering::SeqCst);
        Ok(())
    }
}
type TestConnection = Connection<Coordinator, Store, Transport, TestClock, Browser>;
fn start(store: Store, transport: Transport, clock: TestClock) -> TestConnection {
    Connection::start(
        SessionController::new(Coordinator::default(), store, transport, clock),
        Browser::default(),
        || true,
    )
    .unwrap()
}
fn wait(connection: &TestConnection, condition: impl Fn(&View) -> bool) -> View {
    let deadline = Instant::now() + Duration::from_secs(4);
    loop {
        let view = connection.view();
        if condition(&view) {
            return view;
        }
        assert!(
            Instant::now() < deadline,
            "expected worker transition did not arrive: {:?}",
            view.phase
        );
        thread::sleep(Duration::from_millis(2));
    }
}
fn sign_in(connection: &TestConnection) {
    wait(connection, |v| v.phase == Phase::Ready);
    assert!(connection.sign_in(profile()));
    wait(connection, |v| v.phase == Phase::Ready && v.has_session());
}
fn open(connection: &TestConnection) {
    connection.lifecycle(LifecycleEvent::SessionUnlocked);
    connection.visibility(DashboardVisibility::Detail);
}
fn current(connection: &TestConnection) -> View {
    wait(connection, |v| {
        v.snapshot.reading().status() == ReadingStatus::Current
    })
}
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

#[cfg(windows)]
#[test]
fn native_connected_controls_show_scoped_usage_and_clear_on_sign_out() {
    use crate::{native, options::Options};
    use hormuz_client_platform::PrivateDirectory;
    use windows_sys::Win32::{Foundation::*, UI::WindowsAndMessaging::*};
    fn wide(text: &str) -> Vec<u16> {
        text.encode_utf16().chain(Some(0)).collect()
    }
    fn text(window: HWND, id: i32) -> String {
        let mut value = [0; 1024];
        let length = unsafe {
            GetWindowTextW(
                GetDlgItem(window, id),
                value.as_mut_ptr(),
                value.len() as i32,
            )
        };
        String::from_utf16_lossy(&value[..length as usize])
    }
    fn send(window: HWND, message: u32, command: usize) {
        let mut result = 0;
        assert_ne!(
            unsafe {
                SendMessageTimeoutW(
                    window,
                    message,
                    command,
                    0,
                    SMTO_ABORTIFHUNG,
                    2000,
                    &mut result,
                )
            },
            0,
            "native test command timed out"
        );
    }
    fn until(mut condition: impl FnMut() -> bool) {
        let deadline = Instant::now() + Duration::from_secs(8);
        while !condition() {
            assert!(
                Instant::now() < deadline,
                "native connected transition timed out"
            );
            thread::sleep(Duration::from_millis(5));
        }
    }
    struct WindowCleanup(HWND);
    impl Drop for WindowCleanup {
        fn drop(&mut self) {
            unsafe {
                PostMessageW(self.0, WM_COMMAND, 104, 0);
            }
        }
    }
    let temporary = tempfile::tempdir().unwrap();
    let root = temporary.path().join("connected-private");
    let directory = PrivateDirectory::open(&root).unwrap();
    let owner = directory.try_claim_instance().unwrap();
    let store = Store::default();
    let worker_store = store.clone();
    let transport = Transport::default();
    let worker_transport = transport.clone();
    let clock = TestClock::default();
    let worker_clock = clock.clone();
    // Seed a saved account through the real controller and private profile file,
    // then deny custody for the GUI's initial restore. No native user credential
    // target is read or written by this synthetic test.
    SessionController::new(
        directory.clone(),
        store.clone(),
        transport.clone(),
        clock.clone(),
    )
    .sign_in(&profile(), &Browser::default(), &Operation::default())
    .unwrap();
    store.locked.store(true, Ordering::SeqCst);
    let (finished, completion) = std::sync::mpsc::channel();
    let worker = thread::spawn(move || {
        let result = native::run_with_factory(
            Options {
                smoke: false,
                preview: false,
                state_directory: None,
            },
            directory,
            owner,
            move |directory, notify| {
                let connection = Connection::start(
                    SessionController::new(directory, worker_store, worker_transport, worker_clock),
                    Browser::default(),
                    notify,
                )?;
                // Synthetic harness explicitly controls lifecycle. Native WTS/power
                // behavior still requires its separate real desktop acceptance.
                connection.lifecycle(LifecycleEvent::SessionUnlocked);
                Ok(Box::new(connection) as Box<dyn DesktopConnection>)
            },
        );
        finished.send(result).unwrap();
    });
    let mut window = std::ptr::null_mut();
    until(|| {
        window = unsafe {
            FindWindowW(
                wide("HormuzNativeCompanionPreview").as_ptr(),
                wide("Hormuz companion").as_ptr(),
            )
        };
        if window.is_null() {
            return false;
        }
        let mut owner = 0;
        unsafe {
            GetWindowThreadProcessId(window, &mut owner);
        }
        owner == unsafe { windows_sys::Win32::System::Threading::GetCurrentProcessId() }
    });
    let cleanup = WindowCleanup(window);
    until(|| text(window, 301).contains("credential store"));
    assert!(text(window, 201).is_empty());
    assert!(text(window, 202).is_empty());
    store.locked.store(false, Ordering::SeqCst);
    send(window, WM_COMMAND, 111);
    until(|| text(window, 300).starts_with("Current"));
    assert_eq!(text(window, 201), "https://gateway.example.test");
    assert_eq!(text(window, 202), "org-a");
    assert_eq!(text(window, 203), "openai-primary");
    send(window, WM_COMMAND, 110);
    until(|| text(window, 301).contains("Enter your gateway"));
    assert_eq!(text(window, 302), "Your requests: —");
    for (id, value) in [
        (201, "https://gateway.example.test"),
        (202, "org-a"),
        (203, "openai-primary"),
    ] {
        assert_ne!(
            unsafe { SetWindowTextW(GetDlgItem(window, id), wide(value).as_ptr()) },
            0
        );
    }
    send(window, WM_COMMAND, 109);
    until(|| text(window, 300).starts_with("Current"));
    assert_eq!(
        text(window, 302),
        format!(
            "Your requests: {}",
            fixture()["usage"]["requests"].as_i64().unwrap()
        )
    );
    assert!(!text(window, 303).contains("synthetic"));
    assert!(!text(window, 301).contains("hox_"));
    send(window, WM_COMMAND, 101);
    assert_eq!(unsafe { IsWindowVisible(GetDlgItem(window, 302)) }, 0);
    send(window, WM_CLOSE, 0);
    PrivateDirectory::open(&root)
        .unwrap()
        .request_reopen()
        .unwrap();
    until(|| unsafe { IsWindowVisible(window) != 0 && IsIconic(window) == 0 });
    send(window, WM_COMMAND, 101);
    transport.mode.store(1, Ordering::SeqCst);
    clock.1.store(10, Ordering::SeqCst);
    send(window, WM_COMMAND, 111);
    until(|| text(window, 300).starts_with("Offline"));
    assert!(!text(window, 302).ends_with('—'));
    transport.mode.store(0, Ordering::SeqCst);
    send(window, WM_COMMAND, 110);
    until(|| text(window, 302).ends_with('—') && store.load().unwrap().is_none());
    send(window, WM_COMMAND, 104);
    assert_eq!(completion.recv_timeout(Duration::from_secs(8)).unwrap(), 0);
    worker.join().unwrap();
    std::mem::forget(cleanup); // Already confirmed exact owned GUI exit.
    assert!(PrivateDirectory::open(&root)
        .unwrap()
        .try_claim_instance()
        .is_ok());
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
