use super::*;

#[test]
fn secret_records_and_errors_have_content_free_debug_output() {
    let record = SecretRecord::new(b"synthetic-private-marker".to_vec()).unwrap();
    assert_eq!(format!("{record:?}"), "SecretRecord(<redacted>)");
    assert!(SecretRecord::new(Vec::new()).is_err());
    assert!(SecretRecord::new(vec![0; 32_768]).is_err());
    for error in [
        PlatformError::Unavailable,
        PlatformError::UnsafeStorage,
        PlatformError::Busy,
        PlatformError::Changed,
        PlatformError::TooLarge,
        PlatformError::SecureStoreUnavailable,
        PlatformError::Unsupported,
    ] {
        assert!(!format!("{error:?} {error}").contains("synthetic-private-marker"));
    }
}

#[cfg(not(any(target_os = "macos", windows)))]
#[test]
fn unsupported_platform_never_falls_back_to_ordinary_files() {
    let temporary = tempfile::tempdir().unwrap();
    let path = temporary.path().join("must-not-exist");
    assert!(matches!(
        PrivateDirectory::open(&path),
        Err(PlatformError::Unsupported)
    ));
    assert!(!path.exists());
}

#[cfg(any(target_os = "macos", windows))]
mod native {
    use super::*;
    use std::time::{Duration, Instant};

    fn acquire(directory: &PrivateDirectory) -> PrivateTransaction {
        let deadline = Instant::now() + Duration::from_secs(10);
        loop {
            match directory.try_lock() {
                Ok(guard) => return guard,
                Err(PlatformError::Busy) if Instant::now() < deadline => {
                    std::thread::sleep(Duration::from_millis(2))
                }
                _ => panic!("private coordination failed"),
            }
        }
    }

    #[test]
    fn atomic_configuration_compare_replace_and_failed_stage_preserve_old_bytes() {
        let temporary = tempfile::tempdir().unwrap();
        let directory = PrivateDirectory::open(&temporary.path().join("private")).unwrap();
        let guard = directory.try_lock().unwrap();
        assert!(guard.read("profile.json").unwrap().is_none());
        guard.write("profile.json", b"first", None).unwrap();
        assert_eq!(
            guard.write("profile.json", b"wrong", None),
            Err(PlatformError::Changed)
        );
        assert_eq!(
            guard.write("profile.json", b"wrong", Some(b"outdated")),
            Err(PlatformError::Changed)
        );
        assert_eq!(
            guard.write_with_hook("profile.json", b"new", Some(b"first"), || Err(
                PlatformError::Unavailable
            )),
            Err(PlatformError::Unavailable)
        );
        assert_eq!(guard.read("profile.json").unwrap().unwrap(), b"first");
        guard
            .write("profile.json", b"replacement", Some(b"first"))
            .unwrap();
        assert_eq!(guard.read("profile.json").unwrap().unwrap(), b"replacement");
        assert_eq!(
            guard.write(
                "profile.json",
                &vec![0; MAX_PRIVATE_FILE_BYTES + 1],
                Some(b"replacement")
            ),
            Err(PlatformError::TooLarge)
        );
        assert_eq!(guard.read("profile.json").unwrap().unwrap(), b"replacement");
        let exact = vec![b'x'; MAX_PRIVATE_FILE_BYTES];
        guard.write("large.json", &exact, None).unwrap();
        assert_eq!(guard.read("large.json").unwrap().unwrap(), exact);
    }

    #[test]
    fn unsafe_names_and_hardlinks_cannot_escape_the_private_directory() {
        let temporary = tempfile::tempdir().unwrap();
        let path = temporary.path().join("private");
        let directory = PrivateDirectory::open(&path).unwrap();
        let guard = directory.try_lock().unwrap();
        for name in [
            "",
            ".",
            "..",
            "../escape",
            "a/b",
            "a\\b",
            "a:b",
            "connection.lock",
            "Connection.Lock",
            "instance.lock",
            "Instance.Lock",
            ".write-unowned",
            ".WRITE-unowned",
            "CON",
            "NUL.txt",
            "COM1",
            "lpt1.log",
            "trailing.",
            "line\nfeed",
        ] {
            assert_eq!(guard.read(name), Err(PlatformError::UnsafeStorage));
            assert_eq!(
                guard.write(name, b"x", None),
                Err(PlatformError::UnsafeStorage)
            );
        }
        guard.write("profile.json", b"original", None).unwrap();
        std::fs::hard_link(
            path.join("profile.json"),
            temporary.path().join("outside-link"),
        )
        .unwrap();
        assert_eq!(
            guard.read("profile.json"),
            Err(PlatformError::UnsafeStorage)
        );
        assert_eq!(
            guard.write("profile.json", b"bad", Some(b"original")),
            Err(PlatformError::UnsafeStorage)
        );
    }

    #[test]
    fn independent_handles_serialize_concurrent_read_modify_write() {
        let temporary = tempfile::tempdir().unwrap();
        let path = temporary.path().join("private");
        let directory = PrivateDirectory::open(&path).unwrap();
        let held = directory.try_lock().unwrap();
        assert!(matches!(
            PrivateDirectory::open(&path).unwrap().try_lock(),
            Err(PlatformError::Busy)
        ));
        held.write("count", b"0", None).unwrap();
        drop(held);
        let mut workers = Vec::new();
        for _ in 0..4 {
            let path = path.clone();
            workers.push(std::thread::spawn(move || {
                let directory = PrivateDirectory::open(&path).unwrap();
                for _ in 0..12 {
                    let guard = acquire(&directory);
                    let old = guard.read("count").unwrap().unwrap();
                    let value = std::str::from_utf8(&old).unwrap().parse::<u32>().unwrap();
                    guard
                        .write("count", (value + 1).to_string().as_bytes(), Some(&old))
                        .unwrap();
                }
            }));
        }
        for worker in workers {
            worker.join().unwrap();
        }
        assert_eq!(acquire(&directory).read("count").unwrap().unwrap(), b"48");
    }

    #[test]
    fn crash_child() {
        let Some(root) = std::env::var_os("HORMUZ_SYNTHETIC_PLATFORM_CRASH_ROOT") else {
            return;
        };
        let directory = PrivateDirectory::open(Path::new(&root)).unwrap();
        if std::env::var_os("HORMUZ_SYNTHETIC_PLATFORM_LOCK_PROBE").is_some() {
            std::process::exit(
                if matches!(directory.try_lock(), Err(PlatformError::Busy)) {
                    74
                } else {
                    75
                },
            );
        }
        let guard = directory.try_lock().unwrap();
        let _ = guard.write_with_hook("profile.json", b"unfinished", Some(b"original"), || {
            // Abrupt process exit intentionally skips Rust destructors, leaving
            // a staged private file and relying on kernel lock recovery.
            std::process::exit(73);
        });
        panic!("crash injection was not reached");
    }

    #[test]
    fn interrupted_process_releases_lock_and_preserves_committed_configuration() {
        let temporary = tempfile::tempdir().unwrap();
        let path = temporary.path().join("private");
        let directory = PrivateDirectory::open(&path).unwrap();
        directory
            .try_lock()
            .unwrap()
            .write("profile.json", b"original", None)
            .unwrap();
        let mut child = std::process::Command::new(std::env::current_exe().unwrap())
            .args(["--exact", "tests::native::crash_child"])
            .env("HORMUZ_SYNTHETIC_PLATFORM_CRASH_ROOT", &path)
            .stdout(std::process::Stdio::null())
            .stderr(std::process::Stdio::null())
            .spawn()
            .unwrap();
        let deadline = Instant::now() + Duration::from_secs(10);
        let status = loop {
            if let Some(status) = child.try_wait().unwrap() {
                break status;
            }
            if Instant::now() >= deadline {
                child.kill().unwrap();
                let _ = child.wait();
                panic!("synthetic crash worker timed out");
            }
            std::thread::sleep(Duration::from_millis(5));
        };
        assert_eq!(status.code(), Some(73));
        let guard = directory.try_lock().unwrap();
        assert_eq!(guard.read("profile.json").unwrap().unwrap(), b"original");
        guard
            .write("profile.json", b"recovered", Some(b"original"))
            .unwrap();
        assert_eq!(guard.read("profile.json").unwrap().unwrap(), b"recovered");
    }

    #[test]
    fn a_second_process_cannot_acquire_a_held_connection_lock() {
        let temporary = tempfile::tempdir().unwrap();
        let path = temporary.path().join("private");
        let directory = PrivateDirectory::open(&path).unwrap();
        let _guard = directory.try_lock().unwrap();
        let mut child = std::process::Command::new(std::env::current_exe().unwrap())
            .args(["--exact", "tests::native::crash_child"])
            .env("HORMUZ_SYNTHETIC_PLATFORM_CRASH_ROOT", &path)
            .env("HORMUZ_SYNTHETIC_PLATFORM_LOCK_PROBE", "1")
            .stdout(std::process::Stdio::null())
            .stderr(std::process::Stdio::null())
            .spawn()
            .unwrap();
        let deadline = Instant::now() + Duration::from_secs(10);
        loop {
            if let Some(status) = child.try_wait().unwrap() {
                assert_eq!(status.code(), Some(74));
                break;
            }
            if Instant::now() >= deadline {
                child.kill().unwrap();
                let _ = child.wait();
                panic!("synthetic lock probe timed out");
            }
            std::thread::sleep(Duration::from_millis(5));
        }
    }

    #[cfg(target_os = "macos")]
    #[test]
    fn rejects_unsafe_posix_permissions_symlinks_and_extended_acl_grants() {
        use std::os::unix::fs::{symlink, PermissionsExt};
        let temporary = tempfile::tempdir().unwrap();
        let path = temporary.path().join("private");
        let directory = PrivateDirectory::open(&path).unwrap();
        let guard = directory.try_lock().unwrap();
        guard.write("profile.json", b"original", None).unwrap();
        std::fs::set_permissions(
            path.join("profile.json"),
            std::fs::Permissions::from_mode(0o644),
        )
        .unwrap();
        assert_eq!(
            guard.read("profile.json"),
            Err(PlatformError::UnsafeStorage)
        );
        std::fs::set_permissions(
            path.join("profile.json"),
            std::fs::Permissions::from_mode(0o600),
        )
        .unwrap();
        symlink(path.join("profile.json"), path.join("symlink.json")).unwrap();
        assert_eq!(
            guard.read("symlink.json"),
            Err(PlatformError::UnsafeStorage)
        );
        symlink(&path, temporary.path().join("directory-link")).unwrap();
        assert!(matches!(
            PrivateDirectory::open(&temporary.path().join("directory-link")),
            Err(PlatformError::UnsafeStorage)
        ));
        let status = std::process::Command::new("/bin/chmod")
            .args(["+a", "everyone allow read"])
            .arg(path.join("profile.json"))
            .status()
            .unwrap();
        assert!(status.success());
        assert_eq!(
            guard.read("profile.json"),
            Err(PlatformError::UnsafeStorage)
        );
        std::fs::set_permissions(&path, std::fs::Permissions::from_mode(0o755)).unwrap();
        assert!(matches!(
            directory.try_lock(),
            Err(PlatformError::UnsafeStorage)
        ));
    }

    #[test]
    fn raced_destination_creation_preserves_the_other_file_and_removes_staging() {
        let temporary = tempfile::tempdir().unwrap();
        let path = temporary.path().join("private");
        let directory = PrivateDirectory::open(&path).unwrap();
        let guard = directory.try_lock().unwrap();
        let result = guard.write_with_hook("profile.json", b"replacement", None, || {
            guard.write("profile.json", b"external-create", None)
        });
        assert_eq!(result, Err(PlatformError::Changed));
        assert_eq!(
            guard.read("profile.json").unwrap().unwrap(),
            b"external-create"
        );
        assert!(std::fs::read_dir(&path).unwrap().all(|entry| {
            !entry
                .unwrap()
                .file_name()
                .to_string_lossy()
                .starts_with(".write-")
        }));
    }

    #[test]
    fn concurrent_external_edit_is_restored_at_atomic_exchange() {
        let temporary = tempfile::tempdir().unwrap();
        let path = temporary.path().join("private");
        let directory = PrivateDirectory::open(&path).unwrap();
        let guard = directory.try_lock().unwrap();
        guard.write("profile.json", b"original", None).unwrap();
        let result =
            guard.write_with_hook("profile.json", b"replacement", Some(b"original"), || {
                std::fs::write(path.join("profile.json"), b"external-edit").unwrap();
                Ok(())
            });
        assert_eq!(result, Err(PlatformError::Changed));
        assert_eq!(
            guard.read("profile.json").unwrap().unwrap(),
            b"external-edit"
        );
    }
}
