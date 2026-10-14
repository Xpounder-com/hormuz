//! GTK stays on its main context. The one shared worker owns all session work.
use crate::{
    connection::{Connection, DesktopConnection, Phase},
    presentation, Capabilities, Controls, DisplayBackend,
};
use gtk::{gio, glib, prelude::*};
use hormuz_client_core::SessionState;
use hormuz_client_interaction::{
    Effect, Event, FocusTarget, Metric, SettingsPage, TimerToken, Visibility,
};
use hormuz_client_platform::{
    ApplicationInstance, BrowserOpener, LifecycleEvent, NativeCredentialStore, PlatformError,
    PrivateDirectory,
};
use hormuz_client_session::{DashboardVisibility, NativeTransport, SessionController, SystemClock};
use std::{
    cell::{Cell, RefCell},
    path::PathBuf,
    rc::Rc,
    sync::{Arc, Mutex},
    time::Instant,
};

type Worker = Box<dyn DesktopConnection + Send + Sync>;
type Notifier = Box<dyn Fn() -> bool + Send + Sync>;
#[path = "terminal.rs"]
mod terminal;

struct LaunchContext {
    root: PathBuf,
    relay: PathBuf,
}

struct Browser;
impl BrowserOpener for Browser {
    fn open_authentication_url(&self, url: &str) -> hormuz_client_platform::Result<()> {
        // The session controller validated this URI. Gio receives a literal
        // URI, not a shell command, and this runs on the existing worker.
        gio::AppInfo::launch_default_for_uri(url, None::<&gio::AppLaunchContext>)
            .map_err(|_| PlatformError::Unavailable)
    }
}

/// Native passwd home, not an environment-controlled alternate lock domain.
fn state_directory() -> Result<PathBuf, PlatformError> {
    // SAFETY: reentrant lookup writes only to the supplied buffers. C strings
    // are copied before their backing allocation is dropped.
    unsafe {
        let uid = libc::geteuid();
        if uid == 0 || uid != libc::getuid() {
            return Err(PlatformError::UnsafeStorage);
        }
        let mut record = std::mem::MaybeUninit::<libc::passwd>::uninit();
        let mut result = std::ptr::null_mut();
        let mut bytes = vec![0u8; 65_536];
        if libc::getpwuid_r(
            uid,
            record.as_mut_ptr(),
            bytes.as_mut_ptr().cast(),
            bytes.len(),
            &mut result,
        ) != 0
            || result.is_null()
        {
            return Err(PlatformError::Unavailable);
        }
        let record = record.assume_init();
        if record.pw_dir.is_null() {
            return Err(PlatformError::Unavailable);
        }
        use std::os::unix::ffi::OsStrExt;
        let home = PathBuf::from(std::ffi::OsStr::from_bytes(
            std::ffi::CStr::from_ptr(record.pw_dir).to_bytes(),
        ));
        if !home.is_absolute() {
            return Err(PlatformError::UnsafeStorage);
        }
        Ok(home.join(".hormuz-native"))
    }
}

fn native_worker(
    notify: Notifier,
) -> Result<(Worker, ApplicationInstance, LaunchContext), PlatformError> {
    let root = state_directory()?;
    let executable = std::env::current_exe().map_err(|_| PlatformError::Unavailable)?;
    let relay = executable
        .parent()
        .ok_or(PlatformError::Unavailable)?
        .join("hormuz-client-relay");
    let directory = PrivateDirectory::open(&root)?;
    let owner = directory.try_claim_instance()?;
    let controller = SessionController::new(
        directory,
        NativeCredentialStore::default(),
        NativeTransport,
        SystemClock::default(),
    );
    let worker =
        Connection::start(controller, Browser, notify).map_err(|_| PlatformError::Unavailable)?;
    Ok((Box::new(worker), owner, LaunchContext { root, relay }))
}

fn notifier(sender: async_channel::Sender<()>) -> Notifier {
    Box::new(move || match sender.try_send(()) {
        Ok(()) | Err(async_channel::TrySendError::Full(_)) => true,
        Err(async_channel::TrySendError::Closed(_)) => false,
    })
}

#[derive(Clone, Copy, Default)]
struct Observation {
    session_seen: bool,
    manager_seen: bool,
    locked: bool,
    sleeping: bool,
    lock_generation: u64,
    sleep_generation: u64,
}
impl Observation {
    fn refresh_allowed(self) -> bool {
        self.session_seen && self.manager_seen && !self.locked && !self.sleeping
    }
    fn lock(&mut self, locked: bool) {
        if locked && !self.locked {
            self.lock_generation = self.lock_generation.wrapping_add(1);
        }
        self.locked = locked;
    }
    fn sleep(&mut self, sleeping: bool) {
        if sleeping && !self.sleeping {
            self.sleep_generation = self.sleep_generation.wrapping_add(1);
        }
        self.sleeping = sleeping;
    }
    fn lost_owner(&mut self) {
        self.lock_generation = self.lock_generation.wrapping_add(1);
    }
    fn manager(&mut self, owner_present: bool, sleeping: Option<bool>) {
        let was_seen = self.manager_seen;
        self.manager_seen = owner_present && sleeping.is_some();
        if let Some(sleeping) = sleeping.filter(|_| owner_present) {
            self.sleep(sleeping);
        }
        if was_seen && !self.manager_seen {
            self.lost_owner();
        }
    }
    fn session(&mut self, owner_present: bool, locked: Option<bool>) {
        let was_seen = self.session_seen;
        self.session_seen = owner_present && locked.is_some();
        if let Some(locked) = locked.filter(|_| owner_present) {
            self.lock(locked);
        }
        if was_seen && !self.session_seen {
            self.lost_owner();
        }
    }
}

pub struct Ui {
    window: gtk::ApplicationWindow,
    worker: RefCell<Option<Worker>>,
    owner: RefCell<Option<ApplicationInstance>>,
    controls: RefCell<Controls>,
    started: Instant,
    quitting: Cell<bool>,
    waiting_to_quit: Cell<bool>,
    quit_confirmation: gtk::Box,
    quit_wait: gtk::Button,
    quit_cancel: gtk::Button,
    quit_stop: gtk::Button,
    minimized: Cell<bool>,
    profile_loaded: Cell<bool>,
    timers: RefCell<Vec<(TimerToken, glib::SourceId)>>,
    heading: gtk::Label,
    status: gtk::Label,
    lifecycle_status: gtk::Label,
    metrics: [gtk::Button; 3],
    metric_box: gtk::Box,
    details: gtk::Label,
    detail_box: gtk::Box,
    settings: gtk::Box,
    entries: [gtk::Entry; 4],
    client: gtk::DropDown,
    sign_in: gtk::Button,
    sign_out: gtk::Button,
    cancel: gtk::Button,
    retry: gtk::Button,
    refresh: gtk::Button,
    fold: gtk::Button,
    observation: Arc<Mutex<Observation>>,
    last_observation: Cell<(bool, bool, u64, u64)>,
    proxies: RefCell<Vec<gio::DBusProxy>>,
    notifications: async_channel::Sender<()>,
    launch_context: Option<LaunchContext>,
    terminal: RefCell<Option<terminal::Terminal>>,
    terminal_busy: Cell<bool>,
    terminal_status: gtk::Label,
    launch: gtk::Button,
    stop_terminal: gtk::Button,
}

fn label(text: &str) -> gtk::Label {
    gtk::Label::builder()
        .label(text)
        .xalign(0.0)
        .wrap(true)
        .selectable(true)
        .build()
}

impl Ui {
    fn build(
        app: &gtk::Application,
        worker: Worker,
        owner: Option<ApplicationInstance>,
        launch_context: Option<LaunchContext>,
        sender: async_channel::Sender<()>,
        receiver: async_channel::Receiver<()>,
    ) -> Rc<Self> {
        let window = gtk::ApplicationWindow::builder()
            .application(app)
            .title("Hormuz companion")
            .default_width(480)
            .default_height(680)
            .build();
        let content = gtk::Box::new(gtk::Orientation::Vertical, 12);
        for side in [
            gtk::PositionType::Left,
            gtk::PositionType::Right,
            gtk::PositionType::Top,
            gtk::PositionType::Bottom,
        ] {
            match side {
                gtk::PositionType::Left => content.set_margin_start(20),
                gtk::PositionType::Right => content.set_margin_end(20),
                gtk::PositionType::Top => content.set_margin_top(20),
                _ => content.set_margin_bottom(20),
            }
        }
        let scroller = gtk::ScrolledWindow::builder()
            .hscrollbar_policy(gtk::PolicyType::Never)
            .child(&content)
            .build();
        window.set_child(Some(&scroller));
        let title = label("Hormuz companion");
        title.add_css_class("title-1");
        content.append(&title);
        let backend = gtk::prelude::WidgetExt::display(&window).type_().name();
        let capabilities = Capabilities::ordinary_window(if backend.contains("Wayland") {
            DisplayBackend::Wayland
        } else if backend.contains("X11") {
            DisplayBackend::X11
        } else {
            DisplayBackend::Other
        });
        content.append(&label(capabilities.description()));
        let heading = label("No successful gateway reading");
        let status = label("Checking saved connection…");
        content.append(&heading);
        content.append(&status);
        let lifecycle_status =
            label("Automatic refresh paused until desktop lock/sleep monitoring is available.");
        content.append(&lifecycle_status);
        let metric_box = gtk::Box::new(gtk::Orientation::Vertical, 6);
        let metrics = [
            gtk::Button::with_label("Your requests: —"),
            gtk::Button::with_label("Your tokens: —"),
            gtk::Button::with_label("Your estimated cost: —"),
        ];
        for button in &metrics {
            if let Some(text) = button.child().and_downcast::<gtk::Label>() {
                text.set_wrap(true);
            }
            metric_box.append(button);
        }
        content.append(&metric_box);
        let details = label("");
        let detail_box = gtk::Box::new(gtk::Orientation::Vertical, 6);
        let dismiss = gtk::Button::with_mnemonic("_Close details");
        detail_box.append(&details);
        detail_box.append(&dismiss);
        content.append(&detail_box);
        let toolbar = gtk::Box::new(gtk::Orientation::Horizontal, 6);
        let fold = gtk::Button::with_mnemonic("_Fold");
        let settings_button = gtk::Button::with_mnemonic("_Connection");
        let refresh = gtk::Button::with_mnemonic("_Refresh");
        for button in [&fold, &settings_button, &refresh] {
            toolbar.append(button);
        }
        content.append(&toolbar);
        let settings = gtk::Box::new(gtk::Orientation::Vertical, 6);
        settings.append(&label("Custom gateway connection"));
        settings.append(&label("These fields are not secrets. Authentication opens your browser; credentials remain in Secret Service."));
        let entries: [gtk::Entry; 4] = std::array::from_fn(|_| gtk::Entry::new());
        for (name, entry) in [
            "_Gateway (HTTPS)",
            "_Organization",
            "_Model",
            "_Issuer (optional)",
        ]
        .into_iter()
        .zip(&entries)
        {
            let field_label = gtk::Label::with_mnemonic(name);
            field_label.set_xalign(0.0);
            field_label.set_mnemonic_widget(Some(entry));
            entry.set_hexpand(true);
            entry.set_activates_default(true);
            settings.append(&field_label);
            settings.append(entry);
        }
        let client = gtk::DropDown::from_strings(&["Codex", "Claude"]);
        let client_label = gtk::Label::with_mnemonic("AI _client");
        client_label.set_xalign(0.0);
        client_label.set_mnemonic_widget(Some(&client));
        settings.append(&client_label);
        settings.append(&client);
        let sign_in = gtk::Button::with_mnemonic("_Sign in");
        sign_in.add_css_class("suggested-action");
        let sign_out = gtk::Button::with_mnemonic("Sign _out / disconnect");
        let cancel = gtk::Button::with_mnemonic("Cancel _operation");
        let retry = gtk::Button::with_mnemonic("_Retry saved connection");
        for button in [&sign_in, &sign_out, &cancel, &retry] {
            settings.append(button);
        }
        settings.append(&label("Sign out immediately clears local usage and asks the gateway to revoke the session. No provider request is sent by this screen."));
        content.append(&settings);
        let terminal_status = label(
            "Client not launched. GNOME Terminal and a systemd 254+ user session are required.",
        );
        let launch = gtk::Button::with_mnemonic("_Launch governed client in terminal");
        let stop_terminal = gtk::Button::with_mnemonic("S_top governed client");
        content.append(&terminal_status);
        content.append(&launch);
        content.append(&stop_terminal);
        let quit = gtk::Button::with_mnemonic("_Quit Hormuz");
        content.append(&quit);
        let quit_confirmation = gtk::Box::new(gtk::Orientation::Vertical, 6);
        quit_confirmation.append(&label("A governed client is active or still stopping. Wait leaves its lease open and quits after confirmed completion. Cancel keeps Hormuz open. Stop and Quit interrupts the client and stops its exact owned service."));
        let quit_wait = gtk::Button::with_mnemonic("_Wait for client, then quit");
        let quit_cancel = gtk::Button::with_mnemonic("_Cancel quit / keep client running");
        let quit_stop = gtk::Button::with_mnemonic("_Stop client and Quit");
        for button in [&quit_wait, &quit_cancel, &quit_stop] {
            quit_confirmation.append(button);
        }
        quit_confirmation.set_visible(false);
        content.append(&quit_confirmation);
        window.set_default_widget(Some(&sign_in));
        let ui = Rc::new(Self {
            window,
            worker: RefCell::new(Some(worker)),
            owner: RefCell::new(owner),
            controls: RefCell::new(Controls::default()),
            started: Instant::now(),
            quitting: Cell::new(false),
            waiting_to_quit: Cell::new(false),
            quit_confirmation,
            quit_wait,
            quit_cancel,
            quit_stop,
            minimized: Cell::new(false),
            profile_loaded: Cell::new(false),
            timers: RefCell::new(Vec::new()),
            heading,
            status,
            lifecycle_status,
            metrics,
            metric_box,
            details,
            detail_box,
            settings,
            entries,
            client,
            sign_in,
            sign_out,
            cancel,
            retry,
            refresh,
            fold,
            observation: Arc::new(Mutex::new(Observation::default())),
            last_observation: Cell::new((true, false, 0, 0)),
            proxies: RefCell::new(Vec::new()),
            notifications: sender,
            launch_context,
            terminal: RefCell::new(None),
            terminal_busy: Cell::new(false),
            terminal_status,
            launch,
            stop_terminal,
        });
        ui.controls
            .borrow_mut()
            .dispatch(
                0,
                Event::OpenSettings {
                    page: SettingsPage::Connection,
                },
            )
            .expect("initial monotonic interaction");
        for (button, metric) in
            ui.metrics
                .iter()
                .zip([Metric::Requests, Metric::Tokens, Metric::Cost])
        {
            let weak = Rc::downgrade(&ui);
            button.connect_clicked(move |_| {
                if let Some(ui) = weak.upgrade() {
                    ui.interact(Event::TogglePin { metric });
                }
            });
        }
        for (button, event) in [
            (&ui.fold, Event::ToggleFold),
            (
                &settings_button,
                Event::OpenSettings {
                    page: SettingsPage::Connection,
                },
            ),
            (&dismiss, Event::DismissDetails),
        ] {
            let weak = Rc::downgrade(&ui);
            button.connect_clicked(move |_| {
                if let Some(ui) = weak.upgrade() {
                    ui.interact(event);
                }
            });
        }
        let weak = Rc::downgrade(&ui);
        ui.sign_in.connect_clicked(move |_| {
            if let Some(ui) = weak.upgrade() {
                ui.connect();
            }
        });
        let weak = Rc::downgrade(&ui);
        ui.sign_out.connect_clicked(move |_| {
            if let Some(ui) = weak.upgrade() {
                if let Some(worker) = ui.worker.borrow().as_ref() {
                    worker.sign_out();
                }
                ui.render();
                if ui.terminal.borrow().is_some() {
                    ui.stop_owned_terminal(false);
                }
            }
        });
        let weak = Rc::downgrade(&ui);
        ui.cancel.connect_clicked(move |_| {
            if let Some(ui) = weak.upgrade() {
                if let Some(worker) = ui.worker.borrow().as_ref() {
                    worker.cancel();
                }
                ui.render();
            }
        });
        let weak = Rc::downgrade(&ui);
        ui.retry.connect_clicked(move |_| {
            if let Some(ui) = weak.upgrade() {
                if let Some(worker) = ui.worker.borrow().as_ref() {
                    worker.retry();
                }
                ui.render();
            }
        });
        let weak = Rc::downgrade(&ui);
        ui.refresh.connect_clicked(move |_| {
            if let Some(ui) = weak.upgrade() {
                if let Some(worker) = ui.worker.borrow().as_ref() {
                    worker.refresh();
                }
            }
        });
        let weak = Rc::downgrade(&ui);
        ui.launch.connect_clicked(move |_| {
            if let Some(ui) = weak.upgrade() {
                ui.launch_terminal();
            }
        });
        let weak = Rc::downgrade(&ui);
        ui.stop_terminal.connect_clicked(move |_| {
            if let Some(ui) = weak.upgrade() {
                ui.stop_owned_terminal(false);
            }
        });
        let weak = Rc::downgrade(&ui);
        quit.connect_clicked(move |_| {
            if let Some(ui) = weak.upgrade() {
                ui.shutdown();
            }
        });
        let weak = Rc::downgrade(&ui);
        ui.quit_wait.connect_clicked(move |_| {
            if let Some(ui) = weak.upgrade() {
                ui.waiting_to_quit.set(true);
                ui.quit_wait.set_sensitive(false);
                ui.render();
            }
        });
        let weak = Rc::downgrade(&ui);
        ui.quit_cancel.connect_clicked(move |_| {
            if let Some(ui) = weak.upgrade() {
                ui.waiting_to_quit.set(false);
                ui.quit_confirmation.set_visible(false);
                ui.quit_wait.set_sensitive(true);
                ui.render();
            }
        });
        let weak = Rc::downgrade(&ui);
        ui.quit_stop.connect_clicked(move |_| {
            if let Some(ui) = weak.upgrade() {
                ui.waiting_to_quit.set(false);
                ui.quit_confirmation.set_visible(false);
                ui.begin_shutdown();
            }
        });
        let weak = Rc::downgrade(&ui);
        ui.window.connect_close_request(move |_| {
            if let Some(ui) = weak.upgrade() {
                ui.shutdown();
            }
            glib::Propagation::Stop
        });
        let keys = gtk::EventControllerKey::new();
        let weak = Rc::downgrade(&ui);
        keys.connect_key_pressed(move |_, key, _, _| {
            if key == gtk::gdk::Key::Escape {
                if let Some(ui) = weak.upgrade() {
                    ui.interact(Event::Escape);
                }
                glib::Propagation::Stop
            } else {
                glib::Propagation::Proceed
            }
        });
        ui.window.add_controller(keys);
        let weak = Rc::downgrade(&ui);
        ui.window.connect_map(move |_| {
            if let Some(ui) = weak.upgrade() {
                ui.render();
            }
        });
        let weak = Rc::downgrade(&ui);
        ui.window.connect_unmap(move |_| {
            if let Some(ui) = weak.upgrade() {
                if let Some(worker) = ui.worker.borrow().as_ref() {
                    worker.visibility(DashboardVisibility::Hidden);
                }
            }
        });
        let weak = Rc::downgrade(&ui);
        ui.window.connect_realize(move |window| {
            let Some(surface) = window.surface() else {
                return;
            };
            let Ok(toplevel) = surface.dynamic_cast::<gtk::gdk::Toplevel>() else {
                return;
            };
            let weak = weak.clone();
            toplevel.connect_state_notify(move |surface| {
                if let Some(ui) = weak.upgrade() {
                    ui.minimized
                        .set(surface.state().contains(gtk::gdk::ToplevelState::MINIMIZED));
                    ui.render();
                }
            });
        });
        let weak = Rc::downgrade(&ui);
        glib::spawn_future_local(async move {
            while receiver.recv().await.is_ok() {
                let Some(ui) = weak.upgrade() else {
                    break;
                };
                ui.cleanup_finished_terminal();
                ui.apply_observation();
                ui.render();
            }
        });
        ui.render();
        ui
    }

    fn connect(&self) {
        let values = self.entries.each_ref().map(|entry| entry.text());
        match presentation::profile(
            &values[0],
            &values[1],
            &values[2],
            &values[3],
            if self.client.selected() == 0 {
                "codex"
            } else {
                "claude"
            },
        ) {
            Ok(profile) => {
                if let Some(worker) = self.worker.borrow().as_ref() {
                    worker.sign_in(profile);
                }
                self.render();
            }
            Err(error) => {
                self.status.set_text(&error.to_string());
                self.entries[0].grab_focus();
            }
        }
    }

    fn interact(self: &Rc<Self>, event: Event) {
        let now = self.started.elapsed().as_millis().min(u64::MAX as u128) as u64;
        let effects = self
            .controls
            .borrow_mut()
            .dispatch(now, event)
            .expect("monotonic GTK executor");
        for effect in effects {
            match effect {
                Effect::RequestFocus { target } => {
                    match target {
                        FocusTarget::Widget => self.fold.grab_focus(),
                        FocusTarget::Details => self.metrics[0].grab_focus(),
                        FocusTarget::Settings => self.entries[0].grab_focus(),
                    };
                }
                Effect::ScheduleTimer { timer } => {
                    let weak = Rc::downgrade(self);
                    let source = glib::timeout_add_local_once(
                        std::time::Duration::from_millis(timer.deadline_ms.saturating_sub(now)),
                        move || {
                            if let Some(ui) = weak.upgrade() {
                                ui.timers
                                    .borrow_mut()
                                    .retain(|(token, _)| *token != timer.token);
                                ui.interact(Event::TimerFired { token: timer.token });
                            }
                        },
                    );
                    self.timers.borrow_mut().push((timer.token, source));
                }
                Effect::CancelTimer { token } => {
                    let mut timers = self.timers.borrow_mut();
                    if let Some(index) = timers.iter().position(|(current, _)| *current == token) {
                        timers.remove(index).1.remove();
                    }
                }
            }
        }
        self.render();
    }

    fn render(&self) {
        let worker = self.worker.borrow();
        let Some(worker) = worker.as_ref() else {
            return;
        };
        let view = worker.view();
        let values = presentation::labels(&view);
        self.heading.set_text(&values[0]);
        self.status.set_text(&values[1]);
        for (button, value) in self.metrics.iter().zip(&values[2..]) {
            button.set_label(value);
        }
        let active = view.has_session();
        let busy = view.phase.busy();
        self.sign_in.set_sensitive(!busy && !active);
        self.sign_out.set_sensitive(active || busy);
        self.cancel
            .set_sensitive(busy && view.phase != Phase::SigningOut);
        self.retry.set_sensitive(!busy);
        self.refresh
            .set_sensitive(active && !busy && self.observation.lock().unwrap().refresh_allowed());
        for entry in &self.entries {
            entry.set_sensitive(!active && !busy);
        }
        self.client.set_sensitive(!active && !busy);
        self.launch.set_sensitive(
            view.connection
                .as_ref()
                .is_some_and(|status| status.session_state() == Some(SessionState::Active))
                && !busy
                && self.launch_context.is_some()
                && self.terminal.borrow().is_none()
                && !self.terminal_busy.get()
                && !self.waiting_to_quit.get(),
        );
        self.stop_terminal
            .set_sensitive(self.terminal.borrow().is_some() && !self.terminal_busy.get());
        if let Some(terminal) = self.terminal.borrow().as_ref() {
            self.terminal_status
                .set_text(terminal.phase().description());
        }
        if !self.profile_loaded.get() {
            if let Some(profile) = view.connection.as_ref().and_then(|status| status.profile()) {
                for (entry, value) in self.entries.iter().zip([
                    profile.gateway(),
                    profile.organization(),
                    profile.model(),
                    profile.issuer().unwrap_or(""),
                ]) {
                    entry.set_text(value);
                }
                self.client
                    .set_selected(if profile.client().as_str() == "codex" {
                        0
                    } else {
                        1
                    });
                self.profile_loaded.set(true);
            }
        }
        let interaction = self.controls.borrow().snapshot();
        let expanded = interaction.visibility != Visibility::Folded;
        self.metric_box.set_visible(expanded);
        self.fold
            .set_label(if expanded { "_Fold" } else { "_Expand" });
        self.fold.set_use_underline(true);
        self.settings
            .set_visible(interaction.settings_page.is_some());
        self.detail_box
            .set_visible(interaction.selected_metric.is_some());
        if let Some(metric) = interaction.selected_metric {
            self.details.set_text(
                &presentation::details(&view, metric, interaction.pinned_metric == Some(metric))
                    .replace("\r\n", "\n"),
            );
        }
        worker.visibility(if !self.window.is_mapped() || self.minimized.get() {
            DashboardVisibility::Hidden
        } else if interaction.selected_metric.is_some() {
            DashboardVisibility::Detail
        } else {
            DashboardVisibility::Summary
        });
    }

    fn apply_observation(&self) {
        let observation = *self.observation.lock().unwrap();
        let locked = !observation.session_seen || !observation.manager_seen || observation.locked;
        let next = (
            locked,
            observation.sleeping,
            observation.lock_generation,
            observation.sleep_generation,
        );
        let previous = self.last_observation.replace(next);
        if let Some(worker) = self.worker.borrow().as_ref() {
            // A short lock/sleep followed by resume must still pause scheduling
            // even if both observations share one coalesced UI wake.
            let saw_lock = next.2 != previous.2;
            let saw_sleep = next.3 != previous.3;
            if saw_lock {
                worker.lifecycle(LifecycleEvent::SessionLocked);
            }
            if saw_sleep {
                worker.lifecycle(LifecycleEvent::Sleep);
            }
            if next.0 != previous.0 || (saw_lock && !next.0) {
                worker.lifecycle(if next.0 {
                    LifecycleEvent::SessionLocked
                } else {
                    LifecycleEvent::SessionUnlocked
                });
            }
            if next.1 != previous.1 || (saw_sleep && !next.1) {
                worker.lifecycle(if next.1 {
                    LifecycleEvent::Sleep
                } else {
                    LifecycleEvent::Wake
                });
            }
        }
        self.lifecycle_status
            .set_text(if !observation.session_seen || !observation.manager_seen {
                "Automatic refresh paused: desktop lock/sleep monitoring unavailable."
            } else if observation.locked || observation.sleeping {
                "Automatic refresh paused while the session is locked or sleeping."
            } else {
                "Visible-window refresh active · pauses on lock, sleep and hidden window."
            });
    }

    fn shutdown(self: &Rc<Self>) {
        if self.quitting.get() {
            return;
        }
        let active = self.terminal_busy.get()
            || self
                .terminal
                .borrow()
                .as_ref()
                .is_some_and(|terminal| !terminal.phase().finished());
        if active {
            self.quit_confirmation.set_visible(true);
            self.window.present();
            self.quit_cancel.grab_focus();
            return;
        }
        self.begin_shutdown();
    }

    fn begin_shutdown(self: &Rc<Self>) {
        if self.quitting.replace(true) {
            return;
        }
        self.window.set_sensitive(false);
        if self.terminal_busy.get() {
            return;
        }
        if self.terminal.borrow().is_some() {
            self.stop_owned_terminal(true);
            return;
        }
        self.finish_shutdown();
    }

    fn cleanup_finished_terminal(self: &Rc<Self>) {
        if !self.terminal_busy.get()
            && self
                .terminal
                .borrow()
                .as_ref()
                .is_some_and(|terminal| terminal.phase().finished())
        {
            // Even a final phase can precede the thread's return. Join through
            // the existing bounded blocking path, never on GTK's main context.
            self.stop_owned_terminal(false);
        }
    }

    fn launch_terminal(self: &Rc<Self>) {
        if self.terminal.borrow().is_some()
            || self.terminal_busy.get()
            || self.quitting.get()
            || self.waiting_to_quit.get()
        {
            return;
        }
        let Some(context) = &self.launch_context else {
            return;
        };
        let profile = self
            .worker
            .borrow()
            .as_ref()
            .map(|worker| worker.view())
            .and_then(|view| view.connection)
            .filter(|status| status.session_state() == Some(SessionState::Active))
            .and_then(|status| status.profile().cloned());
        let Some(profile) = profile else {
            return;
        };
        match terminal::Terminal::start(
            context.root.clone(),
            profile.key().to_owned(),
            context.relay.clone(),
            notifier(self.notifications.clone()),
        ) {
            Ok(terminal) => {
                *self.terminal.borrow_mut() = Some(terminal);
            }
            Err(_) => {
                self.terminal_status
                    .set_text("Could not create an owned terminal launch. No client was started.");
            }
        }
        self.render();
    }

    fn stop_owned_terminal(self: &Rc<Self>, quitting: bool) {
        if self.terminal_busy.replace(true) {
            return;
        }
        let terminal = self.terminal.borrow_mut().take();
        let Some(mut terminal) = terminal else {
            self.terminal_busy.set(false);
            if quitting {
                self.finish_shutdown();
            }
            return;
        };
        self.terminal_status
            .set_text("Stopping the exact owned relay service…");
        self.render();
        let ui = self.clone();
        glib::spawn_future_local(async move {
            let result = gio::spawn_blocking(move || {
                let result = terminal.stop();
                (terminal, result)
            })
            .await;
            ui.terminal_busy.set(false);
            match result {
                Ok((terminal, Ok(()))) => {
                    let phase = terminal.phase();
                    drop(terminal); // Already drained: no join on GTK's thread.
                    ui.terminal_status.set_text(phase.description());
                    ui.quit_confirmation.set_visible(false);
                    ui.quit_wait.set_sensitive(true);
                    if quitting || ui.quitting.get() || ui.waiting_to_quit.replace(false) {
                        ui.finish_shutdown();
                    } else {
                        ui.render();
                    }
                }
                Ok((terminal, Err(_))) => {
                    *ui.terminal.borrow_mut() = Some(terminal);
                    ui.quitting.set(false);
                    ui.waiting_to_quit.set(false);
                    ui.quit_wait.set_sensitive(true);
                    ui.window.set_sensitive(true);
                    ui.render();
                }
                Err(_) => {
                    ui.quitting.set(false);
                    ui.waiting_to_quit.set(false);
                    ui.quit_wait.set_sensitive(true);
                    ui.window.set_sensitive(true);
                    ui.terminal_status.set_text(
                        "Terminal shutdown worker failed. App ownership has not been released.",
                    );
                }
            }
        });
    }

    fn finish_shutdown(self: &Rc<Self>) {
        self.quitting.set(true);
        self.waiting_to_quit.set(false);
        self.window.set_sensitive(false);
        self.status.set_text("Stopping the owned session worker…");
        self.notifications.close();
        self.proxies.borrow_mut().clear();
        for (_, source) in self.timers.borrow_mut().drain(..) {
            source.remove();
        }
        let worker = self.worker.borrow_mut().take();
        let owner = self.owner.borrow_mut().take();
        let ui = self.clone();
        glib::spawn_future_local(async move {
            // Accepted revocation is drained before the private owner lease is
            // released. Never join a network/custody worker on GTK's thread.
            let _ = gio::spawn_blocking(move || {
                drop(worker);
                drop(owner);
            })
            .await;
            let app = ui.window.application();
            ui.window.destroy();
            if let Some(app) = app {
                app.quit();
            }
        });
    }
}

fn watch_desktop(ui: &Rc<Ui>) {
    let weak = Rc::downgrade(ui);
    let observation = ui.observation.clone();
    let sender = ui.notifications.clone();
    glib::spawn_future_local(async move {
        let Ok(session) = gio::DBusProxy::for_bus_future(
            gio::BusType::System,
            gio::DBusProxyFlags::DO_NOT_AUTO_START,
            None,
            "org.freedesktop.login1",
            "/org/freedesktop/login1/session/self",
            "org.freedesktop.login1.Session",
        )
        .await
        else {
            return;
        };
        {
            let mut state = observation.lock().unwrap();
            observe_session(&session, &mut state);
        }
        let _ = sender.try_send(());
        let values = observation.clone();
        let events = sender.clone();
        session.connect_g_properties_changed(move |proxy, _, _| {
            observe_session(proxy, &mut values.lock().unwrap());
            let _ = events.try_send(());
        });
        let values = observation.clone();
        let events = sender.clone();
        session.connect_g_name_owner_notify(move |proxy| {
            observe_session(proxy, &mut values.lock().unwrap());
            let _ = events.try_send(());
        });
        let Ok(manager) = gio::DBusProxy::for_bus_future(
            gio::BusType::System,
            gio::DBusProxyFlags::DO_NOT_AUTO_START,
            None,
            "org.freedesktop.login1",
            "/org/freedesktop/login1",
            "org.freedesktop.login1.Manager",
        )
        .await
        else {
            return;
        };
        {
            let mut state = observation.lock().unwrap();
            observe_manager(&manager, &mut state);
        }
        let _ = sender.try_send(());
        let values = observation.clone();
        let events = sender.clone();
        manager.connect_g_signal(move |proxy, _, signal, parameters| {
            if signal == "PrepareForSleep" {
                if let Some((sleeping,)) = parameters.get::<(bool,)>() {
                    values
                        .lock()
                        .unwrap()
                        .manager(proxy.name_owner().is_some(), Some(sleeping));
                    let _ = events.try_send(());
                }
            }
        });
        let values = observation.clone();
        let events = sender.clone();
        manager.connect_g_properties_changed(move |proxy, changed, invalidated| {
            // PrepareForSleep is an authoritative signal. An unrelated cached
            // property change must not replace that observation with an older
            // PreparingForSleep value from the same owner.
            if glib::VariantDict::new(Some(changed)).contains("PreparingForSleep")
                || invalidated.iter().any(|name| *name == "PreparingForSleep")
            {
                observe_manager(proxy, &mut values.lock().unwrap());
                let _ = events.try_send(());
            }
        });
        let values = observation;
        manager.connect_g_name_owner_notify(move |proxy| {
            observe_manager(proxy, &mut values.lock().unwrap());
            let _ = sender.try_send(());
        });
        if let Some(ui) = weak.upgrade() {
            if !ui.quitting.get() {
                ui.proxies.borrow_mut().extend([session, manager]);
            }
        }
    });
    let weak = Rc::downgrade(ui);
    gio::NetworkMonitor::default().connect_network_changed(move |_, _| {
        if let Some(ui) = weak.upgrade() {
            if let Some(worker) = ui.worker.borrow().as_ref() {
                worker.lifecycle(LifecycleEvent::NetworkChanged);
            }
        }
    });
}

fn observe_manager(proxy: &gio::DBusProxy, state: &mut Observation) {
    state.manager(
        proxy.name_owner().is_some(),
        proxy
            .cached_property("PreparingForSleep")
            .and_then(|value| value.get::<bool>()),
    );
}

fn observe_session(proxy: &gio::DBusProxy, state: &mut Observation) {
    state.session(
        proxy.name_owner().is_some(),
        proxy
            .cached_property("LockedHint")
            .and_then(|value| value.get::<bool>()),
    );
}

pub fn run() -> glib::ExitCode {
    let app = gtk::Application::builder()
        .application_id("com.hormuz.client.linux")
        .build();
    let active: Rc<RefCell<Option<Rc<Ui>>>> = Rc::new(RefCell::new(None));
    let starting = Rc::new(Cell::new(false));
    app.connect_activate(move |app| {
        if let Some(ui) = active.borrow().as_ref() { ui.interact(Event::Reopen); ui.window.present(); return; }
        if starting.replace(true) { if let Some(window) = app.active_window() { window.present(); } return; }
        let loading = gtk::ApplicationWindow::builder().application(app).title("Hormuz companion").default_width(480).default_height(240).child(&label("Starting the native session worker…")).build();
        loading.present();
        let app = app.clone();
        let active = active.clone();
        glib::spawn_future_local(async move {
            let (sender, receiver) = async_channel::bounded(1);
            let notify = notifier(sender.clone());
            match gio::spawn_blocking(move || native_worker(notify)).await {
                Ok(Ok((worker, owner, context))) => {
                    let ui = Ui::build(&app, worker, Some(owner), Some(context), sender, receiver);
                    watch_desktop(&ui);
                    ui.window.present();
                    *active.borrow_mut() = Some(ui);
                    loading.destroy();
                }
                _ => { loading.set_child(Some(&label("Could not start the native client. Check private-directory ownership, another running instance, and Secret Service availability. No connection was started."))); }
            }
        });
    });
    app.run()
}

#[cfg(test)]
#[path = "gtk_tests.rs"]
mod tests;
