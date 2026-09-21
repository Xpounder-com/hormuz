use crate::{CredentialStore, PlatformError, Result, SecretRecord};
use oo7::dbus::api::{Collection, DBusSecret, Item, Properties, Service, Session};
use oo7::{Key, Secret};
use std::collections::HashMap;
use std::future::Future;
use std::os::unix::fs::{FileTypeExt, MetadataExt};
use std::path::{Path, PathBuf};
use std::sync::Arc;
use std::time::Duration;
use zbus::zvariant::OwnedObjectPath;

const APPLICATION: &str = "com.hormuz.client";
const SERVICE: &str = "com.hormuz.client.session.v1";
const ACCOUNT: &str = "active-connection-v1";
const LABEL: &str = "Hormuz active connection";
const MAX_RECORD_BYTES: usize = 32_767;
const AES_BLOCK_BYTES: usize = 16;
const MAX_ENCRYPTED_RECORD_BYTES: usize = 32_768;
const MAX_DH_PUBLIC_KEY_BYTES: usize = 128;
const GENERIC_SECRET_SCHEMA: &str = "org.freedesktop.Secret.Generic";
const METHOD_BUDGET: Duration = Duration::from_secs(5);
const OPERATION_BUDGET: Duration = Duration::from_secs(10);

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum BackendError {
    Unavailable,
    MissingCollection,
    Locked,
    Ambiguous,
    PromptRequired,
    InvalidRecord,
}

type BackendResult<T> = std::result::Result<T, BackendError>;

fn unavailable<T>(_error: T) -> BackendError {
    BackendError::Unavailable
}

fn attributes() -> HashMap<&'static str, &'static str> {
    HashMap::from([
        ("application", APPLICATION),
        ("service", SERVICE),
        ("account", ACCOUNT),
    ])
}

fn exact_attributes(actual: &HashMap<String, String>) -> bool {
    let owned = actual.get("application").map(String::as_str) == Some(APPLICATION)
        && actual.get("service").map(String::as_str) == Some(SERVICE)
        && actual.get("account").map(String::as_str) == Some(ACCOUNT);
    owned
        && match actual.len() {
            3 => true,
            // GNOME Keyring persists generic items with this one schema marker.
            // Continue rejecting every other provider-added or foreign field.
            4 => {
                actual.get(oo7::XDG_SCHEMA_ATTRIBUTE).map(String::as_str)
                    == Some(GENERIC_SECRET_SCHEMA)
            }
            _ => false,
        }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
struct ItemState {
    locked: bool,
    exact_attributes: bool,
}

fn classify_items(count: usize, state: Option<ItemState>) -> BackendResult<bool> {
    match (count, state) {
        (0, None) => Ok(false),
        (1, Some(state)) if state.locked => Err(BackendError::Locked),
        (1, Some(state)) if !state.exact_attributes => Err(BackendError::Ambiguous),
        (1, Some(_)) => Ok(true),
        _ => Err(BackendError::Ambiguous),
    }
}

fn no_prompt(path: &OwnedObjectPath) -> BackendResult<()> {
    if path.as_str() == "/" {
        Ok(())
    } else {
        Err(BackendError::PromptRequired)
    }
}

fn created_without_prompt(item: &OwnedObjectPath, prompt: &OwnedObjectPath) -> BackendResult<()> {
    no_prompt(prompt)?;
    if item.as_str() == "/" {
        Err(BackendError::InvalidRecord)
    } else {
        Ok(())
    }
}

fn validate_server_public_key(bytes: &[u8]) -> BackendResult<()> {
    if bytes.is_empty() || bytes.len() > MAX_DH_PUBLIC_KEY_BYTES {
        Err(BackendError::InvalidRecord)
    } else {
        Ok(())
    }
}

fn validate_encrypted_reply(
    expected_session: &str,
    returned_session: &str,
    content_type: oo7::ContentType,
    parameters: &[u8],
    ciphertext: &[u8],
) -> BackendResult<()> {
    if returned_session != expected_session
        // GNOME Keyring normalizes stored blob labels to text/plain while
        // preserving the encrypted bytes. oo7 models the only accepted wire
        // labels as Text and Blob; unknown labels fail during deserialization.
        || !matches!(
            content_type,
            oo7::ContentType::Blob | oo7::ContentType::Text
        )
        || parameters.len() != AES_BLOCK_BYTES
        || ciphertext.is_empty()
        || ciphertext.len() > MAX_ENCRYPTED_RECORD_BYTES
        || !ciphertext.len().is_multiple_of(AES_BLOCK_BYTES)
    {
        Err(BackendError::InvalidRecord)
    } else {
        Ok(())
    }
}

fn verified_user_bus(directory: &Path, uid: u32) -> BackendResult<PathBuf> {
    let metadata = std::fs::symlink_metadata(directory).map_err(unavailable)?;
    if !metadata.is_dir()
        || metadata.file_type().is_symlink()
        || metadata.uid() != uid
        || metadata.mode() & 0o7777 != 0o700
    {
        return Err(BackendError::Unavailable);
    }
    let bus = directory.join("bus");
    let metadata = std::fs::symlink_metadata(&bus).map_err(unavailable)?;
    if !metadata.file_type().is_socket() || metadata.uid() != uid {
        return Err(BackendError::Unavailable);
    }
    Ok(bus)
}

fn verified_process_uid(real_uid: u32, effective_uid: u32, saved_uid: u32) -> BackendResult<u32> {
    if effective_uid == 0 || real_uid != effective_uid || saved_uid != effective_uid {
        Err(BackendError::Unavailable)
    } else {
        Ok(effective_uid)
    }
}

fn canonical_user_bus_address() -> BackendResult<String> {
    let mut real_uid = 0;
    let mut effective_uid = 0;
    let mut saved_uid = 0;
    // SAFETY: valid pointers to owned uid_t values; getresuid only reads the
    // current process credentials and initializes all three outputs on success.
    if unsafe { libc::getresuid(&mut real_uid, &mut effective_uid, &mut saved_uid) } != 0 {
        return Err(BackendError::Unavailable);
    }
    let effective_uid = verified_process_uid(real_uid, effective_uid, saved_uid)?;
    let directory = PathBuf::from(format!("/run/user/{effective_uid}"));
    let bus = verified_user_bus(&directory, effective_uid)?;
    Ok(format!("unix:path={}", bus.display()))
}

struct EncryptedService {
    service: Service,
    session: Arc<Session>,
    key: Arc<Key>,
}

impl EncryptedService {
    async fn connect() -> BackendResult<Self> {
        // Never honor DBUS_SESSION_BUS_ADDRESS from a launcher environment.
        // Credential custody uses only the validated per-UID runtime socket.
        let address = canonical_user_bus_address()?;
        let connection = zbus::connection::Builder::address(address.as_str())
            .map_err(unavailable)?
            .method_timeout(METHOD_BUDGET)
            .build()
            .await
            .map_err(unavailable)?;
        let service = Service::new(&connection).await.map_err(unavailable)?;
        let private_key = Key::generate_private_key().map_err(unavailable)?;
        let public_key = Key::generate_public_key(&private_key).map_err(unavailable)?;
        let (server_key, session) = service
            .open_session(Some(public_key))
            .await
            .map_err(unavailable)?;
        let server_key = server_key.ok_or(BackendError::Unavailable)?;
        validate_server_public_key(server_key.as_ref())?;
        let key = Key::generate_aes_key(&private_key, &server_key).map_err(unavailable)?;
        Ok(Self {
            service,
            session: Arc::new(session),
            key: Arc::new(key),
        })
    }

    async fn default_collection(&self) -> BackendResult<Collection> {
        let collection = self
            .service
            .read_alias("default")
            .await
            .map_err(unavailable)?
            .ok_or(BackendError::MissingCollection)?;
        if collection.is_locked().await.map_err(unavailable)? {
            return Err(BackendError::Locked);
        }
        Ok(collection)
    }

    async fn selected_item(&self, collection: &Collection) -> BackendResult<Option<Item>> {
        let mut items = collection
            .search_items(&attributes())
            .await
            .map_err(unavailable)?;
        if items.len() > 1 {
            return classify_items(items.len(), None).map(|_| None);
        }
        let Some(item) = items.pop() else {
            classify_items(0, None)?;
            return Ok(None);
        };
        let locked = item.is_locked().await.map_err(unavailable)?;
        let actual = item.attributes().await.map_err(unavailable)?;
        classify_items(
            1,
            Some(ItemState {
                locked,
                exact_attributes: exact_attributes(&actual),
            }),
        )?;
        Ok(Some(item))
    }

    fn encrypted_secret(&self, bytes: &[u8]) -> BackendResult<DBusSecret> {
        DBusSecret::new_encrypted(self.session.clone(), Secret::blob(bytes), &self.key)
            .map_err(unavailable)
    }
}

trait Backend {
    fn load(&self) -> BackendResult<Option<SecretRecord>>;
    fn save(&self, bytes: &[u8]) -> BackendResult<()>;
    fn delete(&self) -> BackendResult<()>;
}

#[derive(Default)]
struct SecretServiceBackend;

impl SecretServiceBackend {
    fn run_local<T>(operation: impl Future<Output = BackendResult<T>>) -> BackendResult<T> {
        let runtime = tokio::runtime::Builder::new_current_thread()
            .enable_all()
            .build()
            .map_err(unavailable)?;
        runtime.block_on(async move {
            tokio::time::timeout(OPERATION_BUDGET, operation)
                .await
                .map_err(unavailable)?
        })
    }

    fn run<T: Send>(
        &self,
        operation: impl Future<Output = BackendResult<T>> + Send,
    ) -> BackendResult<T> {
        if tokio::runtime::Handle::try_current().is_err() {
            return Self::run_local(operation);
        }

        // Relay operations run in Tokio's blocking pool, whose threads retain
        // the runtime context. Runtime::block_on would panic there, so move the
        // complete bounded operation to a separate scoped standard thread.
        std::thread::scope(|scope| {
            let worker = std::thread::Builder::new()
                .name("hormuz-secret-service".into())
                .spawn_scoped(scope, move || Self::run_local(operation))
                .map_err(unavailable)?;
            worker.join().map_err(unavailable)?
        })
    }

    async fn load_async() -> BackendResult<Option<SecretRecord>> {
        let service = EncryptedService::connect().await?;
        let collection = service.default_collection().await?;
        let Some(item) = service.selected_item(&collection).await? else {
            return Ok(None);
        };
        let encrypted = item.secret(&service.session).await.map_err(unavailable)?;
        validate_encrypted_reply(
            service.session.inner().path().as_str(),
            encrypted.session().inner().path().as_str(),
            encrypted.content_type(),
            encrypted.parameters(),
            encrypted.value(),
        )?;
        let secret = encrypted.decrypt(Some(&service.key)).map_err(unavailable)?;
        if secret.as_bytes().is_empty() || secret.as_bytes().len() > MAX_RECORD_BYTES {
            return Err(BackendError::InvalidRecord);
        }
        SecretRecord::new(secret.as_bytes().to_vec())
            .map(Some)
            .map_err(|_| BackendError::InvalidRecord)
    }

    async fn save_async(bytes: &[u8]) -> BackendResult<()> {
        let service = EncryptedService::connect().await?;
        let collection = service.default_collection().await?;
        let encrypted = service.encrypted_secret(bytes)?;
        // Preflight rejects a locked, nonexact or duplicate namespace. The
        // raw replace below then binds the secret and exact attributes in one
        // provider operation instead of writing through a raced item proxy.
        let _ = service.selected_item(&collection).await?;

        // The high-level oo7 and secret-service create methods automatically
        // execute returned prompts. Call the low-level method and accept only a
        // completed, prompt-free creation. We never call Prompt or Unlock.
        let properties = Properties::for_item(LABEL, &attributes());
        let reply = collection
            .inner()
            .call_method("CreateItem", &(properties, &encrypted, true))
            .await
            .map_err(unavailable)?;
        let (item_path, prompt_path) = reply
            .body()
            .deserialize::<(OwnedObjectPath, OwnedObjectPath)>()
            .map_err(unavailable)?;
        created_without_prompt(&item_path, &prompt_path)?;

        let selected = service
            .selected_item(&collection)
            .await?
            .ok_or(BackendError::InvalidRecord)?;
        if selected.inner().path().as_str() != item_path.as_str() {
            return Err(BackendError::Ambiguous);
        }
        Ok(())
    }

    async fn delete_async() -> BackendResult<()> {
        let service = EncryptedService::connect().await?;
        let collection = service.default_collection().await?;
        let Some(item) = service.selected_item(&collection).await? else {
            return Ok(());
        };
        // Item::delete also auto-runs a provider prompt, so inspect the raw
        // prompt path and fail closed without displaying or waiting for UI.
        let reply = item
            .inner()
            .call_method("Delete", &())
            .await
            .map_err(unavailable)?;
        let prompt_path = reply
            .body()
            .deserialize::<OwnedObjectPath>()
            .map_err(unavailable)?;
        no_prompt(&prompt_path)?;
        if service.selected_item(&collection).await?.is_some() {
            return Err(BackendError::InvalidRecord);
        }
        Ok(())
    }
}

impl Backend for SecretServiceBackend {
    fn load(&self) -> BackendResult<Option<SecretRecord>> {
        self.run(Self::load_async())
    }

    fn save(&self, bytes: &[u8]) -> BackendResult<()> {
        self.run(Self::save_async(bytes))
    }

    fn delete(&self) -> BackendResult<()> {
        self.run(Self::delete_async())
    }
}

struct Store<B>(B);

impl<B: Backend> CredentialStore for Store<B> {
    fn maximum_record_bytes(&self) -> usize {
        MAX_RECORD_BYTES
    }

    fn load(&self) -> Result<Option<SecretRecord>> {
        self.0
            .load()
            .map_err(|_| PlatformError::SecureStoreUnavailable)
    }

    fn save(&self, record: &SecretRecord) -> Result<()> {
        if record.expose().len() > self.maximum_record_bytes() {
            return Err(PlatformError::TooLarge);
        }
        self.0
            .save(record.expose())
            .map_err(|_| PlatformError::SecureStoreUnavailable)
    }

    fn delete(&self) -> Result<()> {
        self.0
            .delete()
            .map_err(|_| PlatformError::SecureStoreUnavailable)
    }
}

pub struct NativeCredentialStore(Store<SecretServiceBackend>);

impl Default for NativeCredentialStore {
    fn default() -> Self {
        Self(Store(SecretServiceBackend))
    }
}

impl CredentialStore for NativeCredentialStore {
    fn maximum_record_bytes(&self) -> usize {
        self.0.maximum_record_bytes()
    }

    fn load(&self) -> Result<Option<SecretRecord>> {
        self.0.load()
    }

    fn save(&self, record: &SecretRecord) -> Result<()> {
        self.0.save(record)
    }

    fn delete(&self) -> Result<()> {
        self.0.delete()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::Mutex;

    #[derive(Default)]
    struct FakeBackend {
        value: Mutex<Option<Vec<u8>>>,
        failure: Mutex<Option<BackendError>>,
    }

    impl FakeBackend {
        fn fail(error: BackendError) -> Self {
            Self {
                failure: Mutex::new(Some(error)),
                ..Self::default()
            }
        }

        fn check(&self) -> BackendResult<()> {
            if let Some(error) = *self.failure.lock().unwrap() {
                Err(error)
            } else {
                Ok(())
            }
        }
    }

    impl Backend for FakeBackend {
        fn load(&self) -> BackendResult<Option<SecretRecord>> {
            self.check()?;
            self.value
                .lock()
                .unwrap()
                .as_ref()
                .map(|value| SecretRecord::new(value.clone()).map_err(unavailable))
                .transpose()
        }

        fn save(&self, bytes: &[u8]) -> BackendResult<()> {
            self.check()?;
            *self.value.lock().unwrap() = Some(bytes.to_vec());
            Ok(())
        }

        fn delete(&self) -> BackendResult<()> {
            self.check()?;
            *self.value.lock().unwrap() = None;
            Ok(())
        }
    }

    fn path(value: &str) -> OwnedObjectPath {
        OwnedObjectPath::try_from(value).unwrap()
    }

    #[test]
    fn prompt_paths_are_rejected_without_executing_them() {
        assert_eq!(no_prompt(&path("/")), Ok(()));
        assert_eq!(
            no_prompt(&path("/org/freedesktop/secrets/prompt/1")),
            Err(BackendError::PromptRequired)
        );
        assert_eq!(
            created_without_prompt(&path("/"), &path("/")),
            Err(BackendError::InvalidRecord)
        );
        assert_eq!(
            created_without_prompt(
                &path("/org/freedesktop/secrets/item/1"),
                &path("/org/freedesktop/secrets/prompt/1")
            ),
            Err(BackendError::PromptRequired)
        );
    }

    #[test]
    fn malformed_encrypted_replies_are_rejected_before_decryption() {
        let session = "/org/freedesktop/secrets/session/1";
        assert_eq!(
            validate_encrypted_reply(session, session, oo7::ContentType::Blob, &[0; 16], &[0; 16]),
            Ok(())
        );
        assert_eq!(
            validate_encrypted_reply(
                session,
                "/org/freedesktop/secrets/session/2",
                oo7::ContentType::Blob,
                &[0; 16],
                &[0; 16]
            ),
            Err(BackendError::InvalidRecord)
        );
        for parameters in [&[0; 15][..], &[0; 17][..]] {
            assert_eq!(
                validate_encrypted_reply(
                    session,
                    session,
                    oo7::ContentType::Blob,
                    parameters,
                    &[0; 16]
                ),
                Err(BackendError::InvalidRecord)
            );
        }
        for ciphertext in [&[][..], &[0; 15][..], &[0; 17][..], &[0; 32_784][..]] {
            assert_eq!(
                validate_encrypted_reply(
                    session,
                    session,
                    oo7::ContentType::Blob,
                    &[0; 16],
                    ciphertext
                ),
                Err(BackendError::InvalidRecord)
            );
        }
        assert_eq!(
            validate_encrypted_reply(
                session,
                session,
                oo7::ContentType::Blob,
                &[0; 16],
                &[0; MAX_ENCRYPTED_RECORD_BYTES]
            ),
            Ok(())
        );
    }

    #[test]
    fn gnome_normalized_blob_metadata_is_accepted_without_other_relaxation() {
        let session = "/org/freedesktop/secrets/session/1";
        assert_eq!(
            validate_encrypted_reply(session, session, oo7::ContentType::Text, &[0; 16], &[0; 16]),
            Ok(())
        );
        let normalized = HashMap::from([
            ("application".into(), APPLICATION.into()),
            ("service".into(), SERVICE.into()),
            ("account".into(), ACCOUNT.into()),
            (
                oo7::XDG_SCHEMA_ATTRIBUTE.into(),
                GENERIC_SECRET_SCHEMA.into(),
            ),
        ]);
        assert!(exact_attributes(&normalized));
    }

    #[test]
    fn server_public_key_is_nonempty_and_bounded() {
        assert_eq!(validate_server_public_key(&[1]), Ok(()));
        assert_eq!(
            validate_server_public_key(&[1; MAX_DH_PUBLIC_KEY_BYTES]),
            Ok(())
        );
        assert_eq!(
            validate_server_public_key(&[]),
            Err(BackendError::InvalidRecord)
        );
        assert_eq!(
            validate_server_public_key(&[1; MAX_DH_PUBLIC_KEY_BYTES + 1]),
            Err(BackendError::InvalidRecord)
        );
    }

    #[test]
    fn item_selection_rejects_locked_nonexact_and_duplicate_results() {
        assert_eq!(classify_items(0, None), Ok(false));
        assert_eq!(
            classify_items(
                1,
                Some(ItemState {
                    locked: false,
                    exact_attributes: true,
                })
            ),
            Ok(true)
        );
        assert_eq!(
            classify_items(
                1,
                Some(ItemState {
                    locked: true,
                    exact_attributes: true,
                })
            ),
            Err(BackendError::Locked)
        );
        assert_eq!(
            classify_items(
                1,
                Some(ItemState {
                    locked: false,
                    exact_attributes: false,
                })
            ),
            Err(BackendError::Ambiguous)
        );
        assert_eq!(classify_items(2, None), Err(BackendError::Ambiguous));
    }

    #[test]
    fn user_bus_requires_owned_private_directory_and_owned_socket() {
        use std::os::unix::fs::symlink;
        use std::os::unix::fs::PermissionsExt;
        use std::os::unix::net::UnixListener;

        // SAFETY: this call only reads the current process credentials.
        let uid = unsafe { libc::geteuid() };
        let temporary = tempfile::tempdir().unwrap();
        std::fs::set_permissions(temporary.path(), std::fs::Permissions::from_mode(0o700)).unwrap();
        let listener = UnixListener::bind(temporary.path().join("bus")).unwrap();
        assert_eq!(
            verified_user_bus(temporary.path(), uid).unwrap(),
            temporary.path().join("bus")
        );
        std::fs::set_permissions(temporary.path(), std::fs::Permissions::from_mode(0o755)).unwrap();
        assert_eq!(
            verified_user_bus(temporary.path(), uid),
            Err(BackendError::Unavailable)
        );
        std::fs::set_permissions(temporary.path(), std::fs::Permissions::from_mode(0o700)).unwrap();
        drop(listener);
        std::fs::remove_file(temporary.path().join("bus")).unwrap();
        std::fs::write(temporary.path().join("bus"), b"not-a-socket").unwrap();
        assert_eq!(
            verified_user_bus(temporary.path(), uid),
            Err(BackendError::Unavailable)
        );
        std::fs::remove_file(temporary.path().join("bus")).unwrap();
        symlink("/dev/null", temporary.path().join("bus")).unwrap();
        assert_eq!(
            verified_user_bus(temporary.path(), uid),
            Err(BackendError::Unavailable)
        );
    }

    #[test]
    fn canonical_bus_rejects_root_and_changed_process_credentials() {
        assert_eq!(verified_process_uid(1000, 1000, 1000), Ok(1000));
        assert_eq!(
            verified_process_uid(0, 0, 0),
            Err(BackendError::Unavailable)
        );
        assert_eq!(
            verified_process_uid(1000, 1001, 1001),
            Err(BackendError::Unavailable)
        );
        assert_eq!(
            verified_process_uid(1000, 1000, 0),
            Err(BackendError::Unavailable)
        );
    }

    #[test]
    fn synchronous_adapter_uses_an_isolated_worker_from_tokios_blocking_pool() {
        let runtime = tokio::runtime::Builder::new_current_thread()
            .enable_all()
            .build()
            .unwrap();
        runtime.block_on(async move {
            let (inherited_handle, result) = tokio::task::spawn_blocking(|| {
                (
                    tokio::runtime::Handle::try_current().is_ok(),
                    SecretServiceBackend.run(async { Ok(()) }),
                )
            })
            .await
            .unwrap();
            assert!(inherited_handle);
            assert_eq!(result, Ok(()));
        });
    }

    #[test]
    fn locked_missing_ambiguous_and_prompt_states_all_fail_closed() {
        for error in [
            BackendError::Unavailable,
            BackendError::MissingCollection,
            BackendError::Locked,
            BackendError::Ambiguous,
            BackendError::PromptRequired,
            BackendError::InvalidRecord,
        ] {
            let store = Store(FakeBackend::fail(error));
            assert!(matches!(
                store.load(),
                Err(PlatformError::SecureStoreUnavailable)
            ));
            assert_eq!(
                store.save(&SecretRecord::new(b"synthetic".to_vec()).unwrap()),
                Err(PlatformError::SecureStoreUnavailable)
            );
            assert_eq!(store.delete(), Err(PlatformError::SecureStoreUnavailable));
        }
    }

    #[test]
    fn binary_records_replace_and_delete_idempotently_without_file_fallback() {
        let store = Store(FakeBackend::default());
        assert!(store.load().unwrap().is_none());
        let first = SecretRecord::new(vec![0, 255, 1, 128, 2]).unwrap();
        store.save(&first).unwrap();
        assert_eq!(store.load().unwrap().unwrap().expose(), first.expose());
        let replacement = SecretRecord::new(b"synthetic-replacement".to_vec()).unwrap();
        store.save(&replacement).unwrap();
        assert_eq!(
            store.load().unwrap().unwrap().expose(),
            replacement.expose()
        );
        store.delete().unwrap();
        store.delete().unwrap();
        assert!(store.load().unwrap().is_none());
    }

    #[test]
    fn exact_attributes_reject_provider_or_namespace_ambiguity() {
        let expected = HashMap::from([
            ("application".into(), APPLICATION.into()),
            ("service".into(), SERVICE.into()),
            ("account".into(), ACCOUNT.into()),
        ]);
        assert!(exact_attributes(&expected));
        let mut wrong_schema = expected.clone();
        wrong_schema.insert(
            oo7::XDG_SCHEMA_ATTRIBUTE.into(),
            "org.gnome.keyring.Note".into(),
        );
        assert!(!exact_attributes(&wrong_schema));
        let mut additional = expected.clone();
        additional.insert("other".into(), "value".into());
        assert!(!exact_attributes(&additional));
        let mut changed = expected;
        changed.insert("account".into(), "another".into());
        assert!(!exact_attributes(&changed));
    }
}
