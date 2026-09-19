use crate::{CredentialStore, PlatformError, Result, SecretRecord};
use windows_sys::Win32::Foundation::{GetLastError, ERROR_NOT_FOUND};
use windows_sys::Win32::Security::Credentials::*;
use zeroize::Zeroize;

pub struct NativeCredentialStore {
    target: Vec<u16>,
}
impl Default for NativeCredentialStore {
    fn default() -> Self {
        Self {
            target: "Hormuz/session/v1/active-connection"
                .encode_utf16()
                .chain(Some(0))
                .collect(),
        }
    }
}

struct OwnedCredential(*mut CREDENTIALW);
impl Drop for OwnedCredential {
    fn drop(&mut self) {
        // SAFETY: CredRead owns this allocation until CredFree. Zeroize only a
        // valid bounded blob, then release the complete native record once.
        unsafe {
            let record = &mut *self.0;
            if record.CredentialBlobSize <= CRED_MAX_CREDENTIAL_BLOB_SIZE
                && !record.CredentialBlob.is_null()
            {
                std::slice::from_raw_parts_mut(
                    record.CredentialBlob,
                    record.CredentialBlobSize as usize,
                )
                .zeroize();
            }
            CredFree(self.0.cast());
        }
    }
}

impl CredentialStore for NativeCredentialStore {
    fn maximum_record_bytes(&self) -> usize {
        CRED_MAX_CREDENTIAL_BLOB_SIZE as usize
    }

    fn load(&self) -> Result<Option<SecretRecord>> {
        let mut raw = std::ptr::null_mut();
        // SAFETY: a live terminated target and valid output pointer. Successful
        // ownership is immediately transferred to the cleanup guard.
        if unsafe { CredReadW(self.target.as_ptr(), CRED_TYPE_GENERIC, 0, &mut raw) } == 0 {
            return if unsafe { GetLastError() } == ERROR_NOT_FOUND {
                Ok(None)
            } else {
                Err(PlatformError::SecureStoreUnavailable)
            };
        }
        if raw.is_null() {
            return Err(PlatformError::SecureStoreUnavailable);
        }
        let owned = OwnedCredential(raw);
        let value = unsafe { &*owned.0 };
        if value.Type != CRED_TYPE_GENERIC
            || value.CredentialBlobSize == 0
            || value.CredentialBlobSize as usize > self.maximum_record_bytes()
            || value.CredentialBlob.is_null()
        {
            return Err(PlatformError::SecureStoreUnavailable);
        }
        let bytes = unsafe {
            std::slice::from_raw_parts(value.CredentialBlob, value.CredentialBlobSize as usize)
        };
        SecretRecord::new(bytes.to_vec()).map(Some)
    }

    fn save(&self, record: &SecretRecord) -> Result<()> {
        if record.expose().len() > self.maximum_record_bytes() {
            return Err(PlatformError::TooLarge);
        }
        let credential = CREDENTIALW {
            Type: CRED_TYPE_GENERIC,
            TargetName: self.target.as_ptr().cast_mut(),
            CredentialBlobSize: record.expose().len() as u32,
            CredentialBlob: record.expose().as_ptr().cast_mut(),
            Persist: CRED_PERSIST_LOCAL_MACHINE,
            ..Default::default()
        };
        // CredWrite replaces the same current-user target in one native call.
        // LOCAL_MACHINE means persistence on this machine, not all-user access.
        // The API reads both buffers; they remain live throughout the call.
        if unsafe { CredWriteW(&credential, 0) } == 0 {
            Err(PlatformError::SecureStoreUnavailable)
        } else {
            Ok(())
        }
    }

    fn delete(&self) -> Result<()> {
        if unsafe { CredDeleteW(self.target.as_ptr(), CRED_TYPE_GENERIC, 0) } != 0
            || unsafe { GetLastError() } == ERROR_NOT_FOUND
        {
            Ok(())
        } else {
            Err(PlatformError::SecureStoreUnavailable)
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    struct TestStore(NativeCredentialStore);
    impl Drop for TestStore {
        fn drop(&mut self) {
            self.0
                .delete()
                .expect("synthetic Credential Manager cleanup");
        }
    }
    #[test]
    fn native_credential_manager_roundtrip_replacement_and_oversize_failure() {
        let unique = tempfile::tempdir().unwrap();
        let target = format!(
            "Hormuz/synthetic-test/{}-{}",
            std::process::id(),
            unique.path().file_name().unwrap().to_string_lossy()
        );
        let owned = TestStore(NativeCredentialStore {
            target: target.encode_utf16().chain(Some(0)).collect(),
        });
        let store = &owned.0;
        assert!(store.load().unwrap().is_none());
        store
            .save(&SecretRecord::new(b"synthetic-first".to_vec()).unwrap())
            .unwrap();
        store
            .save(&SecretRecord::new(b"synthetic-replacement".to_vec()).unwrap())
            .unwrap();
        let oversized = SecretRecord::new(vec![b'x'; store.maximum_record_bytes() + 1]).unwrap();
        assert_eq!(store.save(&oversized), Err(PlatformError::TooLarge));
        assert_eq!(
            store.load().unwrap().unwrap().expose(),
            b"synthetic-replacement"
        );
        store.delete().unwrap();
        assert!(store.load().unwrap().is_none());
    }
}
