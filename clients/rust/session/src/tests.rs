use super::*;
use hormuz_client_platform::SecretRecord;
use hormuz_client_transport::{ErrorKind, RequestOutcome, TransportError};
use serde_json::{json, Value};
use std::collections::{BTreeMap, VecDeque};
use std::sync::{Arc, Mutex};

const NOW: f64 = 1_780_000_000.0;
fn fixtures() -> Value {
    serde_json::from_str(include_str!(
        "../../../../tests/fixtures/native_client/v1/sessions.json"
    ))
    .unwrap()
}
fn profile() -> ConnectionProfile {
    ConnectionProfile::from_json(&serde_json::to_vec(&fixtures()["record"]["profile"]).unwrap())
        .unwrap()
}
fn record() -> SessionRecord {
    SessionRecord::from_secret(
        &SecretRecord::new(serde_json::to_vec(&fixtures()["record"]).unwrap()).unwrap(),
    )
    .unwrap()
}
fn iso(value: f64) -> String {
    time::OffsetDateTime::from_unix_timestamp(value as i64)
        .unwrap()
        .format(&time::format_description::well_known::Rfc3339)
        .unwrap()
}
fn pair(letter: &str, now: f64) -> Value {
    json!({"access_token":format!("hox_a_{}",letter.repeat(43)),"refresh_token":format!("hox_r_{}",letter.repeat(43)),
        "token_type":"Bearer","access_expires_at":iso(now+600.0),"session_expires_at":iso(NOW+43_200.0)})
}
fn identity() -> Value {
    json!({"schema_id":"hormuz.gateway-identity","schema_version":1,"actor_id":"alice","actor_name":"Alice",
        "team_id":"engineering","team_name":"Engineering","organization_id":"org-a","identity_type":"human",
        "allowed_clients":["codex"],"authentication_source":"session:fixture"})
}

#[derive(Default)]
struct Stored {
    bytes: Option<Vec<u8>>,
    saves: usize,
    fail_save: usize,
    crash_save: usize,
    fail_delete: bool,
    cancel_save: Option<(usize, Operation)>,
}
#[derive(Clone, Default)]
struct Store(Arc<Mutex<Stored>>);
impl CredentialStore for Store {
    fn load(&self) -> hormuz_client_platform::Result<Option<SecretRecord>> {
        Ok(self
            .0
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .bytes
            .as_ref()
            .map(|b| SecretRecord::new(b.clone()).unwrap()))
    }
    fn save(&self, record: &SecretRecord) -> hormuz_client_platform::Result<()> {
        let mut state = self.0.lock().unwrap_or_else(|e| e.into_inner());
        state.saves += 1;
        if state.saves == state.fail_save {
            return Err(PlatformError::SecureStoreUnavailable);
        }
        state.bytes = Some(record.expose().to_vec());
        if let Some((at, operation)) = &state.cancel_save {
            if *at == state.saves {
                operation.cancel();
            }
        }
        assert_ne!(
            state.saves, state.crash_save,
            "synthetic crash after durable intent"
        );
        Ok(())
    }
    fn delete(&self) -> hormuz_client_platform::Result<()> {
        let mut state = self.0.lock().unwrap_or_else(|e| e.into_inner());
        if state.fail_delete {
            return Err(PlatformError::SecureStoreUnavailable);
        }
        state.bytes = None;
        Ok(())
    }
    fn maximum_record_bytes(&self) -> usize {
        2560
    }
}
impl Store {
    fn state(&self) -> Option<SessionState> {
        self.load()
            .unwrap()
            .map(|r| SessionRecord::from_secret(&r).unwrap().state)
    }
}

#[derive(Clone, Default)]
struct Coordinator {
    busy: Arc<AtomicBool>,
    files: Arc<Mutex<BTreeMap<String, Vec<u8>>>>,
}
struct Guard(Coordinator);
impl Drop for Guard {
    fn drop(&mut self) {
        self.0.busy.store(false, Ordering::SeqCst);
    }
}
impl PrivateFiles for Guard {
    fn read(&self, name: &str) -> hormuz_client_platform::Result<Option<Vec<u8>>> {
        Ok(self.0.files.lock().unwrap().get(name).cloned())
    }
    fn write(
        &self,
        name: &str,
        value: &[u8],
        expected: Option<&[u8]>,
    ) -> hormuz_client_platform::Result<()> {
        let mut files = self.0.files.lock().unwrap();
        if files.get(name).map(Vec::as_slice) != expected {
            return Err(PlatformError::Changed);
        }
        files.insert(name.into(), value.to_vec());
        Ok(())
    }
}
impl RefreshCoordinator for Coordinator {
    type Guard = Guard;
    fn try_acquire(&self) -> hormuz_client_platform::Result<Guard> {
        if self.busy.swap(true, Ordering::SeqCst) {
            Err(PlatformError::Busy)
        } else {
            Ok(Guard(self.clone()))
        }
    }
}

#[derive(Clone, Default)]
struct FakeClock(Arc<Mutex<Duration>>);
impl Clock for FakeClock {
    fn now(&self) -> f64 {
        NOW + self.elapsed().as_secs_f64()
    }
    fn elapsed(&self) -> Duration {
        *self.0.lock().unwrap()
    }
    fn sleep(&self, duration: Duration) {
        *self.0.lock().unwrap() += duration;
    }
}
#[derive(Default)]
struct Browser(Mutex<Vec<String>>);
impl BrowserOpener for Browser {
    fn open_authentication_url(&self, url: &str) -> hormuz_client_platform::Result<()> {
        self.0.lock().unwrap().push(url.into());
        Ok(())
    }
}
struct Step {
    path: &'static str,
    result: Result<(u16, Value), TransportError>,
    state: Option<SessionState>,
    cancel: bool,
}
fn step(path: &'static str, status: u16, body: Value) -> Step {
    Step {
        path,
        result: Ok((status, body)),
        state: None,
        cancel: false,
    }
}
fn revoked() -> Step {
    step("/v1/auth/logout", 200, json!({"revoked":true}))
}
fn offline(path: &'static str) -> Step {
    Step {
        path,
        result: Err(TransportError {
            kind: ErrorKind::Offline,
            outcome: RequestOutcome::Unconfirmed,
        }),
        state: None,
        cancel: false,
    }
}
#[derive(Clone)]
struct Transport {
    queue: Arc<Mutex<VecDeque<Step>>>,
    store: Store,
    calls: Arc<Mutex<Vec<String>>>,
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
        let item = self
            .queue
            .lock()
            .unwrap()
            .pop_front()
            .expect("unexpected network request");
        assert!(path.ends_with(item.path), "unexpected endpoint");
        assert_eq!(profile.gateway(), "https://gateway.example.test");
        if let Some(state) = item.state {
            assert_eq!(self.store.state(), Some(state));
        }
        self.calls.lock().unwrap().push(path.into());
        if item.cancel {
            operation.cancel();
        }
        item.result
            .map(|(status, body)| Reply::new(status, serde_json::to_vec(&body).unwrap()).unwrap())
    }
}
struct Harness {
    coordinator: Coordinator,
    store: Store,
    transport: Transport,
    clock: FakeClock,
}
impl Harness {
    fn new(steps: Vec<Step>) -> Self {
        let store = Store::default();
        Self {
            coordinator: Coordinator::default(),
            transport: Transport {
                queue: Arc::new(Mutex::new(steps.into())),
                store: store.clone(),
                calls: Arc::default(),
            },
            store,
            clock: FakeClock::default(),
        }
    }
    fn controller(&self) -> SessionController<Coordinator, Store, Transport, FakeClock> {
        SessionController::new(
            self.coordinator.clone(),
            self.store.clone(),
            self.transport.clone(),
            self.clock.clone(),
        )
    }
    fn seed(&self, record: &SessionRecord) {
        self.coordinator.files.lock().unwrap().insert(
            "profile.json".into(),
            serde_json::to_vec(&record.profile).unwrap(),
        );
        self.store.save(&record.to_secret().unwrap()).unwrap();
    }
    fn done(&self) {
        assert!(self.transport.queue.lock().unwrap().is_empty());
    }
}
fn enrollment() -> Vec<Step> {
    vec![
        step(
            "/v1/auth/enrollments",
            201,
            json!({"enrollment_id":"e".repeat(32),"login_url":format!("https://gateway.example.test/v1/auth/login?enrollment={}","e".repeat(32)),"expires_at":iso(NOW+600.0),"poll_interval_seconds":1}),
        ),
        step("/redeem", 409, json!({"error":"pending"})),
        step("/redeem", 200, pair("a", NOW + 1.0)),
        step("/v1/gateway/whoami", 200, identity()),
    ]
}

#[test]
fn swift_record_codec_preserves_dates_profiles_and_all_states() {
    for state in [
        SessionState::Active,
        SessionState::RefreshPending,
        SessionState::RevocationPending,
    ] {
        let mut value = record();
        value.state = state;
        let bytes = value.to_secret().unwrap();
        let mut expected = fixtures()["record"].clone();
        expected["state"] = serde_json::to_value(state).unwrap();
        assert_eq!(
            serde_json::from_slice::<Value>(bytes.expose()).unwrap(),
            expected
        );
        let roundtrip = SessionRecord::from_secret(&bytes).unwrap();
        assert_eq!(roundtrip.state, state);
        assert_eq!(roundtrip.access_expires, value.access_expires);
        assert_eq!(roundtrip.session_expires, value.session_expires);
        assert!(roundtrip.profile == value.profile);
        assert_eq!(format!("{roundtrip:?}"), "SessionRecord(<redacted>)");
    }
    for field in ["accessToken", "refreshToken", "state", "accessExpiresAt"] {
        let mut wire = fixtures()["record"].clone();
        wire[field] = json!("invalid");
        assert!(SessionRecord::from_secret(
            &SecretRecord::new(serde_json::to_vec(&wire).unwrap()).unwrap()
        )
        .is_err());
    }
}

#[test]
fn shared_transition_vectors_drive_real_controller() {
    for case in fixtures()["cases"].as_array().unwrap() {
        let steps = if case["refreshes"] == 1 {
            vec![step("/v1/auth/refresh", 200, pair("b", NOW))]
        } else {
            vec![]
        };
        let h = Harness::new(steps);
        let mut value = record();
        value.state = serde_json::from_value(case["state"].clone()).unwrap();
        value.access_expires = NOW - FOUNDATION_EPOCH + case["access_remaining"].as_f64().unwrap();
        value.session_expires =
            NOW - FOUNDATION_EPOCH + case["session_remaining"].as_f64().unwrap();
        h.seed(&value);
        let result = h
            .controller()
            .access_credential(&profile(), false, &Operation::default());
        if case["result"] == "credential" {
            assert!(result.is_ok(), "{}", case["id"]);
        } else {
            assert_eq!(
                serde_json::to_value(result.unwrap_err()).unwrap(),
                case["result"]
            );
        }
        h.done();
    }
}

#[test]
fn enrollment_polls_verifies_identity_and_never_writes_credentials_to_files() {
    let h = Harness::new(enrollment());
    let browser = Browser::default();
    h.controller()
        .sign_in(&profile(), &browser, &Operation::default())
        .unwrap();
    assert_eq!(h.store.state(), Some(SessionState::Active));
    h.done();
    assert_eq!(browser.0.lock().unwrap().len(), 1);
    for bytes in h.coordinator.files.lock().unwrap().values() {
        assert!(!String::from_utf8_lossy(bytes).contains("hox_"));
    }
}

#[test]
fn bad_enrollment_and_oversize_profile_stop_before_browser_or_redemption() {
    for field in [
        "login_url",
        "enrollment_id",
        "expires_at",
        "poll_interval_seconds",
    ] {
        let mut steps = enrollment();
        steps.truncate(1);
        if let Ok((_, body)) = &mut steps[0].result {
            body[field] = json!("invalid");
        }
        let h = Harness::new(steps);
        let browser = Browser::default();
        assert!(h
            .controller()
            .sign_in(&profile(), &browser, &Operation::default())
            .is_err());
        assert!(browser.0.lock().unwrap().is_empty());
        assert!(h.store.state().is_none());
        h.done();
    }
    let mut wire = fixtures()["record"]["profile"].clone();
    wire["issuer"] = json!("i".repeat(2048));
    wire["organization"] = json!("o".repeat(200));
    let large = ConnectionProfile::from_json(&serde_json::to_vec(&wire).unwrap()).unwrap();
    let h = Harness::new(vec![]);
    assert_eq!(
        h.controller()
            .sign_in(&large, &Browser::default(), &Operation::default()),
        Err(ClientError::SecureStoreUnavailable)
    );
    h.done();
}

#[test]
fn enrollment_polling_has_a_monotonic_cap_even_with_distant_server_expiry() {
    let mut steps = enrollment();
    steps.truncate(1);
    steps[0].result.as_mut().unwrap().1["expires_at"] = json!(iso(NOW + 100_000_000.0));
    for _ in 0..900 {
        steps.push(step("/redeem", 409, json!({})));
    }
    let h = Harness::new(steps);
    assert_eq!(
        h.controller()
            .sign_in(&profile(), &Browser::default(), &Operation::default()),
        Err(ClientError::LoginTimedOut)
    );
    assert_eq!(h.clock.elapsed(), POLL_BUDGET);
    h.done();
}

#[test]
fn identity_denial_store_failure_and_cancel_after_redemption_revoke_independently() {
    for scenario in 0..4 {
        let mut steps = enrollment();
        if scenario == 0 {
            steps.last_mut().unwrap().result.as_mut().unwrap().1["organization_id"] =
                json!("foreign");
        }
        if scenario == 1 || scenario == 2 {
            steps.pop();
        }
        if scenario == 2 {
            steps.last_mut().unwrap().cancel = true;
        }
        steps.push(revoked());
        let h = Harness::new(steps);
        let operation = Operation::default();
        if scenario == 1 {
            h.store.0.lock().unwrap().fail_save = 1;
        }
        if scenario == 3 {
            h.store.0.lock().unwrap().cancel_save = Some((2, operation.clone()));
        }
        assert!(h
            .controller()
            .sign_in(&profile(), &Browser::default(), &operation)
            .is_err());
        assert!(h.store.state().is_none());
        h.done();
    }
}

#[test]
fn failed_identity_and_failed_cleanup_preserve_only_revocation_intent() {
    let mut steps = enrollment();
    steps.last_mut().unwrap().result = Err(TransportError {
        kind: ErrorKind::Offline,
        outcome: RequestOutcome::Unconfirmed,
    });
    steps.push(offline("/v1/auth/logout"));
    let h = Harness::new(steps);
    assert!(h
        .controller()
        .sign_in(&profile(), &Browser::default(), &Operation::default())
        .is_err());
    assert_eq!(h.store.state(), Some(SessionState::RevocationPending));
    assert_eq!(
        h.controller()
            .access_credential(&profile(), false, &Operation::default())
            .unwrap_err(),
        ClientError::LogoutPending
    );
    h.done();
}

#[test]
fn ambiguous_refresh_and_crash_after_intent_never_replay_on_restart() {
    for crash in [false, true] {
        let mut steps = if crash {
            vec![]
        } else {
            vec![offline("/v1/auth/refresh")]
        };
        if !crash {
            steps[0].state = Some(SessionState::RefreshPending);
        }
        let h = Harness::new(steps);
        h.seed(&record());
        if crash {
            h.store.0.lock().unwrap().crash_save = 2;
        }
        let _ = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
            h.controller()
                .access_credential(&profile(), true, &Operation::default())
        }));
        assert_eq!(h.store.state(), Some(SessionState::RefreshPending));
        assert_eq!(
            h.controller()
                .access_credential(&profile(), false, &Operation::default())
                .unwrap_err(),
            ClientError::RefreshInterrupted
        );
        h.done();
    }
}

#[test]
fn invalid_rotation_commit_failure_and_cancel_preserve_refresh_recovery() {
    for scenario in 0..4 {
        let mut value = pair("b", NOW);
        if scenario == 0 {
            value["session_expires_at"] = json!(iso(NOW + 42_000.0));
        }
        if scenario == 1 {
            value["access_token"] = json!("hox_a_".to_owned() + &"a".repeat(43));
        }
        let mut refresh = step("/v1/auth/refresh", 200, value);
        refresh.cancel = scenario == 3;
        let h = Harness::new(vec![refresh, revoked()]);
        let mut old = record();
        old.session_expires = NOW - FOUNDATION_EPOCH + 43_200.0;
        h.seed(&old);
        if scenario == 2 {
            h.store.0.lock().unwrap().fail_save = 3;
        }
        assert!(h
            .controller()
            .access_credential(&profile(), true, &Operation::default())
            .is_err());
        assert_eq!(h.store.state(), Some(SessionState::RefreshPending));
        h.done();
    }
}

#[test]
fn signout_uses_authoritative_profile_survives_failure_and_retries_only_logout() {
    let mut first = offline("/v1/auth/logout");
    first.state = Some(SessionState::RevocationPending);
    let h = Harness::new(vec![first, revoked(), revoked()]);
    h.seed(&record());
    h.coordinator.files.lock().unwrap().remove("profile.json");
    let c = h.controller();
    assert_eq!(
        c.sign_out(&Operation::default()),
        Err(ClientError::LogoutPending)
    );
    assert_eq!(
        c.access_credential(&profile(), false, &Operation::default())
            .unwrap_err(),
        ClientError::LogoutPending
    );
    h.store.0.lock().unwrap().fail_delete = true;
    assert_eq!(
        h.controller().sign_out(&Operation::default()),
        Err(ClientError::SecureStoreUnavailable)
    );
    assert_eq!(h.store.state(), Some(SessionState::RevocationPending));
    h.store.0.lock().unwrap().fail_delete = false;
    h.controller().sign_out(&Operation::default()).unwrap();
    assert!(h.store.state().is_none());
    h.done();
}

#[test]
fn pending_write_failure_prevents_egress_and_local_signout_latches_closed() {
    let h = Harness::new(vec![]);
    h.seed(&record());
    h.store.0.lock().unwrap().fail_save = 2;
    let c = h.controller();
    assert_eq!(
        c.sign_out(&Operation::default()),
        Err(ClientError::SecureStoreUnavailable)
    );
    assert_eq!(
        c.access_credential(&profile(), false, &Operation::default())
            .unwrap_err(),
        ClientError::LogoutPending
    );
    h.done();
    let h = Harness::new(vec![]);
    h.seed(&record());
    h.store.0.lock().unwrap().fail_save = 2;
    assert_eq!(
        h.controller()
            .access_credential(&profile(), true, &Operation::default())
            .unwrap_err(),
        ClientError::SecureStoreUnavailable
    );
    h.done();
}

#[test]
fn interrupted_signout_retains_intent_and_cancelled_enrollment_never_starts() {
    let h = Harness::new(vec![]);
    h.seed(&record());
    h.store.0.lock().unwrap().crash_save = 2;
    let _ = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
        h.controller().sign_out(&Operation::default())
    }));
    assert_eq!(h.store.state(), Some(SessionState::RevocationPending));
    assert_eq!(
        h.controller()
            .access_credential(&profile(), false, &Operation::default())
            .unwrap_err(),
        ClientError::LogoutPending
    );
    let operation = Operation::default();
    operation.cancel();
    assert!(h
        .controller()
        .sign_in(&profile(), &Browser::default(), &operation)
        .is_err());
    h.done();
}

#[test]
fn transport_safety_errors_remain_specific_until_a_refresh_intent_requires_recovery() {
    for (kind, expected) in [
        (ErrorKind::Redirect, ClientError::UnexpectedRedirect),
        (ErrorKind::ResponseTooLarge, ClientError::ResponseTooLarge),
        (ErrorKind::InvalidResponse, ClientError::InvalidResponse),
        (ErrorKind::Offline, ClientError::GatewayUnavailable),
    ] {
        let error = TransportError {
            kind,
            outcome: RequestOutcome::Unconfirmed,
        };
        let h = Harness::new(vec![Step {
            path: "/v1/auth/enrollments",
            result: Err(error),
            state: None,
            cancel: false,
        }]);
        let browser = Browser::default();
        assert_eq!(
            h.controller()
                .sign_in(&profile(), &browser, &Operation::default()),
            Err(expected)
        );
        assert!(browser.0.lock().unwrap().is_empty());
        h.done();

        let mut steps = enrollment();
        steps.last_mut().unwrap().result = Err(error);
        steps.push(revoked());
        let h = Harness::new(steps);
        assert_eq!(
            h.controller()
                .sign_in(&profile(), &Browser::default(), &Operation::default()),
            Err(expected)
        );
        assert!(h.store.state().is_none());
        h.done();

        let h = Harness::new(vec![Step {
            path: "/v1/auth/refresh",
            result: Err(error),
            state: Some(SessionState::RefreshPending),
            cancel: false,
        }]);
        h.seed(&record());
        assert_eq!(
            h.controller()
                .access_credential(&profile(), true, &Operation::default())
                .unwrap_err(),
            ClientError::RefreshInterrupted
        );
        assert_eq!(h.store.state(), Some(SessionState::RefreshPending));
        h.done();
    }
}

#[test]
fn changed_profile_cannot_redirect_access_and_lock_wait_is_bounded_cancelable() {
    let h = Harness::new(vec![]);
    h.seed(&record());
    let mut other = fixtures()["record"]["profile"].clone();
    other["gateway"] = json!("https://foreign.test");
    h.coordinator
        .files
        .lock()
        .unwrap()
        .insert("profile.json".into(), serde_json::to_vec(&other).unwrap());
    assert_eq!(
        h.controller()
            .access_credential(&profile(), false, &Operation::default())
            .unwrap_err(),
        ClientError::IdentityMismatch
    );
    let _guard = h.coordinator.try_acquire().unwrap();
    assert!(matches!(
        h.controller().status(&Operation::default()),
        Err(ClientError::ProfileBusy)
    ));
    assert_eq!(h.clock.elapsed(), LOCK_BUDGET);
    let operation = Operation::default();
    operation.cancel();
    assert!(h.controller().status(&operation).is_err());
    h.done();
}

#[cfg(any(target_os = "macos", windows))]
#[test]
fn native_coordinator_serializes_concurrent_refresh_and_reuses_committed_record() {
    let temporary = tempfile::tempdir().unwrap();
    let directory =
        hormuz_client_platform::PrivateDirectory::open(&temporary.path().join("private")).unwrap();
    let h = Harness::new(vec![step("/v1/auth/refresh", 200, pair("b", NOW))]);
    let mut old = record();
    old.access_expires = NOW - FOUNDATION_EPOCH + 30.0;
    old.session_expires = NOW - FOUNDATION_EPOCH + 43_200.0;
    h.store.save(&old.to_secret().unwrap()).unwrap();
    directory
        .try_acquire()
        .unwrap()
        .write(
            "profile.json",
            &serde_json::to_vec(&profile()).unwrap(),
            None,
        )
        .unwrap();
    // Fixed wall time with real sleeps for actual competing worker threads.
    struct FixedClock(SystemClock);
    impl Clock for FixedClock {
        fn now(&self) -> f64 {
            NOW
        }
        fn elapsed(&self) -> Duration {
            self.0.elapsed()
        }
        fn sleep(&self, d: Duration) {
            std::thread::sleep(d)
        }
    }
    std::thread::scope(|scope| {
        let mut workers = vec![];
        for _ in 0..2 {
            let c = SessionController::new(
                directory.clone(),
                h.store.clone(),
                h.transport.clone(),
                FixedClock(SystemClock::default()),
            );
            workers.push(scope.spawn(move || {
                c.access_credential(&profile(), false, &Operation::default())
                    .unwrap()
            }));
        }
        let a = workers.remove(0).join().unwrap();
        let b = workers.remove(0).join().unwrap();
        assert_eq!(a.expose(), b.expose());
    });
    h.done();
    assert_eq!(h.transport.calls.lock().unwrap().len(), 1);
}

#[test]
fn real_transport_adapter_preserves_responses_and_cancels_active_requests() {
    use std::io::{Read, Write};
    use std::net::TcpListener;
    let listener = TcpListener::bind("127.0.0.1:0").unwrap();
    let mut wire = fixtures()["record"]["profile"].clone();
    wire["gateway"] = json!(format!("http://{}", listener.local_addr().unwrap()));
    wire["allowLoopbackHTTP"] = json!(true);
    let profile = ConnectionProfile::from_json(&serde_json::to_vec(&wire).unwrap()).unwrap();
    let (sent, received) = std::sync::mpsc::channel();
    let server = std::thread::spawn(move || {
        for index in 0..2 {
            let (mut stream, _) = listener.accept().unwrap();
            stream
                .set_read_timeout(Some(Duration::from_secs(5)))
                .unwrap();
            let mut bytes = [0u8; 4096];
            assert!(stream.read(&mut bytes).unwrap() > 0);
            if index == 0 {
                stream.write_all(b"HTTP/1.1 409 Conflict\r\nContent-Type: application/json\r\nContent-Length: 2\r\nConnection: close\r\n\r\n{}").unwrap();
            } else {
                sent.send(()).unwrap();
                // Wait for the client's cancellation to close this socket.
                let _ = stream.read(&mut bytes);
            }
        }
    });
    let reply = NativeTransport
        .request(
            &profile,
            "/v1/auth/enrollments",
            Some(b"{}"),
            None,
            &Operation::default(),
        )
        .unwrap();
    assert_eq!(reply.status, 409);
    let operation = Operation::default();
    std::thread::scope(|scope| {
        let worker = scope.spawn(|| {
            NativeTransport.request(&profile, "/v1/auth/refresh", Some(b"{}"), None, &operation)
        });
        received.recv_timeout(Duration::from_secs(5)).unwrap();
        operation.cancel();
        let error = worker.join().unwrap().unwrap_err();
        assert_eq!(error.kind, ErrorKind::Cancelled);
        assert_eq!(error.outcome, RequestOutcome::Unconfirmed);
    });
    server.join().unwrap();
}

fn usage_reply() -> Value {
    serde_json::from_str::<Value>(include_str!(
        "../../../../tests/fixtures/native_client/v1/snapshots.json"
    ))
    .unwrap()["usage"]
        .clone()
}

#[test]
fn coordinated_snapshot_retains_valid_data_without_inventing_zero_or_advancing_failed_time() {
    let h = Harness::new(vec![
        step("/v1/gateway/whoami", 200, identity()),
        step("/v1/gateway/usage", 200, usage_reply()),
        step("/v1/gateway/whoami", 200, identity()),
        offline("/v1/gateway/usage"),
        step("/v1/gateway/whoami", 200, identity()),
        step("/v1/gateway/usage", 200, json!({})),
        step("/v1/gateway/whoami", 401, json!({})),
    ]);
    h.seed(&record());
    let controller = h.controller();
    controller.take_snapshot_change();
    controller
        .refresh_snapshot(&profile(), &Operation::default())
        .unwrap();
    let first = controller.snapshot();
    assert!(first.identity().is_some());
    assert_eq!(first.scope(), "current_actor");
    assert!(controller.take_snapshot_change().is_some());
    h.clock.sleep(Duration::from_secs(1));
    assert!(controller
        .refresh_snapshot(&profile(), &Operation::default())
        .is_err());
    assert_eq!(
        controller.snapshot().reading().status(),
        ReadingStatus::Offline
    );
    assert!(controller.snapshot().reading().usage() == first.reading().usage());
    assert_eq!(
        controller.snapshot().reading().checked_at_epoch_seconds(),
        first.reading().checked_at_epoch_seconds()
    );
    assert!(controller
        .refresh_snapshot(&profile(), &Operation::default())
        .is_err());
    assert_eq!(
        controller.snapshot().reading().status(),
        ReadingStatus::Stale
    );
    assert!(controller
        .refresh_snapshot(&profile(), &Operation::default())
        .is_err());
    assert_eq!(
        controller.snapshot().reading().status(),
        ReadingStatus::NeedsAuthentication
    );
    assert!(controller.snapshot().total_tokens().is_none());
    h.done();
}

#[test]
fn external_session_replacement_clears_cached_identity_before_network_failure() {
    let h = Harness::new(vec![
        step("/v1/gateway/whoami", 200, identity()),
        step("/v1/gateway/usage", 200, usage_reply()),
        offline("/v1/gateway/whoami"),
    ]);
    h.seed(&record());
    let c = h.controller();
    c.refresh_snapshot(&profile(), &Operation::default())
        .unwrap();
    let replacement = serde_json::from_value::<CredentialPair>(pair("b", NOW))
        .unwrap()
        .record(&profile(), NOW)
        .unwrap();
    h.seed(&replacement);
    assert!(c
        .refresh_snapshot(&profile(), &Operation::default())
        .is_err());
    assert!(c.snapshot().identity().is_none());
    assert!(c.snapshot().total_tokens().is_none());
    h.done();
}

#[test]
fn known_local_rotation_preserves_cache_but_failed_refresh_removes_usable_identity() {
    let h = Harness::new(vec![
        step("/v1/gateway/whoami", 200, identity()),
        step("/v1/gateway/usage", 200, usage_reply()),
        step("/v1/auth/refresh", 200, pair("b", NOW)),
        offline("/v1/gateway/whoami"),
        offline("/v1/auth/refresh"),
    ]);
    let mut old = record();
    old.session_expires = NOW - FOUNDATION_EPOCH + 43_200.0;
    h.seed(&old);
    let c = h.controller();
    c.refresh_snapshot(&profile(), &Operation::default())
        .unwrap();
    let total = c.snapshot().total_tokens();
    c.access_credential(&profile(), true, &Operation::default())
        .unwrap();
    assert!(c
        .refresh_snapshot(&profile(), &Operation::default())
        .is_err());
    assert_eq!(c.snapshot().total_tokens(), total);
    assert!(c
        .access_credential(&profile(), true, &Operation::default())
        .is_err());
    assert!(c.snapshot().identity().is_none());
    assert_eq!(
        c.snapshot().reading().status(),
        ReadingStatus::NeedsAuthentication
    );
    h.done();
}

#[test]
fn signout_during_usage_fetch_immediately_clears_snapshot_and_discards_the_late_reply() {
    struct FixedClock(SystemClock);
    impl Clock for FixedClock {
        fn now(&self) -> f64 {
            NOW
        }
        fn elapsed(&self) -> Duration {
            self.0.elapsed()
        }
        fn sleep(&self, d: Duration) {
            self.0.sleep(d)
        }
    }
    struct BlockUsage {
        inner: Transport,
        started: std::sync::mpsc::Sender<()>,
        release: Mutex<std::sync::mpsc::Receiver<()>>,
    }
    impl SessionTransport for BlockUsage {
        fn request(
            &self,
            p: &ConnectionProfile,
            path: &str,
            body: Option<&[u8]>,
            access: Option<&str>,
            op: &Operation,
        ) -> Result<Reply, TransportError> {
            let reply = self.inner.request(p, path, body, access, op);
            if path == "/v1/gateway/usage" {
                self.started.send(()).unwrap();
                self.release
                    .lock()
                    .unwrap()
                    .recv_timeout(Duration::from_secs(5))
                    .unwrap();
            }
            reply
        }
    }
    let h = Harness::new(vec![
        step("/v1/gateway/whoami", 200, identity()),
        step("/v1/gateway/usage", 200, usage_reply()),
        revoked(),
    ]);
    h.seed(&record());
    let (started, received) = std::sync::mpsc::channel();
    let (release, waiting) = std::sync::mpsc::channel();
    let c = SessionController::new(
        h.coordinator.clone(),
        h.store.clone(),
        BlockUsage {
            inner: h.transport.clone(),
            started,
            release: Mutex::new(waiting),
        },
        FixedClock(SystemClock::default()),
    );
    std::thread::scope(|scope| {
        let refresh = scope.spawn(|| c.refresh_snapshot(&profile(), &Operation::default()));
        received.recv_timeout(Duration::from_secs(5)).unwrap();
        let logout = scope.spawn(|| c.sign_out(&Operation::default()));
        let deadline = std::time::Instant::now() + Duration::from_secs(5);
        while c.snapshot().reading().status() != ReadingStatus::NeedsAuthentication {
            assert!(std::time::Instant::now() < deadline);
            std::thread::sleep(Duration::from_millis(1));
        }
        release.send(()).unwrap();
        assert!(refresh.join().unwrap().is_err());
        logout.join().unwrap().unwrap();
    });
    assert!(c.snapshot().total_tokens().is_none());
    assert!(h.store.state().is_none());
    h.done();
}
