//! All Win32 ownership stays on the GUI thread. The shared core remains safe Rust.
use super::placement::{place, scale, Rect};
use crate::connection::{Connection, DesktopConnection};
use crate::options::Options;
use hormuz_client_platform::NativeCredentialStore;
use hormuz_client_session::{NativeTransport, SystemClock};
use std::cell::OnceCell;
#[path = "native_connection.rs"]
mod connected;
#[path = "native_interaction.rs"]
mod interactions;
type NativeConnection = Connection<
    PrivateDirectory,
    NativeCredentialStore,
    NativeTransport,
    SystemClock,
    connected::Browser,
>;
use hormuz_client_platform::{ApplicationInstance, PrivateDirectory};
use std::{
    cell::Cell,
    mem::size_of,
    ptr::{null, null_mut},
    sync::{
        atomic::{AtomicU8, Ordering},
        Arc,
    },
};
use windows_sys::Win32::{
    Foundation::*,
    Graphics::Gdi::*,
    System::LibraryLoader::GetModuleHandleW,
    UI::{
        Controls::WM_MOUSELEAVE, HiDpi::*, Input::KeyboardAndMouse::*, Shell::*,
        WindowsAndMessaging::*,
    },
};

const CLASS: &str = "HormuzNativeCompanionPreview";
const TITLE: &str = "Hormuz - synthetic preview";
const STYLE: u32 = WS_OVERLAPPED | WS_CAPTION | WS_SYSMENU | WS_MINIMIZEBOX;
const TRAY_CALLBACK: u32 = WM_APP + 1;
const INSTANCE_REOPEN: u32 = WM_APP + 2;
const CONNECTION_CHANGED: u32 = WM_APP + 3;
const NETWORK_CHANGED: u32 = WM_APP + 4;
// NIN_KEYSELECT is a C header expression, not emitted by windows-sys metadata.
const TRAY_KEY_SELECT: u32 = NIN_SELECT | NINF_KEY;
const FOLD: usize = 101;
const HIDE: usize = 102;
const SHOW: usize = 103;
const EXIT: usize = 104;
const SMOKE_TIMER: usize = 1;

struct App {
    connection: OnceCell<Box<dyn DesktopConnection>>,
    network: OnceCell<crate::network::NetworkEvents>,
    inputs: Cell<[HWND; 13]>,
    profile_loaded: Cell<bool>,
    session_notifications: Cell<bool>,
    preview: bool,
    activation: Arc<AtomicU8>, // idle, one pending notification, or shutting down
    interaction: interactions::Controller,
    dpi: Cell<u32>,
    controls: Cell<[HWND; 8]>,
    font: Cell<HFONT>,
    tray_ready: Cell<bool>,
    taskbar_message: u32,
    smoke: bool,
    smoke_step: Cell<u8>,
    exit_code: Cell<i32>,
}

fn wide(value: &str) -> Vec<u16> {
    value.encode_utf16().chain(Some(0)).collect()
}

pub fn run(options: Options, directory: PrivateDirectory, owner: ApplicationInstance) -> i32 {
    run_with_factory(options, directory, owner, |directory, notify| {
        let controller = hormuz_client_session::SessionController::new(
            directory,
            NativeCredentialStore::default(),
            NativeTransport,
            SystemClock::default(),
        );
        NativeConnection::start(controller, connected::Browser, notify)
            .map(|connection| Box::new(connection) as Box<dyn DesktopConnection>)
    })
}
pub(super) type Notifier = Box<dyn Fn() -> bool + Send + Sync>;
pub(super) fn run_with_factory(
    options: Options,
    directory: PrivateDirectory,
    owner: ApplicationInstance,
    factory: impl FnOnce(PrivateDirectory, Notifier) -> std::io::Result<Box<dyn DesktopConnection>>,
) -> i32 {
    let smoke = options.smoke;
    let preview = options.preview || smoke;
    // SAFETY: GUI initialization and every HWND below remain on this thread.
    // App outlives the window and every callback; Windows copies class/control strings.
    unsafe {
        if SetProcessDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2) == 0 {
            return 1;
        }
        let instance = GetModuleHandleW(null());
        if instance.is_null() {
            return 1;
        }
        let taskbar_message = RegisterWindowMessageW(wide("TaskbarCreated").as_ptr());
        if taskbar_message == 0 {
            return 1;
        }
        let mut app = Box::new(App {
            connection: OnceCell::new(),
            network: OnceCell::new(),
            inputs: Cell::new([null_mut(); 13]),
            profile_loaded: Cell::new(false),
            session_notifications: Cell::new(false),
            preview,
            activation: Arc::new(AtomicU8::new(0)),
            interaction: interactions::Controller::new(),
            dpi: Cell::new(96),
            controls: Cell::new([null_mut(); 8]),
            font: Cell::new(null_mut()),
            tray_ready: Cell::new(false),
            taskbar_message,
            smoke,
            smoke_step: Cell::new(0),
            exit_code: Cell::new(0),
        });
        let class_name = wide(CLASS);
        let class = WNDCLASSW {
            lpfnWndProc: Some(window_proc),
            hInstance: instance,
            lpszClassName: class_name.as_ptr(),
            hCursor: LoadCursorW(null_mut(), IDC_ARROW),
            hIcon: LoadIconW(null_mut(), IDI_APPLICATION),
            hbrBackground: (COLOR_WINDOW + 1) as HBRUSH,
            ..std::mem::zeroed()
        };
        if RegisterClassW(&class) == 0 {
            return 1;
        }
        let hwnd = CreateWindowExW(
            WS_EX_APPWINDOW,
            class_name.as_ptr(),
            wide(if preview { TITLE } else { "Hormuz companion" }).as_ptr(),
            STYLE,
            CW_USEDEFAULT,
            CW_USEDEFAULT,
            336,
            250,
            null_mut(),
            null_mut(),
            instance,
            (&*app as *const App).cast(),
        );
        if hwnd.is_null() {
            UnregisterClassW(class_name.as_ptr(), instance);
            return 1;
        }
        let descriptions = [
            ("STATIC", "Hormuz companion preview", 0),
            ("STATIC", "Synthetic data - no gateway connection", 0),
            ("STATIC", "Requests: 128 (synthetic)", 0),
            ("STATIC", "Tokens: 24,600 (synthetic)", 0),
            ("STATIC", "Est. cost: $0.42 (synthetic)", 0),
            ("BUTTON", "&Fold", FOLD),
            ("BUTTON", "&Hide", HIDE),
            ("BUTTON", "E&xit", EXIT),
        ];
        let mut controls = [null_mut(); 8];
        for (index, (kind, text, id)) in descriptions.into_iter().enumerate() {
            controls[index] = CreateWindowExW(
                0,
                wide(kind).as_ptr(),
                wide(text).as_ptr(),
                WS_CHILD
                    | WS_VISIBLE
                    | if id == 0 {
                        0
                    } else {
                        WS_TABSTOP | BS_PUSHBUTTON as u32
                    },
                0,
                0,
                1,
                1,
                hwnd,
                (if !preview && id == 0 { 300 + index } else { id }) as HMENU,
                instance,
                null(),
            );
            if controls[index].is_null() {
                app.exit_code.set(1);
                DestroyWindow(hwnd);
                UnregisterClassW(class_name.as_ptr(), instance);
                return 1;
            }
        }
        app.controls.set(controls);
        if !preview && !connected::initialize(hwnd, &app, directory, factory) {
            DestroyWindow(hwnd);
            UnregisterClassW(class_name.as_ptr(), instance);
            return 1;
        }
        if !interactions::initialize(hwnd, &app) {
            app.exit_code.set(1);
            DestroyWindow(hwnd);
            UnregisterClassW(class_name.as_ptr(), instance);
            return 1;
        }
        let activation_window = hwnd as usize;
        let activation = app.activation.clone();
        let Ok(listener) = owner.listen_for_reopen(move || {
            // At most one coalesced native notification is needed; the pipe
            // worker never reads or mutates the GUI's App allocation.
            match activation.compare_exchange(0, 1, Ordering::SeqCst, Ordering::SeqCst) {
                Ok(_) => {
                    if PostMessageW(activation_window as HWND, INSTANCE_REOPEN, 0, 0) != 0 {
                        true
                    } else {
                        let _ =
                            activation.compare_exchange(1, 0, Ordering::SeqCst, Ordering::SeqCst);
                        false
                    }
                }
                Err(1) => true,
                Err(_) => false,
            }
        }) else {
            DestroyWindow(hwnd);
            UnregisterClassW(class_name.as_ptr(), instance);
            return 1;
        };
        app.dpi.set(GetDpiForWindow(hwnd).max(96));
        app.tray_ready.set(add_tray(hwnd));
        reflow(hwnd, &app);
        ShowWindow(hwnd, SW_SHOW);
        SetForegroundWindow(hwnd);
        SetFocus(if preview {
            controls[5]
        } else {
            app.inputs.get()[1]
        });
        interactions::start(hwnd, &app);
        connected::visibility(hwnd, &app);
        if smoke && SetTimer(hwnd, SMOKE_TIMER, 300, None) == 0 {
            app.exit_code.set(1);
            DestroyWindow(hwnd);
        }
        let mut message: MSG = std::mem::zeroed();
        loop {
            let result = GetMessageW(&mut message, null_mut(), 0, 0);
            if result <= 0 {
                if result < 0 {
                    app.exit_code.set(1);
                    DestroyWindow(hwnd);
                }
                break;
            }
            if message.message == WM_KEYDOWN && message.wParam == VK_ESCAPE as usize {
                hide(hwnd, &app);
            } else if IsDialogMessageW(hwnd, &message) == 0 {
                TranslateMessage(&message);
                DispatchMessageW(&message);
            }
        }
        if !app.font.get().is_null() {
            DeleteObject(app.font.get());
        }
        // Drain credential work before allowing another primary to acquire
        // ownership. Workers never retain pointers to App or its controls.
        drop(app.network.take());
        drop(app.connection.take());
        drop(listener);
        UnregisterClassW(class_name.as_ptr(), instance);
        app.exit_code.get()
    }
}

unsafe fn tray_data(hwnd: HWND) -> NOTIFYICONDATAW {
    // SAFETY: All unused fields are zero; the Shell API consumes this only during the call.
    let mut data: NOTIFYICONDATAW = unsafe { std::mem::zeroed() };
    data.cbSize = size_of::<NOTIFYICONDATAW>() as u32;
    data.hWnd = hwnd;
    data.uID = 1;
    data
}

unsafe fn add_tray(hwnd: HWND) -> bool {
    // SAFETY: hwnd belongs to this GUI thread; the system icon is shared, not owned.
    unsafe {
        let mut data = tray_data(hwnd);
        data.uFlags = NIF_ICON | NIF_MESSAGE | NIF_TIP | NIF_SHOWTIP;
        data.uCallbackMessage = TRAY_CALLBACK;
        data.hIcon = LoadIconW(null_mut(), IDI_APPLICATION);
        let tip = wide("Hormuz - open companion");
        data.szTip[..tip.len()].copy_from_slice(&tip);
        if data.hIcon.is_null() || Shell_NotifyIconW(NIM_ADD, &data) == 0 {
            return false;
        }
        data.Anonymous.uVersion = NOTIFYICON_VERSION_4;
        if Shell_NotifyIconW(NIM_SETVERSION, &data) == 0 {
            Shell_NotifyIconW(NIM_DELETE, &data);
            return false;
        }
        true
    }
}

unsafe fn show(hwnd: HWND, app: &App) {
    // SAFETY: Live GUI-thread windows, with Cell state safe across synchronous callbacks.
    unsafe {
        interactions::reopen(hwnd, app);
    }
}

unsafe fn hide(hwnd: HWND, app: &App) {
    // If Explorer cannot supply a tray entry, keep taskbar recovery available.
    unsafe {
        interactions::hide(hwnd, app);
    }
}

unsafe fn reflow(hwnd: HWND, app: &App) {
    // SAFETY: All handles belong to this thread. Copy Cell values before calls;
    // do not hold mutable Rust references across a reentrant window procedure.
    unsafe {
        let dpi = app.dpi.get();
        let monitor = MonitorFromWindow(hwnd, MONITOR_DEFAULTTONEAREST);
        let mut info: MONITORINFO = std::mem::zeroed();
        info.cbSize = size_of::<MONITORINFO>() as u32;
        if GetMonitorInfoW(monitor, &mut info) == 0 {
            return;
        }
        let mut outer = RECT {
            left: 0,
            top: 0,
            right: scale(if app.preview { 336 } else { 432 }, dpi),
            bottom: scale(
                if app.preview {
                    if app.interaction.folded() {
                        106
                    } else {
                        216
                    }
                } else if app.interaction.folded() {
                    168
                } else {
                    488
                },
                dpi,
            ),
        };
        if AdjustWindowRectExForDpi(&mut outer, STYLE, 0, WS_EX_APPWINDOW, dpi) == 0 {
            return;
        }
        let area = info.rcWork;
        let rect = place(
            Rect {
                left: area.left,
                top: area.top,
                right: area.right,
                bottom: area.bottom,
            },
            outer.right - outer.left,
            outer.bottom - outer.top,
            scale(12, dpi),
        );
        SetWindowPos(
            hwnd,
            null_mut(),
            rect.left,
            rect.top,
            rect.right - rect.left,
            rect.bottom - rect.top,
            SWP_NOZORDER | SWP_NOACTIVATE,
        );
        let controls = app.controls.get();
        for (index, control) in controls.iter().take(5).enumerate() {
            let y = if app.preview {
                [16, 40, 82, 106, 130][index]
            } else {
                [12, 36, 118, 142, 166][index]
            };
            MoveWindow(
                *control,
                scale(16, dpi),
                scale(y, dpi),
                scale(if app.preview { 304 } else { 400 }, dpi),
                scale(if !app.preview && index == 1 { 78 } else { 22 }, dpi),
                1,
            );
            ShowWindow(
                *control,
                if app.interaction.folded() && index >= 2 {
                    SW_HIDE
                } else {
                    SW_SHOW
                },
            );
        }
        let y = if app.preview {
            if app.interaction.folded() {
                64
            } else {
                176
            }
        } else if app.interaction.folded() {
            128
        } else {
            448
        };
        for (index, control) in controls.iter().skip(5).enumerate() {
            MoveWindow(
                *control,
                scale(16 + index as i32 * 104, dpi),
                scale(y, dpi),
                scale(96, dpi),
                scale(28, dpi),
                1,
            );
        }
        SetWindowTextW(
            controls[5],
            wide(if app.interaction.folded() {
                "&Expand"
            } else {
                "&Fold"
            })
            .as_ptr(),
        );
        connected::layout(app);
        let font = CreateFontW(
            -scale(14, dpi),
            0,
            0,
            0,
            FW_NORMAL as i32,
            0,
            0,
            0,
            DEFAULT_CHARSET as u32,
            OUT_DEFAULT_PRECIS as u32,
            CLIP_DEFAULT_PRECIS as u32,
            DEFAULT_QUALITY as u32,
            DEFAULT_PITCH as u32,
            wide("Segoe UI").as_ptr(),
        );
        if !font.is_null() {
            for control in controls
                .into_iter()
                .chain(app.inputs.get())
                .filter(|h| !h.is_null())
            {
                SendMessageW(control, WM_SETFONT, font as usize, 1);
            }
            let previous = app.font.replace(font);
            if !previous.is_null() {
                DeleteObject(previous);
            }
        }
    }
}

unsafe fn tray_menu(hwnd: HWND, app: &App) {
    // SAFETY: Menu has one owner and is destroyed after the synchronous popup returns.
    unsafe {
        let menu = CreatePopupMenu();
        if menu.is_null() {
            show(hwnd, app);
            return;
        }
        for (id, text) in [(SHOW, "&Show Hormuz"), (HIDE, "&Hide"), (EXIT, "E&xit")] {
            if AppendMenuW(menu, MF_STRING, id, wide(text).as_ptr()) == 0 {
                DestroyMenu(menu);
                show(hwnd, app);
                return;
            }
        }
        let mut point: POINT = std::mem::zeroed();
        if GetCursorPos(&mut point) == 0 {
            DestroyMenu(menu);
            show(hwnd, app);
            return;
        }
        SetForegroundWindow(hwnd);
        let command = TrackPopupMenuEx(
            menu,
            TPM_RETURNCMD | TPM_NONOTIFY | TPM_RIGHTBUTTON,
            point.x,
            point.y,
            hwnd,
            null(),
        );
        DestroyMenu(menu);
        if command != 0 {
            SendMessageW(hwnd, WM_COMMAND, command as usize, 0);
        }
        PostMessageW(hwnd, WM_NULL, 0, 0);
    }
}

unsafe fn smoke_step(hwnd: HWND, app: &App) {
    // This checks real HWND lifecycle calls, not mouse, UIA or physical-monitor acceptance.
    unsafe {
        let okay = match app.smoke_step.get() {
            0 => {
                let visible = IsWindowVisible(hwnd) != 0
                    && app.controls.get().iter().all(|h| IsWindow(*h) != 0);
                let mut before: RECT = std::mem::zeroed();
                let mut after: RECT = std::mem::zeroed();
                let before_ok = GetWindowRect(hwnd, &mut before) != 0;
                SendMessageW(hwnd, WM_COMMAND, FOLD, 0);
                SendMessageW(hwnd, interactions::DRAIN, 0, 0);
                visible
                    && before_ok
                    && GetWindowRect(hwnd, &mut after) != 0
                    && after.bottom - after.top < before.bottom - before.top
                    && IsWindowVisible(app.controls.get()[2]) == 0
                    && IsWindowVisible(app.controls.get()[5]) != 0
            }
            1 => {
                SendMessageW(hwnd, WM_CLOSE, 0, 0);
                SendMessageW(hwnd, interactions::DRAIN, 0, 0);
                if app.tray_ready.get() {
                    IsWindowVisible(hwnd) == 0
                } else {
                    IsIconic(hwnd) != 0
                }
            }
            2 => {
                SendMessageW(hwnd, TRAY_CALLBACK, 0, NIN_SELECT as isize);
                SendMessageW(hwnd, interactions::DRAIN, 0, 0);
                IsWindowVisible(hwnd) != 0
                    && IsIconic(hwnd) == 0
                    && IsWindowVisible(app.controls.get()[2]) != 0
            }
            _ => {
                SendMessageW(hwnd, WM_COMMAND, FOLD, 0);
                SendMessageW(hwnd, interactions::DRAIN, 0, 0);
                let mut before: RECT = std::mem::zeroed();
                let mut after: RECT = std::mem::zeroed();
                let before_ok = GetWindowRect(hwnd, &mut before) != 0;
                SendMessageW(hwnd, WM_COMMAND, FOLD, 0);
                SendMessageW(hwnd, interactions::DRAIN, 0, 0);
                let expanded = before_ok
                    && GetWindowRect(hwnd, &mut after) != 0
                    && after.bottom - after.top > before.bottom - before.top
                    && IsWindowVisible(app.controls.get()[2]) != 0;
                app.exit_code.set(if expanded { 0 } else { 1 });
                println!(
                    "windows_shell_smoke={} tray_registered={}",
                    if expanded { "passed" } else { "failed" },
                    app.tray_ready.get()
                );
                DestroyWindow(hwnd);
                return;
            }
        };
        if !okay {
            app.exit_code.set(1);
            DestroyWindow(hwnd);
        }
        app.smoke_step.set(app.smoke_step.get() + 1);
    }
}

unsafe extern "system" fn window_proc(
    hwnd: HWND,
    message: u32,
    wparam: WPARAM,
    lparam: LPARAM,
) -> LRESULT {
    // SAFETY: WM_NCCREATE supplies CREATESTRUCTW for this call. The pointer stored
    // in GWLP_USERDATA is owned by run() until after WM_NCDESTROY and loop exit.
    unsafe {
        if message == WM_NCCREATE {
            let creation = &*(lparam as *const CREATESTRUCTW);
            SetWindowLongPtrW(hwnd, GWLP_USERDATA, creation.lpCreateParams as isize);
        }
        let pointer = GetWindowLongPtrW(hwnd, GWLP_USERDATA) as *const App;
        let Some(app) = pointer.as_ref() else {
            return DefWindowProcW(hwnd, message, wparam, lparam);
        };
        if message == app.taskbar_message {
            app.tray_ready.set(add_tray(hwnd));
            if !app.tray_ready.get() {
                show(hwnd, app);
            }
            return 0;
        }
        match message {
            interactions::DRAIN => {
                interactions::drain(hwnd, app);
                0
            }
            WM_SETFOCUS | WM_KILLFOCUS => {
                interactions::focus(
                    hwnd,
                    app,
                    if message == WM_SETFOCUS {
                        hwnd
                    } else {
                        wparam as HWND
                    },
                );
                DefWindowProcW(hwnd, message, wparam, lparam)
            }
            WM_ACTIVATE => {
                if wparam as u32 & 0xffff == WA_INACTIVE {
                    interactions::focus(hwnd, app, null_mut());
                    interactions::pointer(hwnd, app, hwnd, message);
                }
                DefWindowProcW(hwnd, message, wparam, lparam)
            }
            WM_MOUSEMOVE | WM_MOUSELEAVE | WM_NCMOUSEMOVE | WM_NCMOUSELEAVE => {
                interactions::pointer(hwnd, app, hwnd, message);
                DefWindowProcW(hwnd, message, wparam, lparam)
            }
            NETWORK_CHANGED => {
                if app.network.get().is_some_and(|events| events.take()) {
                    if let Some(connection) = app.connection.get() {
                        connection
                            .lifecycle(hormuz_client_platform::LifecycleEvent::NetworkChanged);
                    }
                }
                0
            }
            CONNECTION_CHANGED => {
                connected::update(app);
                0
            }
            WM_SHOWWINDOW | WM_SIZE => {
                // WM_SHOWWINDOW precedes the actual visibility change. Its
                // wParam is the observation; IsWindowVisible may still be old.
                let visible = if message == WM_SHOWWINDOW {
                    wparam != 0 && IsIconic(hwnd) == 0
                } else {
                    wparam != SIZE_MINIMIZED as usize && IsWindowVisible(hwnd) != 0
                };
                interactions::visibility(hwnd, app, visible);
                connected::visibility(hwnd, app);
                DefWindowProcW(hwnd, message, wparam, lparam)
            }
            WM_WTSSESSION_CHANGE => {
                connected::session_changed(app, wparam);
                0
            }
            WM_POWERBROADCAST => {
                connected::power_changed(app, wparam);
                1
            }
            INSTANCE_REOPEN => {
                if app
                    .activation
                    .compare_exchange(1, 0, Ordering::SeqCst, Ordering::SeqCst)
                    .is_ok()
                {
                    show(hwnd, app);
                }
                0
            }
            WM_CLOSE => {
                hide(hwnd, app);
                0
            }
            WM_COMMAND => {
                match wparam & 0xffff {
                    connected::SIGN_IN => connected::sign_in(app),
                    connected::SIGN_OUT => {
                        if let Some(connection) = app.connection.get() {
                            connection.sign_out();
                            connected::update(app);
                        }
                    }
                    connected::RETRY => {
                        if let Some(connection) = app.connection.get() {
                            connection.retry();
                            connected::update(app);
                        }
                    }
                    FOLD => {
                        interactions::toggle_fold(hwnd, app);
                    }
                    HIDE | 2 => hide(hwnd, app), // IDCANCEL from dialog keyboard navigation.
                    SHOW => show(hwnd, app),
                    EXIT => {
                        DestroyWindow(hwnd);
                    }
                    _ => {}
                }
                0
            }
            TRAY_CALLBACK => {
                match lparam as u32 & 0xffff {
                    NIN_SELECT | TRAY_KEY_SELECT => show(hwnd, app),
                    WM_CONTEXTMENU => tray_menu(hwnd, app),
                    _ => {}
                }
                0
            }
            WM_DPICHANGED => {
                app.dpi.set((wparam & 0xffff) as u32);
                if lparam != 0 {
                    let suggested = &*(lparam as *const RECT);
                    SetWindowPos(
                        hwnd,
                        null_mut(),
                        suggested.left,
                        suggested.top,
                        suggested.right - suggested.left,
                        suggested.bottom - suggested.top,
                        SWP_NOZORDER | SWP_NOACTIVATE,
                    );
                }
                reflow(hwnd, app);
                0
            }
            WM_DISPLAYCHANGE | WM_SETTINGCHANGE => {
                reflow(hwnd, app);
                0
            }
            WM_TIMER if app.smoke && wparam == SMOKE_TIMER => {
                smoke_step(hwnd, app);
                0
            }
            WM_TIMER => {
                interactions::timer(hwnd, app, wparam);
                0
            }
            WM_DESTROY => {
                app.activation.store(2, Ordering::SeqCst);
                interactions::stop(hwnd, app);
                connected::shutdown(hwnd, app);
                KillTimer(hwnd, SMOKE_TIMER);
                Shell_NotifyIconW(NIM_DELETE, &tray_data(hwnd));
                PostQuitMessage(app.exit_code.get());
                0
            }
            WM_NCDESTROY => {
                SetWindowLongPtrW(hwnd, GWLP_USERDATA, 0);
                DefWindowProcW(hwnd, message, wparam, lparam)
            }
            _ => DefWindowProcW(hwnd, message, wparam, lparam),
        }
    }
}
