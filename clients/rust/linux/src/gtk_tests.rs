#![allow(dead_code)] // Reuse the unchanged shared session/worker fixtures.
include!("../../desktop/src/connection_test_support.rs");
use super::{notifier, Observation, Ui};
use gtk::{gio, glib, prelude::*};
use hormuz_client_interaction::{Event, Metric};

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
    let ui = Ui::build(&app, Box::new(connection), None, None, sender, receiver);
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
        !ui.launch.is_sensitive(),
        "isolated GTK fixtures have no production launch context"
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
    ui.sign_out.emit_clicked();
    assert!(
        ui.metrics[0].label().unwrap().contains('—'),
        "sign-out clears values before network completion"
    );
    pump_until(|| ui.sign_in.is_sensitive());
    assert!(store.bytes.lock().unwrap().is_none());
    ui.shutdown();
    pump_until(|| !ui.window.is_visible());
    assert!(ui.worker.borrow().is_none());
    assert!(ui.owner.borrow().is_none());
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
