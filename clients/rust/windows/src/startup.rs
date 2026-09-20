use crate::options::Options;
use hormuz_client_platform::{ApplicationInstance, PlatformError, PrivateDirectory, Result};
use std::ffi::OsString;
use std::os::windows::ffi::OsStringExt;
use std::path::PathBuf;
use std::ptr::null_mut;
use windows_sys::Win32::System::Com::CoTaskMemFree;
use windows_sys::Win32::UI::Shell::{FOLDERID_LocalAppData, SHGetKnownFolderPath};

fn default_root() -> Result<PathBuf> {
    let mut path = null_mut();
    // Resolve the current user's native known folder; environment overrides do
    // not create a second ownership/refresh domain for the same credential.
    if unsafe { SHGetKnownFolderPath(&FOLDERID_LocalAppData, 0, null_mut(), &mut path) } < 0
        || path.is_null()
    {
        return Err(PlatformError::Unavailable);
    }
    let mut length = 0;
    while length < 32_768 && unsafe { *path.add(length) } != 0 {
        length += 1;
    }
    let result = if length < 32_768 {
        Ok(PathBuf::from(OsString::from_wide(unsafe {
            std::slice::from_raw_parts(path, length)
        }))
        .join("HormuzNativeClient"))
    } else {
        Err(PlatformError::Unavailable)
    };
    unsafe {
        CoTaskMemFree(path.cast());
    }
    result
}

pub enum Startup {
    Primary {
        directory: PrivateDirectory,
        owner: ApplicationInstance,
    },
    Reopened,
}

pub fn begin(options: &Options) -> Result<Startup> {
    let root = options
        .state_directory
        .clone()
        .map(Ok)
        .unwrap_or_else(default_root)?;
    let directory = PrivateDirectory::open(&root)?;
    match directory.try_claim_instance() {
        Ok(owner) => Ok(Startup::Primary { directory, owner }),
        Err(PlatformError::Busy) => {
            if directory.request_reopen().is_ok() {
                return Ok(Startup::Reopened);
            }
            // The owner may have crashed during startup or activation. Only a
            // fresh successful kernel-lock acquisition permits a replacement.
            let owner = directory.try_claim_instance()?;
            Ok(Startup::Primary { directory, owner })
        }
        Err(error) => Err(error),
    }
}
