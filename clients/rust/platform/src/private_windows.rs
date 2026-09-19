use super::{validate_name, MAX_PRIVATE_FILE_BYTES};
use crate::{PlatformError, Result};
use std::ffi::{c_void, OsString};
use std::fs::File;
use std::io::{Read, Write};
use std::os::windows::ffi::{OsStrExt, OsStringExt};
use std::os::windows::io::{AsRawHandle, FromRawHandle, OwnedHandle};
use std::path::{Path, PathBuf};
use std::ptr::{null, null_mut};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Arc;
use windows_sys::Win32::Foundation::*;
use windows_sys::Win32::Security::Authorization::*;
use windows_sys::Win32::Security::*;
use windows_sys::Win32::Storage::FileSystem::*;
use windows_sys::Win32::System::Threading::{GetCurrentProcess, OpenProcessToken};

struct LocalAllocation(*mut c_void);
impl Drop for LocalAllocation {
    fn drop(&mut self) {
        unsafe {
            LocalFree(self.0);
        }
    }
}

fn wide(path: &Path) -> Result<Vec<u16>> {
    let mut value: Vec<_> = path.as_os_str().encode_wide().collect();
    if value.contains(&0) {
        return Err(PlatformError::UnsafeStorage);
    }
    value.push(0);
    Ok(value)
}

struct UserSid {
    words: Vec<u32>,
    text: String,
}
impl UserSid {
    fn pointer(&self) -> PSID {
        self.words.as_ptr().cast_mut().cast()
    }
    fn current() -> Result<Self> {
        let mut token = null_mut();
        if unsafe { OpenProcessToken(GetCurrentProcess(), TOKEN_QUERY, &mut token) } == 0 {
            return Err(PlatformError::Unavailable);
        }
        let token = unsafe { OwnedHandle::from_raw_handle(token) };
        let mut bytes = 0;
        unsafe {
            GetTokenInformation(token.as_raw_handle(), TokenUser, null_mut(), 0, &mut bytes);
        }
        if bytes == 0 || bytes > 4096 {
            return Err(PlatformError::Unavailable);
        }
        // Native TOKEN_USER contains pointers, so the query buffer is aligned.
        let mut buffer = vec![0_usize; (bytes as usize).div_ceil(std::mem::size_of::<usize>())];
        if unsafe {
            GetTokenInformation(
                token.as_raw_handle(),
                TokenUser,
                buffer.as_mut_ptr().cast(),
                bytes,
                &mut bytes,
            )
        } == 0
        {
            return Err(PlatformError::Unavailable);
        }
        let user = unsafe { &*buffer.as_ptr().cast::<TOKEN_USER>() };
        if user.User.Sid.is_null() || unsafe { IsValidSid(user.User.Sid) } == 0 {
            return Err(PlatformError::Unavailable);
        }
        let length = unsafe { GetLengthSid(user.User.Sid) };
        if length == 0 || length > SECURITY_MAX_SID_SIZE {
            return Err(PlatformError::Unavailable);
        }
        let mut words = vec![0_u32; (length as usize).div_ceil(4)];
        if unsafe { CopySid(length, words.as_mut_ptr().cast(), user.User.Sid) } == 0 {
            return Err(PlatformError::Unavailable);
        }
        let mut string = null_mut();
        if unsafe { ConvertSidToStringSidW(words.as_ptr().cast_mut().cast(), &mut string) } == 0 {
            return Err(PlatformError::Unavailable);
        }
        let allocation = LocalAllocation(string.cast());
        let mut length = 0;
        // A textual SID has a documented finite size; never scan unbounded data.
        while length < 256 && unsafe { *string.add(length) } != 0 {
            length += 1;
        }
        if length == 256 {
            return Err(PlatformError::Unavailable);
        }
        let text = String::from_utf16(unsafe { std::slice::from_raw_parts(string, length) })
            .map_err(|_| PlatformError::Unavailable)?;
        drop(allocation);
        Ok(Self { words, text })
    }
    fn descriptor(&self) -> Result<LocalAllocation> {
        // Explicit owner, protected DACL, one full-access ACE for this user.
        // Files inherit no broad parent ACL and are never made public first.
        descriptor(&format!("O:{}D:P(A;;FA;;;{})", self.text, self.text))
    }
}

fn descriptor(sddl: &str) -> Result<LocalAllocation> {
    let sddl: Vec<_> = sddl.encode_utf16().chain(Some(0)).collect();
    let mut raw = null_mut();
    if unsafe {
        ConvertStringSecurityDescriptorToSecurityDescriptorW(
            sddl.as_ptr(),
            SDDL_REVISION_1,
            &mut raw,
            null_mut(),
        )
    } == 0
        || raw.is_null()
    {
        return Err(PlatformError::Unavailable);
    }
    Ok(LocalAllocation(raw))
}

fn validate(file: &File, directory: bool, user: &UserSid) -> Result<()> {
    let handle = file.as_raw_handle();
    let mut info = BY_HANDLE_FILE_INFORMATION::default();
    if unsafe { GetFileType(handle) } != FILE_TYPE_DISK
        || unsafe { GetFileInformationByHandle(handle, &mut info) } == 0
        || info.dwFileAttributes & FILE_ATTRIBUTE_REPARSE_POINT != 0
        || (info.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY != 0) != directory
        || (!directory
            && (info.nNumberOfLinks != 1
                || info.nFileSizeHigh != 0
                || info.nFileSizeLow as usize > MAX_PRIVATE_FILE_BYTES))
    {
        return Err(PlatformError::UnsafeStorage);
    }
    let mut owner = null_mut();
    let mut dacl = null_mut();
    let mut raw = null_mut();
    if unsafe {
        GetSecurityInfo(
            handle,
            SE_FILE_OBJECT,
            OWNER_SECURITY_INFORMATION | DACL_SECURITY_INFORMATION,
            &mut owner,
            null_mut(),
            &mut dacl,
            null_mut(),
            &mut raw,
        )
    } != ERROR_SUCCESS
        || raw.is_null()
    {
        return Err(PlatformError::UnsafeStorage);
    }
    let _owned = LocalAllocation(raw);
    let mut control = 0;
    let mut revision = 0;
    if owner.is_null()
        || dacl.is_null()
        || unsafe { IsValidSid(owner) } == 0
        || unsafe { EqualSid(owner, user.pointer()) } == 0
        || unsafe { GetSecurityDescriptorControl(raw, &mut control, &mut revision) } == 0
        || control & SE_DACL_PROTECTED == 0
        || unsafe { (*dacl).AceCount } != 1
    {
        return Err(PlatformError::UnsafeStorage);
    }
    let mut ace = null_mut();
    if unsafe { GetAce(dacl, 0, &mut ace) } == 0 || ace.is_null() {
        return Err(PlatformError::UnsafeStorage);
    }
    let ace = unsafe { &*ace.cast::<ACCESS_ALLOWED_ACE>() };
    let sid = std::ptr::addr_of!(ace.SidStart).cast_mut().cast();
    // ACCESS_ALLOWED_ACE_TYPE is 0 in winnt.h. Require exactly the private ACL
    // we create, not merely a read-only bit or an apparently effective grant.
    if ace.Header.AceType != 0
        || ace.Header.AceFlags != 0
        || ace.Mask != FILE_ALL_ACCESS
        || unsafe { IsValidSid(sid) } == 0
        || unsafe { EqualSid(sid, user.pointer()) } == 0
    {
        return Err(PlatformError::UnsafeStorage);
    }
    Ok(())
}

struct Root {
    file: File,
    path: PathBuf,
    user: UserSid,
}
#[derive(Clone)]
pub struct PrivateDirectory {
    root: Arc<Root>,
}
pub struct PrivateTransaction {
    root: Arc<Root>,
    _lock: File,
}

fn open(
    root: &Root,
    name: &str,
    access: u32,
    disposition: u32,
    sharing: u32,
) -> Result<Option<File>> {
    validate(&root.file, true, &root.user)?;
    let path = wide(&root.path.join(name))?;
    let descriptor = root.user.descriptor()?;
    let attributes = SECURITY_ATTRIBUTES {
        nLength: std::mem::size_of::<SECURITY_ATTRIBUTES>() as u32,
        lpSecurityDescriptor: descriptor.0,
        bInheritHandle: 0,
    };
    let handle = unsafe {
        CreateFileW(
            path.as_ptr(),
            access | READ_CONTROL,
            sharing,
            &attributes,
            disposition,
            FILE_FLAG_OPEN_REPARSE_POINT,
            null_mut(),
        )
    };
    if handle == INVALID_HANDLE_VALUE {
        return match unsafe { GetLastError() } {
            ERROR_FILE_NOT_FOUND => Ok(None),
            ERROR_FILE_EXISTS | ERROR_ALREADY_EXISTS => Err(PlatformError::Changed),
            _ => Err(PlatformError::UnsafeStorage),
        };
    }
    let file = unsafe { File::from_raw_handle(handle) };
    validate(&file, false, &root.user)?;
    Ok(Some(file))
}

fn read(root: &Root, name: &str) -> Result<Option<Vec<u8>>> {
    let Some(mut file) = open(
        root,
        name,
        GENERIC_READ,
        OPEN_EXISTING,
        FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
    )?
    else {
        return Ok(None);
    };
    let mut bytes = Vec::new();
    (&mut file)
        .take((MAX_PRIVATE_FILE_BYTES + 1) as u64)
        .read_to_end(&mut bytes)
        .map_err(|_| PlatformError::Unavailable)?;
    validate(&file, false, &root.user)?;
    if bytes.len() > MAX_PRIVATE_FILE_BYTES {
        return Err(PlatformError::TooLarge);
    }
    Ok(Some(bytes))
}

impl PrivateDirectory {
    pub fn open(path: &Path) -> Result<Self> {
        if !path.is_absolute() {
            return Err(PlatformError::UnsafeStorage);
        }
        let user = UserSid::current()?;
        let descriptor = user.descriptor()?;
        let attributes = SECURITY_ATTRIBUTES {
            nLength: std::mem::size_of::<SECURITY_ATTRIBUTES>() as u32,
            lpSecurityDescriptor: descriptor.0,
            bInheritHandle: 0,
        };
        let path = wide(path)?;
        if unsafe { CreateDirectoryW(path.as_ptr(), &attributes) } == 0
            && unsafe { GetLastError() } != ERROR_ALREADY_EXISTS
        {
            return Err(PlatformError::Unavailable);
        }
        // Deny directory deletion/rename while this handle anchors operations.
        let handle = unsafe {
            CreateFileW(
                path.as_ptr(),
                GENERIC_READ | READ_CONTROL,
                FILE_SHARE_READ | FILE_SHARE_WRITE,
                null(),
                OPEN_EXISTING,
                FILE_FLAG_BACKUP_SEMANTICS | FILE_FLAG_OPEN_REPARSE_POINT,
                null_mut(),
            )
        };
        if handle == INVALID_HANDLE_VALUE {
            return Err(PlatformError::UnsafeStorage);
        }
        let file = unsafe { File::from_raw_handle(handle) };
        validate(&file, true, &user)?;
        let mut final_path = vec![0_u16; 32_768];
        let length = unsafe {
            GetFinalPathNameByHandleW(
                file.as_raw_handle(),
                final_path.as_mut_ptr(),
                final_path.len() as u32,
                FILE_NAME_NORMALIZED,
            )
        } as usize;
        if length == 0 || length >= final_path.len() {
            return Err(PlatformError::UnsafeStorage);
        }
        let path = PathBuf::from(OsString::from_wide(&final_path[..length]));
        Ok(Self {
            root: Arc::new(Root { file, path, user }),
        })
    }
    pub fn try_lock(&self) -> Result<PrivateTransaction> {
        let file = open(
            &self.root,
            "connection.lock",
            GENERIC_READ | GENERIC_WRITE,
            OPEN_ALWAYS,
            FILE_SHARE_READ | FILE_SHARE_WRITE,
        )?
        .ok_or(PlatformError::Unavailable)?;
        file.try_lock().map_err(|error| match error {
            std::fs::TryLockError::WouldBlock => PlatformError::Busy,
            std::fs::TryLockError::Error(_) => PlatformError::Unavailable,
        })?;
        Ok(PrivateTransaction {
            root: self.root.clone(),
            _lock: file,
        })
    }
}

struct Temporary {
    path: PathBuf,
    remove: bool,
}
impl Drop for Temporary {
    fn drop(&mut self) {
        if self.remove {
            let _ = std::fs::remove_file(&self.path);
        }
    }
}

impl PrivateTransaction {
    pub fn read(&self, name: &str) -> Result<Option<Vec<u8>>> {
        validate_name(name)?;
        read(&self.root, name)
    }
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
        if bytes.len() > MAX_PRIVATE_FILE_BYTES {
            return Err(PlatformError::TooLarge);
        }
        if self.read(name)?.as_deref() != expected {
            return Err(PlatformError::Changed);
        }
        static SEQUENCE: AtomicU64 = AtomicU64::new(0);
        let temporary_name = format!(
            ".write-{}-{}",
            std::process::id(),
            SEQUENCE.fetch_add(1, Ordering::Relaxed)
        );
        let backup_name = format!("{temporary_name}-old");
        if read(&self.root, &backup_name)?.is_some() {
            return Err(PlatformError::UnsafeStorage);
        }
        let mut file = open(
            &self.root,
            &temporary_name,
            GENERIC_READ | GENERIC_WRITE,
            CREATE_NEW,
            FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
        )?
        .ok_or(PlatformError::Unavailable)?;
        let mut temporary = Temporary {
            path: self.root.path.join(&temporary_name),
            remove: true,
        };
        let mut backup = Temporary {
            path: self.root.path.join(&backup_name),
            remove: false,
        };
        file.write_all(bytes)
            .and_then(|_| file.sync_all())
            .map_err(|_| PlatformError::Unavailable)?;
        // ReplaceFile opens its replacement without sharing. Keeping our write
        // handle open would make every replacement fail with a sharing error.
        drop(file);
        before_exchange()?;
        validate(&self.root.file, true, &self.root.user)?;
        let target = wide(&self.root.path.join(name))?;
        let source = wide(&temporary.path)?;
        let previous = wide(&backup.path)?;
        // Some native failure codes mean the original has already moved to the
        // backup name. Preserve every candidate until the outcome is understood.
        temporary.remove = false;
        let replaced = if expected.is_some() {
            unsafe {
                ReplaceFileW(
                    target.as_ptr(),
                    source.as_ptr(),
                    previous.as_ptr(),
                    0,
                    null(),
                    null(),
                )
            }
        } else {
            unsafe { MoveFileExW(source.as_ptr(), target.as_ptr(), MOVEFILE_WRITE_THROUGH) }
        };
        if replaced == 0 {
            if read(&self.root, name).ok() == Some(None)
                && matches!(read(&self.root, &backup_name), Ok(Some(_)))
            {
                // Restore only into a missing destination; never overwrite a
                // new external edit while recovering a partial native failure.
                unsafe {
                    MoveFileExW(previous.as_ptr(), target.as_ptr(), MOVEFILE_WRITE_THROUGH);
                }
            }
            return Err(PlatformError::Unavailable);
        }
        let displaced_matches = match read(&self.root, &backup_name) {
            Ok(displaced) => displaced.as_deref() == expected,
            Err(_) => false,
        };
        if expected.is_some() && !displaced_matches {
            if read(&self.root, name)?.as_deref() == Some(bytes)
                && unsafe {
                    ReplaceFileW(
                        target.as_ptr(),
                        previous.as_ptr(),
                        source.as_ptr(),
                        0,
                        null(),
                        null(),
                    )
                } != 0
            {
                temporary.remove = true;
                return Err(PlatformError::Changed);
            }
            return Err(PlatformError::Unavailable);
        }
        backup.remove = true;
        temporary.remove = true;
        // Flush replacement data; Windows exposes no portable directory fsync.
        open(
            &self.root,
            name,
            GENERIC_WRITE,
            OPEN_EXISTING,
            FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
        )?
        .ok_or(PlatformError::Unavailable)?
        .sync_all()
        .map_err(|_| PlatformError::Unavailable)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn rejects_a_real_acl_grant_to_everyone_even_with_readonly_attributes() {
        let temporary = tempfile::tempdir().unwrap();
        let directory = PrivateDirectory::open(&temporary.path().join("private")).unwrap();
        let guard = directory.try_lock().unwrap();
        guard.write("profile.json", b"synthetic", None).unwrap();
        let path = directory.root.path.join("profile.json");
        let permissions = std::fs::metadata(&path).unwrap().permissions();
        let mut readonly = permissions.clone();
        readonly.set_readonly(true);
        std::fs::set_permissions(&path, readonly).unwrap();
        let sddl = format!(
            "O:{}D:P(A;;FA;;;{})(A;;FR;;;WD)",
            directory.root.user.text, directory.root.user.text
        );
        let changed = descriptor(&sddl).unwrap();
        assert_ne!(
            unsafe {
                SetFileSecurityW(
                    wide(&path).unwrap().as_ptr(),
                    DACL_SECURITY_INFORMATION | PROTECTED_DACL_SECURITY_INFORMATION,
                    changed.0,
                )
            },
            0
        );
        assert_eq!(
            guard.read("profile.json"),
            Err(PlatformError::UnsafeStorage)
        );
        std::fs::set_permissions(&path, permissions).unwrap();
    }

    #[test]
    fn native_owner_acl_and_reparse_checks_fail_closed() {
        let temporary = tempfile::tempdir().unwrap();
        let inherited = temporary.path().join("inherited");
        std::fs::create_dir(&inherited).unwrap();
        assert!(matches!(
            PrivateDirectory::open(&inherited),
            Err(PlatformError::UnsafeStorage)
        ));
        let directory = PrivateDirectory::open(&temporary.path().join("private")).unwrap();
        let guard = directory.try_lock().unwrap();
        guard.write("profile.json", b"synthetic", None).unwrap();
        let mut other = UserSid::current().unwrap();
        let last = other.words.len() - 1;
        other.words[last] ^= 1;
        assert_eq!(
            validate(&directory.root.file, true, &other),
            Err(PlatformError::UnsafeStorage)
        );
        // The CI runner has symlink rights. A setup failure must not silently
        // turn this reparse-point check into a pass.
        std::os::windows::fs::symlink_file(
            directory.root.path.join("profile.json"),
            directory.root.path.join("link.json"),
        )
        .unwrap();
        assert_eq!(guard.read("link.json"), Err(PlatformError::UnsafeStorage));
        std::os::windows::fs::symlink_dir(&directory.root.path, temporary.path().join("root-link"))
            .unwrap();
        assert!(matches!(
            PrivateDirectory::open(&temporary.path().join("root-link")),
            Err(PlatformError::UnsafeStorage)
        ));
    }
}
