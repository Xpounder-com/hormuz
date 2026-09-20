//! GUI-thread observations and one-shot Win32 timers for the shared reducer.
use super::*;
use crate::interaction::{Bridge, Command, TimerAction};
use hormuz_client_interaction::{Event, FocusTarget, PointerTarget, SettingsPage, Visibility};
use std::{cell::RefCell, time::Instant};
use windows_sys::Win32::UI::Controls::{GetComboBoxInfo, COMBOBOXINFO};

pub const DRAIN: u32 = WM_APP + 5;
const SUBCLASS: usize = 338;

pub struct Controller {
    bridge: RefCell<Bridge>,
    began: Instant,
    rendered: Cell<Visibility>,
    rendered_settings: Cell<bool>,
    pointer: Cell<PointerTarget>,
    focus: Cell<Option<FocusTarget>>,
    observed_visible: Cell<bool>,
    posted: Cell<bool>,
    activate: Cell<bool>,
    ready: Cell<bool>,
    applying: Cell<bool>,
    stopped: Cell<bool>,
}

impl Controller {
    pub fn new() -> Self {
        Self {
            bridge: RefCell::new(Bridge::new()),
            began: Instant::now(),
            rendered: Cell::new(Visibility::Expanded),
            rendered_settings: Cell::new(false),
            pointer: Cell::new(PointerTarget::Outside),
            focus: Cell::new(None),
            observed_visible: Cell::new(true),
            posted: Cell::new(false),
            activate: Cell::new(false),
            ready: Cell::new(false),
            applying: Cell::new(false),
            stopped: Cell::new(false),
        }
    }

    pub fn folded(&self) -> bool {
        self.rendered.get() == Visibility::Folded
    }

    pub fn settings_open(&self) -> bool {
        self.rendered_settings.get()
    }

    fn now_ms(&self) -> Option<u64> {
        self.began.elapsed().as_millis().try_into().ok()
    }

    fn observe_visibility(&self, visible: bool) -> Option<Event> {
        if !self.ready.get() || self.applying.get() || self.stopped.get() {
            return None;
        }
        (self.observed_visible.replace(visible) != visible).then_some(if visible {
            Event::Reopen
        } else {
            Event::Hide
        })
    }
}

unsafe fn fail(hwnd: HWND, app: &App) {
    app.exit_code.set(1);
    unsafe {
        DestroyWindow(hwnd);
    }
}

unsafe fn post(hwnd: HWND, app: &App) {
    if !app.interaction.posted.replace(true) && unsafe { PostMessageW(hwnd, DRAIN, 0, 0) } == 0 {
        unsafe {
            fail(hwnd, app);
        }
    }
}

pub unsafe fn input(hwnd: HWND, app: &App, event: Event) {
    if app.interaction.stopped.get() {
        return;
    }
    let result = app.interaction.bridge.borrow_mut().push(event);
    unsafe {
        if result.is_err() {
            fail(hwnd, app);
        } else {
            post(hwnd, app);
        }
    }
}

pub unsafe fn reopen(hwnd: HWND, app: &App) {
    app.interaction.activate.set(true);
    unsafe {
        input(hwnd, app, Event::Reopen);
    }
}

pub unsafe fn hide(hwnd: HWND, app: &App) {
    app.interaction.activate.set(false);
    unsafe {
        input(hwnd, app, Event::Hide);
    }
}

pub unsafe fn toggle_fold(hwnd: HWND, app: &App) {
    unsafe {
        // Sample before the explicit action, so an as-yet undelivered mouse
        // enter cannot immediately undo a keyboard/UIA Fold in this batch.
        focus(hwnd, app, GetFocus());
        pointer(hwnd, app, hwnd, WM_NULL);
        input(hwnd, app, Event::ToggleFold);
    }
}

unsafe fn command(hwnd: HWND, app: &App, command: Command) {
    if app.interaction.stopped.get() {
        return;
    }
    unsafe {
        focus(hwnd, app, GetFocus());
        pointer(hwnd, app, hwnd, WM_NULL);
        if app.interaction.stopped.get() {
            return;
        }
        let result = app.interaction.bridge.borrow_mut().command(command);
        if result.is_err() {
            fail(hwnd, app);
        } else {
            post(hwnd, app);
        }
    }
}

pub unsafe fn settings(hwnd: HWND, app: &App) {
    if !app.preview {
        unsafe { command(hwnd, app, Command::Settings) }
    }
}

pub unsafe fn escape(hwnd: HWND, app: &App) {
    unsafe {
        if !connected::escape_popup(app) {
            command(hwnd, app, Command::Escape);
        }
    }
}

pub unsafe fn timer(hwnd: HWND, app: &App, id: usize) {
    if app.interaction.stopped.get() {
        return;
    }
    let result = app.interaction.bridge.borrow_mut().timer_fired(id);
    unsafe {
        match result {
            Ok(true) => {
                KillTimer(hwnd, id);
                post(hwnd, app);
            }
            Ok(false) => {} // Cancelled/unknown IDs never acquire a newer token.
            Err(_) => fail(hwnd, app),
        }
    }
}

unsafe fn arm(hwnd: HWND, id: usize, deadline_ms: u64, now_ms: u64) -> bool {
    let delay = deadline_ms.saturating_sub(now_ms).clamp(1, u32::MAX as u64) as u32;
    // With a non-null HWND the caller's ID labels WM_TIMER; the return value
    // only promises nonzero on success, not equality with that ID.
    unsafe { SetTimer(hwnd, id, delay, None) != 0 }
}

pub unsafe fn drain(hwnd: HWND, app: &App) {
    let controller = &app.interaction;
    if controller.stopped.get() {
        return;
    }
    // Posted messages precede hardware input in GetMessage. Observe the current
    // native focus/hit target before committing a callback, even when the OS
    // has not dispatched its corresponding input notification yet.
    unsafe {
        focus(hwnd, app, GetFocus());
        pointer(hwnd, app, hwnd, WM_NULL);
    }
    if controller.stopped.get() {
        return;
    }
    controller.posted.set(false);
    let Some(now) = controller.now_ms() else {
        unsafe {
            fail(hwnd, app);
        }
        return;
    };
    let result = controller.bridge.borrow_mut().flush(now);
    let Ok(update) = result else {
        unsafe {
            fail(hwnd, app);
        }
        return;
    };
    // No RefCell borrow or mutable App reference crosses any reentrant Win32 call.
    let before = controller.rendered.replace(update.snapshot.visibility);
    let settings = update.snapshot.settings_page.is_some();
    let before_settings = controller.rendered_settings.replace(settings);
    controller
        .observed_visible
        .set(update.snapshot.visibility != Visibility::Hidden);
    controller.applying.set(true);
    unsafe {
        if update.snapshot.visibility == Visibility::Hidden {
            controller.activate.set(false);
            // An OS-driven minimize already satisfies hidden interaction state.
            // Preserve its taskbar restoration route instead of hiding it again.
            if IsWindowVisible(hwnd) != 0 && IsIconic(hwnd) == 0 {
                ShowWindow(
                    hwnd,
                    if app.tray_ready.get() {
                        SW_HIDE
                    } else {
                        SW_MINIMIZE
                    },
                );
            }
            controller.pointer.set(PointerTarget::Outside);
            controller.focus.set(None);
        } else {
            if before != update.snapshot.visibility || before_settings != settings {
                reflow(hwnd, app);
            }
            let activate = controller.activate.replace(false);
            if activate || update.focus == Some(FocusTarget::Settings) {
                ShowWindow(hwnd, SW_RESTORE);
                SetForegroundWindow(hwnd);
            }
            if update.focus == Some(FocusTarget::Settings) {
                SetFocus(connected::settings_focus(app));
            } else if activate
                || update.focus == Some(FocusTarget::Widget)
                || (!GetFocus().is_null()
                    && IsChild(hwnd, GetFocus()) != 0
                    && IsWindowVisible(GetFocus()) == 0)
            {
                SetFocus(
                    if !activate && before_settings && !settings && !controller.folded() {
                        app.settings_button.get()
                    } else {
                        app.controls.get()[5]
                    },
                );
            }
        }
        if controller.stopped.get() {
            return;
        }
        for action in update.timers {
            match action {
                TimerAction::Cancel { id } => {
                    KillTimer(hwnd, id);
                }
                TimerAction::Arm { id, deadline_ms } => {
                    if !controller
                        .now_ms()
                        .is_some_and(|now| arm(hwnd, id, deadline_ms, now))
                    {
                        fail(hwnd, app);
                        return;
                    }
                }
            }
        }
        controller.applying.set(false);
        connected::visibility(hwnd, app);
        focus(hwnd, app, GetFocus());
        if before_settings != settings && !controller.folded() {
            pointer(hwnd, app, hwnd, WM_NULL);
        }
    }
}

pub unsafe fn visibility(hwnd: HWND, app: &App, visible: bool) {
    if let Some(event) = app.interaction.observe_visibility(visible) {
        unsafe {
            if event == Event::Hide {
                app.interaction.activate.set(false);
            }
            input(hwnd, app, event);
        }
    }
}

unsafe fn combo_parts(combo: HWND) -> [HWND; 2] {
    let mut info = COMBOBOXINFO {
        cbSize: size_of::<COMBOBOXINFO>() as u32,
        ..unsafe { std::mem::zeroed() }
    };
    if !combo.is_null() && unsafe { GetComboBoxInfo(combo, &mut info) } != 0 {
        [info.hwndItem, info.hwndList]
    } else {
        [null_mut(); 2]
    }
}

unsafe fn form_contains(inputs: [HWND; 13], open: bool, target: HWND) -> bool {
    // A combo's list can be an owned popup rather than a child of the root.
    // Only include the exact native handles reported by our own combo control.
    !target.is_null()
        && open
        && inputs
            .into_iter()
            .chain(unsafe { combo_parts(inputs[9]) })
            .any(|control| {
                !control.is_null() && unsafe { control == target || IsChild(control, target) != 0 }
            })
}

pub unsafe fn focus(hwnd: HWND, app: &App, target: HWND) {
    let controller = &app.interaction;
    if !controller.ready.get()
        || controller.stopped.get()
        || controller.rendered.get() == Visibility::Hidden
    {
        return;
    }
    let inside = !target.is_null()
        && unsafe {
            let foreground = GetForegroundWindow();
            (foreground == hwnd || GetAncestor(foreground, GA_ROOTOWNER) == hwnd)
                && (target == hwnd
                    || IsChild(hwnd, target) != 0
                    || form_contains(app.inputs.get(), controller.settings_open(), target))
                && IsWindowVisible(target) != 0
        };
    let region = inside.then(|| {
        if unsafe { form_contains(app.inputs.get(), controller.settings_open(), target) } {
            FocusTarget::Settings
        } else {
            FocusTarget::Widget
        }
    });
    if controller.focus.replace(region) != region {
        unsafe {
            input(hwnd, app, Event::FocusChanged { target: region });
        }
    }
}

pub unsafe fn pointer(hwnd: HWND, app: &App, _source: HWND, _message: u32) {
    let controller = &app.interaction;
    if !controller.ready.get()
        || controller.stopped.get()
        || controller.rendered.get() == Visibility::Hidden
    {
        return;
    }
    unsafe {
        let mut point: POINT = std::mem::zeroed();
        if GetCursorPos(&mut point) == 0 {
            return;
        }
        let target = WindowFromPoint(point);
        let in_form = form_contains(app.inputs.get(), controller.settings_open(), target);
        let inside = !target.is_null() && (target == hwnd || IsChild(hwnd, target) != 0 || in_form);
        if inside {
            // Sampling can observe entry before WM_MOUSEMOVE arrives. Arm the
            // actual child/root now so leaving without another move is observed.
            // Subclass nested native controls too; they may own the hit target.
            if target != hwnd
                && SetWindowSubclass(target, Some(control_proc), SUBCLASS, hwnd as usize) == 0
            {
                fail(hwnd, app);
                return;
            }
            let mut client: RECT = std::mem::zeroed();
            let mut client_point = point;
            if GetClientRect(target, &mut client) == 0
                || ScreenToClient(target, &mut client_point) == 0
            {
                fail(hwnd, app);
                return;
            }
            let nonclient = client_point.x < client.left
                || client_point.x >= client.right
                || client_point.y < client.top
                || client_point.y >= client.bottom;
            let mut tracking = TRACKMOUSEEVENT {
                cbSize: size_of::<TRACKMOUSEEVENT>() as u32,
                dwFlags: TME_LEAVE | if nonclient { TME_NONCLIENT } else { 0 },
                hwndTrack: target,
                dwHoverTime: 0,
            };
            if TrackMouseEvent(&mut tracking) == 0 {
                fail(hwnd, app);
                return;
            }
        }
        let region = if !inside {
            PointerTarget::Outside
        } else if in_form {
            PointerTarget::Settings
        } else if target == app.settings_button.get() {
            PointerTarget::SettingsHandle
        } else {
            PointerTarget::Widget
        };
        let before = controller.pointer.replace(region);
        if before != region {
            if before != PointerTarget::Outside {
                input(hwnd, app, Event::PointerExit { target: before });
            }
            if region != PointerTarget::Outside {
                input(hwnd, app, Event::PointerEnter { target: region });
            }
        }
    }
}

pub unsafe fn initialize(hwnd: HWND, app: &App) -> bool {
    for control in app
        .controls
        .get()
        .into_iter()
        .chain(app.inputs.get())
        .chain([app.settings_button.get()])
        .chain(unsafe { combo_parts(app.inputs.get()[9]) })
        .filter(|h| !h.is_null())
    {
        if unsafe { SetWindowSubclass(control, Some(control_proc), SUBCLASS, hwnd as usize) } == 0 {
            return false;
        }
    }
    true
}

pub unsafe fn start(hwnd: HWND, app: &App) {
    // Initial layout happens before ShowWindow. Do not interpret that hidden
    // construction state as a user's Hide, or lose a later Show in the batch.
    app.interaction.ready.set(true);
    unsafe {
        if !app.preview {
            input(
                hwnd,
                app,
                Event::OpenSettings {
                    page: SettingsPage::Home,
                },
            );
        }
        visibility(hwnd, app, IsWindowVisible(hwnd) != 0 && IsIconic(hwnd) == 0);
        focus(hwnd, app, GetFocus());
        pointer(hwnd, app, hwnd, WM_NULL);
    }
}

pub unsafe fn stop(hwnd: HWND, app: &App) {
    app.interaction.stopped.set(true);
    app.interaction.ready.set(false);
    let timers = app.interaction.bridge.borrow_mut().stop();
    for id in timers {
        unsafe {
            KillTimer(hwnd, id);
        }
    }
}

unsafe extern "system" fn control_proc(
    control: HWND,
    message: u32,
    wparam: WPARAM,
    lparam: LPARAM,
    _: usize,
    root: usize,
) -> LRESULT {
    unsafe {
        let hwnd = root as HWND;
        if message == WM_NCDESTROY {
            RemoveWindowSubclass(control, Some(control_proc), SUBCLASS);
        } else if let Some(app) = (GetWindowLongPtrW(hwnd, GWLP_USERDATA) as *const App).as_ref() {
            match message {
                WM_SETFOCUS => focus(hwnd, app, control),
                WM_KILLFOCUS => focus(hwnd, app, wparam as HWND),
                WM_MOUSEMOVE | WM_MOUSELEAVE | WM_NCMOUSEMOVE | WM_NCMOUSELEAVE => {
                    pointer(hwnd, app, control, message)
                }
                _ => {}
            }
        }
        DefSubclassProc(control, message, wparam, lparam)
    }
}

#[cfg(test)]
#[path = "native_interaction_tests.rs"]
mod tests;
