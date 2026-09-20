use super::{validate_name, MAX_PRIVATE_FILE_BYTES};
use crate::{PlatformError, Result};
use std::ffi::CString;
use std::fs::File;
use std::io::{Read, Write};
use std::os::fd::{AsRawFd, FromRawFd};
use std::os::unix::ffi::OsStrExt;
use std::os::unix::fs::MetadataExt;
use std::path::Path;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Arc;

extern "C" {
    fn acl_get_fd_np(fd: i32, kind: u32) -> *mut std::ffi::c_void;
    fn acl_get_entry(
        acl: *mut std::ffi::c_void,
        entry: i32,
        output: *mut *mut std::ffi::c_void,
    ) -> i32;
    fn acl_valid(acl: *mut std::ffi::c_void) -> i32;
    fn acl_free(acl: *mut std::ffi::c_void) -> i32;
}

#[derive(Clone)]
pub struct PrivateDirectory {
    root: Arc<File>,
}
pub struct PrivateTransaction {
    root: Arc<File>,
    _lock: File,
}

impl Drop for PrivateTransaction {
    fn drop(&mut self) {
        // A concurrent fork must not extend this transaction's lifetime.
        let _ = self._lock.unlock();
    }
}

/// Exclusive application ownership, independent of connection refresh. Keep it
/// alive until all owned workers and helpers have stopped. Never delete its
/// sentinel: the kernel lock, not file existence or a PID, determines ownership.
pub struct ApplicationInstance {
    _root: Arc<File>,
    _lock: File,
}

impl Drop for ApplicationInstance {
    fn drop(&mut self) {
        // A concurrent fork may temporarily inherit the open file description
        // before CLOEXEC closes it. Explicitly release our lease, rather than
        // waiting for every inherited descriptor to close. The guard is never
        // cloned and its owner has already drained its workers/helpers.
        let _ = self._lock.unlock();
    }
}

fn validate(file: &File, directory: bool) -> Result<()> {
    let meta = file.metadata().map_err(|_| PlatformError::UnsafeStorage)?;
    if meta.uid() != unsafe { libc::getuid() }
        || meta.mode() & 0o077 != 0
        || if directory {
            !meta.is_dir()
        } else {
            !meta.is_file() || meta.nlink() != 1 || meta.len() > MAX_PRIVATE_FILE_BYTES as u64
        }
    {
        return Err(PlatformError::UnsafeStorage);
    }
    // POSIX mode bits alone do not rule out macOS extended ACL grants.
    // Reject every extended entry, including inherited entries, instead of
    // silently changing permissions on an existing user object.
    let acl = unsafe { acl_get_fd_np(file.as_raw_fd(), 0x100) };
    if acl.is_null() {
        // Darwin reports ENOENT when this live descriptor has no extended ACL.
        return if std::io::Error::last_os_error().raw_os_error() == Some(libc::ENOENT) {
            Ok(())
        } else {
            Err(PlatformError::UnsafeStorage)
        };
    }
    let mut entry = std::ptr::null_mut();
    let valid = unsafe { acl_valid(acl) } == 0;
    let first = unsafe { acl_get_entry(acl, 0, &mut entry) };
    let no_entries =
        first == -1 && std::io::Error::last_os_error().raw_os_error() == Some(libc::EINVAL);
    unsafe {
        acl_free(acl);
    }
    if !valid || !no_entries {
        return Err(PlatformError::UnsafeStorage);
    }
    Ok(())
}

fn open_at(root: &File, name: &CString, flags: i32) -> Result<Option<File>> {
    // SAFETY: live owned directory descriptor and NUL-terminated single name;
    // any successful descriptor is immediately transferred to File ownership.
    let fd = unsafe {
        libc::openat(
            root.as_raw_fd(),
            name.as_ptr(),
            flags | libc::O_NOFOLLOW | libc::O_CLOEXEC | libc::O_NONBLOCK,
            0o600,
        )
    };
    if fd < 0 {
        return if std::io::Error::last_os_error().raw_os_error() == Some(libc::ENOENT) {
            Ok(None)
        } else {
            Err(PlatformError::UnsafeStorage)
        };
    }
    let file = unsafe { File::from_raw_fd(fd) };
    validate(&file, false)?;
    Ok(Some(file))
}

fn read_at(root: &File, name: &CString) -> Result<Option<Vec<u8>>> {
    validate(root, true)?;
    let Some(mut file) = open_at(root, name, libc::O_RDONLY)? else {
        return Ok(None);
    };
    let mut bytes = Vec::new();
    (&mut file)
        .take((MAX_PRIVATE_FILE_BYTES + 1) as u64)
        .read_to_end(&mut bytes)
        .map_err(|_| PlatformError::Unavailable)?;
    validate(&file, false)?;
    if bytes.len() > MAX_PRIVATE_FILE_BYTES {
        return Err(PlatformError::TooLarge);
    }
    Ok(Some(bytes))
}

impl PrivateDirectory {
    /// Creates only the final directory, with a private mode from the start.
    /// Its existing parent must be supplied by the native shell.
    pub fn open(path: &Path) -> Result<Self> {
        if !path.is_absolute() {
            return Err(PlatformError::UnsafeStorage);
        }
        let path: std::path::PathBuf = path.components().collect();
        let path =
            CString::new(path.as_os_str().as_bytes()).map_err(|_| PlatformError::UnsafeStorage)?;
        let made = unsafe { libc::mkdir(path.as_ptr(), 0o700) };
        if made != 0 && std::io::Error::last_os_error().raw_os_error() != Some(libc::EEXIST) {
            return Err(PlatformError::Unavailable);
        }
        let fd = unsafe {
            libc::open(
                path.as_ptr(),
                libc::O_RDONLY | libc::O_DIRECTORY | libc::O_NOFOLLOW | libc::O_CLOEXEC,
            )
        };
        if fd < 0 {
            return Err(PlatformError::UnsafeStorage);
        }
        let root = unsafe { File::from_raw_fd(fd) };
        validate(&root, true)?;
        Ok(Self {
            root: Arc::new(root),
        })
    }

    /// Nonblocking interprocess coordination. The caller owns retry/deadline
    /// policy; a retained guard keeps the kernel lock across asynchronous work.
    pub fn try_lock(&self) -> Result<PrivateTransaction> {
        Ok(PrivateTransaction {
            root: self.root.clone(),
            _lock: self.lock_file("connection.lock", false, || {})?,
        })
    }

    /// The native shell must use the same root for every startup entry point.
    /// Busy means activate/reopen the existing shell; it does not permit a
    /// second helper owner. This method sends no interprocess messages.
    pub fn try_claim_instance(&self) -> Result<ApplicationInstance> {
        self.claim_instance_with_hook(|| {})
    }

    pub(crate) fn claim_instance_with_hook(
        &self,
        after_lock: impl FnOnce(),
    ) -> Result<ApplicationInstance> {
        let file = self.lock_file("instance.lock", true, after_lock)?;
        if file
            .metadata()
            .map_err(|_| PlatformError::Unavailable)?
            .len()
            != 0
        {
            return Err(PlatformError::UnsafeStorage);
        }
        Ok(ApplicationInstance {
            _root: self.root.clone(),
            _lock: file,
        })
    }

    fn lock_file(&self, name: &str, empty: bool, after_lock: impl FnOnce()) -> Result<File> {
        validate(&self.root, true)?;
        let name = CString::new(name).unwrap();
        // Concurrent first creation has been observed to return ENOENT on
        // macOS even through a retained directory descriptor. Make one bounded
        // attempt to open the winner's existing sentinel. Never infer Busy,
        // retry an unsafe object, or spin on a held kernel lock.
        let file = match open_at(&self.root, &name, libc::O_RDWR | libc::O_CREAT)? {
            Some(file) => file,
            None => open_at(&self.root, &name, libc::O_RDWR)?.ok_or(PlatformError::Unavailable)?,
        };
        // Reject malformed sentinels before contention can mask their shape.
        // Instance acquisition checks again after taking the kernel lock.
        if empty
            && file
                .metadata()
                .map_err(|_| PlatformError::Unavailable)?
                .len()
                != 0
        {
            return Err(PlatformError::UnsafeStorage);
        }
        file.try_lock().map_err(|error| match error {
            std::fs::TryLockError::WouldBlock => PlatformError::Busy,
            std::fs::TryLockError::Error(_) => PlatformError::Unavailable,
        })?;
        after_lock();
        let current =
            open_at(&self.root, &name, libc::O_RDONLY)?.ok_or(PlatformError::UnsafeStorage)?;
        let a = file.metadata().map_err(|_| PlatformError::Unavailable)?;
        let b = current.metadata().map_err(|_| PlatformError::Unavailable)?;
        if a.ino() != b.ino() || a.dev() != b.dev() {
            return Err(PlatformError::UnsafeStorage);
        }
        Ok(file)
    }
}

struct Temporary {
    root: Arc<File>,
    name: CString,
    remove: bool,
}
impl Drop for Temporary {
    fn drop(&mut self) {
        if self.remove {
            unsafe {
                libc::unlinkat(self.root.as_raw_fd(), self.name.as_ptr(), 0);
            }
        }
    }
}

impl PrivateTransaction {
    pub fn read(&self, name: &str) -> Result<Option<Vec<u8>>> {
        validate_name(name)?;
        read_at(&self.root, &CString::new(name).unwrap())
    }

    /// Atomic compare-and-replace for non-secret configuration only. File mode
    /// is always 0600; executable installation is a separate launch boundary.
    pub fn write(&self, name: &str, bytes: &[u8], expected: Option<&[u8]>) -> Result<()> {
        self.write_with_hook(name, bytes, expected, || Ok(()))
    }

    pub(crate) fn write_with_hook(
        &self,
        name: &str,
        bytes: &[u8],
        expected: Option<&[u8]>,
        before_exchange: impl FnOnce() -> Result<()>,
    ) -> Result<()> {
        validate_name(name)?;
        validate(&self.root, true)?;
        if bytes.len() > MAX_PRIVATE_FILE_BYTES {
            return Err(PlatformError::TooLarge);
        }
        if self.read(name)?.as_deref() != expected {
            return Err(PlatformError::Changed);
        }
        static SEQUENCE: AtomicU64 = AtomicU64::new(0);
        let temporary_name = CString::new(format!(
            ".write-{}-{}",
            std::process::id(),
            SEQUENCE.fetch_add(1, Ordering::Relaxed)
        ))
        .unwrap();
        let mut file = open_at(
            &self.root,
            &temporary_name,
            libc::O_WRONLY | libc::O_CREAT | libc::O_EXCL,
        )?
        .ok_or(PlatformError::Unavailable)?;
        let mut temporary = Temporary {
            root: self.root.clone(),
            name: temporary_name,
            remove: true,
        };
        file.write_all(bytes)
            .and_then(|_| file.sync_all())
            .map_err(|_| PlatformError::Unavailable)?;
        before_exchange()?;
        validate(&self.root, true)?;
        let target = CString::new(name).unwrap();
        let flags = if expected.is_none() {
            libc::RENAME_EXCL
        } else {
            libc::RENAME_SWAP
        };
        let renamed = unsafe {
            libc::renameatx_np(
                self.root.as_raw_fd(),
                temporary.name.as_ptr(),
                self.root.as_raw_fd(),
                target.as_ptr(),
                flags,
            )
        };
        if renamed != 0 {
            return Err(match std::io::Error::last_os_error().raw_os_error() {
                Some(libc::EEXIST | libc::ENOENT) => PlatformError::Changed,
                _ => PlatformError::Unavailable,
            });
        }
        let displaced_matches = match read_at(&self.root, &temporary.name) {
            Ok(displaced) => displaced.as_deref() == expected,
            Err(_) => false,
        };
        if expected.is_some() && !displaced_matches {
            // Restore an external edit detected at the actual exchange. If a
            // second external edit prevents safe rollback, preserve both files.
            temporary.remove = false;
            if read_at(&self.root, &target)?.as_deref() == Some(bytes)
                && unsafe {
                    libc::renameatx_np(
                        self.root.as_raw_fd(),
                        temporary.name.as_ptr(),
                        self.root.as_raw_fd(),
                        target.as_ptr(),
                        libc::RENAME_SWAP,
                    )
                } == 0
            {
                temporary.remove = true;
                return Err(PlatformError::Changed);
            }
            return Err(PlatformError::Unavailable);
        }
        self.root.sync_all().map_err(|_| PlatformError::Unavailable)
    }
}
