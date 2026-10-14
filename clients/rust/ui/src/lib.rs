//! C ownership boundary; the existing desktop worker owns session scheduling.
//! macOS custody is supplied explicitly by the Swift executable, never selected
//! implicitly by this crate. Display and custody allocations are distinct.
#![deny(unsafe_op_in_unsafe_fn)]

use hormuz_client_core::{ConnectionProfile, ContextOptimizationPreference, SessionState};
use hormuz_client_desktop::connection::{Connection, Phase};
use hormuz_client_platform::{
    BrowserOpener, CredentialStore, LifecycleEvent, PlatformError, PrivateDirectory, SecretRecord,
};
use hormuz_client_session::{DashboardVisibility, NativeTransport, SessionController, SystemClock};
use std::ffi::c_void;
use std::path::Path;
use std::sync::{Arc, Mutex, MutexGuard};

const MAX_INPUT: usize = 128 * 1024;

fn lock<T>(mutex: &Mutex<T>) -> MutexGuard<'_, T> {
    mutex.lock().unwrap_or_else(|error| error.into_inner())
}

/// Each callback is synchronous, worker-only, bounded and non-reentrant. Read
/// transfers one owned secret to Rust; write only borrows the opaque handle for
/// its duration. The host context remains alive until Ui is freed and joined.
#[repr(C)]
#[derive(Clone, Copy)]
pub struct Host {
    pub context: *mut c_void,
    pub read: Option<unsafe extern "C" fn(*mut c_void, *mut *mut Secret) -> i32>,
    pub write: Option<unsafe extern "C" fn(*mut c_void, *const Secret) -> i32>,
    pub delete: Option<unsafe extern "C" fn(*mut c_void) -> i32>,
    pub open_browser: Option<unsafe extern "C" fn(*mut c_void, *const u8, usize) -> i32>,
}
// Host's documented contract requires thread-safe custody callbacks/context.
unsafe impl Send for Host {}
unsafe impl Sync for Host {}

struct HostStore(Host);
impl CredentialStore for HostStore {
    fn load(&self) -> Result<Option<SecretRecord>, PlatformError> {
        let mut output = std::ptr::null_mut();
        let result = unsafe { (self.0.read.unwrap())(self.0.context, &mut output) };
        if result == 0 && output.is_null() {
            return Ok(None);
        }
        if result == 1 && !output.is_null() {
            let secret = unsafe { Box::from_raw(output) }.0;
            return Ok(Some(secret));
        }
        // Even a failing callback must not leak an allocation it transferred.
        if !output.is_null() {
            drop(unsafe { Box::from_raw(output) });
        }
        Err(PlatformError::SecureStoreUnavailable)
    }
    fn save(&self, record: &SecretRecord) -> Result<(), PlatformError> {
        let secret = Secret(SecretRecord::new(record.expose().to_vec())?);
        let result = unsafe { (self.0.write.unwrap())(self.0.context, &secret) };
        if result == 0 {
            Ok(())
        } else {
            Err(PlatformError::SecureStoreUnavailable)
        }
    }
    fn delete(&self) -> Result<(), PlatformError> {
        if unsafe { (self.0.delete.unwrap())(self.0.context) } == 0 {
            Ok(())
        } else {
            Err(PlatformError::SecureStoreUnavailable)
        }
    }
    fn maximum_record_bytes(&self) -> usize {
        32_767
    }
}
struct HostBrowser(Host);
impl BrowserOpener for HostBrowser {
    fn open_authentication_url(&self, url: &str) -> Result<(), PlatformError> {
        if unsafe { (self.0.open_browser.unwrap())(self.0.context, url.as_ptr(), url.len()) } == 0 {
            Ok(())
        } else {
            Err(PlatformError::Unavailable)
        }
    }
}

type Desktop = Connection<PrivateDirectory, HostStore, NativeTransport, SystemClock, HostBrowser>;
pub struct Ui {
    desktop: Desktop,
    signal: Arc<Signal>,
}
pub struct Bytes(Vec<u8>);
pub struct Secret(SecretRecord);
pub struct Subscription {
    signal: Arc<Signal>,
    id: u64,
}
type Wake = unsafe extern "C" fn(*mut c_void);
struct Subscriber {
    id: u64,
    callback: Wake,
    context: *mut c_void,
}
unsafe impl Send for Subscriber {}
#[derive(Default)]
struct SignalState {
    closed: bool,
    pending: bool,
    next_id: u64,
    subscriber: Option<Subscriber>,
    last: Option<Vec<u8>>,
}
#[derive(Default)]
struct Signal(Mutex<SignalState>);
impl Signal {
    fn notify(&self) -> bool {
        let mut state = lock(&self.0);
        if state.closed {
            return false;
        }
        if !state.pending {
            state.pending = true;
            if let Some(subscriber) = &state.subscriber {
                // Wake is not a view callback: it must only enqueue a UI turn.
                // Holding the gate makes unsubscribe wait for its completion.
                unsafe { (subscriber.callback)(subscriber.context) };
            }
        }
        true
    }
}
impl Drop for Ui {
    fn drop(&mut self) {
        let mut state = lock(&self.signal.0);
        state.closed = true;
        state.subscriber = None;
        // desktop then cancels and joins its worker before host/context release.
    }
}
impl Drop for Subscription {
    fn drop(&mut self) {
        let mut state = lock(&self.signal.0);
        if state
            .subscriber
            .as_ref()
            .is_some_and(|value| value.id == self.id)
        {
            state.subscriber = None;
        }
    }
}

unsafe fn input<'a>(data: *const u8, length: usize) -> Option<&'a [u8]> {
    if length > MAX_INPUT || (length != 0 && data.is_null()) {
        return None;
    }
    if length == 0 {
        Some(&[])
    } else {
        Some(unsafe { std::slice::from_raw_parts(data, length) })
    }
}
unsafe fn ui<'a>(handle: *const Ui) -> Option<&'a Ui> {
    unsafe { handle.as_ref() }
}
fn bytes(value: Vec<u8>) -> *mut Bytes {
    Box::into_raw(Box::new(Bytes(value)))
}
// Browser enrollment remains shell-owned on Mac. This command only exposes
// the existing shared manual enrollment API, not a hosted enrollment substitute.
fn manual_profile(bytes: &[u8]) -> bool {
    let Ok(value) = serde_json::from_slice::<serde_json::Value>(bytes) else {
        return false;
    };
    value.is_object()
        && value.get("desktopManaged").is_none_or(|v| v == false)
        && value
            .get("desktopProfileVersion")
            .is_none_or(|v| v.is_null())
}
fn view(handle: &Ui) -> Vec<u8> {
    let view = handle.desktop.view();
    let (phase, error) = match view.phase {
        Phase::Checking => ("checking", None),
        Phase::Ready => ("ready", None),
        Phase::SigningIn => ("signingIn", None),
        Phase::SigningOut => ("signingOut", None),
        Phase::Failed(error) => ("failed", Some(error)),
    };
    serde_json::to_vec(&serde_json::json!({
        "schema_version": 1, "phase": phase, "error": error,
        "connection": view.connection, "snapshot": &*view.snapshot,
    }))
    .expect("validated display projection is serializable")
}

/// ABI v1. Invalid pointers, double-free and calls racing free are caller errors.
/// All non-free calls accept null handles and fail closed.
#[no_mangle]
pub extern "C" fn hormuz_ui_abi_version() -> u32 {
    1
}

#[no_mangle]
/// # Safety
/// Non-null handles must be live allocations of the declared type. Input and
/// output buffers must cover their lengths and not overlap. The caller must
/// obey the ownership, callback lifetime and no-free-race contract in HormuzRust.h.
pub unsafe extern "C" fn hormuz_ui_new(root: *const u8, length: usize, host: Host) -> *mut Ui {
    if host.read.is_none()
        || host.write.is_none()
        || host.delete.is_none()
        || host.open_browser.is_none()
    {
        return std::ptr::null_mut();
    }
    let Some(root) = (unsafe { input(root, length) }).and_then(|v| std::str::from_utf8(v).ok())
    else {
        return std::ptr::null_mut();
    };
    let Ok(directory) = PrivateDirectory::open(Path::new(root)) else {
        return std::ptr::null_mut();
    };
    let signal = Arc::new(Signal::default());
    let notify = signal.clone();
    let controller = SessionController::new(
        directory,
        HostStore(host),
        NativeTransport,
        SystemClock::default(),
    );
    let Ok(desktop) = Desktop::start(controller, HostBrowser(host), move || notify.notify()) else {
        return std::ptr::null_mut();
    };
    Box::into_raw(Box::new(Ui { desktop, signal }))
}

#[no_mangle]
/// # Safety
/// Non-null handles must be live allocations of the declared type. Input and
/// output buffers must cover their lengths and not overlap. The caller must
/// obey the ownership, callback lifetime and no-free-race contract in HormuzRust.h.
pub unsafe extern "C" fn hormuz_ui_free(handle: *mut Ui) {
    if !handle.is_null() {
        drop(unsafe { Box::from_raw(handle) });
    }
}
#[no_mangle]
/// # Safety
/// Non-null handles must be live allocations of the declared type. Input and
/// output buffers must cover their lengths and not overlap. The caller must
/// obey the ownership, callback lifetime and no-free-race contract in HormuzRust.h.
pub unsafe extern "C" fn hormuz_ui_connect(
    handle: *const Ui,
    data: *const u8,
    length: usize,
) -> bool {
    let Some(handle) = (unsafe { ui(handle) }) else {
        return false;
    };
    let Some(profile) = (unsafe { input(data, length) })
        .filter(|v| manual_profile(v))
        .and_then(|v| ConnectionProfile::from_json(v).ok())
    else {
        return false;
    };
    handle.desktop.sign_in(profile)
}
#[no_mangle]
/// # Safety
/// Non-null handles must be live allocations of the declared type. Input and
/// output buffers must cover their lengths and not overlap. The caller must
/// obey the ownership, callback lifetime and no-free-race contract in HormuzRust.h.
pub unsafe extern "C" fn hormuz_ui_disconnect(handle: *const Ui) {
    if let Some(handle) = unsafe { ui(handle) } {
        handle.desktop.sign_out();
    }
}
#[no_mangle]
/// # Safety
/// Non-null handles must be live allocations of the declared type. Input and
/// output buffers must cover their lengths and not overlap. The caller must
/// obey the ownership, callback lifetime and no-free-race contract in HormuzRust.h.
pub unsafe extern "C" fn hormuz_ui_retry(handle: *const Ui) -> bool {
    unsafe { ui(handle) }.is_some_and(|value| value.desktop.retry())
}
#[no_mangle]
/// # Safety
/// Non-null handles must be live allocations of the declared type. Input and
/// output buffers must cover their lengths and not overlap. The caller must
/// obey the ownership, callback lifetime and no-free-race contract in HormuzRust.h.
pub unsafe extern "C" fn hormuz_ui_cancel(handle: *const Ui) -> bool {
    unsafe { ui(handle) }.is_some_and(|value| value.desktop.cancel())
}
#[no_mangle]
/// # Safety
/// Non-null handles must be live allocations of the declared type. Input and
/// output buffers must cover their lengths and not overlap. The caller must
/// obey the ownership, callback lifetime and no-free-race contract in HormuzRust.h.
pub unsafe extern "C" fn hormuz_ui_refresh(handle: *const Ui) -> bool {
    unsafe { ui(handle) }.is_some_and(|value| value.desktop.refresh())
}
#[no_mangle]
/// # Safety
/// Non-null handles must be live allocations of the declared type. Input and
/// output buffers must cover their lengths and not overlap. The caller must
/// obey the ownership, callback lifetime and no-free-race contract in HormuzRust.h.
pub unsafe extern "C" fn hormuz_ui_visibility(handle: *const Ui, visibility: u32) -> bool {
    let Some(handle) = (unsafe { ui(handle) }) else {
        return false;
    };
    let value = match visibility {
        0 => DashboardVisibility::Hidden,
        1 => DashboardVisibility::Summary,
        2 => DashboardVisibility::Detail,
        _ => return false,
    };
    handle.desktop.visibility(value);
    true
}
#[no_mangle]
/// # Safety
/// Non-null handles must be live allocations of the declared type. Input and
/// output buffers must cover their lengths and not overlap. The caller must
/// obey the ownership, callback lifetime and no-free-race contract in HormuzRust.h.
pub unsafe extern "C" fn hormuz_ui_lifecycle(handle: *const Ui, event: u32) -> bool {
    let Some(handle) = (unsafe { ui(handle) }) else {
        return false;
    };
    let event = match event {
        0 => LifecycleEvent::Sleep,
        1 => LifecycleEvent::Wake,
        2 => LifecycleEvent::NetworkChanged,
        3 => LifecycleEvent::SessionLocked,
        4 => LifecycleEvent::SessionUnlocked,
        5 => LifecycleEvent::Quit,
        _ => return false,
    };
    handle.desktop.lifecycle(event);
    true
}
#[no_mangle]
/// # Safety
/// Non-null handles must be live allocations of the declared type. Input and
/// output buffers must cover their lengths and not overlap. The caller must
/// obey the ownership, callback lifetime and no-free-race contract in HormuzRust.h.
pub unsafe extern "C" fn hormuz_ui_snapshot(handle: *const Ui) -> *mut Bytes {
    unsafe { ui(handle) }.map_or(std::ptr::null_mut(), |handle| bytes(view(handle)))
}
#[no_mangle]
/// # Safety
/// Non-null handles must be live allocations of the declared type. Input and
/// output buffers must cover their lengths and not overlap. The caller must
/// obey the ownership, callback lifetime and no-free-race contract in HormuzRust.h.
pub unsafe extern "C" fn hormuz_ui_subscribe(
    handle: *const Ui,
    callback: Option<Wake>,
    context: *mut c_void,
) -> *mut Subscription {
    let (Some(handle), Some(callback)) = (unsafe { ui(handle) }, callback) else {
        return std::ptr::null_mut();
    };
    let mut state = lock(&handle.signal.0);
    if state.closed || state.subscriber.is_some() {
        return std::ptr::null_mut();
    }
    state.next_id = state
        .next_id
        .checked_add(1)
        .expect("subscription identity exhausted");
    let id = state.next_id;
    state.subscriber = Some(Subscriber {
        id,
        callback,
        context,
    });
    state.pending = true;
    state.last = None;
    unsafe { callback(context) };
    Box::into_raw(Box::new(Subscription {
        signal: handle.signal.clone(),
        id,
    }))
}
#[no_mangle]
/// # Safety
/// Non-null handles must be live allocations of the declared type. Input and
/// output buffers must cover their lengths and not overlap. The caller must
/// obey the ownership, callback lifetime and no-free-race contract in HormuzRust.h.
pub unsafe extern "C" fn hormuz_ui_unsubscribe(subscription: *mut Subscription) {
    if !subscription.is_null() {
        drop(unsafe { Box::from_raw(subscription) });
    }
}
#[no_mangle]
/// # Safety
/// Non-null handles must be live allocations of the declared type. Input and
/// output buffers must cover their lengths and not overlap. The caller must
/// obey the ownership, callback lifetime and no-free-race contract in HormuzRust.h.
pub unsafe extern "C" fn hormuz_ui_take_change(handle: *const Ui) -> *mut Bytes {
    let Some(handle) = (unsafe { ui(handle) }) else {
        return std::ptr::null_mut();
    };
    {
        let mut state = lock(&handle.signal.0);
        if !state.pending || state.closed {
            return std::ptr::null_mut();
        }
        state.pending = false;
    }
    let value = view(handle);
    let mut state = lock(&handle.signal.0);
    if state.last.as_ref() == Some(&value) {
        return std::ptr::null_mut();
    }
    state.last = Some(value.clone());
    bytes(value)
}

/// Shell-owned launch and settings actions get a fresh verified display profile,
/// never a credential. Execution/persistence remain the shell's existing path.
#[no_mangle]
/// # Safety
/// Non-null handles must be live allocations of the declared type. Input and
/// output buffers must cover their lengths and not overlap. The caller must
/// obey the ownership, callback lifetime and no-free-race contract in HormuzRust.h.
pub unsafe extern "C" fn hormuz_ui_launch_profile(handle: *const Ui) -> *mut Bytes {
    let Some(handle) = (unsafe { ui(handle) }) else {
        return std::ptr::null_mut();
    };
    let view = handle.desktop.view();
    let Some(status) = view.connection else {
        return std::ptr::null_mut();
    };
    if view.phase.busy()
        || status.session_state() != Some(SessionState::Active)
        || status
            .expires_at_epoch_seconds()
            .is_none_or(|value| value <= hormuz_client_session::Clock::now(&SystemClock::default()))
        || view.snapshot.reading().status() != hormuz_client_core::ReadingStatus::Current
    {
        return std::ptr::null_mut();
    }
    status.profile().map_or(std::ptr::null_mut(), |profile| {
        bytes(serde_json::to_vec(profile).unwrap())
    })
}
#[no_mangle]
pub extern "C" fn hormuz_ui_context_setting(enabled: bool) -> *mut Bytes {
    bytes(
        ContextOptimizationPreference::new(enabled)
            .to_file_bytes()
            .to_vec(),
    )
}
#[no_mangle]
/// # Safety
/// Non-null handles must be live allocations of the declared type. Input and
/// output buffers must cover their lengths and not overlap. The caller must
/// obey the ownership, callback lifetime and no-free-race contract in HormuzRust.h.
pub unsafe extern "C" fn hormuz_ui_bytes_length(value: *const Bytes) -> usize {
    unsafe { value.as_ref() }.map_or(0, |value| value.0.len())
}
#[no_mangle]
/// # Safety
/// Non-null handles must be live allocations of the declared type. Input and
/// output buffers must cover their lengths and not overlap. The caller must
/// obey the ownership, callback lifetime and no-free-race contract in HormuzRust.h.
pub unsafe extern "C" fn hormuz_ui_bytes_copy(
    value: *const Bytes,
    output: *mut u8,
    capacity: usize,
) -> bool {
    let Some(value) = (unsafe { value.as_ref() }) else {
        return false;
    };
    unsafe { copy(&value.0, output, capacity) }
}
unsafe fn copy(value: &[u8], output: *mut u8, capacity: usize) -> bool {
    if capacity < value.len() || (!value.is_empty() && output.is_null()) {
        return false;
    }
    if !value.is_empty() {
        unsafe { std::ptr::copy_nonoverlapping(value.as_ptr(), output, value.len()) };
    }
    true
}
#[no_mangle]
/// # Safety
/// Non-null handles must be live allocations of the declared type. Input and
/// output buffers must cover their lengths and not overlap. The caller must
/// obey the ownership, callback lifetime and no-free-race contract in HormuzRust.h.
pub unsafe extern "C" fn hormuz_ui_bytes_free(value: *mut Bytes) {
    if !value.is_null() {
        drop(unsafe { Box::from_raw(value) });
    }
}
#[no_mangle]
/// # Safety
/// Non-null handles must be live allocations of the declared type. Input and
/// output buffers must cover their lengths and not overlap. The caller must
/// obey the ownership, callback lifetime and no-free-race contract in HormuzRust.h.
pub unsafe extern "C" fn hormuz_ui_secret_new(data: *const u8, length: usize) -> *mut Secret {
    if length == 0 || length > 32_767 {
        return std::ptr::null_mut();
    }
    unsafe { input(data, length) }
        .and_then(|bytes| SecretRecord::new(bytes.to_vec()).ok())
        .map_or(std::ptr::null_mut(), |record| {
            Box::into_raw(Box::new(Secret(record)))
        })
}
#[no_mangle]
/// # Safety
/// Non-null handles must be live allocations of the declared type. Input and
/// output buffers must cover their lengths and not overlap. The caller must
/// obey the ownership, callback lifetime and no-free-race contract in HormuzRust.h.
pub unsafe extern "C" fn hormuz_ui_secret_length(value: *const Secret) -> usize {
    unsafe { value.as_ref() }.map_or(0, |value| value.0.expose().len())
}
#[no_mangle]
/// # Safety
/// Non-null handles must be live allocations of the declared type. Input and
/// output buffers must cover their lengths and not overlap. The caller must
/// obey the ownership, callback lifetime and no-free-race contract in HormuzRust.h.
pub unsafe extern "C" fn hormuz_ui_secret_copy(
    value: *const Secret,
    output: *mut u8,
    capacity: usize,
) -> bool {
    let Some(value) = (unsafe { value.as_ref() }) else {
        return false;
    };
    unsafe { copy(value.0.expose(), output, capacity) }
}
#[no_mangle]
/// # Safety
/// Non-null handles must be live allocations of the declared type. Input and
/// output buffers must cover their lengths and not overlap. The caller must
/// obey the ownership, callback lifetime and no-free-race contract in HormuzRust.h.
pub unsafe extern "C" fn hormuz_ui_secret_free(value: *mut Secret) {
    if !value.is_null() {
        drop(unsafe { Box::from_raw(value) });
    }
}

#[cfg(test)]
mod tests;
