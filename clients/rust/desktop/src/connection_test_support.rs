use crate::connection::{Connection, Phase, View};
use hormuz_client_core::ConnectionProfile;
use hormuz_client_platform::{BrowserOpener, CredentialStore, LifecycleEvent, RefreshCoordinator};
use hormuz_client_session::{Clock, DashboardVisibility, Operation, SessionController, SessionTransport};
use std::{sync::{Arc, Mutex}, thread};
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
