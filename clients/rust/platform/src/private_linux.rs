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

const ACCESS_ACL: &[u8] = b"system.posix_acl_access\0";
const DEFAULT_ACL: &[u8] = b"system.posix_acl_default\0";
const STAGING_ATTEMPTS: usize = 16;

fn verified_process_uid(
    real_uid: libc::uid_t,
    effective_uid: libc::uid_t,
    saved_uid: libc::uid_t,
) -> Result<u32> {
    if effective_uid == 0 || real_uid != effective_uid || saved_uid != effective_uid {
        Err(PlatformError::UnsafeStorage)
    } else {
        Ok(effective_uid)
    }
}

fn current_process_uid() -> Result<u32> {
    let mut real_uid = 0;
    let mut effective_uid = 0;
    let mut saved_uid = 0;
    // SAFETY: valid pointers to owned uid_t values; getresuid only reads the
    // current process credentials and initializes every output on success.
    if unsafe { libc::getresuid(&mut real_uid, &mut effective_uid, &mut saved_uid) } != 0 {
        return Err(PlatformError::Unavailable);
    }
    verified_process_uid(real_uid, effective_uid, saved_uid)
}

fn acl_absent(file: &File, name: &[u8]) -> Result<()> {
    // SAFETY: the descriptor and NUL-terminated attribute name are live for
    // this call. A zero-sized query does not dereference the null value pointer.
    let result = unsafe {
        libc::fgetxattr(
            file.as_raw_fd(),
            name.as_ptr().cast(),
            std::ptr::null_mut(),
            0,
        )
    };
    if result >= 0 {
        return Err(PlatformError::UnsafeStorage);
    }
    if std::io::Error::last_os_error().raw_os_error() == Some(libc::ENODATA) {
        Ok(())
    } else {
        Err(PlatformError::UnsafeStorage)
    }
}

fn validate(file: &File, directory: bool, uid: u32) -> Result<()> {
    if current_process_uid()? != uid {
        return Err(PlatformError::UnsafeStorage);
    }
    let metadata = file.metadata().map_err(|_| PlatformError::UnsafeStorage)?;
    let expected_mode = if directory { 0o700 } else { 0o600 };
    if metadata.uid() != uid
        || metadata.mode() & 0o7777 != expected_mode
        || if directory {
            !metadata.is_dir()
        } else {
            !metadata.is_file()
                || metadata.nlink() != 1
                || metadata.len() > MAX_PRIVATE_FILE_BYTES as u64
        }
    {
        return Err(PlatformError::UnsafeStorage);
    }
    acl_absent(file, ACCESS_ACL)?;
    if directory {
        acl_absent(file, DEFAULT_ACL)?;
    }
    Ok(())
}

fn open_error(error: i32) -> PlatformError {
    match error {
        libc::EACCES
        | libc::EISDIR
        | libc::ELOOP
        | libc::ENODEV
        | libc::ENOEXEC
        | libc::ENOTDIR
        | libc::ENXIO
        | libc::EPERM => PlatformError::UnsafeStorage,
        _ => PlatformError::Unavailable,
    }
}

fn rename_error(error: i32, no_replace: bool, staged_source_matches: bool) -> PlatformError {
    match (error, no_replace, staged_source_matches) {
        (libc::EEXIST, true, _) | (libc::ENOENT, false, true) => PlatformError::Changed,
        _ => PlatformError::Unavailable,
    }
}

struct Root {
    file: File,
    uid: u32,
    sequence: AtomicU64,
}

fn open_existing_at(root: &Root, name: &CString, flags: i32) -> Result<Option<File>> {
    validate(&root.file, true, root.uid)?;
    // SAFETY: root is a retained directory descriptor, name is one
    // NUL-terminated component, and a successful descriptor is transferred to
    // File immediately.
    let descriptor = unsafe {
        libc::openat(
            root.file.as_raw_fd(),
            name.as_ptr(),
            flags | libc::O_NOFOLLOW | libc::O_CLOEXEC | libc::O_NONBLOCK,
        )
    };
    if descriptor < 0 {
        let error = std::io::Error::last_os_error()
            .raw_os_error()
            .unwrap_or(libc::EIO);
        return if error == libc::ENOENT {
            Ok(None)
        } else {
            Err(open_error(error))
        };
    }
    // SAFETY: descriptor is newly owned after a successful openat.
    let file = unsafe { File::from_raw_fd(descriptor) };
    validate(&file, false, root.uid)?;
    Ok(Some(file))
}

/// Returns None only when the single destination name already exists.
fn create_at(root: &Root, name: &CString, flags: i32) -> Result<Option<File>> {
    validate(&root.file, true, root.uid)?;
    // SAFETY: root and name have the same validity guarantees as open_existing_at.
    let descriptor = unsafe {
        libc::openat(
            root.file.as_raw_fd(),
            name.as_ptr(),
            flags
                | libc::O_CREAT
                | libc::O_EXCL
                | libc::O_NOFOLLOW
                | libc::O_CLOEXEC
                | libc::O_NONBLOCK,
            0o600,
        )
    };
    if descriptor < 0 {
        let error = std::io::Error::last_os_error()
            .raw_os_error()
            .unwrap_or(libc::EIO);
        return if error == libc::EEXIST {
            Ok(None)
        } else {
            Err(open_error(error))
        };
    }
    // SAFETY: descriptor is newly owned after a successful openat.
    let file = unsafe { File::from_raw_fd(descriptor) };
    // The process umask may remove owner bits. Normalize only the object this
    // call created; existing objects are never repaired.
    if unsafe { libc::fchmod(file.as_raw_fd(), 0o600) } != 0 {
        return Err(PlatformError::Unavailable);
    }
    validate(&file, false, root.uid)?;
    Ok(Some(file))
}

struct Snapshot {
    bytes: Vec<u8>,
    identity: FileIdentity,
}

fn read_snapshot_at(root: &Root, name: &CString) -> Result<Option<Snapshot>> {
    let Some(mut file) = open_existing_at(root, name, libc::O_RDONLY)? else {
        return Ok(None);
    };
    let identity = file_identity(&file)?;
    let mut bytes = Vec::new();
    (&mut file)
        .take((MAX_PRIVATE_FILE_BYTES + 1) as u64)
        .read_to_end(&mut bytes)
        .map_err(|_| PlatformError::Unavailable)?;
    validate(&file, false, root.uid)?;
    if bytes.len() > MAX_PRIVATE_FILE_BYTES {
        return Err(PlatformError::TooLarge);
    }
    if file_identity(&file)? != identity {
        return Err(PlatformError::UnsafeStorage);
    }
    Ok(Some(Snapshot { bytes, identity }))
}

fn read_at(root: &Root, name: &CString) -> Result<Option<Vec<u8>>> {
    Ok(read_snapshot_at(root, name)?.map(|snapshot| snapshot.bytes))
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
struct FileIdentity {
    device: u64,
    inode: u64,
}

fn file_identity(file: &File) -> Result<FileIdentity> {
    let metadata = file.metadata().map_err(|_| PlatformError::Unavailable)?;
    Ok(FileIdentity {
        device: metadata.dev(),
        inode: metadata.ino(),
    })
}

/// Recovery may need to identify our staged inode after a raced permission or
/// ACL edit. Do not accept this as a public read: it intentionally checks only
/// native identity, owner, regular-file shape, link count, bound and bytes.
fn recovery_matches(root: &Root, name: &CString, identity: FileIdentity, bytes: &[u8]) -> bool {
    if validate(&root.file, true, root.uid).is_err() {
        return false;
    }
    // SAFETY: retained root and a NUL-terminated single component. Failure is
    // an ordinary non-match during conservative recovery.
    let descriptor = unsafe {
        libc::openat(
            root.file.as_raw_fd(),
            name.as_ptr(),
            libc::O_RDONLY | libc::O_NOFOLLOW | libc::O_CLOEXEC | libc::O_NONBLOCK,
        )
    };
    if descriptor < 0 {
        return false;
    }
    // SAFETY: descriptor is newly owned after a successful openat.
    let mut file = unsafe { File::from_raw_fd(descriptor) };
    let Ok(before) = file.metadata() else {
        return false;
    };
    if !before.is_file()
        || before.uid() != root.uid
        || before.nlink() != 1
        || before.len() != bytes.len() as u64
        || file_identity(&file) != Ok(identity)
    {
        return false;
    }
    let mut actual = Vec::new();
    if (&mut file)
        .take((MAX_PRIVATE_FILE_BYTES + 1) as u64)
        .read_to_end(&mut actual)
        .is_err()
        || actual != bytes
    {
        return false;
    }
    let Ok(after) = file.metadata() else {
        return false;
    };
    after.is_file()
        && after.uid() == root.uid
        && after.nlink() == 1
        && after.len() == bytes.len() as u64
        && file_identity(&file) == Ok(identity)
}

fn committed_matches(
    root: &Root,
    name: &CString,
    identity: FileIdentity,
    bytes: &[u8],
) -> Result<bool> {
    let Some(snapshot) = read_snapshot_at(root, name)? else {
        return Ok(false);
    };
    Ok(snapshot.identity == identity && snapshot.bytes == bytes)
}

fn pathname_matches(root: &Root, name: &CString, identity: FileIdentity) -> bool {
    if validate(&root.file, true, root.uid).is_err() {
        return false;
    }
    // SAFETY: retained directory descriptor and NUL-terminated single name.
    let descriptor = unsafe {
        libc::openat(
            root.file.as_raw_fd(),
            name.as_ptr(),
            libc::O_RDONLY | libc::O_NOFOLLOW | libc::O_CLOEXEC | libc::O_NONBLOCK,
        )
    };
    if descriptor < 0 {
        return false;
    }
    // SAFETY: descriptor is newly owned after a successful openat.
    let file = unsafe { File::from_raw_fd(descriptor) };
    file_identity(&file) == Ok(identity)
}

struct LockedFile {
    file: File,
    creator_pid: libc::pid_t,
}

impl Drop for LockedFile {
    fn drop(&mut self) {
        // flock leases follow the open file description across fork. Explicit
        // unlock prevents an inherited descriptor from extending this guard,
        // but an inherited child guard must not unlock the parent's live lease.
        if unsafe { libc::getpid() } == self.creator_pid {
            let _ = self.file.unlock();
        }
    }
}

#[derive(Clone)]
pub struct PrivateDirectory {
    root: Arc<Root>,
}

pub struct PrivateTransaction {
    root: Arc<Root>,
    _lock: LockedFile,
}

/// Exclusive application ownership, independent of connection refresh. Keep
/// it alive until all owned workers and helpers have stopped.
pub struct ApplicationInstance {
    _root: Arc<Root>,
    _lock: LockedFile,
}

impl PrivateDirectory {
    /// Creates only the final directory. The native shell supplies its already
    /// existing parent and every startup entry point must choose the same root.
    pub fn open(path: &Path) -> Result<Self> {
        if !path.is_absolute() {
            return Err(PlatformError::UnsafeStorage);
        }
        let uid = current_process_uid()?;
        let normalized: std::path::PathBuf = path.components().collect();
        let path = CString::new(normalized.as_os_str().as_bytes())
            .map_err(|_| PlatformError::UnsafeStorage)?;
        // SAFETY: path is an owned NUL-terminated absolute path.
        let made = unsafe { libc::mkdir(path.as_ptr(), 0o700) };
        let created = made == 0;
        if !created && std::io::Error::last_os_error().raw_os_error() != Some(libc::EEXIST) {
            return Err(PlatformError::Unavailable);
        }
        // SAFETY: path remains live and a successful descriptor is transferred
        // immediately to File ownership.
        let descriptor = unsafe {
            libc::open(
                path.as_ptr(),
                libc::O_RDONLY | libc::O_DIRECTORY | libc::O_NOFOLLOW | libc::O_CLOEXEC,
            )
        };
        if descriptor < 0 {
            let error = std::io::Error::last_os_error()
                .raw_os_error()
                .unwrap_or(libc::EIO);
            return Err(open_error(error));
        }
        // SAFETY: descriptor is newly owned after a successful open.
        let file = unsafe { File::from_raw_fd(descriptor) };
        if created && unsafe { libc::fchmod(file.as_raw_fd(), 0o700) } != 0 {
            return Err(PlatformError::Unavailable);
        }
        validate(&file, true, uid)?;
        Ok(Self {
            root: Arc::new(Root {
                file,
                uid,
                sequence: AtomicU64::new(0),
            }),
        })
    }

    /// Nonblocking interprocess refresh coordination. Retry and deadline policy
    /// remain with the session controller.
    pub fn try_lock(&self) -> Result<PrivateTransaction> {
        Ok(PrivateTransaction {
            root: self.root.clone(),
            _lock: self.lock_file("connection.lock", false, || {})?,
        })
    }

    /// Busy means the future native shell must activate/reopen the existing
    /// owner. This lease does not itself send an activation message.
    pub fn try_claim_instance(&self) -> Result<ApplicationInstance> {
        self.claim_instance_with_hook(|| {})
    }

    pub(crate) fn claim_instance_with_hook(
        &self,
        after_lock: impl FnOnce(),
    ) -> Result<ApplicationInstance> {
        let lock = self.lock_file("instance.lock", true, after_lock)?;
        Ok(ApplicationInstance {
            _root: self.root.clone(),
            _lock: lock,
        })
    }

    fn lock_file(&self, name: &str, empty: bool, after_lock: impl FnOnce()) -> Result<LockedFile> {
        validate(&self.root.file, true, self.root.uid)?;
        let name = CString::new(name).unwrap();
        let file = match create_at(&self.root, &name, libc::O_RDWR)? {
            Some(file) => file,
            None => open_existing_at(&self.root, &name, libc::O_RDWR)?
                .ok_or(PlatformError::Unavailable)?,
        };
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
        // Construct the explicit-unlock guard before any fallible post-lock work.
        let locked = LockedFile {
            file,
            creator_pid: unsafe { libc::getpid() },
        };
        after_lock();
        validate(&locked.file, false, self.root.uid)?;
        if empty
            && locked
                .file
                .metadata()
                .map_err(|_| PlatformError::Unavailable)?
                .len()
                != 0
        {
            return Err(PlatformError::UnsafeStorage);
        }
        let current = open_existing_at(&self.root, &name, libc::O_RDONLY)?
            .ok_or(PlatformError::UnsafeStorage)?;
        if file_identity(&locked.file)? != file_identity(&current)? {
            return Err(PlatformError::UnsafeStorage);
        }
        Ok(locked)
    }
}

struct Temporary {
    root: Arc<Root>,
    name: CString,
    identity: FileIdentity,
    remove: bool,
}

impl Drop for Temporary {
    fn drop(&mut self) {
        if self.remove && pathname_matches(&self.root, &self.name, self.identity) {
            // SAFETY: retained root and an owned NUL-terminated staging name.
            unsafe {
                libc::unlinkat(self.root.file.as_raw_fd(), self.name.as_ptr(), 0);
            }
        }
    }
}

impl Temporary {
    /// Best-effort cleanup after the containing directory has made the target
    /// durable. Linux has no unlink-by-inode operation, so the same-UID process
    /// boundary still permits a name swap between this check and unlinkat.
    fn remove_known(&mut self, identity: FileIdentity, bytes: &[u8]) -> Result<()> {
        self.remove = false;
        if !recovery_matches(&self.root, &self.name, identity, bytes) {
            return Err(PlatformError::Unavailable);
        }
        // SAFETY: retained root and an owned NUL-terminated staging name.
        if unsafe { libc::unlinkat(self.root.file.as_raw_fd(), self.name.as_ptr(), 0) } != 0 {
            return Err(PlatformError::Unavailable);
        }
        self.root
            .file
            .sync_all()
            .map_err(|_| PlatformError::Unavailable)
    }
}

impl PrivateTransaction {
    pub fn read(&self, name: &str) -> Result<Option<Vec<u8>>> {
        validate_name(name)?;
        read_at(&self.root, &CString::new(name).unwrap())
    }

    /// Atomic compare-and-replace for non-secret configuration only.
    pub fn write(&self, name: &str, bytes: &[u8], expected: Option<&[u8]>) -> Result<()> {
        self.write_with_hook(name, bytes, expected, || Ok(()))
    }

    fn create_temporary(&self) -> Result<(Temporary, File)> {
        for _ in 0..STAGING_ATTEMPTS {
            let sequence = self.root.sequence.fetch_add(1, Ordering::Relaxed);
            let name = CString::new(format!(".write-{}-{sequence}", std::process::id())).unwrap();
            if let Some(file) = create_at(&self.root, &name, libc::O_RDWR)? {
                let identity = file_identity(&file)?;
                return Ok((
                    Temporary {
                        root: self.root.clone(),
                        name,
                        identity,
                        remove: true,
                    },
                    file,
                ));
            }
        }
        Err(PlatformError::Unavailable)
    }

    pub(crate) fn write_with_hook(
        &self,
        name: &str,
        bytes: &[u8],
        expected: Option<&[u8]>,
        before_exchange: impl FnOnce() -> Result<()>,
    ) -> Result<()> {
        validate_name(name)?;
        validate(&self.root.file, true, self.root.uid)?;
        if bytes.len() > MAX_PRIVATE_FILE_BYTES {
            return Err(PlatformError::TooLarge);
        }
        if self.read(name)?.as_deref() != expected {
            return Err(PlatformError::Changed);
        }
        let (mut temporary, mut file) = self.create_temporary()?;
        file.write_all(bytes)
            .and_then(|_| file.sync_all())
            .map_err(|_| PlatformError::Unavailable)?;
        validate(&file, false, self.root.uid)?;
        let staged_identity = file_identity(&file)?;
        temporary.remove = false;
        let hook_result = before_exchange();
        if pathname_matches(&self.root, &temporary.name, staged_identity) {
            temporary.remove = true;
        }
        hook_result?;

        // A hook or concurrent process may have replaced the staging pathname.
        // Preserve ambiguous candidates instead of deleting an object we did not
        // verify as the descriptor we created.
        temporary.remove = false;
        validate(&self.root.file, true, self.root.uid)?;
        validate(&file, false, self.root.uid)?;
        let current_stage = open_existing_at(&self.root, &temporary.name, libc::O_RDONLY)?
            .ok_or(PlatformError::UnsafeStorage)?;
        if file_identity(&current_stage)? != staged_identity {
            return Err(PlatformError::UnsafeStorage);
        }
        temporary.remove = true;

        let target = CString::new(name).unwrap();
        let no_replace = expected.is_none();
        let flags = if no_replace {
            libc::RENAME_NOREPLACE
        } else {
            libc::RENAME_EXCHANGE
        };
        // SAFETY: both names are single components relative to the same retained
        // directory descriptor. Linux performs the selected operation atomically.
        let renamed = unsafe {
            libc::renameat2(
                self.root.file.as_raw_fd(),
                temporary.name.as_ptr(),
                self.root.file.as_raw_fd(),
                target.as_ptr(),
                flags,
            )
        };
        if renamed != 0 {
            let error = std::io::Error::last_os_error()
                .raw_os_error()
                .unwrap_or(libc::EIO);
            let staged_source_matches = !no_replace
                && error == libc::ENOENT
                && pathname_matches(&self.root, &temporary.name, staged_identity);
            return Err(rename_error(error, no_replace, staged_source_matches));
        }
        // An exchange leaves the displaced destination at the staging name.
        // Keep that recovery copy until validation and directory sync succeed.
        temporary.remove = false;

        // Public success requires the installed pathname and retained staged
        // descriptor to pass the full owner/mode/ACL/link validation again.
        let committed = validate(&file, false, self.root.uid)
            .and_then(|()| committed_matches(&self.root, &target, staged_identity, bytes));

        if let Some(expected) = expected {
            let displaced = read_snapshot_at(&self.root, &temporary.name);
            let displaced_matches = displaced.as_ref().is_ok_and(|snapshot| {
                snapshot
                    .as_ref()
                    .is_some_and(|value| value.bytes.as_slice() == expected)
            });
            let conflict = match (&committed, &displaced) {
                (Ok(true), Ok(Some(_))) if displaced_matches => None,
                (Err(error), _) => Some(*error),
                (_, Err(error)) => Some(*error),
                _ => Some(PlatformError::Changed),
            };
            if let Some(conflict) = conflict {
                // Restore the external edit only while the installed target is
                // still the exact staged inode with unchanged bytes and the
                // displaced name remains the captured inode with the captured
                // bytes. Otherwise both names are retained for recovery.
                let displaced_still_matches = displaced
                    .as_ref()
                    .ok()
                    .and_then(Option::as_ref)
                    .is_some_and(|snapshot| {
                        recovery_matches(
                            &self.root,
                            &temporary.name,
                            snapshot.identity,
                            &snapshot.bytes,
                        )
                    });
                if displaced_still_matches
                    && recovery_matches(&self.root, &target, staged_identity, bytes)
                    && unsafe {
                        libc::renameat2(
                            self.root.file.as_raw_fd(),
                            temporary.name.as_ptr(),
                            self.root.file.as_raw_fd(),
                            target.as_ptr(),
                            libc::RENAME_EXCHANGE,
                        )
                    } == 0
                {
                    let displaced = displaced
                        .as_ref()
                        .ok()
                        .and_then(Option::as_ref)
                        .expect("rollback precheck requires a displaced snapshot");
                    // Do not report a restored conflict unless both exchanged
                    // names still refer to the bound inodes and bytes.
                    if committed_matches(&self.root, &target, displaced.identity, &displaced.bytes)
                        != Ok(true)
                        || !recovery_matches(&self.root, &temporary.name, staged_identity, bytes)
                    {
                        return Err(PlatformError::Unavailable);
                    }
                    if self.root.file.sync_all().is_err() {
                        return Err(PlatformError::Unavailable);
                    }
                    temporary.remove_known(staged_identity, bytes)?;
                    return Err(conflict);
                }
                return Err(PlatformError::Unavailable);
            }
            let displaced_identity = displaced
                .unwrap()
                .expect("matched displaced snapshot must exist")
                .identity;
            if self.root.file.sync_all().is_err() {
                return Err(PlatformError::Unavailable);
            }
            temporary.remove_known(displaced_identity, expected)?;
            return Ok(());
        } else if committed != Ok(true) {
            return Err(committed.err().unwrap_or(PlatformError::Unavailable));
        }

        self.root
            .file
            .sync_all()
            .map_err(|_| PlatformError::Unavailable)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::os::unix::fs::{symlink, OpenOptionsExt, PermissionsExt};

    const INHERITED_LOCK_FD: &str = "HORMUZ_SYNTHETIC_INHERITED_LOCK_FD";
    const INHERITED_LOCK_PID: &str = "HORMUZ_SYNTHETIC_INHERITED_LOCK_PID";

    #[test]
    fn inherited_lock_drop_child() {
        let Some(descriptor) = std::env::var_os(INHERITED_LOCK_FD) else {
            return;
        };
        let descriptor = descriptor.to_string_lossy().parse().unwrap();
        let creator_pid = std::env::var(INHERITED_LOCK_PID).unwrap().parse().unwrap();
        // SAFETY: the parent passes one non-CLOEXEC duplicate owned by this
        // child process. LockedFile closes it after deliberately skipping the
        // creator-only explicit unlock.
        let file = unsafe { File::from_raw_fd(descriptor) };
        file.metadata().unwrap();
        drop(LockedFile { file, creator_pid });
    }

    #[test]
    fn inherited_child_drop_does_not_unlock_the_parent_lease() {
        let temporary = tempfile::tempdir().unwrap();
        let directory = PrivateDirectory::open(&temporary.path().join("private")).unwrap();
        let guard = directory.try_lock().unwrap();
        // F_DUPFD clears CLOEXEC and keeps the same open file description,
        // matching the lock-sharing property of a descriptor inherited at fork.
        let descriptor = unsafe { libc::fcntl(guard._lock.file.as_raw_fd(), libc::F_DUPFD, 200) };
        assert!(descriptor >= 200);
        let status = std::process::Command::new(std::env::current_exe().unwrap())
            .args([
                "--exact",
                "private::implementation::tests::inherited_lock_drop_child",
            ])
            .env(INHERITED_LOCK_FD, descriptor.to_string())
            .env(INHERITED_LOCK_PID, guard._lock.creator_pid.to_string())
            .status()
            .unwrap();
        // SAFETY: this is the parent's owned duplicate; the original guard
        // descriptor remains open for the contention probe below.
        assert_eq!(unsafe { libc::close(descriptor) }, 0);
        assert!(status.success());
        assert!(matches!(directory.try_lock(), Err(PlatformError::Busy)));
        drop(guard);
        directory.try_lock().unwrap();
    }

    fn acl_entry(bytes: &mut Vec<u8>, tag: u16, permissions: u16, id: u32) {
        bytes.extend_from_slice(&tag.to_le_bytes());
        bytes.extend_from_slice(&permissions.to_le_bytes());
        bytes.extend_from_slice(&id.to_le_bytes());
    }

    fn install_acl(file: &File, default: bool) {
        let mut bytes = 2_u32.to_le_bytes().to_vec();
        let undefined = u32::MAX;
        let named_uid = if unsafe { libc::geteuid() } == 65_534 {
            65_533
        } else {
            65_534
        };
        acl_entry(&mut bytes, 0x01, if default { 0o7 } else { 0o6 }, undefined);
        acl_entry(&mut bytes, 0x02, 0o4, named_uid);
        acl_entry(&mut bytes, 0x04, 0, undefined);
        // Keep an access ACL's effective group class at zero so mode bits remain
        // 0600; a default ACL can grant read to prove inherited entries reject.
        acl_entry(&mut bytes, 0x10, if default { 0o4 } else { 0 }, undefined);
        acl_entry(&mut bytes, 0x20, 0, undefined);
        let name = if default { DEFAULT_ACL } else { ACCESS_ACL };
        // SAFETY: valid live descriptor, NUL-terminated name and initialized ACL
        // wire bytes used only in this owned temporary test object.
        assert_eq!(
            unsafe {
                libc::fsetxattr(
                    file.as_raw_fd(),
                    name.as_ptr().cast(),
                    bytes.as_ptr().cast(),
                    bytes.len(),
                    0,
                )
            },
            0,
            "synthetic POSIX ACL setup failed: {}",
            std::io::Error::last_os_error()
        );
    }

    #[test]
    fn uid_boundary_rejects_root_and_changed_credentials() {
        assert_eq!(verified_process_uid(1000, 1000, 1000), Ok(1000));
        assert_eq!(
            verified_process_uid(0, 0, 0),
            Err(PlatformError::UnsafeStorage)
        );
        assert_eq!(
            verified_process_uid(1000, 1001, 1001),
            Err(PlatformError::UnsafeStorage)
        );
        assert_eq!(
            verified_process_uid(1000, 1000, 0),
            Err(PlatformError::UnsafeStorage)
        );
    }

    #[test]
    fn root_open_errors_distinguish_unsafe_paths_from_resource_failures() {
        assert_eq!(open_error(libc::ELOOP), PlatformError::UnsafeStorage);
        assert_eq!(open_error(libc::ENOTDIR), PlatformError::UnsafeStorage);
        assert_eq!(open_error(libc::EACCES), PlatformError::UnsafeStorage);
        assert_eq!(open_error(libc::EMFILE), PlatformError::Unavailable);
        assert_eq!(open_error(libc::ENFILE), PlatformError::Unavailable);
        assert_eq!(open_error(libc::ENOMEM), PlatformError::Unavailable);
        assert_eq!(open_error(libc::EINTR), PlatformError::Unavailable);
        assert_eq!(open_error(libc::EIO), PlatformError::Unavailable);
    }

    #[test]
    fn rename_errors_bind_changed_to_the_operation_and_staged_source() {
        assert_eq!(
            rename_error(libc::EEXIST, true, false),
            PlatformError::Changed
        );
        assert_eq!(
            rename_error(libc::EEXIST, false, true),
            PlatformError::Unavailable
        );
        assert_eq!(
            rename_error(libc::ENOENT, true, true),
            PlatformError::Unavailable
        );
        assert_eq!(
            rename_error(libc::ENOENT, false, true),
            PlatformError::Changed
        );
        assert_eq!(
            rename_error(libc::ENOENT, false, false),
            PlatformError::Unavailable
        );
        assert_eq!(
            rename_error(libc::EIO, false, true),
            PlatformError::Unavailable
        );
    }

    #[test]
    fn rejects_public_or_executable_modes_symlinks_and_posix_acls() {
        let temporary = tempfile::tempdir().unwrap();
        let path = temporary.path().join("private");
        let directory = PrivateDirectory::open(&path).unwrap();
        let guard = directory.try_lock().unwrap();
        guard.write("profile.json", b"original", None).unwrap();
        let profile = path.join("profile.json");

        std::fs::set_permissions(&profile, std::fs::Permissions::from_mode(0o644)).unwrap();
        assert_eq!(
            guard.read("profile.json"),
            Err(PlatformError::UnsafeStorage)
        );
        std::fs::set_permissions(&profile, std::fs::Permissions::from_mode(0o700)).unwrap();
        assert_eq!(
            guard.read("profile.json"),
            Err(PlatformError::UnsafeStorage)
        );
        std::fs::set_permissions(&profile, std::fs::Permissions::from_mode(0o600)).unwrap();

        symlink(&profile, path.join("symlink.json")).unwrap();
        assert_eq!(
            guard.read("symlink.json"),
            Err(PlatformError::UnsafeStorage)
        );
        symlink(&path, temporary.path().join("directory-link")).unwrap();
        assert!(matches!(
            PrivateDirectory::open(&temporary.path().join("directory-link")),
            Err(PlatformError::UnsafeStorage)
        ));

        let file = File::open(&profile).unwrap();
        install_acl(&file, false);
        assert_eq!(
            file.metadata().unwrap().mode() & 0o777,
            0o600,
            "ACL fixture must not rely on public mode bits"
        );
        assert_eq!(
            guard.read("profile.json"),
            Err(PlatformError::UnsafeStorage)
        );
        drop(file);
        drop(guard);

        let root = File::open(&path).unwrap();
        install_acl(&root, true);
        assert!(matches!(
            directory.try_lock(),
            Err(PlatformError::UnsafeStorage)
        ));
    }

    #[test]
    fn instance_sentinel_rejects_public_mode_and_posix_acl() {
        let temporary = tempfile::tempdir().unwrap();
        let path = temporary.path().join("private");
        let directory = PrivateDirectory::open(&path).unwrap();
        drop(directory.try_claim_instance().unwrap());
        let sentinel = path.join("instance.lock");
        std::fs::set_permissions(&sentinel, std::fs::Permissions::from_mode(0o644)).unwrap();
        assert!(matches!(
            directory.try_claim_instance(),
            Err(PlatformError::UnsafeStorage)
        ));
        std::fs::set_permissions(&sentinel, std::fs::Permissions::from_mode(0o600)).unwrap();
        let file = File::open(&sentinel).unwrap();
        install_acl(&file, false);
        assert!(matches!(
            directory.try_claim_instance(),
            Err(PlatformError::UnsafeStorage)
        ));
    }

    #[test]
    fn recovery_requires_the_staged_identity_and_unchanged_bytes() {
        let temporary = tempfile::tempdir().unwrap();
        let directory = PrivateDirectory::open(&temporary.path().join("private")).unwrap();
        let guard = directory.try_lock().unwrap();
        guard.write("first", b"same", None).unwrap();
        guard.write("second", b"same", None).unwrap();
        let first = CString::new("first").unwrap();
        let second = CString::new("second").unwrap();
        let file = open_existing_at(&directory.root, &first, libc::O_RDONLY)
            .unwrap()
            .unwrap();
        let identity = file_identity(&file).unwrap();
        assert!(recovery_matches(&directory.root, &first, identity, b"same"));
        assert!(!recovery_matches(
            &directory.root,
            &second,
            identity,
            b"same"
        ));
        std::fs::write(temporary.path().join("private/first"), b"edit").unwrap();
        assert!(!recovery_matches(
            &directory.root,
            &first,
            identity,
            b"same"
        ));
    }

    #[test]
    fn bounded_staging_collisions_skip_abandoned_files_without_adopting_them() {
        let temporary = tempfile::tempdir().unwrap();
        let path = temporary.path().join("private");
        let directory = PrivateDirectory::open(&path).unwrap();
        let guard = directory.try_lock().unwrap();
        for sequence in 0..3 {
            let candidate = path.join(format!(".write-{}-{sequence}", std::process::id()));
            let mut file = std::fs::OpenOptions::new()
                .create_new(true)
                .write(true)
                .mode(0o600)
                .open(candidate)
                .unwrap();
            file.write_all(b"abandoned").unwrap();
        }
        guard.write("profile.json", b"committed", None).unwrap();
        assert_eq!(guard.read("profile.json").unwrap().unwrap(), b"committed");
        for sequence in 0..3 {
            assert_eq!(
                std::fs::read(path.join(format!(".write-{}-{sequence}", std::process::id())))
                    .unwrap(),
                b"abandoned"
            );
        }

        let bounded_path = temporary.path().join("bounded");
        let bounded = PrivateDirectory::open(&bounded_path).unwrap();
        let bounded_guard = bounded.try_lock().unwrap();
        for sequence in 0..STAGING_ATTEMPTS {
            std::fs::OpenOptions::new()
                .create_new(true)
                .write(true)
                .mode(0o600)
                .open(bounded_path.join(format!(".write-{}-{sequence}", std::process::id())))
                .unwrap();
        }
        assert_eq!(
            bounded_guard.write("profile.json", b"blocked", None),
            Err(PlatformError::Unavailable)
        );
    }

    #[test]
    fn hook_error_preserves_a_replacement_at_the_staging_name() {
        let temporary = tempfile::tempdir().unwrap();
        let path = temporary.path().join("private");
        let directory = PrivateDirectory::open(&path).unwrap();
        let guard = directory.try_lock().unwrap();
        let result = guard.write_with_hook("profile.json", b"staged", None, || {
            let staging = std::fs::read_dir(&path)
                .unwrap()
                .map(|entry| entry.unwrap().path())
                .find(|candidate| {
                    candidate
                        .file_name()
                        .unwrap()
                        .to_string_lossy()
                        .starts_with(".write-")
                })
                .unwrap();
            std::fs::rename(&staging, path.join("moved-staged")).unwrap();
            let mut replacement = std::fs::OpenOptions::new()
                .create_new(true)
                .write(true)
                .mode(0o600)
                .open(&staging)
                .unwrap();
            replacement.write_all(b"external").unwrap();
            Err(PlatformError::Unavailable)
        });
        assert_eq!(result, Err(PlatformError::Unavailable));
        assert_eq!(std::fs::read(path.join("moved-staged")).unwrap(), b"staged");
        let preserved = std::fs::read_dir(&path)
            .unwrap()
            .map(|entry| entry.unwrap().path())
            .find(|candidate| {
                candidate
                    .file_name()
                    .unwrap()
                    .to_string_lossy()
                    .starts_with(".write-")
            })
            .unwrap();
        assert_eq!(std::fs::read(preserved).unwrap(), b"external");
        assert!(!path.join("profile.json").exists());
    }
}
