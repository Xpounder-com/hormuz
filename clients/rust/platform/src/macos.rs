use crate::{CredentialStore, PlatformError, Result, SecretRecord};
use core_foundation::array::CFArray;
use core_foundation::base::{CFType, TCFType};
use core_foundation::boolean::CFBoolean;
use core_foundation::data::CFData;
use core_foundation::dictionary::CFDictionary;
use core_foundation::string::{CFString, CFStringRef};
use security_framework::os::macos::keychain::SecKeychain;
use security_framework_sys::base::{errSecItemNotFound, errSecSuccess};
use security_framework_sys::item::*;
use security_framework_sys::keychain_item::{
    SecItemAdd, SecItemCopyMatching, SecItemDelete, SecItemUpdate,
};

// File-Keychain-compatible per-query suppression, preserving the existing
// backend. A future Data Protection migration belongs to native integration.
extern "C" {
    static kSecMatchLimitOne: CFStringRef;
    static kSecUseAuthenticationUI: CFStringRef;
    static kSecUseAuthenticationUIFail: CFStringRef;
}

pub struct NativeCredentialStore {
    service: String,
    // Used only by native tests to target a separately created empty keychain.
    keychain: Option<SecKeychain>,
}

impl Default for NativeCredentialStore {
    fn default() -> Self {
        Self {
            service: "com.hormuz.mac.session.v1".into(),
            keychain: None,
        }
    }
}

impl NativeCredentialStore {
    fn query(&self, add: bool) -> Vec<(CFString, CFType)> {
        // SAFETY: framework constants have process lifetime; CF wrappers retain
        // the referenced values, and each dictionary owns its members.
        unsafe {
            let mut values = vec![
                (
                    CFString::wrap_under_get_rule(kSecClass),
                    CFString::wrap_under_get_rule(kSecClassGenericPassword).into_CFType(),
                ),
                (
                    CFString::wrap_under_get_rule(kSecAttrService),
                    CFString::new(&self.service).into_CFType(),
                ),
                (
                    CFString::wrap_under_get_rule(kSecAttrAccount),
                    CFString::new("active-connection-v1").into_CFType(),
                ),
                (
                    CFString::wrap_under_get_rule(kSecAttrSynchronizable),
                    CFBoolean::false_value().into_CFType(),
                ),
                (
                    CFString::wrap_under_get_rule(kSecUseAuthenticationUI),
                    CFString::wrap_under_get_rule(kSecUseAuthenticationUIFail).into_CFType(),
                ),
            ];
            if let Some(keychain) = &self.keychain {
                if add {
                    values.push((
                        CFString::wrap_under_get_rule(kSecUseKeychain),
                        keychain.as_CFType(),
                    ));
                } else {
                    values.push((
                        CFString::wrap_under_get_rule(kSecMatchSearchList),
                        CFArray::from_CFTypes(std::slice::from_ref(keychain)).into_CFType(),
                    ));
                }
            }
            values
        }
    }
}

impl CredentialStore for NativeCredentialStore {
    fn maximum_record_bytes(&self) -> usize {
        32_767
    }

    fn load(&self) -> Result<Option<SecretRecord>> {
        let mut query = self.query(false);
        // SAFETY: static framework constants, retained CF values and valid output
        // pointer. The Copy result is released exactly once by its owned wrapper.
        let (status, raw) = unsafe {
            query.push((
                CFString::wrap_under_get_rule(kSecReturnData),
                CFBoolean::true_value().into_CFType(),
            ));
            query.push((
                CFString::wrap_under_get_rule(kSecMatchLimit),
                CFString::wrap_under_get_rule(kSecMatchLimitOne).into_CFType(),
            ));
            let query = CFDictionary::from_CFType_pairs(&query);
            let mut raw = std::ptr::null();
            let status = SecItemCopyMatching(query.as_concrete_TypeRef(), &mut raw);
            (status, raw)
        };
        if status == errSecItemNotFound {
            return Ok(None);
        }
        if status != errSecSuccess || raw.is_null() {
            return Err(PlatformError::SecureStoreUnavailable);
        }
        let owned = unsafe { CFType::wrap_under_create_rule(raw) };
        let data = owned
            .downcast::<CFData>()
            .ok_or(PlatformError::SecureStoreUnavailable)?;
        if data.is_empty() || data.len() > self.maximum_record_bytes() as isize {
            return Err(PlatformError::SecureStoreUnavailable);
        }
        SecretRecord::new(data.bytes().to_vec()).map(Some)
    }

    fn save(&self, record: &SecretRecord) -> Result<()> {
        if record.expose().len() > self.maximum_record_bytes() {
            return Err(PlatformError::TooLarge);
        }
        let query = CFDictionary::from_CFType_pairs(&self.query(false));
        // Update in place. Never delete an existing record before writing its
        // replacement, and never redirect a failed write to a plaintext file.
        let value = unsafe {
            (
                CFString::wrap_under_get_rule(kSecValueData),
                CFData::from_buffer(record.expose()).into_CFType(),
            )
        };
        let attributes = CFDictionary::from_CFType_pairs(std::slice::from_ref(&value));
        let updated = unsafe {
            SecItemUpdate(
                query.as_concrete_TypeRef(),
                attributes.as_concrete_TypeRef(),
            )
        };
        if updated == errSecSuccess {
            return Ok(());
        }
        if updated != errSecItemNotFound {
            return Err(PlatformError::SecureStoreUnavailable);
        }
        let mut add = self.query(true);
        add.push(value);
        let add = CFDictionary::from_CFType_pairs(&add);
        let status = unsafe { SecItemAdd(add.as_concrete_TypeRef(), std::ptr::null_mut()) };
        if status == errSecSuccess {
            Ok(())
        } else {
            Err(PlatformError::SecureStoreUnavailable)
        }
    }

    fn delete(&self) -> Result<()> {
        let query = CFDictionary::from_CFType_pairs(&self.query(false));
        let status = unsafe { SecItemDelete(query.as_concrete_TypeRef()) };
        if status == errSecSuccess || status == errSecItemNotFound {
            Ok(())
        } else {
            Err(PlatformError::SecureStoreUnavailable)
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use security_framework::os::macos::keychain::CreateOptions;
    use security_framework_sys::base::SecKeychainRef;
    extern "C" {
        fn SecKeychainLock(keychain: SecKeychainRef) -> i32;
        fn SecKeychainDelete(keychain: SecKeychainRef) -> i32;
    }
    struct OwnedTestKeychain(SecKeychain);
    impl Drop for OwnedTestKeychain {
        fn drop(&mut self) {
            // Only the explicit ephemeral keychain created below is deleted.
            let status = unsafe { SecKeychainDelete(self.0.as_concrete_TypeRef()) };
            assert_eq!(status, errSecSuccess, "synthetic keychain cleanup failed");
        }
    }
    #[test]
    fn isolated_keychain_updates_one_record_and_locked_failures_preserve_it() {
        let temporary = tempfile::tempdir().unwrap();
        let mut keychain = OwnedTestKeychain(
            CreateOptions::new()
                .password("synthetic-test-password")
                .prompt_user(false)
                .create(temporary.path().join("synthetic.keychain"))
                .unwrap(),
        );
        let store = NativeCredentialStore {
            service: "com.hormuz.synthetic.platform-test".into(),
            keychain: Some(keychain.0.clone()),
        };
        assert!(store.load().unwrap().is_none());
        store
            .save(&SecretRecord::new(b"synthetic-first".to_vec()).unwrap())
            .unwrap();
        store
            .save(&SecretRecord::new(b"synthetic-replacement".to_vec()).unwrap())
            .unwrap();
        assert_eq!(
            store.load().unwrap().unwrap().expose(),
            b"synthetic-replacement"
        );
        assert_eq!(
            unsafe { SecKeychainLock(keychain.0.as_concrete_TypeRef()) },
            errSecSuccess
        );
        assert!(matches!(
            store.load(),
            Err(PlatformError::SecureStoreUnavailable)
        ));
        assert_eq!(
            store.save(&SecretRecord::new(b"synthetic-blocked".to_vec()).unwrap()),
            Err(PlatformError::SecureStoreUnavailable)
        );
        keychain.0.unlock(Some("synthetic-test-password")).unwrap();
        assert_eq!(
            store.load().unwrap().unwrap().expose(),
            b"synthetic-replacement"
        );
        store.delete().unwrap();
        store.delete().unwrap();
        assert!(store.load().unwrap().is_none());
    }
}
