#![allow(dead_code)] // Common fixture also exposes portable worker helpers.
include!("../../desktop/src/connection_test_support.rs");
use hormuz_client_desktop::connection::DesktopConnection;

#[cfg(windows)]
#[test]
fn native_connected_controls_show_scoped_usage_and_clear_on_sign_out() {
    use crate::{native, options::Options};
    use hormuz_client_platform::PrivateDirectory;
    use windows_sys::Win32::UI::Input::KeyboardAndMouse::{VK_ESCAPE, VK_TAB};
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
    fn focus(window: HWND) -> HWND {
        unsafe {
            let mut info = GUITHREADINFO {
                cbSize: std::mem::size_of::<GUITHREADINFO>() as u32,
                ..std::mem::zeroed()
            };
            assert_ne!(
                GetGUIThreadInfo(
                    GetWindowThreadProcessId(window, std::ptr::null_mut()),
                    &mut info
                ),
                0
            );
            info.hwndFocus
        }
    }
    fn key(control: HWND, key: u16) {
        unsafe {
            assert_ne!(PostMessageW(control, WM_KEYDOWN, key as usize, 1), 0);
            assert_ne!(PostMessageW(control, WM_KEYUP, key as usize, 0xc0000001), 0);
        }
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
    until(|| unsafe { IsWindowVisible(window) != 0 && IsIconic(window) == 0 });
    until(|| unsafe { IsWindowVisible(GetDlgItem(window, 201)) != 0 });
    assert_eq!(
        text(window, 112),
        "&Back",
        "startup must retain connection-form access"
    );
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
    assert_eq!(text(window, 113), "Your requests: —");
    // Back and Escape navigate the actual native form before the shell hides.
    // Focus moves through native edit/combo controls; no synthetic shared focus
    // observation substitutes for the GUI thread's real focus owner here.
    send(window, WM_COMMAND, 112);
    until(|| unsafe { IsWindowVisible(GetDlgItem(window, 201)) == 0 });
    assert_eq!(text(window, 112), "&Settings");
    assert_ne!(unsafe { IsWindowVisible(GetDlgItem(window, 113)) }, 0);
    send(window, WM_COMMAND, 112);
    until(|| focus(window) == unsafe { GetDlgItem(window, 201) });
    for next in [202, 203, 204, 205] {
        key(focus(window), VK_TAB);
        until(|| focus(window) == unsafe { GetDlgItem(window, next) });
        assert_ne!(unsafe { IsWindowVisible(GetDlgItem(window, 201)) }, 0);
    }
    let combo = unsafe { GetDlgItem(window, 205) };
    send(combo, CB_SHOWDROPDOWN, 1);
    until(|| unsafe { SendMessageW(combo, CB_GETDROPPEDSTATE, 0, 0) != 0 });
    key(combo, VK_ESCAPE);
    until(|| unsafe { SendMessageW(combo, CB_GETDROPPEDSTATE, 0, 0) == 0 });
    assert_ne!(unsafe { IsWindowVisible(GetDlgItem(window, 201)) }, 0);
    key(focus(window), VK_ESCAPE);
    until(|| unsafe { IsWindowVisible(GetDlgItem(window, 201)) == 0 }
        && focus(window) == unsafe { GetDlgItem(window, 112) });
    assert_ne!(unsafe { IsWindowVisible(window) }, 0);
    key(focus(window), VK_ESCAPE);
    until(|| unsafe { IsWindowVisible(window) == 0 || IsIconic(window) != 0 });
    PrivateDirectory::open(&root)
        .unwrap()
        .request_reopen()
        .unwrap();
    until(|| unsafe {
        IsWindowVisible(window) != 0
            && IsIconic(window) == 0
            && IsWindowVisible(GetDlgItem(window, 113)) != 0
    });
    assert_eq!(unsafe { IsWindowVisible(GetDlgItem(window, 201)) }, 0);
    send(window, WM_COMMAND, 112);
    until(|| focus(window) == unsafe { GetDlgItem(window, 201) });
    println!("native_connection_settings=passed startup=visible keyboard=posted_messages combo_escape=local back_escape_reopen=passed");
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
        text(window, 113),
        format!(
            "Your requests: {}",
            fixture()["usage"]["requests"].as_i64().unwrap()
        )
    );
    assert!(!text(window, 114).contains("synthetic"));
    assert!(!text(window, 301).contains("hox_"));
    send(window, WM_COMMAND, 112);
    until(|| unsafe { IsWindowVisible(GetDlgItem(window, 201)) == 0 });
    let mut metric_before: RECT = unsafe { std::mem::zeroed() };
    assert_ne!(
        unsafe { GetWindowRect(GetDlgItem(window, 114), &mut metric_before) },
        0
    );
    send(window, WM_COMMAND, 114);
    until(|| unsafe { IsWindowVisible(GetDlgItem(window, 401)) != 0 });
    let mut metric_after: RECT = unsafe { std::mem::zeroed() };
    assert_ne!(
        unsafe { GetWindowRect(GetDlgItem(window, 114), &mut metric_after) },
        0
    );
    assert!((metric_before.top - metric_after.top).abs() <= 2);
    assert!(text(window, 401).contains("Input:"));
    assert!(text(window, 401).contains("Output:"));
    assert!(text(window, 401).contains("Pinned"));
    send(window, WM_COMMAND, 116);
    until(|| unsafe { IsWindowVisible(GetDlgItem(window, 401)) == 0 });
    send(window, WM_COMMAND, 115);
    until(|| unsafe { IsWindowVisible(GetDlgItem(window, 401)) != 0 });
    assert!(text(window, 401).contains("not a provider bill"));
    send(window, WM_COMMAND, 112);
    until(|| unsafe {
        IsWindowVisible(GetDlgItem(window, 401)) == 0
            && IsWindowVisible(GetDlgItem(window, 201)) != 0
    });
    send(window, WM_COMMAND, 112);
    until(|| unsafe { IsWindowVisible(GetDlgItem(window, 201)) == 0 });
    println!(
        "native_metric_details=passed tokens=broken_down cost=estimated settings_dismissal=passed"
    );
    // External native visibility notifications must agree with the reducer,
    // including startup's hidden construction and later OS-driven restoration.
    unsafe {
        ShowWindow(window, SW_HIDE);
    }
    until(|| unsafe { IsWindowVisible(window) == 0 });
    unsafe {
        ShowWindow(window, SW_RESTORE);
    }
    until(|| unsafe {
        IsWindowVisible(window) != 0
            && IsIconic(window) == 0
            && IsWindowVisible(GetDlgItem(window, 113)) != 0
    });
    send(window, WM_COMMAND, 101);
    until(|| unsafe { IsWindowVisible(GetDlgItem(window, 113)) == 0 });
    send(window, WM_CLOSE, 0);
    PrivateDirectory::open(&root)
        .unwrap()
        .request_reopen()
        .unwrap();
    until(|| unsafe { IsWindowVisible(window) != 0 && IsIconic(window) == 0 });
    until(|| unsafe { IsWindowVisible(GetDlgItem(window, 113)) != 0 });
    send(window, WM_COMMAND, 112);
    until(|| unsafe { IsWindowVisible(GetDlgItem(window, 201)) != 0 });
    transport.mode.store(1, Ordering::SeqCst);
    clock.1.store(10, Ordering::SeqCst);
    send(window, WM_COMMAND, 111);
    until(|| text(window, 300).starts_with("Offline"));
    assert!(!text(window, 113).ends_with('—'));
    transport.mode.store(0, Ordering::SeqCst);
    send(window, WM_COMMAND, 110);
    until(|| text(window, 113).ends_with('—') && store.load().unwrap().is_none());
    send(window, WM_COMMAND, 104);
    assert_eq!(completion.recv_timeout(Duration::from_secs(8)).unwrap(), 0);
    worker.join().unwrap();
    std::mem::forget(cleanup); // Already confirmed exact owned GUI exit.
    assert!(PrivateDirectory::open(&root)
        .unwrap()
        .try_claim_instance()
        .is_ok());
}
