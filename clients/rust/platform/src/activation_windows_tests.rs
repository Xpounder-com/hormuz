use super::*;
use std::sync::atomic::{AtomicUsize, Ordering};

fn root() -> (tempfile::TempDir, PrivateDirectory) {
    let temporary = tempfile::tempdir().unwrap();
    let directory = PrivateDirectory::open(&temporary.path().join("private")).unwrap();
    (temporary, directory)
}

#[test]
fn private_reopen_roundtrip_retains_instance_but_not_refresh_lock() {
    let (_temporary, directory) = root();
    let calls = Arc::new(AtomicUsize::new(0));
    let observed = calls.clone();
    let listener = directory
        .try_claim_instance()
        .unwrap()
        .listen_for_reopen(move || {
            observed.fetch_add(1, Ordering::SeqCst);
            true
        })
        .unwrap();
    for _ in 0..8 {
        directory.request_reopen().unwrap();
    }
    assert_eq!(calls.load(Ordering::SeqCst), 8);
    assert!(matches!(
        directory.try_claim_instance(),
        Err(PlatformError::Busy)
    ));
    assert!(directory.try_lock().is_ok());
    let started = Instant::now();
    drop(listener);
    assert!(started.elapsed() < Duration::from_secs(5));
    assert!(directory.try_claim_instance().is_ok());
}

#[test]
fn rejects_malformed_and_oversized_frames_without_activation() {
    let (_temporary, directory) = root();
    let calls = Arc::new(AtomicUsize::new(0));
    let observed = calls.clone();
    let _listener = directory
        .try_claim_instance()
        .unwrap()
        .listen_for_reopen(move || {
            observed.fetch_add(1, Ordering::SeqCst);
            true
        })
        .unwrap();
    for request in [vec![0; 8], REOPEN[..4].to_vec(), vec![b'x'; 64]] {
        let pipe = connect(&directory.root).unwrap();
        let stop = event().unwrap();
        let _ = write(pipe.as_raw_handle(), &request, stop.as_raw_handle());
        let mut reply = [0; 8];
        assert!(read(pipe.as_raw_handle(), &mut reply, stop.as_raw_handle()).is_err());
    }
    assert_eq!(calls.load(Ordering::SeqCst), 0);
    directory.request_reopen().unwrap();
    assert_eq!(calls.load(Ordering::SeqCst), 1);
}

#[test]
fn silent_peer_times_out_and_cannot_prevent_listener_shutdown() {
    let (_temporary, directory) = root();
    let listener = directory
        .try_claim_instance()
        .unwrap()
        .listen_for_reopen(|| true)
        .unwrap();
    let silent = connect(&directory.root).unwrap();
    // A competing good request has a finite budget and succeeds after the
    // silent peer's one-second read deadline, without a permanent polling loop.
    directory.request_reopen().unwrap();
    drop(silent);
    let _another_silent_peer = connect(&directory.root).unwrap();
    let started = Instant::now();
    drop(listener);
    assert!(started.elapsed() < Duration::from_secs(5));
    assert!(directory.try_claim_instance().is_ok());
}

#[test]
fn native_peer_checks_reject_a_different_expected_user() {
    let (_temporary, directory) = root();
    let _listener = directory
        .try_claim_instance()
        .unwrap()
        .listen_for_reopen(|| true)
        .unwrap();
    let pipe = connect(&directory.root).unwrap();
    assert_eq!(
        peer(pipe.as_raw_handle(), false, &directory.root.user).unwrap(),
        unsafe { GetCurrentProcessId() }
    );
    let mut wrong_user = UserSid::current().unwrap();
    wrong_user.text = "S-1-5-18".into(); // LocalSystem, not the interactive CI user.
    assert!(peer(pipe.as_raw_handle(), false, &wrong_user).is_err());
    assert!(peer(INVALID_HANDLE_VALUE, false, &directory.root.user).is_err());
}

#[test]
fn client_rejects_a_public_endpoint_before_sending_a_command() {
    let (_temporary, directory) = root();
    let name = endpoint(&directory.root).unwrap();
    let descriptor =
        super::super::descriptor(&format!("O:{}D:P(A;;FA;;;WD)", directory.root.user.text))
            .unwrap();
    let security = SECURITY_ATTRIBUTES {
        nLength: std::mem::size_of::<SECURITY_ATTRIBUTES>() as u32,
        lpSecurityDescriptor: descriptor.0,
        bInheritHandle: 0,
    };
    let pipe = owned(unsafe {
        CreateNamedPipeW(
            name.as_ptr(),
            PIPE_ACCESS_DUPLEX | FILE_FLAG_OVERLAPPED | FILE_FLAG_FIRST_PIPE_INSTANCE,
            PIPE_TYPE_MESSAGE | PIPE_READMODE_MESSAGE | PIPE_REJECT_REMOTE_CLIENTS,
            1,
            8,
            8,
            IO_TIMEOUT_MS,
            &security,
        )
    })
    .unwrap();
    let server = thread::spawn(move || {
        let stop = event().unwrap();
        let ready = event().unwrap();
        let mut pending = OVERLAPPED {
            hEvent: ready.as_raw_handle(),
            ..Default::default()
        };
        let started = unsafe { ConnectNamedPipe(pipe.as_raw_handle(), &mut pending) };
        let connected = (started == 0 && unsafe { GetLastError() } == ERROR_PIPE_CONNECTED)
            || complete(
                pipe.as_raw_handle(),
                &mut pending,
                started,
                stop.as_raw_handle(),
                3000,
            )
            .is_ok();
        assert!(connected);
        let mut request = [0; 8];
        assert!(read(pipe.as_raw_handle(), &mut request, stop.as_raw_handle()).is_err());
    });
    assert!(directory.request_reopen().is_err());
    server.join().unwrap();
}

#[test]
fn cannot_start_a_second_server_or_acknowledge_rejected_ui_admission() {
    let (_temporary, directory) = root();
    let _listener = directory
        .try_claim_instance()
        .unwrap()
        .listen_for_reopen(|| false)
        .unwrap();
    assert!(create_pipe(&directory.root).is_err());
    assert!(directory.request_reopen().is_err());
}
