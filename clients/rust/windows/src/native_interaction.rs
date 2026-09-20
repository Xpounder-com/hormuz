//! GUI-thread observations and one-shot Win32 timers for the shared reducer.
use super::*;
use crate::interaction::{Bridge, TimerAction};
use hormuz_client_interaction::{Event, FocusTarget, PointerTarget, Visibility};
use std::{cell::RefCell, time::Instant};

pub const DRAIN: u32 = WM_APP + 5;
const SUBCLASS: usize = 338;

pub struct Controller {
    bridge: RefCell<Bridge>,
    began: Instant,
    rendered: Cell<Visibility>,
    pointer_inside: Cell<bool>,
    focus_inside: Cell<bool>,
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
            pointer_inside: Cell::new(false),
            focus_inside: Cell::new(false),
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

    fn now_ms(&self) -> Option<u64> {
        self.began.elapsed().as_millis().try_into().ok()
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
    controller.applying.set(true);
    unsafe {
        if update.snapshot.visibility == Visibility::Hidden {
            controller.activate.set(false);
            ShowWindow(
                hwnd,
                if app.tray_ready.get() {
                    SW_HIDE
                } else {
                    SW_MINIMIZE
                },
            );
            controller.pointer_inside.set(false);
            controller.focus_inside.set(false);
        } else {
            if before != update.snapshot.visibility {
                reflow(hwnd, app);
            }
            if controller.activate.replace(false) {
                ShowWindow(hwnd, SW_RESTORE);
                SetForegroundWindow(hwnd);
                SetFocus(app.controls.get()[5]);
            }
            if update.focus == Some(FocusTarget::Widget)
                || (!GetFocus().is_null()
                    && IsChild(hwnd, GetFocus()) != 0
                    && IsWindowVisible(GetFocus()) == 0)
            {
                SetFocus(app.controls.get()[5]);
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
    }
}

pub unsafe fn visibility(hwnd: HWND, app: &App) {
    let controller = &app.interaction;
    if !controller.ready.get() || controller.applying.get() || controller.stopped.get() {
        return;
    }
    let visible = unsafe { IsWindowVisible(hwnd) != 0 && IsIconic(hwnd) == 0 };
    unsafe {
        if !visible && controller.rendered.get() != Visibility::Hidden {
            hide(hwnd, app);
        } else if visible && controller.rendered.get() == Visibility::Hidden {
            input(hwnd, app, Event::Reopen);
        }
    }
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
                && (target == hwnd || IsChild(hwnd, target) != 0)
        };
    if controller.focus_inside.replace(inside) != inside {
        unsafe {
            input(
                hwnd,
                app,
                Event::FocusChanged {
                    target: inside.then_some(FocusTarget::Widget),
                },
            );
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
        let inside = !target.is_null() && (target == hwnd || IsChild(hwnd, target) != 0);
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
        if controller.pointer_inside.replace(inside) != inside {
            input(
                hwnd,
                app,
                if inside {
                    Event::PointerEnter {
                        target: PointerTarget::Widget,
                    }
                } else {
                    Event::PointerExit {
                        target: PointerTarget::Widget,
                    }
                },
            );
        }
    }
}

pub unsafe fn initialize(hwnd: HWND, app: &App) -> bool {
    for control in app
        .controls
        .get()
        .into_iter()
        .chain(app.inputs.get())
        .filter(|h| !h.is_null())
    {
        if unsafe { SetWindowSubclass(control, Some(control_proc), SUBCLASS, hwnd as usize) } == 0 {
            return false;
        }
    }
    app.interaction.ready.set(true);
    true
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
