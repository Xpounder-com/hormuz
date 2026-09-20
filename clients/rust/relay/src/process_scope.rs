//! A launched client and its in-scope descendants share one lifetime. The
//! platform primitives live here so relay and credential paths remain safe Rust.

use std::io;
use std::process::{Child, Command, ExitStatus};

#[cfg(windows)]
use std::os::windows::io::{AsRawHandle, FromRawHandle, OwnedHandle};

/// Closing the panel does not own or drop this handle. The dedicated launcher
/// keeps it until client exit; dropping it cancels its supervised scope.
pub(crate) struct OwnedClient {
    child: Option<Child>,
    #[cfg(unix)]
    group: Option<i32>,
    #[cfg(windows)]
    job: Option<OwnedHandle>,
}

impl OwnedClient {
    pub(crate) fn spawn(command: &mut Command) -> io::Result<Self> {
        #[cfg(unix)]
        {
            use std::os::unix::process::CommandExt;
            command.process_group(0);
            let mut child = command.spawn()?;
            let group = match i32::try_from(child.id()) {
                Ok(group) if group > 0 => group,
                _ => {
                    let _ = child.kill();
                    let _ = child.wait();
                    return Err(io::Error::other("client process ID is out of range"));
                }
            };
            Ok(Self {
                child: Some(child),
                group: Some(group),
            })
        }

        #[cfg(windows)]
        {
            use std::os::windows::process::CommandExt;
            use windows_sys::Win32::System::Threading::CREATE_SUSPENDED;

            // The initial thread cannot create a descendant before the job
            // owns it. Children then join the job by default.
            let job = create_kill_on_close_job()?;
            command.creation_flags(CREATE_SUSPENDED);
            let mut child = command.spawn()?;
            let process = child.as_raw_handle();
            let assigned = unsafe {
                windows_sys::Win32::System::JobObjects::AssignProcessToJobObject(
                    job.as_raw_handle(),
                    process,
                )
            };
            if assigned == 0 {
                let error = io::Error::last_os_error();
                let _ = child.kill();
                let _ = child.wait();
                return Err(error);
            }
            if let Err(error) = resume_initial_thread(child.id()) {
                // Assignment succeeded, so closing the job also kills the
                // suspended direct child before it can run.
                drop(job);
                let _ = child.wait();
                return Err(error);
            }
            Ok(Self {
                child: Some(child),
                job: Some(job),
            })
        }
    }

    pub(crate) fn spawn_interactive(command: &mut Command) -> io::Result<Self> {
        #[cfg(unix)]
        if unsafe { libc::isatty(libc::STDIN_FILENO) } == 1 {
            // Shell job control keeps the terminal's foreground/background
            // relationship with the launcher. A separate PGID would break
            // Ctrl-Z and background->fg for an inherited interactive TTY.
            return Ok(Self {
                child: Some(command.spawn()?),
                group: None,
            });
        }
        Self::spawn(command)
    }

    pub(crate) fn wait_status(&mut self) -> io::Result<ExitStatus> {
        #[cfg(unix)]
        {
            if self.group.is_none() {
                let status = self.child_mut()?.wait()?;
                self.child = None;
                return Ok(status);
            }
            // WNOWAIT leaves the group leader as a zombie until after the
            // group is killed, preventing process-group ID reuse in between.
            observe_exit(self.child_id()?, false)?;
            self.reap_exited_unix_child()
        }

        #[cfg(windows)]
        {
            let status = self.child_mut()?.wait()?;
            self.stop_scope()?;
            self.child = None;
            Ok(status)
        }
    }

    pub(crate) fn try_wait_status(&mut self) -> io::Result<Option<ExitStatus>> {
        #[cfg(unix)]
        {
            if self.group.is_none() {
                let status = self.child_mut()?.try_wait()?;
                if status.is_some() {
                    self.child = None;
                }
                return Ok(status);
            }
            if !observe_exit(self.child_id()?, true)? {
                return Ok(None);
            }
            self.reap_exited_unix_child().map(Some)
        }

        #[cfg(windows)]
        {
            let Some(status) = self.child_mut()?.try_wait()? else {
                return Ok(None);
            };
            self.stop_scope()?;
            self.child = None;
            Ok(Some(status))
        }
    }

    fn child_mut(&mut self) -> io::Result<&mut Child> {
        self.child
            .as_mut()
            .ok_or_else(|| io::Error::other("client already reaped"))
    }

    #[cfg(unix)]
    fn child_id(&self) -> io::Result<i32> {
        self.group
            .ok_or_else(|| io::Error::other("client process group already stopped"))
    }

    #[cfg(unix)]
    fn reap_exited_unix_child(&mut self) -> io::Result<ExitStatus> {
        #[cfg(target_vendor = "apple")]
        let group = self.child_id()?;
        let stop = self.stop_scope();
        let status = self.child_mut()?.wait()?;
        self.child = None;
        // Do not retry a group signal after the leader has been reaped: its
        // numeric ID may then be reused by an unrelated process.
        self.group = None;
        #[cfg(not(target_vendor = "apple"))]
        stop?;
        #[cfg(target_vendor = "apple")]
        if let Err(error) = stop {
            // macOS returns EPERM when the only remaining group member is the
            // zombie leader. After reaping that leader, a read-only probe
            // distinguishes this case from a live, unkillable descendant.
            if error.raw_os_error() == Some(libc::EPERM)
                && unsafe { libc::kill(-group, 0) } == -1
                && io::Error::last_os_error().raw_os_error() == Some(libc::ESRCH)
            {
                return Ok(status);
            }
            return Err(error);
        }
        Ok(status)
    }

    fn stop_scope(&mut self) -> io::Result<()> {
        #[cfg(unix)]
        let group_result = if let Some(group) = self.group {
            // The child was placed in a new group at spawn. Never signal the
            // launcher's own group or an arbitrary PID from user input.
            if unsafe { libc::kill(-group, libc::SIGKILL) } != 0 {
                let error = io::Error::last_os_error();
                if error.raw_os_error() != Some(libc::ESRCH) {
                    Err(error)
                } else {
                    self.group = None;
                    Ok(())
                }
            } else {
                self.group = None;
                Ok(())
            }
        } else {
            Ok(())
        };

        #[cfg(unix)]
        group_result?;

        #[cfg(windows)]
        if let Some(job) = self.job.take() {
            // KILL_ON_JOB_CLOSE is the final backstop if explicit termination
            // fails or the launcher exits while the job still has descendants.
            let result = unsafe {
                windows_sys::Win32::System::JobObjects::TerminateJobObject(job.as_raw_handle(), 1)
            };
            let error = io::Error::last_os_error();
            drop(job);
            if result == 0 {
                return Err(error);
            }
        }
        Ok(())
    }
}

impl Drop for OwnedClient {
    fn drop(&mut self) {
        let _ = self.stop_scope();
        if let Some(mut child) = self.child.take() {
            let _ = child.kill();
            let _ = child.wait();
        }
    }
}

#[cfg(unix)]
fn observe_exit(pid: i32, nonblocking: bool) -> io::Result<bool> {
    let mut flags = libc::WEXITED | libc::WNOWAIT;
    if nonblocking {
        flags |= libc::WNOHANG;
    }
    loop {
        let mut info: libc::siginfo_t = unsafe { std::mem::zeroed() };
        let result = unsafe { libc::waitid(libc::P_PID, pid as libc::id_t, &mut info, flags) };
        if result == 0 {
            #[cfg(target_vendor = "apple")]
            let observed_pid = info.si_pid;
            #[cfg(not(target_vendor = "apple"))]
            let observed_pid = unsafe { info.si_pid() };
            return Ok(observed_pid == pid);
        }
        let error = io::Error::last_os_error();
        if error.kind() != io::ErrorKind::Interrupted {
            return Err(error);
        }
    }
}

#[cfg(windows)]
fn create_kill_on_close_job() -> io::Result<OwnedHandle> {
    use windows_sys::Win32::System::JobObjects::{
        CreateJobObjectW, JobObjectExtendedLimitInformation, SetInformationJobObject,
        JOBOBJECT_EXTENDED_LIMIT_INFORMATION, JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
    };

    let raw = unsafe { CreateJobObjectW(std::ptr::null(), std::ptr::null()) };
    if raw.is_null() {
        return Err(io::Error::last_os_error());
    }
    let job = unsafe { OwnedHandle::from_raw_handle(raw) };
    let mut limits = JOBOBJECT_EXTENDED_LIMIT_INFORMATION::default();
    limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
    let result = unsafe {
        SetInformationJobObject(
            job.as_raw_handle(),
            JobObjectExtendedLimitInformation,
            (&raw const limits).cast(),
            std::mem::size_of_val(&limits) as u32,
        )
    };
    if result == 0 {
        return Err(io::Error::last_os_error());
    }
    Ok(job)
}

#[cfg(windows)]
fn resume_initial_thread(pid: u32) -> io::Result<()> {
    use windows_sys::Win32::Foundation::INVALID_HANDLE_VALUE;
    use windows_sys::Win32::System::Diagnostics::ToolHelp::{
        CreateToolhelp32Snapshot, Thread32First, Thread32Next, TH32CS_SNAPTHREAD, THREADENTRY32,
    };
    use windows_sys::Win32::System::Threading::{OpenThread, ResumeThread, THREAD_SUSPEND_RESUME};

    let raw = unsafe { CreateToolhelp32Snapshot(TH32CS_SNAPTHREAD, 0) };
    if raw == INVALID_HANDLE_VALUE {
        return Err(io::Error::last_os_error());
    }
    let snapshot = unsafe { OwnedHandle::from_raw_handle(raw) };
    let mut entry = THREADENTRY32 {
        dwSize: std::mem::size_of::<THREADENTRY32>() as u32,
        ..THREADENTRY32::default()
    };
    let mut found = unsafe { Thread32First(snapshot.as_raw_handle(), &mut entry) } != 0;
    while found {
        if entry.th32OwnerProcessID == pid {
            let raw = unsafe { OpenThread(THREAD_SUSPEND_RESUME, 0, entry.th32ThreadID) };
            if raw.is_null() {
                return Err(io::Error::last_os_error());
            }
            let thread = unsafe { OwnedHandle::from_raw_handle(raw) };
            let previous = unsafe { ResumeThread(thread.as_raw_handle()) };
            if previous != 1 {
                return Err(io::Error::other(
                    "client initial thread could not be resumed",
                ));
            }
            return Ok(());
        }
        found = unsafe { Thread32Next(snapshot.as_raw_handle(), &mut entry) } != 0;
    }
    Err(io::Error::new(
        io::ErrorKind::NotFound,
        "client initial thread not found",
    ))
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;
    use std::path::{Path, PathBuf};
    use std::process::Stdio;
    use std::thread;
    use std::time::{Duration, Instant};

    const WORKER_TEST: &str = "process_scope::tests::worker";

    fn worker_command(mode: &str, ready: &Path, escaped: &Path) -> Command {
        let mut command = Command::new(std::env::current_exe().unwrap());
        command
            .args(["--exact", WORKER_TEST])
            .env("HORMUZ_RELAY_SCOPE_TEST_MODE", mode)
            .env("HORMUZ_RELAY_SCOPE_TEST_READY", ready)
            .env("HORMUZ_RELAY_SCOPE_TEST_ESCAPED", escaped)
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null());
        command
    }

    fn wait_for(path: &Path) {
        let deadline = Instant::now() + Duration::from_secs(5);
        while !path.exists() && Instant::now() < deadline {
            thread::sleep(Duration::from_millis(10));
        }
        assert!(path.exists(), "fake client did not start");
    }

    fn assert_descendant_stopped(path: &Path) {
        // A detached helper would write this marker after its parent exited.
        thread::sleep(Duration::from_millis(800));
        assert!(!path.exists(), "client descendant survived scope cleanup");
    }

    #[test]
    fn directly_exiting_child_is_reaped() {
        let temporary = tempfile::tempdir().unwrap();
        let mut command = worker_command(
            "direct_exit",
            &temporary.path().join("ready"),
            &temporary.path().join("escaped"),
        );
        let mut child = OwnedClient::spawn(&mut command).unwrap();
        let status = child.wait_status().unwrap();
        assert!(status.success());
    }

    #[test]
    fn normal_exit_stops_descendants() {
        let temporary = tempfile::tempdir().unwrap();
        let ready = temporary.path().join("ready");
        let escaped = temporary.path().join("escaped");
        let mut child = OwnedClient::spawn(&mut worker_command("exit", &ready, &escaped)).unwrap();
        wait_for(&ready);
        assert!(child.wait_status().unwrap().success());
        assert_descendant_stopped(&escaped);
    }

    #[test]
    fn failed_exit_stops_descendants() {
        let temporary = tempfile::tempdir().unwrap();
        let ready = temporary.path().join("ready");
        let escaped = temporary.path().join("escaped");
        let mut child = OwnedClient::spawn(&mut worker_command("fail", &ready, &escaped)).unwrap();
        wait_for(&ready);
        assert_eq!(child.wait_status().unwrap().code(), Some(37));
        assert_descendant_stopped(&escaped);
    }

    #[test]
    fn cancellation_stops_descendants_and_reaps_direct_child() {
        let temporary = tempfile::tempdir().unwrap();
        let ready = temporary.path().join("ready");
        let escaped = temporary.path().join("escaped");
        let child = OwnedClient::spawn(&mut worker_command("block", &ready, &escaped)).unwrap();
        wait_for(&ready);
        let started = Instant::now();
        drop(child);
        assert!(started.elapsed() < Duration::from_secs(5));
        assert_descendant_stopped(&escaped);
    }

    #[test]
    fn version_style_poll_stops_descendants() {
        let temporary = tempfile::tempdir().unwrap();
        let ready = temporary.path().join("ready");
        let escaped = temporary.path().join("escaped");
        let mut child = OwnedClient::spawn(&mut worker_command("exit", &ready, &escaped)).unwrap();
        wait_for(&ready);
        let deadline = Instant::now() + Duration::from_secs(5);
        loop {
            if let Some(status) = child.try_wait_status().unwrap() {
                assert!(status.success());
                break;
            }
            assert!(Instant::now() < deadline, "fake version check did not exit");
            thread::sleep(Duration::from_millis(10));
        }
        assert_descendant_stopped(&escaped);
    }

    #[cfg(unix)]
    #[test]
    fn tty_launch_preserves_shell_process_group() {
        if unsafe { libc::isatty(libc::STDIN_FILENO) } != 1 {
            assert!(
                std::env::var_os("HORMUZ_RELAY_REQUIRE_TEST_TTY").is_none(),
                "test runner did not provide a terminal"
            );
            return;
        }
        let original = unsafe { libc::tcgetpgrp(libc::STDIN_FILENO) };
        let temporary = tempfile::tempdir().unwrap();
        let mut command = worker_command(
            "terminal",
            &temporary.path().join("ready"),
            &temporary.path().join("escaped"),
        );
        command.stdin(Stdio::inherit());
        let mut child = OwnedClient::spawn_interactive(&mut command).unwrap();
        assert!(child.group.is_none());
        assert!(child.wait_status().unwrap().success());
        assert_eq!(unsafe { libc::tcgetpgrp(libc::STDIN_FILENO) }, original);
    }

    #[cfg(unix)]
    #[test]
    fn rapidly_exiting_tty_client_preserves_exit_status() {
        if unsafe { libc::isatty(libc::STDIN_FILENO) } != 1 {
            assert!(std::env::var_os("HORMUZ_RELAY_REQUIRE_TEST_TTY").is_none());
            return;
        }
        let original = unsafe { libc::tcgetpgrp(libc::STDIN_FILENO) };
        let temporary = tempfile::tempdir().unwrap();
        let mut command = worker_command(
            "direct_exit",
            &temporary.path().join("ready"),
            &temporary.path().join("escaped"),
        );
        command.stdin(Stdio::inherit());
        let mut child = OwnedClient::spawn_interactive(&mut command).unwrap();
        assert!(child.group.is_none());
        assert_eq!(child.wait_status().unwrap().code(), Some(0));
        assert_eq!(unsafe { libc::tcgetpgrp(libc::STDIN_FILENO) }, original);
    }

    // The worker must deliberately leave its descendant running to prove
    // whether the outer launcher's process scope cleans it up.
    #[allow(clippy::zombie_processes)]
    #[test]
    fn worker() {
        let Ok(mode) = std::env::var("HORMUZ_RELAY_SCOPE_TEST_MODE") else {
            return;
        };
        let ready = PathBuf::from(std::env::var_os("HORMUZ_RELAY_SCOPE_TEST_READY").unwrap());
        let escaped = PathBuf::from(std::env::var_os("HORMUZ_RELAY_SCOPE_TEST_ESCAPED").unwrap());
        if mode == "direct_exit" {
            return;
        }
        #[cfg(unix)]
        if mode == "terminal" {
            assert_eq!(
                unsafe { libc::tcgetpgrp(libc::STDIN_FILENO) },
                unsafe { libc::getpgrp() },
                "interactive fake client did not receive terminal foreground"
            );
            return;
        }
        if mode == "descendant" {
            thread::sleep(Duration::from_millis(500));
            fs::write(escaped, b"survived").unwrap();
            return;
        }
        let mut descendant = worker_command("descendant", &ready, &escaped);
        descendant.spawn().unwrap();
        fs::write(ready, b"started").unwrap();
        if mode == "block" {
            thread::sleep(Duration::from_secs(30));
        }
        if mode == "fail" {
            std::process::exit(37);
        }
    }
}
