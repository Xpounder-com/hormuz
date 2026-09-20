use crate::{PlatformError, PrivateDirectory};
use std::path::Path;
use std::process::{Child, Command, Stdio};
use std::sync::{Arc, Barrier};
use std::time::{Duration, Instant};

fn child_status(mut child: Child) -> i32 {
    let deadline = Instant::now() + Duration::from_secs(10);
    loop {
        if let Some(status) = child.try_wait().unwrap() {
            return status.code().unwrap();
        }
        if Instant::now() >= deadline {
            child.kill().unwrap();
            let _ = child.wait();
            panic!("synthetic instance worker timed out");
        }
        std::thread::sleep(Duration::from_millis(5));
    }
}

fn worker(path: &Path, mode: &str) -> Child {
    Command::new(std::env::current_exe().unwrap())
        .args(["--exact", "instance_tests::instance_child"])
        .env("HORMUZ_SYNTHETIC_INSTANCE_ROOT", path)
        .env("HORMUZ_SYNTHETIC_INSTANCE_MODE", mode)
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .spawn()
        .unwrap()
}

#[test]
fn instance_child() {
    let Some(path) = std::env::var_os("HORMUZ_SYNTHETIC_INSTANCE_ROOT") else {
        return;
    };
    let directory = PrivateDirectory::open(Path::new(&path)).unwrap();
    if std::env::var("HORMUZ_SYNTHETIC_INSTANCE_MODE").unwrap() == "probe" {
        let blocked = matches!(directory.try_claim_instance(), Err(PlatformError::Busy));
        let refresh_works = directory.try_lock().is_ok();
        std::process::exit(if blocked && refresh_works { 81 } else { 82 });
    }
    let _instance = directory.try_claim_instance().unwrap();
    // Deliberately bypass Drop. Recovery must depend on kernel ownership, not
    // sentinel removal, a stale PID or time-based lock stealing.
    std::process::exit(83);
}

#[test]
fn competing_startups_have_one_owner_and_refresh_remains_independent() {
    let temporary = tempfile::tempdir().unwrap();
    let path = temporary.path().join("private");
    let directory = PrivateDirectory::open(&path).unwrap();
    let start = Arc::new(Barrier::new(8));
    let claimed = Arc::new(Barrier::new(8));
    let mut workers = Vec::new();
    for _ in 0..8 {
        let path = path.clone();
        let start = start.clone();
        let claimed = claimed.clone();
        workers.push(std::thread::spawn(move || {
            let directory = PrivateDirectory::open(&path);
            start.wait();
            let instance = directory.and_then(|directory| directory.try_claim_instance());
            claimed.wait();
            // Report failures only after every worker reaches the second
            // barrier, so a failing assertion cannot strand another worker.
            match instance {
                Ok(instance) => {
                    drop(instance);
                    true
                }
                Err(PlatformError::Busy) => false,
                Err(error) => panic!("instance acquisition failed: {error}"),
            }
        }));
    }
    let owners = workers
        .into_iter()
        .map(|worker| usize::from(worker.join().unwrap()))
        .sum::<usize>();
    assert_eq!(owners, 1);
    let instance = directory.try_claim_instance().unwrap();
    assert_eq!(child_status(worker(&path, "probe")), 81);
    let refresh = directory.try_lock().unwrap();
    assert!(matches!(directory.try_lock(), Err(PlatformError::Busy)));
    drop(instance);
    let instance = directory.try_claim_instance().unwrap();
    assert!(matches!(directory.try_lock(), Err(PlatformError::Busy)));
    drop(refresh);
    directory.try_lock().unwrap();
    drop(instance);
    assert!(path.join("instance.lock").is_file());
}

#[test]
fn process_crash_leaves_a_safe_sentinel_and_releases_ownership() {
    let temporary = tempfile::tempdir().unwrap();
    let path = temporary.path().join("private");
    let directory = PrivateDirectory::open(&path).unwrap();
    assert_eq!(child_status(worker(&path, "crash")), 83);
    assert_eq!(
        std::fs::metadata(path.join("instance.lock")).unwrap().len(),
        0
    );
    let instance = directory.try_claim_instance().unwrap();
    assert!(matches!(
        directory.try_claim_instance(),
        Err(PlatformError::Busy)
    ));
    drop(instance);
    directory.try_claim_instance().unwrap();
}

#[test]
fn instance_sentinel_rejects_nonempty_files_hardlinks_and_directories() {
    let temporary = tempfile::tempdir().unwrap();
    for name in ["nonempty", "hardlink", "directory"] {
        let path = temporary.path().join(name);
        let directory = PrivateDirectory::open(&path).unwrap();
        let sentinel = path.join("instance.lock");
        if name == "directory" {
            std::fs::create_dir(&sentinel).unwrap();
        } else {
            drop(directory.try_claim_instance().unwrap());
            if name == "nonempty" {
                std::fs::write(&sentinel, b"synthetic-unowned-content").unwrap();
            } else {
                std::fs::hard_link(&sentinel, temporary.path().join("other-link")).unwrap();
            }
        }
        let result = directory.try_claim_instance();
        assert!(
            matches!(result, Err(PlatformError::UnsafeStorage)),
            "synthetic {name} sentinel returned {:?}",
            result.err()
        );
        assert!(
            sentinel.exists(),
            "unsafe sentinels are never repaired or deleted"
        );
        if name == "nonempty" {
            assert_eq!(
                std::fs::read(sentinel).unwrap(),
                b"synthetic-unowned-content"
            );
        }
    }
}

#[cfg(target_os = "macos")]
#[test]
fn mac_removed_directory_exhausts_creation_recovery_without_claiming_or_recreating() {
    let temporary = tempfile::tempdir().unwrap();
    let path = temporary.path().join("private");
    let directory = PrivateDirectory::open(&path).unwrap();
    std::fs::remove_dir(&path).unwrap();
    assert!(matches!(
        directory.try_claim_instance(),
        Err(PlatformError::Unavailable)
    ));
    assert!(!path.exists());
}

#[test]
fn malformed_sentinel_is_rejected_before_a_held_kernel_lock_masks_it() {
    let temporary = tempfile::tempdir().unwrap();
    let path = temporary.path().join("private");
    let directory = PrivateDirectory::open(&path).unwrap();
    directory
        .try_lock()
        .unwrap()
        .write("unowned-sentinel", b"preserve", None)
        .unwrap();
    let sentinel = path.join("instance.lock");
    std::fs::rename(path.join("unowned-sentinel"), &sentinel).unwrap();
    let held = std::fs::OpenOptions::new()
        .read(true)
        .write(true)
        .open(&sentinel)
        .unwrap();
    held.try_lock().unwrap();
    assert!(matches!(
        directory.try_claim_instance(),
        Err(PlatformError::UnsafeStorage)
    ));
    drop(held);
    assert_eq!(std::fs::read(sentinel).unwrap(), b"preserve");
}

#[cfg(target_os = "macos")]
#[test]
fn mac_instance_rejects_symlinks_public_modes_and_extended_acls() {
    use std::os::unix::fs::{symlink, PermissionsExt};
    let temporary = tempfile::tempdir().unwrap();
    let path = temporary.path().join("private");
    let directory = PrivateDirectory::open(&path).unwrap();
    let sentinel = path.join("instance.lock");
    let refresh = directory.try_lock().unwrap();
    refresh.write("unowned", b"preserve", None).unwrap();
    symlink(path.join("unowned"), &sentinel).unwrap();
    assert!(matches!(
        directory.try_claim_instance(),
        Err(PlatformError::UnsafeStorage)
    ));
    assert_eq!(refresh.read("unowned").unwrap().unwrap(), b"preserve");
    std::fs::remove_file(&sentinel).unwrap();
    drop(directory.try_claim_instance().unwrap());
    std::fs::set_permissions(&sentinel, std::fs::Permissions::from_mode(0o644)).unwrap();
    assert!(matches!(
        directory.try_claim_instance(),
        Err(PlatformError::UnsafeStorage)
    ));
    std::fs::set_permissions(&sentinel, std::fs::Permissions::from_mode(0o600)).unwrap();
    assert!(Command::new("/bin/chmod")
        .args(["+a", "everyone allow read"])
        .arg(&sentinel)
        .status()
        .unwrap()
        .success());
    assert!(matches!(
        directory.try_claim_instance(),
        Err(PlatformError::UnsafeStorage)
    ));
}

#[cfg(target_os = "macos")]
#[test]
fn mac_changed_sentinel_is_rejected_after_acquisition_without_deleting_either_file() {
    use std::os::unix::fs::OpenOptionsExt;
    let temporary = tempfile::tempdir().unwrap();
    let path = temporary.path().join("private");
    let directory = PrivateDirectory::open(&path).unwrap();
    let sentinel = path.join("instance.lock");
    let displaced = path.join("displaced");
    let result = directory.claim_instance_with_hook(|| {
        std::fs::rename(&sentinel, &displaced).unwrap();
        std::fs::OpenOptions::new()
            .create_new(true)
            .write(true)
            .mode(0o600)
            .open(&sentinel)
            .unwrap();
    });
    assert!(matches!(result, Err(PlatformError::UnsafeStorage)));
    assert!(sentinel.exists());
    assert!(displaced.exists());
    directory.try_claim_instance().unwrap();
}

#[cfg(windows)]
#[test]
fn windows_instance_handle_prevents_sentinel_replacement() {
    let temporary = tempfile::tempdir().unwrap();
    let path = temporary.path().join("private");
    let directory = PrivateDirectory::open(&path).unwrap();
    let instance = directory.try_claim_instance().unwrap();
    let sentinel = path.join("instance.lock");
    assert!(std::fs::rename(&sentinel, path.join("displaced")).is_err());
    assert!(std::fs::remove_file(&sentinel).is_err());
    drop(instance);
    directory.try_claim_instance().unwrap();
}

#[cfg(target_os = "macos")]
#[test]
fn dropping_owner_releases_lease_even_while_a_fork_inherits_the_descriptor() {
    let temporary = tempfile::tempdir().unwrap();
    let directory = PrivateDirectory::open(&temporary.path().join("private")).unwrap();
    let owner = directory.try_claim_instance().unwrap();
    let mut control = [0; 2];
    assert_eq!(unsafe { libc::pipe(control.as_mut_ptr()) }, 0);
    let child = unsafe { libc::fork() };
    if child == 0 {
        // After fork in a threaded test process, use only async-signal-safe
        // syscalls. Retain the inherited lock without touching any Rust state.
        unsafe {
            libc::close(control[1]);
            let mut byte = 0_u8;
            libc::read(control[0], (&mut byte as *mut u8).cast(), 1);
            libc::_exit(0);
        }
    }
    if child < 0 {
        unsafe {
            libc::close(control[0]);
            libc::close(control[1]);
        }
        panic!("synthetic fork failed");
    }
    unsafe {
        libc::close(control[0]);
    }
    drop(owner);
    let replacement = directory.try_claim_instance();
    // Always release/reap the owned child before asserting the regression.
    unsafe {
        let byte = 0_u8;
        libc::write(control[1], (&byte as *const u8).cast(), 1);
        libc::close(control[1]);
        let mut status = 0;
        assert_eq!(libc::waitpid(child, &mut status, 0), child);
        assert_eq!(status, 0);
    }
    assert!(
        replacement.is_ok(),
        "inherited descriptor retained the lease"
    );
}
