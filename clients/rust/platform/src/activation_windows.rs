//! One content-free reopen command. No credentials, paths or general RPC.
use super::{validate, validate_security, ApplicationInstance, PrivateDirectory, Root, UserSid};
use crate::{PlatformError, Result};
use std::os::windows::io::{AsRawHandle, FromRawHandle, OwnedHandle};
use std::ptr::{null, null_mut};
use std::sync::Arc;
use std::thread::{self, JoinHandle};
use std::time::{Duration, Instant};
use windows_sys::Win32::Foundation::*;
use windows_sys::Win32::Security::SECURITY_ATTRIBUTES;
use windows_sys::Win32::Storage::FileSystem::*;
use windows_sys::Win32::System::RemoteDesktop::ProcessIdToSessionId;
use windows_sys::Win32::System::{Pipes::*, Threading::*, IO::*};
use windows_sys::Win32::UI::WindowsAndMessaging::AllowSetForegroundWindow;

const REOPEN: [u8; 8] = *b"HMZ\x01OPEN";
const ACK: [u8; 8] = *b"HMZ\x01OKAY";
const DONE: [u8; 8] = *b"HMZ\x01DONE";
const IO_TIMEOUT_MS: u32 = 1000;
const CONNECT_BUDGET: Duration = Duration::from_secs(3);

fn owned(handle: HANDLE) -> Result<OwnedHandle> {
    if handle.is_null() || handle == INVALID_HANDLE_VALUE {
        Err(PlatformError::Unavailable)
    } else {
        // SAFETY: the caller transfers one freshly created handle.
        Ok(unsafe { OwnedHandle::from_raw_handle(handle) })
    }
}

fn event() -> Result<OwnedHandle> {
    owned(unsafe { CreateEventW(null(), 1, 0, null()) })
}

fn session(pid: u32) -> Result<u32> {
    let mut id = 0;
    if unsafe { ProcessIdToSessionId(pid, &mut id) } == 0 {
        return Err(PlatformError::Unavailable);
    }
    Ok(id)
}

fn endpoint(root: &Root) -> Result<Vec<u16>> {
    validate(&root.file, true, &root.user)?;
    let mut identity = BY_HANDLE_FILE_INFORMATION::default();
    if unsafe { GetFileInformationByHandle(root.file.as_raw_handle(), &mut identity) } == 0 {
        return Err(PlatformError::Unavailable);
    }
    // The retained private directory's kernel identity is stable through path
    // aliases. No private path or credential enters the public pipe name.
    Ok(format!(
        "\\\\.\\pipe\\Hormuz.Native.Activation.v1.{}.{:x}.{:x}.{:x}.{}",
        session(unsafe { GetCurrentProcessId() })?,
        identity.dwVolumeSerialNumber,
        identity.nFileIndexHigh,
        identity.nFileIndexLow,
        root.user.text,
    )
    .encode_utf16()
    .chain(Some(0))
    .collect())
}

fn peer(pipe: HANDLE, server: bool, user: &UserSid) -> Result<u32> {
    let mut pid = 0;
    let okay = unsafe {
        if server {
            GetNamedPipeClientProcessId(pipe, &mut pid)
        } else {
            GetNamedPipeServerProcessId(pipe, &mut pid)
        }
    };
    if okay == 0 || pid == 0 || session(pid)? != session(unsafe { GetCurrentProcessId() })? {
        return Err(PlatformError::UnsafeStorage);
    }
    let process = owned(unsafe { OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, 0, pid) })?;
    let actual = UserSid::of_process(process.as_raw_handle())?;
    if actual.text != user.text {
        return Err(PlatformError::UnsafeStorage);
    }
    Ok(pid)
}

fn complete(
    pipe: HANDLE,
    pending: &mut OVERLAPPED,
    started: i32,
    stop: HANDLE,
    timeout: u32,
) -> Result<u32> {
    let error = if started == 0 {
        unsafe { GetLastError() }
    } else {
        ERROR_SUCCESS
    };
    if started == 0 && error != ERROR_IO_PENDING {
        return Err(PlatformError::Unavailable);
    }
    if started == 0 {
        let handles = [stop, pending.hEvent];
        if unsafe { WaitForMultipleObjects(2, handles.as_ptr(), 0, timeout) } != WAIT_OBJECT_0 + 1 {
            // Cancellation is not completion. Drain before releasing either
            // OVERLAPPED or its buffer, including when shutdown wins the race.
            unsafe {
                CancelIoEx(pipe, pending);
                let mut ignored = 0;
                GetOverlappedResult(pipe, pending, &mut ignored, 1);
            }
            return Err(PlatformError::Unavailable);
        }
    }
    let mut transferred = 0;
    if unsafe { GetOverlappedResult(pipe, pending, &mut transferred, 0) } == 0 {
        return Err(PlatformError::Unavailable);
    }
    Ok(transferred)
}

fn read(pipe: HANDLE, buffer: &mut [u8], stop: HANDLE) -> Result<usize> {
    let ready = event()?;
    let mut pending = OVERLAPPED {
        hEvent: ready.as_raw_handle(),
        ..Default::default()
    };
    // SAFETY: both stable stack storage and the buffer remain alive until the
    // operation completes or cancellation has been drained by complete().
    let started = unsafe {
        ReadFile(
            pipe,
            buffer.as_mut_ptr(),
            buffer.len() as u32,
            null_mut(),
            &mut pending,
        )
    };
    complete(pipe, &mut pending, started, stop, IO_TIMEOUT_MS).map(|n| n as usize)
}

fn write(pipe: HANDLE, buffer: &[u8], stop: HANDLE) -> Result<()> {
    let ready = event()?;
    let mut pending = OVERLAPPED {
        hEvent: ready.as_raw_handle(),
        ..Default::default()
    };
    let started = unsafe {
        WriteFile(
            pipe,
            buffer.as_ptr(),
            buffer.len() as u32,
            null_mut(),
            &mut pending,
        )
    };
    if complete(pipe, &mut pending, started, stop, IO_TIMEOUT_MS)? as usize != buffer.len() {
        return Err(PlatformError::Unavailable);
    }
    Ok(())
}

fn create_pipe(root: &Root) -> Result<OwnedHandle> {
    let name = endpoint(root)?;
    let descriptor = root.user.descriptor()?;
    let security = SECURITY_ATTRIBUTES {
        nLength: std::mem::size_of::<SECURITY_ATTRIBUTES>() as u32,
        lpSecurityDescriptor: descriptor.0,
        bInheritHandle: 0,
    };
    let pipe = owned(unsafe {
        CreateNamedPipeW(
            name.as_ptr(),
            PIPE_ACCESS_DUPLEX | FILE_FLAG_OVERLAPPED | FILE_FLAG_FIRST_PIPE_INSTANCE,
            PIPE_TYPE_MESSAGE | PIPE_READMODE_MESSAGE | PIPE_WAIT | PIPE_REJECT_REMOTE_CLIENTS,
            1,
            8,
            8,
            IO_TIMEOUT_MS,
            &security,
        )
    })?;
    validate_security(pipe.as_raw_handle(), &root.user)?;
    Ok(pipe)
}

/// Owns the app lease until its pipe worker has stopped. Drop cancels a pending
/// accept/read/write, joins the worker, then releases the kernel ownership lock.
pub struct InstanceListener {
    stop: Arc<OwnedHandle>,
    worker: Option<JoinHandle<()>>,
    _owner: ApplicationInstance,
}

impl ApplicationInstance {
    /// The callback must be nonblocking (for example, post one UI notification).
    /// True acknowledges admission to the UI queue, not completed rendering.
    pub fn listen_for_reopen(
        self,
        notify: impl Fn() -> bool + Send + 'static,
    ) -> Result<InstanceListener> {
        let pipe = create_pipe(&self._root)?;
        let stop = Arc::new(event()?);
        let worker_stop = stop.clone();
        let root = self._root.clone();
        let worker = thread::Builder::new()
            .name("hormuz-activation".into())
            .spawn(move || {
                let pipe = pipe.as_raw_handle();
                let stop = worker_stop.as_raw_handle();
                while unsafe { WaitForSingleObject(stop, 0) } != WAIT_OBJECT_0 {
                    let Ok(ready) = event() else { break };
                    let mut pending = OVERLAPPED {
                        hEvent: ready.as_raw_handle(),
                        ..Default::default()
                    };
                    let started = unsafe { ConnectNamedPipe(pipe, &mut pending) };
                    let connected = (started == 0
                        && unsafe { GetLastError() } == ERROR_PIPE_CONNECTED)
                        || complete(pipe, &mut pending, started, stop, INFINITE).is_ok();
                    if connected && peer(pipe, true, &root.user).is_ok() {
                        let mut request = [0_u8; 8];
                        if read(pipe, &mut request, stop) == Ok(8)
                            && request == REOPEN
                            && unsafe { WaitForSingleObject(stop, 0) } != WAIT_OBJECT_0
                            && notify()
                            && write(pipe, &ACK, stop).is_ok()
                        {
                            // Do not disconnect and discard the buffered ACK
                            // until the client has consumed it. This final read
                            // has the same deadline as any untrusted peer I/O.
                            let mut done = [0_u8; 8];
                            let _ = read(pipe, &mut done, stop);
                        }
                    }
                    unsafe {
                        DisconnectNamedPipe(pipe);
                    }
                }
            })
            .map_err(|_| PlatformError::Unavailable)?;
        Ok(InstanceListener {
            stop,
            worker: Some(worker),
            _owner: self,
        })
    }
}

impl Drop for InstanceListener {
    fn drop(&mut self) {
        unsafe {
            SetEvent(self.stop.as_raw_handle());
        }
        if let Some(worker) = self.worker.take() {
            let _ = worker.join();
        }
    }
}

impl PrivateDirectory {
    /// Bounded worker operation. Only an exact v1 REOPEN frame is sent after
    /// checking the server's user, desktop session and protected private ACL.
    pub fn request_reopen(&self) -> Result<()> {
        let pipe = connect(&self.root)?;
        let pid = peer(pipe.as_raw_handle(), false, &self.root.user)?;
        validate_security(pipe.as_raw_handle(), &self.root.user)?;
        // The foreground-launching process may grant its existing foreground
        // right to the verified resident process. Failure cannot grant more.
        unsafe {
            AllowSetForegroundWindow(pid);
        }
        let stop = event()?;
        write(pipe.as_raw_handle(), &REOPEN, stop.as_raw_handle())?;
        let mut ack = [0_u8; 8];
        if read(pipe.as_raw_handle(), &mut ack, stop.as_raw_handle())? != 8 || ack != ACK {
            return Err(PlatformError::Unavailable);
        }
        write(pipe.as_raw_handle(), &DONE, stop.as_raw_handle())?;
        Ok(())
    }
}

fn connect(root: &Root) -> Result<OwnedHandle> {
    let name = endpoint(root)?;
    let deadline = Instant::now() + CONNECT_BUDGET;
    loop {
        let raw = unsafe {
            CreateFileW(
                name.as_ptr(),
                GENERIC_READ | GENERIC_WRITE | READ_CONTROL,
                0,
                null(),
                OPEN_EXISTING,
                FILE_FLAG_OVERLAPPED | SECURITY_SQOS_PRESENT | SECURITY_IDENTIFICATION,
                null_mut(),
            )
        };
        if raw != INVALID_HANDLE_VALUE {
            let pipe = owned(raw)?;
            let mode = PIPE_READMODE_MESSAGE;
            if unsafe { SetNamedPipeHandleState(pipe.as_raw_handle(), &mode, null(), null()) } == 0
            {
                return Err(PlatformError::Unavailable);
            }
            return Ok(pipe);
        }
        let error = unsafe { GetLastError() };
        if !matches!(error, ERROR_PIPE_BUSY | ERROR_FILE_NOT_FOUND) || Instant::now() >= deadline {
            return Err(PlatformError::Unavailable);
        }
        // WaitNamedPipe returns immediately if the primary is still creating
        // its endpoint. Back off without spinning; startup has one total budget.
        unsafe {
            WaitNamedPipeW(name.as_ptr(), 25);
        }
        thread::sleep(Duration::from_millis(10));
    }
}

#[cfg(test)]
#[path = "activation_windows_tests.rs"]
mod tests;
