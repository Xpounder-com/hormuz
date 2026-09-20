use super::*;
use std::{thread, time::Duration};

#[test]
fn native_visibility_observations_keep_hide_then_show_before_rendering() {
    let controller = Controller::new();
    assert_eq!(controller.observe_visibility(false), None);
    controller.ready.set(true);
    assert_eq!(controller.observe_visibility(true), None);
    let mut bridge = Bridge::new();
    bridge
        .push(controller.observe_visibility(false).unwrap())
        .unwrap();
    bridge
        .push(controller.observe_visibility(true).unwrap())
        .unwrap();
    assert_eq!(controller.rendered.get(), Visibility::Expanded);
    assert_eq!(
        bridge.flush(0).unwrap().snapshot.visibility,
        Visibility::Expanded
    );
    assert_eq!(controller.observe_visibility(true), None);
    controller.applying.set(true);
    assert_eq!(controller.observe_visibility(false), None);
    controller.applying.set(false);
    assert_eq!(controller.observe_visibility(false), Some(Event::Hide));
    controller.stopped.set(true);
    assert_eq!(controller.observe_visibility(true), None);
}

struct OwnedWindow(HWND);
impl Drop for OwnedWindow {
    fn drop(&mut self) {
        unsafe {
            DestroyWindow(self.0);
        }
    }
}

fn window() -> OwnedWindow {
    let hwnd = unsafe {
        CreateWindowExW(
            0,
            wide("STATIC").as_ptr(),
            wide("Synthetic interaction timer").as_ptr(),
            0,
            0,
            0,
            0,
            0,
            HWND_MESSAGE,
            null_mut(),
            GetModuleHandleW(null()),
            null(),
        )
    };
    assert!(!hwnd.is_null());
    OwnedWindow(hwnd)
}

fn receive(window: HWND) -> usize {
    let deadline = Instant::now() + Duration::from_secs(3);
    loop {
        let mut message: MSG = unsafe { std::mem::zeroed() };
        if unsafe { PeekMessageW(&mut message, window, WM_TIMER, WM_TIMER, PM_REMOVE) } != 0 {
            return message.wParam;
        }
        assert!(
            Instant::now() < deadline,
            "owned native timer was not delivered"
        );
        thread::sleep(Duration::from_millis(1));
    }
}

fn apply(window: HWND, actions: &[TimerAction], now: u64) {
    for action in actions {
        unsafe {
            match *action {
                TimerAction::Arm { id, deadline_ms } => assert!(arm(window, id, deadline_ms, now)),
                TimerAction::Cancel { id } => {
                    KillTimer(window, id);
                }
            }
        }
    }
}

#[test]
fn real_win32_timer_rearms_early_delivery_and_rejects_queued_cancelled_identity() {
    // Creating even a message-only HWND can lock the process DPI context. Keep
    // this timer probe out of the connected GUI test's process initialization.
    let mut child = std::process::Command::new(std::env::current_exe().unwrap())
        .args([
            "--exact",
            "native::interactions::tests::native_timer_child",
            "--nocapture",
        ])
        .env("HORMUZ_SYNTHETIC_INTERACTION_TIMER_CHILD", "1")
        .spawn()
        .unwrap();
    let deadline = Instant::now() + Duration::from_secs(12);
    loop {
        if let Some(status) = child.try_wait().unwrap() {
            assert!(status.success());
            return;
        }
        if Instant::now() >= deadline {
            let _ = child.kill();
            let _ = child.wait();
            panic!("isolated native timer probe timed out");
        }
        thread::sleep(Duration::from_millis(5));
    }
}

#[test]
fn native_timer_child() {
    if std::env::var_os("HORMUZ_SYNTHETIC_INTERACTION_TIMER_CHILD").is_none() {
        return;
    }
    let window = window();
    let mut bridge = Bridge::new();
    bridge.push(Event::ToggleFold).unwrap();
    bridge.push(Event::Reopen).unwrap();
    let update = bridge.flush(0).unwrap();
    let TimerAction::Arm { id: old, .. } = update.timers[0] else {
        panic!()
    };
    apply(window.0, &update.timers, 0);
    assert_eq!(receive(window.0), old);
    unsafe {
        KillTimer(window.0, old);
    }
    assert!(bridge.timer_fired(old).unwrap());
    bridge.push(Event::Reopen).unwrap();
    let update = bridge.flush(250).unwrap();
    assert_eq!(update.snapshot.visibility, Visibility::Expanded);
    let TimerAction::Arm { id: current, .. } = update.timers[1] else {
        panic!()
    };
    assert_ne!(old, current);
    apply(window.0, &update.timers, 250);

    // KillTimer does not remove already-posted WM_TIMER messages. Deliver one
    // old ID through the real HWND queue after the replacement is installed.
    assert_ne!(unsafe { PostMessageW(window.0, WM_TIMER, old, 0) }, 0);
    assert_eq!(receive(window.0), old);
    assert!(!bridge.timer_fired(old).unwrap());

    assert_ne!(unsafe { PostMessageW(window.0, WM_TIMER, current, 0) }, 0);
    assert_eq!(receive(window.0), current);
    unsafe {
        KillTimer(window.0, current);
    }
    assert!(bridge.timer_fired(current).unwrap());
    let update = bridge.flush(251).unwrap();
    assert_eq!(
        update.timers,
        [TimerAction::Arm {
            id: current,
            deadline_ms: 500
        }]
    );
    apply(window.0, &update.timers, 251);
    assert_eq!(receive(window.0), current);
    unsafe {
        KillTimer(window.0, current);
    }
    bridge.timer_fired(current).unwrap();
    let update = bridge.flush(500).unwrap();
    assert_eq!(update.snapshot.visibility, Visibility::Folded);
    apply(window.0, &update.timers, 500);
    assert!(bridge.stop().is_empty());
    println!("native_interaction_timer=passed");
}
