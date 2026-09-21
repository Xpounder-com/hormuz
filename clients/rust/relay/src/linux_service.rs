//! Require an on-demand user service to own a Linux client process tree.
//!
//! A process group or `PR_SET_PDEATHSIG` cannot contain a grandchild which
//! forks or starts a new session. A systemd service with this launcher as its
//! main process owns the whole cgroup after normal exit or abrupt death.

use std::ffi::OsStr;
use std::io::{self, Read, Seek, SeekFrom};
use std::process::{Command, Stdio};
use std::thread;
use std::time::{Duration, Instant};

const SHOW_BUDGET: Duration = Duration::from_secs(3);
const MAX_SHOW_BYTES: u64 = 4096;

pub(crate) fn require_user_service() -> io::Result<()> {
    let membership = std::fs::read_to_string("/proc/self/cgroup")?;
    let path = unified_path(&membership).ok_or_else(unavailable)?;
    let unit = service_unit(path).ok_or_else(unavailable)?;
    let properties = show_unit(unit)?;
    if valid_service(&properties, path, std::process::id()) {
        Ok(())
    } else {
        Err(unavailable())
    }
}

fn unavailable() -> io::Error {
    io::Error::new(
        io::ErrorKind::PermissionDenied,
        "a verified on-demand user systemd service is required",
    )
}

fn unified_path(membership: &str) -> Option<&str> {
    let mut paths = membership.lines().filter_map(|line| line.strip_prefix("0::"));
    let path = paths.next()?;
    if path.starts_with('/') && paths.next().is_none() {
        Some(path)
    } else {
        None
    }
}

fn service_unit(path: &str) -> Option<&str> {
    let unit = path.rsplit('/').find(|part| part.ends_with(".service"))?;
    if unit.is_empty() || unit.contains('\0') {
        None
    } else {
        Some(unit)
    }
}

fn systemctl_environment(name: &OsStr) -> bool {
    matches!(
        name.to_str(),
        Some("XDG_RUNTIME_DIR" | "DBUS_SESSION_BUS_ADDRESS" | "HOME" | "USER" | "LOGNAME")
    )
}

fn show_unit(unit: &str) -> io::Result<String> {
    // This is a root-owned program path, never a command resolved from an
    // untrusted PATH. Captured output and time are bounded before any client
    // version probe or relay listener starts.
    let mut stdout = tempfile::tempfile()?;
    let mut command = Command::new("/usr/bin/systemctl");
    command
        .args([
            "--user",
            "--no-pager",
            "show",
            unit,
            "--property=ControlGroup,MainPID,ExitType,KillMode,KillSignal,Transient,ActiveState",
        ])
        .env_clear()
        .envs(std::env::vars_os().filter(|(name, _)| systemctl_environment(name)))
        .stdin(Stdio::null())
        .stdout(Stdio::from(stdout.try_clone()?))
        .stderr(Stdio::null());
    let mut child = command.spawn()?;
    let deadline = Instant::now() + SHOW_BUDGET;
    let status = loop {
        match child.try_wait() {
            Ok(Some(status)) => break status,
            Ok(None) if Instant::now() >= deadline => {
                let _ = child.kill();
                let _ = child.wait();
                return Err(io::Error::new(io::ErrorKind::TimedOut, "systemctl show timed out"));
            }
            Ok(None) => thread::sleep(Duration::from_millis(10)),
            Err(error) => {
                let _ = child.kill();
                let _ = child.wait();
                return Err(error);
            }
        }
    };
    if !status.success() {
        return Err(unavailable());
    }
    stdout.seek(SeekFrom::Start(0))?;
    let mut bytes = Vec::new();
    stdout.take(MAX_SHOW_BYTES + 1).read_to_end(&mut bytes)?;
    if bytes.len() > MAX_SHOW_BYTES as usize {
        return Err(unavailable());
    }
    String::from_utf8(bytes).map_err(|_| unavailable())
}

fn property<'a>(output: &'a str, name: &str) -> Option<&'a str> {
    let mut values = output.lines().filter_map(|line| {
        let (key, value) = line.split_once('=')?;
        (key == name).then_some(value)
    });
    let value = values.next()?;
    values.next().is_none().then_some(value)
}

fn valid_service(output: &str, path: &str, pid: u32) -> bool {
    property(output, "ControlGroup") == Some(path)
        && property(output, "MainPID").and_then(|value| value.parse::<u32>().ok()) == Some(pid)
        && property(output, "ExitType") == Some("main")
        && property(output, "KillMode") == Some("control-group")
        && matches!(property(output, "KillSignal"), Some("9" | "SIGKILL"))
        && property(output, "Transient") == Some("yes")
        && property(output, "ActiveState") == Some("active")
}

#[cfg(test)]
mod tests {
    use super::*;

    const GOOD: &str = "ControlGroup=/user.slice/user-1000.slice/user@1000.service/app.slice/hormuz-test.service\nMainPID=123\nExitType=main\nKillMode=control-group\nKillSignal=9\nTransient=yes\nActiveState=active\n";
    const PATH: &str = "/user.slice/user-1000.slice/user@1000.service/app.slice/hormuz-test.service";

    #[test]
    fn parses_only_a_unified_service_membership() {
        assert_eq!(unified_path(&format!("0::{PATH}\n")), Some(PATH));
        assert_eq!(unified_path("0::/\n"), Some("/"));
        assert_eq!(unified_path(&format!("0::{PATH}\n0::{PATH}\n")), None);
        assert_eq!(service_unit(PATH), Some("hormuz-test.service"));
        assert_eq!(service_unit("/user.slice/test.scope"), None);
    }

    #[test]
    fn requires_the_main_pid_and_tree_kill_properties() {
        assert!(valid_service(GOOD, PATH, 123));
        assert!(!valid_service(GOOD, PATH, 124));
        for (old, new) in [
            ("ExitType=main", "ExitType=cgroup"),
            ("KillMode=control-group", "KillMode=process"),
            ("KillSignal=9", "KillSignal=15"),
            ("Transient=yes", "Transient=no"),
            ("ActiveState=active", "ActiveState=inactive"),
        ] {
            assert!(!valid_service(&GOOD.replace(old, new), PATH, 123));
        }
        assert!(!valid_service(&format!("{GOOD}KillMode=control-group\n"), PATH, 123));
        assert!(!valid_service(GOOD, "/another.service", 123));
    }

    #[test]
    fn does_not_pass_provider_environment_to_systemctl() {
        assert!(systemctl_environment(OsStr::new("XDG_RUNTIME_DIR")));
        assert!(!systemctl_environment(OsStr::new("OPENAI_API_KEY")));
        assert!(!systemctl_environment(OsStr::new("ANTHROPIC_AUTH_TOKEN")));
    }
}

#[cfg(test)]
#[allow(unsafe_code)]
mod host_tests {
    use super::*;
    use std::fs;
    use std::net::{SocketAddr, TcpListener, TcpStream};
    use std::path::{Path, PathBuf};
    use std::process::Child;
    use std::time::{SystemTime, UNIX_EPOCH};

    const TEST_NAME: &str = "linux_service::host_tests::user_service_contains_detached_listener";
    const STAGE: &str = "HORMUZ_SERVICE_TEST_STAGE";
    const ROOT: &str = "HORMUZ_SERVICE_TEST_ROOT";
    const MODE: &str = "HORMUZ_SERVICE_TEST_MODE";

    fn fixture(stage: &str, root: &Path, mode: &str) -> Command {
        let mut command = Command::new(std::env::current_exe().unwrap());
        command
            .args(["--exact", TEST_NAME, "--nocapture"])
            .env_clear()
            .env(STAGE, stage)
            .env(ROOT, root)
            .env(MODE, mode)
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null());
        command
    }

    fn launcher(root: &Path, mode: &str) {
        let deadline = Instant::now() + Duration::from_secs(3);
        while require_user_service().is_err() && Instant::now() < deadline {
            thread::sleep(Duration::from_millis(10));
        }
        require_user_service().unwrap();
        fs::write(root.join("launcher-pid"), std::process::id().to_string()).unwrap();
        assert!(fixture("direct", root, mode).spawn().unwrap().wait().unwrap().success());
        let deadline = Instant::now() + Duration::from_secs(20);
        while Instant::now() < deadline {
            if mode == "normal" && root.join("release").exists() {
                return;
            }
            thread::sleep(Duration::from_millis(10));
        }
        panic!("synthetic launcher was not stopped before its fixture deadline");
    }

    fn direct(root: &Path, mode: &str) {
        // util-linux setsid makes the grandchild independent of its parent's
        // process group and session. It still inherits the cgroup at fork.
        let mut command = Command::new("/usr/bin/setsid");
        command
            .arg(std::env::current_exe().unwrap())
            .args(["--exact", TEST_NAME, "--nocapture"])
            .env_clear()
            .env(STAGE, "grandchild")
            .env(ROOT, root)
            .env(MODE, mode)
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null());
        command.spawn().unwrap();
    }

    fn grandchild(root: &Path) {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let membership = fs::read_to_string("/proc/self/cgroup").unwrap();
        let cgroup = unified_path(&membership).unwrap();
        let stat = fs::read_to_string("/proc/self/stat").unwrap();
        let fields = stat.rsplit_once(") ").unwrap().1;
        let session: u32 = fields.split_whitespace().nth(3).unwrap().parse().unwrap();
        let ready = format!(
            "{} {} {session} {cgroup}",
            listener.local_addr().unwrap(),
            std::process::id()
        );
        let temporary = root.join("ready.tmp");
        fs::write(&temporary, ready).unwrap();
        fs::rename(temporary, root.join("ready")).unwrap();
        // Bound any orphan if a test or service-manager assertion regresses.
        thread::sleep(Duration::from_secs(30));
    }

    struct UnitGuard {
        unit: String,
        wrapper: Child,
    }
    impl Drop for UnitGuard {
        fn drop(&mut self) {
            let _ = Command::new("/usr/bin/systemctl")
                .args(["--user", "stop", &self.unit])
                .stdout(Stdio::null())
                .stderr(Stdio::null())
                .status();
            let _ = self.wrapper.kill();
            let _ = self.wrapper.wait();
        }
    }

    fn wait_ready(path: &Path, guard: &mut UnitGuard) -> (SocketAddr, u32, String) {
        let deadline = Instant::now() + Duration::from_secs(8);
        while Instant::now() < deadline {
            if let Ok(value) = fs::read_to_string(path) {
                let mut parts = value.split_whitespace();
                let address: SocketAddr = parts.next().unwrap().parse().unwrap();
                let pid: u32 = parts.next().unwrap().parse().unwrap();
                let session: u32 = parts.next().unwrap().parse().unwrap();
                let cgroup = parts.next().unwrap().to_owned();
                assert_eq!(session, pid, "grandchild did not enter a new session");
                assert!(parts.next().is_none());
                return (address, pid, cgroup);
            }
            if let Some(status) = guard.wrapper.try_wait().unwrap() {
                let log = fs::read_to_string(path.with_file_name("service.log"))
                    .unwrap_or_default();
                panic!("user service exited before fixture startup: {status}; log={log}");
            }
            thread::sleep(Duration::from_millis(20));
        }
        panic!("synthetic grandchild did not open its listener");
    }

    fn run_case(mode: &str) {
        let temporary = tempfile::tempdir().unwrap();
        let root = temporary.path();
        let nonce = SystemTime::now().duration_since(UNIX_EPOCH).unwrap().as_nanos();
        let token = format!("test-{}-{mode}-{nonce}", std::process::id());
        let unit = format!("hormuz-relay-{token}.service");
        let script = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("run-in-user-service.sh");
        let log = fs::File::create(root.join("service.log")).unwrap();
        let mut command = Command::new(script);
        command
            .arg(token)
            .arg("/usr/bin/env")
            .arg(format!("{STAGE}=launcher"))
            .arg(format!("{ROOT}={}", root.display()))
            .arg(format!("{MODE}={mode}"))
            .arg(std::env::current_exe().unwrap())
            .args(["--exact", TEST_NAME, "--nocapture"])
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::from(log));
        let mut guard = UnitGuard {
            unit: unit.clone(),
            wrapper: command.spawn().unwrap(),
        };
        let (address, _grandchild, cgroup) = wait_ready(&root.join("ready"), &mut guard);
        assert!(cgroup.ends_with(&format!("/{unit}")), "unexpected cgroup: {cgroup}");
        assert!(TcpStream::connect_timeout(&address, Duration::from_millis(250)).is_ok());
        match mode {
            "normal" => fs::write(root.join("release"), b"").unwrap(),
            "cancel" => {
                let status = Command::new("/usr/bin/systemctl")
                    .args(["--user", "stop", &unit])
                    .status()
                    .unwrap();
                assert!(status.success());
            }
            "abrupt" => {
                let pid: i32 = fs::read_to_string(root.join("launcher-pid"))
                    .unwrap()
                    .parse()
                    .unwrap();
                // SAFETY: the PID was written by this test's live synthetic
                // launcher, and the unit guard retains its unique service.
                assert_eq!(unsafe { libc::kill(pid, libc::SIGKILL) }, 0);
            }
            _ => unreachable!(),
        }
        let deadline = Instant::now() + Duration::from_secs(8);
        let events = Path::new("/sys/fs/cgroup")
            .join(cgroup.trim_start_matches('/'))
            .join("cgroup.events");
        while Instant::now() < deadline {
            let closed = TcpStream::connect_timeout(&address, Duration::from_millis(100)).is_err();
            let empty = !events.exists()
                || fs::read_to_string(&events)
                    .unwrap()
                    .lines()
                    .any(|line| line == "populated 0");
            if closed && empty {
                return;
            }
            thread::sleep(Duration::from_millis(20));
        }
        panic!("detached grandchild listener survived {mode} service termination");
    }

    #[test]
    fn user_service_contains_detached_listener() {
        if let Ok(stage) = std::env::var(STAGE) {
            let root = PathBuf::from(std::env::var(ROOT).unwrap());
            let mode = std::env::var(MODE).unwrap();
            match stage.as_str() {
                "launcher" => launcher(&root, &mode),
                "direct" => direct(&root, &mode),
                "grandchild" => grandchild(&root),
                _ => panic!("unknown synthetic stage"),
            }
            return;
        }
        if !Path::new("/usr/bin/systemd-run").is_file()
            || !Path::new("/usr/bin/setsid").is_file()
            || std::env::var_os("XDG_RUNTIME_DIR").is_none()
        {
            eprintln!("host-only cgroup test skipped: a real Linux user manager is unavailable");
            return;
        }
        let manager = Command::new("/usr/bin/systemctl")
            .args(["--user", "show-environment"])
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .status();
        if !manager.is_ok_and(|status| status.success()) {
            eprintln!("host-only cgroup test skipped: no reachable systemd user manager");
            return;
        }
        for mode in ["normal", "cancel", "abrupt"] {
            run_case(mode);
        }
    }
}
