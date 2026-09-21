//! A Linux-only direct-child guard for abrupt launcher death. The kernel
//! clears `PR_SET_PDEATHSIG` on fork, so this is not descendant containment.
//! The signal follows the thread that calls `spawn`, not the whole launcher;
//! current client and version-probe paths wait on that same thread.

use std::io;
use std::os::unix::process::CommandExt;
use std::process::{Child, Command};

pub(crate) fn spawn(command: &mut Command) -> io::Result<Child> {
    spawn_with_parent(command, std::process::id() as libc::pid_t)
}

fn spawn_with_parent(command: &mut Command, expected_parent: libc::pid_t) -> io::Result<Child> {
    // SAFETY: Command executes this closure in the forked child before exec.
    // It captures only a pid_t, makes the prctl/getppid syscalls, and returns
    // errno-backed errors; it takes no locks and performs no allocation.
    unsafe {
        command.pre_exec(move || {
            if libc::prctl(
                libc::PR_SET_PDEATHSIG,
                libc::SIGKILL as libc::c_ulong,
                0 as libc::c_ulong,
                0 as libc::c_ulong,
                0 as libc::c_ulong,
            ) == -1
            {
                return Err(io::Error::last_os_error());
            }
            // The parent may have died between fork and prctl. Reject that
            // child before it can exec an AI client or version command.
            if libc::getppid() != expected_parent {
                return Err(io::Error::from_raw_os_error(libc::ESRCH));
            }
            Ok(())
        });
    }
    command.spawn()
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;
    use std::net::{SocketAddr, TcpListener, TcpStream};
    use std::path::{Path, PathBuf};
    use std::process::Stdio;
    use std::thread;
    use std::time::{Duration, Instant};

    const TEST_NAME: &str =
        "process_scope_linux::tests::launcher_death_closes_direct_client_listener";
    const STAGE: &str = "HORMUZ_LINUX_PARENT_DEATH_TEST_STAGE";
    const READY: &str = "HORMUZ_LINUX_PARENT_DEATH_TEST_READY";

    #[test]
    fn rejects_child_when_parent_disappeared_before_exec() {
        let mut command = Command::new("/bin/true");
        let wrong_parent = std::process::id() as libc::pid_t + 1;
        let error = spawn_with_parent(&mut command, wrong_parent).unwrap_err();
        assert_eq!(error.raw_os_error(), Some(libc::ESRCH));
    }

    #[test]
    fn guarded_normal_child_exits_cleanly() {
        let mut command = Command::new("/bin/true");
        assert!(spawn(&mut command).unwrap().wait().unwrap().success());
    }

    fn wait_for_ready(
        path: &Path,
        deadline: Instant,
    ) -> Option<(SocketAddr, libc::pid_t, libc::pid_t)> {
        while Instant::now() < deadline {
            if let Ok(value) = fs::read_to_string(path) {
                let mut parts = value.split_whitespace();
                let address = parts.next()?.parse().ok()?;
                let pid = parts.next()?.parse().ok()?;
                let group = parts.next()?.parse().ok()?;
                return Some((address, pid, group));
            }
            thread::sleep(Duration::from_millis(10));
        }
        None
    }

    fn fixture_command(stage: &str, ready: &Path) -> Command {
        let mut command = Command::new(std::env::current_exe().unwrap());
        let output = fs::File::create(ready.with_extension(format!("{stage}.log"))).unwrap();
        command
            .args(["--exact", TEST_NAME, "--nocapture"])
            .env_clear()
            .env(STAGE, stage)
            .env(READY, ready)
            .stdin(Stdio::null())
            .stdout(Stdio::from(output.try_clone().unwrap()))
            .stderr(Stdio::from(output));
        command
    }

    fn fixture_log(ready: &Path, stage: &str) -> String {
        fs::read_to_string(ready.with_extension(format!("{stage}.log")))
            .unwrap_or_default()
            .chars()
            .take(2048)
            .collect()
    }

    struct Supervisor(Child);
    impl Drop for Supervisor {
        fn drop(&mut self) {
            let _ = self.0.kill();
            let _ = self.0.wait();
        }
    }

    #[test]
    fn launcher_death_closes_direct_client_listener() {
        let stage = std::env::var(STAGE).ok();
        let ready = std::env::var_os(READY).map(PathBuf::from);
        if stage.as_deref() == Some("client") {
            let ready = ready.unwrap();
            // libtest runs this function on a new thread, whose parent-death
            // setting is cleared on clone. Prove the process lifetime through
            // its owned listener instead of reading this worker's setting.
            let listener = TcpListener::bind("127.0.0.1:0").unwrap();
            // SAFETY: getpgrp reads the process group of this fixture only.
            let group = unsafe { libc::getpgrp() };
            let value = format!(
                "{} {} {group}",
                listener.local_addr().unwrap(),
                std::process::id()
            );
            let temporary = ready.with_extension("tmp");
            fs::write(&temporary, value).unwrap();
            fs::rename(temporary, ready).unwrap();
            // Bound a failed fixture's lifetime even if the parent-death guard
            // regresses and the outer test is interrupted.
            thread::sleep(Duration::from_secs(20));
            return;
        }
        if stage.as_deref() == Some("supervisor") {
            let ready = ready.unwrap();
            let mut command = fixture_command("client", &ready);
            // A direct client can move to another process group without
            // escaping its own parent-death signal.
            command.process_group(0);
            let mut child = spawn(&mut command).unwrap();
            let _ = child.wait();
            return;
        }

        let temporary = tempfile::tempdir().unwrap();
        let ready = temporary.path().join("listener");
        let mut supervisor = Supervisor(fixture_command("supervisor", &ready).spawn().unwrap());
        let (address, child_pid, group) =
            wait_for_ready(&ready, Instant::now() + Duration::from_secs(5))
                .unwrap_or_else(|| {
                    panic!(
                        "synthetic client did not open its listener; supervisor={:?}; supervisor log={}; client log={}",
                        supervisor.0.try_wait().unwrap(),
                        fixture_log(&ready, "supervisor"),
                        fixture_log(&ready, "client")
                    )
                });
        assert_eq!(
            group, child_pid,
            "fixture did not enter its own process group"
        );
        assert!(TcpStream::connect_timeout(&address, Duration::from_millis(250)).is_ok());
        supervisor.0.kill().unwrap();
        supervisor.0.wait().unwrap();
        let deadline = Instant::now() + Duration::from_secs(5);
        let mut closed = false;
        while Instant::now() < deadline {
            if TcpStream::connect_timeout(&address, Duration::from_millis(100)).is_err() {
                closed = true;
                break;
            }
            thread::sleep(Duration::from_millis(20));
        }
        if !closed {
            // SAFETY: child_pid came from this controlled fixture and its
            // listener is still open, so it is still the process to clean up.
            unsafe { libc::kill(child_pid, libc::SIGKILL) };
        }
        assert!(closed, "client listener survived abrupt launcher death");
    }
}
