use super::*;
use std::sync::atomic::{AtomicUsize, Ordering};

struct CustodyFixture {
    record: Mutex<Option<Vec<u8>>>,
    writes: AtomicUsize,
    deletes: AtomicUsize,
}
unsafe extern "C" fn fixture_read(context: *mut c_void, output: *mut *mut Secret) -> i32 {
    let fixture = unsafe { &*(context as *const CustodyFixture) };
    let record = lock(&fixture.record);
    unsafe {
        *output = record.as_ref().map_or(std::ptr::null_mut(), |value| {
            hormuz_ui_secret_new(value.as_ptr(), value.len())
        });
    }
    i32::from(record.is_some())
}
unsafe extern "C" fn fixture_write(context: *mut c_void, secret: *const Secret) -> i32 {
    let fixture = unsafe { &*(context as *const CustodyFixture) };
    let mut data = vec![0; unsafe { hormuz_ui_secret_length(secret) }];
    assert!(unsafe { hormuz_ui_secret_copy(secret, data.as_mut_ptr(), data.len()) });
    let value: serde_json::Value = serde_json::from_slice(&data).unwrap();
    assert_eq!(value["state"], "revocationPending");
    *lock(&fixture.record) = Some(data);
    fixture.writes.fetch_add(1, Ordering::SeqCst);
    0
}
unsafe extern "C" fn fixture_delete(context: *mut c_void) -> i32 {
    let fixture = unsafe { &*(context as *const CustodyFixture) };
    *lock(&fixture.record) = None;
    fixture.deletes.fetch_add(1, Ordering::SeqCst);
    0
}

unsafe extern "C" fn read(_: *mut c_void, output: *mut *mut Secret) -> i32 {
    unsafe {
        *output = std::ptr::null_mut();
    }
    0
}
unsafe extern "C" fn write(_: *mut c_void, _: *const Secret) -> i32 {
    0
}
unsafe extern "C" fn delete(_: *mut c_void) -> i32 {
    0
}
unsafe extern "C" fn browser(_: *mut c_void, _: *const u8, _: usize) -> i32 {
    0
}
fn host() -> Host {
    Host {
        context: std::ptr::null_mut(),
        read: Some(read),
        write: Some(write),
        delete: Some(delete),
        open_browser: Some(browser),
    }
}
unsafe extern "C" fn wake(context: *mut c_void) {
    unsafe { &*(context as *const AtomicUsize) }.fetch_add(1, Ordering::SeqCst);
}
fn create(root: &Path) -> *mut Ui {
    let root = root.join("private-state");
    let text = root.to_str().unwrap();
    unsafe { hormuz_ui_new(text.as_ptr(), text.len(), host()) }
}

#[test]
fn distinct_owned_display_and_secret_buffers_copy_without_borrowed_bytes() {
    unsafe {
        let display = hormuz_ui_context_setting(true);
        let mut short = [0; 1];
        assert!(!hormuz_ui_bytes_copy(
            display,
            short.as_mut_ptr(),
            short.len()
        ));
        assert_eq!(short, [0]);
        let mut full = vec![0; hormuz_ui_bytes_length(display)];
        assert!(hormuz_ui_bytes_copy(display, full.as_mut_ptr(), full.len()));
        assert_eq!(full, b"{\"enabled\":true,\"schema_version\":1}");
        hormuz_ui_bytes_free(display);
        let secret = hormuz_ui_secret_new(b"synthetic-only".as_ptr(), 14);
        let mut copy = vec![0; hormuz_ui_secret_length(secret)];
        assert!(hormuz_ui_secret_copy(secret, copy.as_mut_ptr(), copy.len()));
        assert_eq!(copy, b"synthetic-only");
        hormuz_ui_secret_free(secret);
        assert!(hormuz_ui_secret_new(std::ptr::null(), 1).is_null());
        assert!(hormuz_ui_secret_new(std::ptr::null(), 0).is_null());
        let oversized = vec![b'x'; 32_768];
        assert!(hormuz_ui_secret_new(oversized.as_ptr(), oversized.len()).is_null());
    }
}

#[test]
fn one_subscription_and_repeated_teardown_never_wake_after_close() {
    let root = tempfile::tempdir().unwrap();
    let count = AtomicUsize::new(0);
    for _ in 0..32 {
        let handle = create(root.path());
        assert!(!handle.is_null());
        unsafe {
            let context = &count as *const _ as *mut c_void;
            let subscription = hormuz_ui_subscribe(handle, Some(wake), context);
            assert!(!subscription.is_null());
            assert!(hormuz_ui_subscribe(handle, Some(wake), context).is_null());
            hormuz_ui_unsubscribe(subscription);
            let before = count.load(Ordering::SeqCst);
            // Explicit cancellation restores from custody; only this local UI
            // operation is cancelled, not a governed relay/request handle.
            assert!(hormuz_ui_cancel(handle));
            hormuz_ui_free(handle);
            assert_eq!(count.load(Ordering::SeqCst), before);
        }
    }
}

#[test]
fn subscription_can_outlive_closed_core_without_retaining_worker_or_host() {
    let root = tempfile::tempdir().unwrap();
    let count = AtomicUsize::new(0);
    let handle = create(root.path());
    assert!(!handle.is_null());
    unsafe {
        let subscription =
            hormuz_ui_subscribe(handle, Some(wake), &count as *const _ as *mut c_void);
        assert!(!subscription.is_null());
        hormuz_ui_free(handle);
        let before = count.load(Ordering::SeqCst);
        hormuz_ui_unsubscribe(subscription);
        assert_eq!(count.load(Ordering::SeqCst), before);
    }
}

#[test]
fn boundaries_are_bounded_fail_closed_and_no_credential_is_in_snapshot() {
    assert_eq!(hormuz_ui_abi_version(), 1);
    let root = tempfile::tempdir().unwrap();
    let handle = create(root.path());
    assert!(!handle.is_null());
    unsafe {
        assert!(!hormuz_ui_visibility(handle, 3));
        assert!(!hormuz_ui_lifecycle(handle, 6));
        assert!(!hormuz_ui_connect(handle, std::ptr::null(), MAX_INPUT + 1));
        assert!(!hormuz_ui_connect(std::ptr::null(), std::ptr::null(), 0));
        assert!(hormuz_ui_launch_profile(handle).is_null());
        let output = hormuz_ui_snapshot(handle);
        let data = &(*output).0;
        let json: serde_json::Value = serde_json::from_slice(data).unwrap();
        assert_eq!(json["schema_version"], 1);
        let text = std::str::from_utf8(data).unwrap();
        for forbidden in ["accessToken", "refreshToken", "hox_a_", "hox_r_"] {
            assert!(!text.contains(forbidden));
        }
        hormuz_ui_bytes_free(output);
        hormuz_ui_free(handle);
    }
}

#[test]
fn hosted_profiles_are_rejected_instead_of_downgraded() {
    assert!(manual_profile(br#"{"desktopManaged":false}"#));
    assert!(!manual_profile(br#"{"desktopManaged":true}"#));
    assert!(!manual_profile(br#"{"desktopManaged":"false"}"#));
    assert!(!manual_profile(
        br#"{"desktopManaged":true,"desktopProfileVersion":1}"#
    ));
    assert!(!manual_profile(br#"{"desktopProfileVersion":1}"#));
}

#[test]
fn freeing_after_accepted_disconnect_drains_host_custody_intent_and_revocation() {
    use std::io::{Read, Write};
    use std::time::Duration;
    let listener = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
    listener.set_nonblocking(true).unwrap();
    let address = listener.local_addr().unwrap();
    let server = std::thread::spawn(move || {
        let deadline = std::time::Instant::now() + Duration::from_secs(5);
        let mut socket = loop {
            match listener.accept() {
                Ok((socket, _)) => break socket,
                Err(error)
                    if error.kind() == std::io::ErrorKind::WouldBlock
                        && std::time::Instant::now() < deadline =>
                {
                    std::thread::sleep(Duration::from_millis(5))
                }
                Err(error) => panic!("fixture request did not arrive: {}", error.kind()),
            }
        };
        socket
            .set_read_timeout(Some(Duration::from_secs(2)))
            .unwrap();
        let mut headers = Vec::new();
        let mut byte = [0];
        while !headers.ends_with(b"\r\n\r\n") {
            assert!(headers.len() < 4096);
            socket.read_exact(&mut byte).unwrap();
            headers.push(byte[0]);
        }
        assert!(headers.starts_with(b"POST /v1/auth/logout HTTP/1.1\r\n"));
        let content_length = std::str::from_utf8(&headers)
            .unwrap()
            .lines()
            .find_map(|line| {
                line.to_ascii_lowercase()
                    .strip_prefix("content-length:")
                    .map(|value| value.trim().parse::<usize>().unwrap())
            })
            .unwrap();
        assert!(content_length <= 4096);
        socket.read_exact(&mut vec![0; content_length]).unwrap();
        socket
            .write_all(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: 16\r\nConnection: close\r\n\r\n{\"revoked\":true}")
            .unwrap();
    });
    let mut record: serde_json::Value = serde_json::from_str(include_str!(
        "../../../../tests/fixtures/native_client/v1/sessions.json"
    ))
    .unwrap();
    record = record["record"].take();
    record["profile"]["gateway"] = serde_json::json!(format!("http://{address}"));
    record["profile"]["allowLoopbackHTTP"] = serde_json::json!(true);
    let fixture = CustodyFixture {
        record: Mutex::new(Some(serde_json::to_vec(&record).unwrap())),
        writes: AtomicUsize::new(0),
        deletes: AtomicUsize::new(0),
    };
    // Keep the temp owner alive until the worker has joined.
    let temp = tempfile::tempdir().unwrap();
    let root = temp.path().join("private-state");
    let text = root.to_str().unwrap();
    let host = Host {
        context: &fixture as *const _ as *mut c_void,
        read: Some(fixture_read),
        write: Some(fixture_write),
        delete: Some(fixture_delete),
        open_browser: Some(browser),
    };
    unsafe {
        let handle = hormuz_ui_new(text.as_ptr(), text.len(), host);
        assert!(!handle.is_null());
        hormuz_ui_disconnect(handle);
        assert!(!hormuz_ui_cancel(handle)); // Accepted logout cannot be undone.
        hormuz_ui_free(handle); // Joins the worker before host/context release.
    }
    server.join().unwrap();
    assert_eq!(fixture.writes.load(Ordering::SeqCst), 1);
    assert_eq!(fixture.deletes.load(Ordering::SeqCst), 1);
    assert!(lock(&fixture.record).is_none());
}
