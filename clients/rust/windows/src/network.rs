//! One coalesced OS network-change notification; no address or interface data.
use std::cell::Cell;
use std::sync::atomic::{AtomicBool, Ordering};
use windows_sys::Win32::{
    Foundation::*, NetworkManagement::IpHelper::*, Networking::WinSock::AF_UNSPEC,
    UI::WindowsAndMessaging::PostMessageW,
};

struct Context {
    window: usize,
    message: u32,
    pending: AtomicBool,
    closing: AtomicBool,
}
pub struct NetworkEvents {
    handle: HANDLE,
    stopped: Cell<bool>,
    context: Option<Box<Context>>,
}
impl NetworkEvents {
    pub fn register(window: HWND, message: u32) -> Option<Self> {
        let context = Box::new(Context {
            window: window as usize,
            message,
            pending: AtomicBool::new(false),
            closing: AtomicBool::new(false),
        });
        let mut handle = std::ptr::null_mut();
        if unsafe {
            NotifyIpInterfaceChange(
                AF_UNSPEC,
                Some(changed),
                (&*context as *const Context).cast(),
                false,
                &mut handle,
            )
        } != NO_ERROR
        {
            return None;
        }
        Some(Self {
            handle,
            stopped: Cell::new(false),
            context: Some(context),
        })
    }
    pub fn stop(&self) -> bool {
        if self.stopped.get() {
            return true;
        }
        if let Some(context) = &self.context {
            context.closing.store(true, Ordering::SeqCst);
        }
        let stopped = unsafe { CancelMibChangeNotify2(self.handle) } == NO_ERROR;
        self.stopped.set(stopped);
        stopped
    }
    pub fn take(&self) -> bool {
        self.context
            .as_ref()
            .is_some_and(|c| c.pending.swap(false, Ordering::SeqCst))
    }
}
unsafe extern "system" fn changed(
    context: *const std::ffi::c_void,
    _row: *const MIB_IPINTERFACE_ROW,
    _kind: MIB_NOTIFICATION_TYPE,
) {
    // The registration owns Context until cancellation joins every callback.
    // Never hold resources that the GUI needs to cancel this subscription.
    let context = unsafe { &*context.cast::<Context>() };
    if !context.closing.load(Ordering::SeqCst)
        && !context.pending.swap(true, Ordering::SeqCst)
        && unsafe { PostMessageW(context.window as HWND, context.message, 0, 0) } == 0
    {
        context.pending.store(false, Ordering::SeqCst);
    }
}
impl Drop for NetworkEvents {
    fn drop(&mut self) {
        let stopped = self.stop();
        if let Some(context) = self.context.take() {
            context.closing.store(true, Ordering::SeqCst);
            // Called on the GUI owner, never on the callback thread. A native
            // cancellation failure must not free storage a callback may retain.
            if !stopped {
                Box::leak(context);
            }
        }
    }
}
