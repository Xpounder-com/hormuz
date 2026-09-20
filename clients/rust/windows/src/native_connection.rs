//! Native controls and browser/session adapters. Never handles credentials.
use super::*;
use crate::{connection::Phase, presentation};
use hormuz_client_core::{AIClient, ClientError};
use hormuz_client_platform::{BrowserOpener, LifecycleEvent, PlatformError};
use hormuz_client_session::DashboardVisibility;
use windows_sys::Win32::System::{Com::*, RemoteDesktop::*};
use windows_sys::Win32::UI::Controls::EM_SETLIMITTEXT;

pub const SIGN_IN: usize = 109;
pub const SIGN_OUT: usize = 110;
pub const RETRY: usize = 111;
pub const SETTINGS: usize = 112;

pub struct Browser;
impl BrowserOpener for Browser {
    fn open_authentication_url(&self, url: &str) -> hormuz_client_platform::Result<()> {
        // The session layer already requires the exact gateway enrollment URL.
        // Production form inputs require HTTPS; do not allow arbitrary protocols.
        if !url.starts_with("https://") || url.contains('\0') || url.len() > 4096 {
            return Err(PlatformError::Unavailable);
        }
        let url = wide(url);
        let verb = wide("open");
        unsafe {
            if CoInitializeEx(
                null(),
                (COINIT_APARTMENTTHREADED | COINIT_DISABLE_OLE1DDE) as u32,
            ) < 0
            {
                return Err(PlatformError::Unavailable);
            }
            let mut info = SHELLEXECUTEINFOW {
                cbSize: size_of::<SHELLEXECUTEINFOW>() as u32,
                fMask: SEE_MASK_NOASYNC | SEE_MASK_FLAG_NO_UI,
                lpVerb: verb.as_ptr(),
                lpFile: url.as_ptr(),
                nShow: SW_SHOWNORMAL,
                ..std::mem::zeroed()
            };
            // No shell command, parameter string, process handle or URL logging.
            // NOASYNC completes dispatch before this worker uninitializes COM.
            let opened = ShellExecuteExW(&mut info) != 0;
            CoUninitialize();
            if opened {
                Ok(())
            } else {
                Err(PlatformError::Unavailable)
            }
        }
    }
}

unsafe fn unlocked() -> bool {
    unsafe {
        let mut buffer = null_mut();
        let mut bytes = 0;
        if WTSQuerySessionInformationW(
            WTS_CURRENT_SERVER_HANDLE,
            WTS_CURRENT_SESSION,
            WTSSessionInfoEx,
            &mut buffer,
            &mut bytes,
        ) == 0
        {
            return false;
        }
        let result = if !buffer.is_null() && bytes as usize >= size_of::<WTSINFOEXW>() {
            let info = &*buffer.cast::<WTSINFOEXW>();
            info.Level == 1
                && info.Data.WTSInfoExLevel1.SessionState == WTSActive
                && info.Data.WTSInfoExLevel1.SessionFlags as u32 == WTS_SESSIONSTATE_UNLOCK
        } else {
            false
        };
        WTSFreeMemory(buffer.cast());
        result
    }
}

pub unsafe fn initialize(
    hwnd: HWND,
    app: &App,
    directory: PrivateDirectory,
    factory: impl FnOnce(PrivateDirectory, Notifier) -> std::io::Result<Box<dyn DesktopConnection>>,
) -> bool {
    unsafe {
        let button = CreateWindowExW(
            0,
            wide("BUTTON").as_ptr(),
            wide("&Settings").as_ptr(),
            WS_CHILD | WS_VISIBLE | WS_TABSTOP | BS_PUSHBUTTON as u32,
            0,
            0,
            1,
            1,
            hwnd,
            SETTINGS as HMENU,
            GetModuleHandleW(null()),
            null(),
        );
        if button.is_null() {
            return false;
        }
        app.settings_button.set(button);
        let definitions = [
            ("STATIC", "&Gateway (HTTPS)", 0),
            ("EDIT", "", 201),
            ("STATIC", "&Organization ID", 0),
            ("EDIT", "", 202),
            ("STATIC", "&Model alias", 0),
            ("EDIT", "openai-primary", 203),
            ("STATIC", "&Issuer (optional)", 0),
            ("EDIT", "", 204),
            ("STATIC", "&Client", 0),
            ("COMBOBOX", "", 205),
            ("BUTTON", "Sign &in", SIGN_IN),
            ("BUTTON", "Sign &out / cancel", SIGN_OUT),
            ("BUTTON", "&Retry connection", RETRY),
        ];
        let mut inputs = [null_mut(); 13];
        for (index, (kind, text, id)) in definitions.into_iter().enumerate() {
            let style = WS_CHILD
                | WS_VISIBLE
                | if kind == "EDIT" {
                    WS_TABSTOP | WS_BORDER | ES_AUTOHSCROLL as u32
                } else if kind == "COMBOBOX" {
                    WS_TABSTOP | CBS_DROPDOWNLIST as u32 | WS_VSCROLL
                } else if kind == "BUTTON" {
                    WS_TABSTOP | BS_PUSHBUTTON as u32
                } else {
                    0
                };
            inputs[index] = CreateWindowExW(
                0,
                wide(kind).as_ptr(),
                wide(text).as_ptr(),
                style,
                0,
                0,
                1,
                1,
                hwnd,
                id as HMENU,
                GetModuleHandleW(null()),
                null(),
            );
            if inputs[index].is_null() {
                return false;
            }
            if kind == "EDIT" {
                SendMessageW(
                    inputs[index],
                    EM_SETLIMITTEXT,
                    if id == 201 || id == 204 { 2048 } else { 200 },
                    0,
                );
            }
        }
        app.inputs.set(inputs);
        for text in ["Codex", "Claude Code"] {
            SendMessageW(inputs[9], CB_ADDSTRING, 0, wide(text).as_ptr() as isize);
        }
        SendMessageW(inputs[9], CB_SETCURSEL, 0, 0);
        if WTSRegisterSessionNotification(hwnd, NOTIFY_FOR_THIS_SESSION) == 0 {
            return false;
        }
        app.session_notifications.set(true);
        let Some(network) = crate::network::NetworkEvents::register(hwnd, NETWORK_CHANGED) else {
            return false;
        };
        if app.network.set(network).is_err() {
            return false;
        }
        let destination = hwnd as usize;
        let active = app.activation.clone();
        let Ok(connection) = factory(
            directory,
            Box::new(move || {
                if active.load(Ordering::SeqCst) != 2 {
                    PostMessageW(destination as HWND, CONNECTION_CHANGED, 0, 0) != 0
                } else {
                    false
                }
            }),
        ) else {
            return false;
        };
        if unlocked() {
            connection.lifecycle(LifecycleEvent::SessionUnlocked);
        }
        if app.connection.set(connection).is_err() {
            return false;
        }
        update(app);
        true
    }
}

pub unsafe fn update(app: &App) {
    let Some(connection) = app.connection.get() else {
        return;
    };
    let view = connection.view();
    unsafe {
        for (control, text) in app.controls.get().iter().zip(presentation::labels(&view)) {
            SetWindowTextW(*control, wide(&text).as_ptr());
        }
        let inputs = app.inputs.get();
        if !app.profile_loaded.get() && !view.phase.busy() {
            if let Some(profile) = view.connection.as_ref().and_then(|s| s.profile()) {
                for (index, text) in [
                    (1, profile.gateway()),
                    (3, profile.organization()),
                    (5, profile.model()),
                    (7, profile.issuer().unwrap_or("")),
                ] {
                    SetWindowTextW(inputs[index], wide(text).as_ptr());
                }
                SendMessageW(
                    inputs[9],
                    CB_SETCURSEL,
                    usize::from(profile.client() == AIClient::ClaudeCode),
                    0,
                );
                app.profile_loaded.set(true);
            }
        }
        let editable = !view.has_session() && !view.phase.busy();
        for index in [1, 3, 5, 7, 9, 10] {
            EnableWindow(inputs[index], editable as i32);
        }
        EnableWindow(
            inputs[11],
            (view.phase != Phase::SigningOut && view.phase != Phase::Checking) as i32,
        );
        EnableWindow(inputs[12], (!view.phase.busy()) as i32);
    }
}
unsafe fn input(handle: HWND) -> Result<String, ClientError> {
    unsafe {
        let length = GetWindowTextLengthW(handle);
        if !(0..=2048).contains(&length) {
            return Err(ClientError::InvalidProfile);
        }
        let mut value = vec![0; length as usize + 1];
        let read = GetWindowTextW(handle, value.as_mut_ptr(), value.len() as i32);
        if read != length {
            return Err(ClientError::InvalidProfile);
        }
        String::from_utf16(&value[..length as usize]).map_err(|_| ClientError::InvalidProfile)
    }
}
pub unsafe fn sign_in(app: &App) {
    let Some(connection) = app.connection.get() else {
        return;
    };
    unsafe {
        let inputs = app.inputs.get();
        let profile = (|| {
            let client = match SendMessageW(inputs[9], CB_GETCURSEL, 0, 0) {
                0 => "codex",
                1 => "claude-code",
                _ => return Err(ClientError::InvalidProfile),
            };
            presentation::profile(
                &input(inputs[1])?,
                &input(inputs[3])?,
                &input(inputs[5])?,
                &input(inputs[7])?,
                client,
            )
        })();
        match profile {
            Ok(profile) => {
                connection.sign_in(profile);
                update(app);
            }
            Err(error) => {
                SetWindowTextW(app.controls.get()[1], wide(&error.to_string()).as_ptr());
            }
        }
    }
}
pub unsafe fn layout(app: &App, button_y: i32) {
    if app.preview {
        return;
    }
    unsafe {
        let dpi = app.dpi.get();
        let settings = app.interaction.settings_open() && !app.interaction.folded();
        MoveWindow(
            app.settings_button.get(),
            scale(328, dpi),
            scale(button_y, dpi),
            scale(88, dpi),
            scale(28, dpi),
            1,
        );
        SetWindowTextW(
            app.settings_button.get(),
            wide(if settings { "&Back" } else { "&Settings" }).as_ptr(),
        );
        if !settings {
            SendMessageW(app.inputs.get()[9], CB_SHOWDROPDOWN, 0, 0);
        }
        for (index, handle) in app.inputs.get().into_iter().enumerate() {
            let (x, y, width, height) = if index < 10 {
                (
                    if index % 2 == 0 { 16 } else { 160 },
                    202 + (index / 2) as i32 * 36,
                    if index % 2 == 0 { 140 } else { 256 },
                    if index == 9 { 160 } else { 26 },
                )
            } else {
                (16 + (index - 10) as i32 * 136, 402, 128, 30)
            };
            MoveWindow(
                handle,
                scale(x, dpi),
                scale(y, dpi),
                scale(width, dpi),
                scale(height, dpi),
                1,
            );
            ShowWindow(handle, if settings { SW_SHOW } else { SW_HIDE });
        }
    }
}

pub unsafe fn settings_focus(app: &App) -> HWND {
    // A restored session can disable all profile edits. Focus an actual enabled
    // form action then; while checking, leave focus on the Settings/Back button.
    let inputs = app.inputs.get();
    [1, 3, 5, 7, 9, 10, 11, 12]
        .into_iter()
        .map(|index| inputs[index])
        .find(|handle| unsafe { IsWindowVisible(*handle) != 0 && IsWindowEnabled(*handle) != 0 })
        .unwrap_or(app.settings_button.get())
}

pub unsafe fn escape_popup(app: &App) -> bool {
    let combo = app.inputs.get()[9];
    if combo.is_null() || unsafe { SendMessageW(combo, CB_GETDROPPEDSTATE, 0, 0) } == 0 {
        return false;
    }
    // Let the native combo cancel its own pending selection before Escape can
    // navigate the settings surface. No shared page state models this popup.
    unsafe { SendMessageW(combo, WM_KEYDOWN, VK_ESCAPE as usize, 1) };
    true
}
pub unsafe fn visibility(hwnd: HWND, app: &App) {
    if let Some(connection) = app.connection.get() {
        let visibility = if unsafe { IsWindowVisible(hwnd) == 0 || IsIconic(hwnd) != 0 } {
            DashboardVisibility::Hidden
        } else if app.interaction.folded() {
            DashboardVisibility::Summary
        } else {
            DashboardVisibility::Detail
        };
        connection.visibility(visibility);
    }
}
pub unsafe fn session_changed(app: &App, event: usize) {
    if let Some(connection) = app.connection.get() {
        match event as u32 {
            WTS_SESSION_LOCK | WTS_CONSOLE_DISCONNECT | WTS_REMOTE_DISCONNECT => {
                connection.lifecycle(LifecycleEvent::SessionLocked)
            }
            WTS_SESSION_UNLOCK | WTS_CONSOLE_CONNECT | WTS_REMOTE_CONNECT => {
                connection.lifecycle(if unsafe { unlocked() } {
                    LifecycleEvent::SessionUnlocked
                } else {
                    LifecycleEvent::SessionLocked
                });
            }
            _ => {}
        }
    }
}
pub fn power_changed(app: &App, event: usize) {
    if let Some(connection) = app.connection.get() {
        match event as u32 {
            PBT_APMSUSPEND => connection.lifecycle(LifecycleEvent::Sleep),
            PBT_APMRESUMEAUTOMATIC | PBT_APMRESUMESUSPEND => {
                connection.lifecycle(LifecycleEvent::Wake)
            }
            _ => {}
        }
    }
}
pub unsafe fn shutdown(hwnd: HWND, app: &App) {
    unsafe {
        if app.network.get().is_some_and(|events| !events.stop()) {
            app.exit_code.set(1);
        }
        if app.session_notifications.replace(false) {
            WTSUnRegisterSessionNotification(hwnd);
        }
        if let Some(connection) = app.connection.get() {
            connection.lifecycle(LifecycleEvent::Quit);
        }
    }
}
