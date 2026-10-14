#![allow(dead_code)] // Reuse the unchanged shared session/worker fixtures.
include!("../../desktop/src/connection_test_support.rs");
use super::{notifier, terminal, LaunchContext, Observation, Ui};
use gtk::{gio, glib, prelude::*};
use hormuz_client_interaction::{Event, Metric};
use std::rc::Rc;

fn pump_until(condition: impl Fn() -> bool) {
    let context = glib::MainContext::default();
    let deadline = Instant::now() + Duration::from_secs(8);
    while !condition() {
        while context.pending() {
            context.iteration(false);
        }
        assert!(Instant::now() < deadline, "GTK/worker transition timed out");
        thread::sleep(Duration::from_millis(2));
    }
    while context.pending() {
        context.iteration(false);
    }
}

fn screenshot(ui: &Ui, name: &str) {
    let Some(root) = std::env::var_os("HORMUZ_GTK_PROOF_DIRECTORY") else {
        return;
    };
    let paintable = gtk::WidgetPaintable::new(Some(&ui.window));
    let snapshot = gtk::Snapshot::new();
    paintable.snapshot(
        &snapshot,
        ui.window.width() as f64,
        ui.window.height() as f64,
    );
    let node = snapshot
        .to_node()
        .expect("mapped native window must render");
    let renderer = ui.window.renderer().expect("mapped native renderer");
    renderer
        .render_texture(&node, None)
        .save_to_png(std::path::PathBuf::from(root).join(name))
        .expect("native screenshot must be saved");
}

/// This is an actual GTK window and the same controls used by production.
/// Only custody/transport/clock/browser are isolated; no test CLI override is
/// exposed, and no real enrollment, Secret Service or provider call occurs.
#[test]
fn actual_gtk_controls_drive_the_existing_session_and_truthful_snapshot() {
    if let Some(root) = std::env::var_os("HORMUZ_GTK_PROOF_DIRECTORY") {
        let execution = serde_json::json!({
            "executable": std::env::current_exe().unwrap(),
            "debug_assertions": cfg!(debug_assertions),
            "scope": "source_native_gtk_widgets_isolated_session",
        });
        std::fs::write(
            std::path::PathBuf::from(root).join("gtk-test-executable.json"),
            serde_json::to_vec_pretty(&execution).unwrap(),
        )
        .unwrap();
    }
    gtk::init().expect("native GTK display is required; this test must not skip");
    let app = gtk::Application::builder()
        .application_id("com.hormuz.client.gtkfixture")
        .flags(gio::ApplicationFlags::NON_UNIQUE)
        .build();
    app.register(None::<&gio::Cancellable>).unwrap();
    let store = Store::default();
    let transport = Transport::default();
    let clock = TestClock::default();
    let (sender, receiver) = async_channel::bounded(1);
    let connection = Connection::start(
        SessionController::new(
            Coordinator::default(),
            store.clone(),
            transport.clone(),
            clock.clone(),
        ),
        Browser::default(),
        notifier(sender.clone()),
    )
    .unwrap();
    let terminal_root = terminal::test_support::root();
    let ui = Ui::build(
        &app,
        Box::new(connection),
        None,
        Some(LaunchContext {
            root: terminal_root.path().to_owned(),
            relay: "/opt/hormuz/hormuz-client-relay".into(),
        }),
        sender,
        receiver,
    );
    ui.window.present();
    pump_until(|| ui.window.is_mapped() && ui.sign_in.is_sensitive());
    assert_eq!(
        ui.metrics[0].property::<gtk::AccessibleRole>("accessible-role"),
        gtk::AccessibleRole::Button
    );
    assert_eq!(
        ui.sign_in
            .property::<gtk::AccessibleRole>("accessible-role"),
        gtk::AccessibleRole::Button
    );
    assert!(ui.entries[0].grab_focus());
    assert!(ui.metrics[0].label().unwrap().contains('—'));
    assert!(
        !ui.refresh.is_sensitive(),
        "unknown desktop lock state is fail-closed"
    );
    let profile = profile();
    for (entry, value) in ui.entries.iter().zip([
        profile.gateway(),
        profile.organization(),
        profile.model(),
        profile.issuer().unwrap_or(""),
    ]) {
        entry.set_text(value);
    }
    ui.sign_in.emit_clicked();
    pump_until(|| ui.sign_out.is_sensitive() && ui.status.text().contains("gateway usage"));
    assert_eq!(
        transport.usage.load(Ordering::SeqCst),
        0,
        "fixture starts locked"
    );
    *ui.observation.lock().unwrap() = Observation {
        session_seen: true,
        manager_seen: true,
        locked: false,
        sleeping: false,
        ..Observation::default()
    };
    ui.apply_observation();
    ui.render();
    pump_until(|| ui.heading.text().starts_with("Current"));
    assert!(ui.metrics[0].label().unwrap().contains("41"));
    assert!(ui.metrics[1].label().unwrap().contains("3000"));
    assert!(ui.metrics[2]
        .label()
        .unwrap()
        .contains("gateway requests only"));
    assert!(ui.refresh.is_sensitive());
    assert!(!ui.entries[0].is_sensitive());
    assert!(
        ui.launch.is_sensitive(),
        "active fixture profile enables the same native Launch control"
    );
    screenshot(&ui, "connected-desktop.png");
    ui.metrics[1].emit_clicked();
    assert_eq!(
        ui.controls.borrow().snapshot().pinned_metric,
        Some(Metric::Tokens)
    );
    assert!(ui.detail_box.is_visible());
    assert!(ui.details.text().contains("Input:"));
    screenshot(&ui, "pinned-details.png");
    ui.interact(Event::Escape);
    assert!(!ui.detail_box.is_visible());
    assert!(ui.window.is_visible(), "no tray-dependent hidden state");
    ui.fold.emit_clicked();
    assert!(!ui.metric_box.is_visible());
    assert!(ui.window.is_visible());
    ui.fold.emit_clicked();
    assert!(ui.metric_box.is_visible());
    ui.window.set_default_size(320, 760);
    pump_until(|| ui.window.width() <= 340);
    screenshot(&ui, "connected-narrow.png");
    let successful_time = ui
        .heading
        .text()
        .split("Last success:")
        .nth(1)
        .unwrap()
        .to_owned();
    transport.mode.store(1, Ordering::SeqCst);
    clock.1.store(60, Ordering::SeqCst);
    ui.refresh.emit_clicked();
    pump_until(|| ui.heading.text().starts_with("Offline"));
    assert!(
        ui.heading.text().contains(&successful_time),
        "offline values keep their original successful time"
    );
    assert!(ui.metrics[0].label().unwrap().contains("41"));
    transport.mode.store(5, Ordering::SeqCst);
    clock.1.store(120, Ordering::SeqCst);
    ui.refresh.emit_clicked();
    pump_until(|| ui.metrics[0].label().unwrap().contains('—'));
    assert!(
        ui.metrics[1].label().unwrap().contains('—'),
        "wrong-scope readings expose no usage"
    );
    // Native widget actions use the real owned terminal manager with an
    // isolated same-UID socket peer, never GNOME Terminal or a provider client.
    let (stopped, _) = attach_terminal(&ui, terminal_root.path());
    pump_until(|| terminal_phase(&ui) == Some(terminal::Phase::Running));
    std::fs::write(terminal_root.path().join("client-finished"), []).unwrap();
    pump_until(|| ui.terminal.borrow().is_none() && !ui.terminal_busy.get());
    assert!(ui.launch.is_sensitive(), "natural exit releases Launch");
    assert_eq!(stopped.load(Ordering::SeqCst), 1);
    let failed = terminal::Terminal::start(
        terminal_root.path().to_owned(),
        "invalid fixture profile".into(),
        "/opt/hormuz/hormuz-client-relay".into(),
        notifier(ui.notifications.clone()),
    )
    .unwrap();
    *ui.terminal.borrow_mut() = Some(failed);
    pump_until(|| ui.terminal.borrow().is_none() && !ui.terminal_busy.get());
    assert!(ui.launch.is_sensitive(), "failed launch releases retry");
    assert!(ui.terminal_status.text().starts_with("Could not launch"));
    let active_root = terminal::test_support::root();
    let (stopped, _) = attach_terminal(&ui, active_root.path());
    pump_until(|| terminal_phase(&ui) == Some(terminal::Phase::Running));
    ui.window.close();
    assert!(ui.quit_confirmation.is_visible());
    assert!(!ui.quitting.get());
    assert_eq!(stopped.load(Ordering::SeqCst), 0);
    ui.quit_cancel.emit_clicked();
    assert!(!ui.quit_confirmation.is_visible());
    ui.window.set_visible(false);
    ui.window.present();
    pump_until(|| ui.window.is_mapped());
    assert_eq!(terminal_phase(&ui), Some(terminal::Phase::Running));
    assert_eq!(stopped.load(Ordering::SeqCst), 0, "cancel/hide keeps lease");
    ui.window.close();
    ui.quit_wait.emit_clicked();
    assert!(ui.waiting_to_quit.get());
    assert!(ui.quit_confirmation.is_visible());
    assert!(!ui.quitting.get());
    assert_eq!(stopped.load(Ordering::SeqCst), 0, "Wait never sends Stop");
    ui.quit_cancel.emit_clicked();
    assert!(!ui.waiting_to_quit.get());
    ui.stop_terminal.emit_clicked();
    pump_until(|| ui.terminal.borrow().is_none() && !ui.terminal_busy.get());
    assert_eq!(stopped.load(Ordering::SeqCst), 1);
    ui.sign_out.emit_clicked();
    assert!(
        ui.metrics[0].label().unwrap().contains('—'),
        "sign-out clears values before network completion"
    );
    pump_until(|| ui.sign_in.is_sensitive());
    assert!(store.bytes.lock().unwrap().is_none());
    let waiting_root = terminal::test_support::root();
    let (stopped, _) = attach_terminal(&ui, waiting_root.path());
    pump_until(|| terminal_phase(&ui) == Some(terminal::Phase::Running));
    ui.window.close();
    ui.quit_wait.emit_clicked();
    assert_eq!(stopped.load(Ordering::SeqCst), 0);
    std::fs::write(waiting_root.path().join("client-finished"), []).unwrap();
    pump_until(|| !ui.window.is_visible());
    assert!(ui.worker.borrow().is_none());
    assert!(ui.owner.borrow().is_none());
    assert_eq!(stopped.load(Ordering::SeqCst), 1);

    let (sender, receiver) = async_channel::bounded(1);
    let connection = Connection::start(
        SessionController::new(
            Coordinator::default(),
            Store::default(),
            Transport::default(),
            TestClock::default(),
        ),
        Browser::default(),
        notifier(sender.clone()),
    )
    .unwrap();
    let stop_ui = Ui::build(&app, Box::new(connection), None, None, sender, receiver);
    stop_ui.window.present();
    let stop_root = terminal::test_support::root();
    let (stopped, stop_ok) = attach_terminal(&stop_ui, stop_root.path());
    pump_until(|| terminal_phase(&stop_ui) == Some(terminal::Phase::Running));
    stop_ok.store(false, Ordering::SeqCst);
    stop_ui.window.close();
    assert!(stop_ui.quit_confirmation.is_visible());
    stop_ui.quit_stop.emit_clicked();
    pump_until(|| !stop_ui.terminal_busy.get());
    assert_eq!(terminal_phase(&stop_ui), Some(terminal::Phase::StopFailed));
    assert!(stop_ui.window.is_visible());
    assert!(stop_ui.window.is_sensitive());
    assert!(!stop_ui.quitting.get());
    assert!(!stop_ui.waiting_to_quit.get());
    assert_eq!(stopped.load(Ordering::SeqCst), 1);
    assert!(
        stop_ui.worker.borrow().is_some(),
        "failed quit keeps app owned"
    );
    stop_ok.store(true, Ordering::SeqCst);
    stop_ui.window.close();
    stop_ui.quit_stop.emit_clicked();
    pump_until(|| !stop_ui.window.is_visible());
    assert_eq!(stopped.load(Ordering::SeqCst), 2);
    assert!(stop_ui.worker.borrow().is_none());
}

fn terminal_phase(ui: &Ui) -> Option<terminal::Phase> {
    ui.terminal
        .borrow()
        .as_ref()
        .map(|terminal| terminal.phase())
}

fn attach_terminal(ui: &Rc<Ui>, root: &std::path::Path) -> (Arc<AtomicUsize>, Arc<AtomicBool>) {
    let stops = Arc::new(AtomicUsize::new(0));
    let stop_ok = Arc::new(AtomicBool::new(true));
    let terminal = terminal::test_support::start(
        root,
        terminal::test_support::Fixture {
            launched: Arc::new(AtomicBool::new(false)),
            stop_ok: stop_ok.clone(),
            stops: stops.clone(),
            delay: false,
        },
        notifier(ui.notifications.clone()),
    );
    *ui.terminal.borrow_mut() = Some(terminal);
    ui.render();
    (stops, stop_ok)
}

#[test]
fn missing_or_locked_logind_observations_do_not_enable_refresh() {
    assert!(!Observation::default().refresh_allowed());
    assert!(!Observation {
        session_seen: true,
        manager_seen: false,
        locked: false,
        sleeping: false,
        ..Observation::default()
    }
    .refresh_allowed());
    assert!(!Observation {
        session_seen: true,
        manager_seen: true,
        locked: true,
        sleeping: false,
        ..Observation::default()
    }
    .refresh_allowed());
    assert!(!Observation {
        session_seen: true,
        manager_seen: true,
        locked: false,
        sleeping: true,
        ..Observation::default()
    }
    .refresh_allowed());
    assert!(Observation {
        session_seen: true,
        manager_seen: true,
        locked: false,
        sleeping: false,
        ..Observation::default()
    }
    .refresh_allowed());
    let mut coalesced = Observation::default();
    coalesced.lock(true);
    coalesced.lock(false);
    coalesced.sleep(true);
    coalesced.sleep(false);
    assert_eq!((coalesced.locked, coalesced.sleeping), (false, false));
    assert_eq!(
        (coalesced.lock_generation, coalesced.sleep_generation),
        (1, 1),
        "short pauses must survive notification coalescing"
    );
}

#[test]
fn logind_manager_reconnect_requires_a_current_owner_and_valid_sleep_property() {
    let mut state = Observation {
        session_seen: true,
        ..Observation::default()
    };
    state.manager(true, Some(false));
    assert!(state.refresh_allowed());
    state.manager(false, Some(false));
    assert!(!state.refresh_allowed(), "old cached values have no owner");
    assert_eq!(state.lock_generation, 1);
    state.manager(true, None);
    assert!(
        !state.refresh_allowed(),
        "reconnected but unknown stays paused"
    );
    state.manager(true, Some(true));
    assert!(
        !state.refresh_allowed(),
        "valid sleeping state stays paused"
    );
    state.manager(true, Some(false));
    assert!(
        state.refresh_allowed(),
        "healthy reconnect restores refresh"
    );
    state.manager(true, None);
    assert!(
        !state.refresh_allowed(),
        "invalidated property pauses again"
    );
    assert_eq!(state.lock_generation, 2);
}

#[test]
fn logind_session_reconnect_never_treats_an_unknown_lock_hint_as_unlocked() {
    let mut state = Observation {
        manager_seen: true,
        ..Observation::default()
    };
    state.session(true, Some(false));
    assert!(state.refresh_allowed());
    state.session(false, Some(false));
    assert!(!state.refresh_allowed());
    state.session(true, None);
    assert!(!state.refresh_allowed());
    state.session(true, Some(true));
    assert!(!state.refresh_allowed());
    state.session(true, Some(false));
    assert!(state.refresh_allowed());
}
