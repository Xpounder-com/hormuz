//! Require an on-demand user service to own a Linux client process tree.
//!
//! A process group or `PR_SET_PDEATHSIG` cannot contain a grandchild which
//! forks or starts a new session. A systemd service with this launcher as its
//! main process owns the whole cgroup after normal exit or abrupt death.

use std::io::{self, Read, Seek, SeekFrom};
use std::os::unix::fs::{FileTypeExt, MetadataExt};
use std::path::PathBuf;
use std::process::{Command, Stdio};
use std::thread;
use std::time::{Duration, Instant};

const SERVICE_BUDGET: Duration = Duration::from_secs(3);
const STOP_BUDGET: Duration = Duration::from_secs(5);
const POLL_INTERVAL: Duration = Duration::from_millis(10);
const MAX_SHOW_BYTES: u64 = 4096;
const MAX_UNIT_TOKEN_BYTES: usize = 128;

pub(crate) fn require_user_service() -> io::Result<()> {
    let expires_at = Instant::now() + SERVICE_BUDGET;
    let mut deadline = Deadline::new(expires_at, Instant::now);
    deadline.check()?;
    let membership = std::fs::read_to_string("/proc/self/cgroup")?;
    deadline.check()?;
    let path = unified_path(&membership).ok_or_else(unavailable)?;
    let unit = service_unit(path).ok_or_else(unavailable)?;
    verify_service(
        path,
        std::process::id(),
        &mut deadline,
        |deadline| show_unit(unit, deadline),
        thread::sleep,
    )
}

fn unavailable() -> io::Error {
    io::Error::new(
        io::ErrorKind::PermissionDenied,
        "a verified on-demand user systemd service is required",
    )
}

fn timed_out() -> io::Error {
    io::Error::new(
        io::ErrorKind::TimedOut,
        "systemd service verification timed out",
    )
}

struct Deadline<Now> {
    expires_at: Instant,
    now: Now,
}

impl<Now> Deadline<Now>
where
    Now: FnMut() -> Instant,
{
    fn new(expires_at: Instant, now: Now) -> Self {
        Self { expires_at, now }
    }

    fn check(&mut self) -> io::Result<()> {
        self.remaining().map(|_| ())
    }

    fn remaining(&mut self) -> io::Result<Duration> {
        let remaining = self.expires_at.saturating_duration_since((self.now)());
        if remaining.is_zero() {
            Err(timed_out())
        } else {
            Ok(remaining)
        }
    }
}

fn unified_path(membership: &str) -> Option<&str> {
    let mut paths = membership
        .lines()
        .filter_map(|line| line.strip_prefix("0::"));
    let path = paths.next()?;
    if path.starts_with('/') && paths.next().is_none() {
        Some(path)
    } else {
        None
    }
}

fn service_unit(path: &str) -> Option<&str> {
    let unit = path.rsplit('/').find(|part| part.ends_with(".service"))?;
    if !unit.starts_with("hormuz-relay-") || unit.contains('\0') {
        None
    } else {
        Some(unit)
    }
}

fn stop_unit_name(token: &str) -> io::Result<String> {
    if token.is_empty()
        || token.len() > MAX_UNIT_TOKEN_BYTES
        || !token
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'_' | b'-'))
    {
        return Err(io::Error::new(
            io::ErrorKind::InvalidInput,
            "invalid relay unit token",
        ));
    }
    Ok(format!("hormuz-relay-{token}.service"))
}

fn user_runtime_directory(uid: u32) -> PathBuf {
    PathBuf::from(format!("/run/user/{uid}"))
}

fn canonical_user_bus() -> io::Result<(PathBuf, PathBuf)> {
    // SAFETY: these calls only read the current process credentials.
    let (real_uid, effective_uid) = unsafe { (libc::getuid(), libc::geteuid()) };
    if effective_uid == 0 || real_uid != effective_uid {
        return Err(unavailable());
    }
    let directory = user_runtime_directory(effective_uid);
    let metadata = std::fs::symlink_metadata(&directory)?;
    if !metadata.is_dir()
        || metadata.file_type().is_symlink()
        || metadata.uid() != effective_uid
        || metadata.mode() & 0o7777 != 0o700
    {
        return Err(unavailable());
    }
    let bus = directory.join("bus");
    let metadata = std::fs::symlink_metadata(&bus)?;
    if !metadata.file_type().is_socket() || metadata.uid() != effective_uid {
        return Err(unavailable());
    }
    Ok((directory, bus))
}

fn systemctl_command() -> io::Result<Command> {
    // This is a root-owned program path, never a command resolved from an
    // untrusted PATH. Only the canonical per-UID bus is used.
    let (runtime, bus) = canonical_user_bus()?;
    let mut command = Command::new("/usr/bin/systemctl");
    command.env_clear().env("XDG_RUNTIME_DIR", runtime).env(
        "DBUS_SESSION_BUS_ADDRESS",
        format!("unix:path={}", bus.display()),
    );
    Ok(command)
}

fn kill_and_reap(child: &mut std::process::Child) {
    let _ = child.kill();
    let _ = child.wait();
}

fn wait_command<Now>(
    child: &mut std::process::Child,
    deadline: &mut Deadline<Now>,
) -> io::Result<std::process::ExitStatus>
where
    Now: FnMut() -> Instant,
{
    loop {
        if let Err(error) = deadline.check() {
            kill_and_reap(child);
            return Err(error);
        }
        match child.try_wait() {
            Ok(Some(status)) => {
                deadline.check()?;
                return Ok(status);
            }
            Ok(None) => {
                let remaining = match deadline.remaining() {
                    Ok(remaining) => remaining,
                    Err(error) => {
                        kill_and_reap(child);
                        return Err(error);
                    }
                };
                thread::sleep(POLL_INTERVAL.min(remaining));
            }
            Err(error) => {
                kill_and_reap(child);
                return Err(error);
            }
        }
    }
}

fn read_show_output<Now, Output>(
    stdout: &mut Output,
    deadline: &mut Deadline<Now>,
) -> io::Result<String>
where
    Now: FnMut() -> Instant,
    Output: Read + Seek,
{
    deadline.check()?;
    stdout.seek(SeekFrom::Start(0))?;
    let mut bytes = Vec::new();
    stdout.take(MAX_SHOW_BYTES + 1).read_to_end(&mut bytes)?;
    if bytes.len() > MAX_SHOW_BYTES as usize {
        return Err(unavailable());
    }
    let output = String::from_utf8(bytes).map_err(|_| unavailable())?;
    deadline.check()?;
    Ok(output)
}

fn show_unit<Now>(unit: &str, deadline: &mut Deadline<Now>) -> io::Result<String>
where
    Now: FnMut() -> Instant,
{
    // Captured output and time are bounded before any client version probe
    // or relay listener starts.
    deadline.check()?;
    let mut stdout = tempfile::tempfile()?;
    let mut command = systemctl_command()?;
    command
        .args([
            "--user",
            "--no-pager",
            "show",
            unit,
            "--property=Id,LoadState,ControlGroup,MainPID,ExitType,RemainAfterExit,Restart,KillMode,KillSignal,Transient,ActiveState",
        ])
        .stdin(Stdio::null())
        .stdout(Stdio::from(stdout.try_clone()?))
        .stderr(Stdio::null());
    deadline.check()?;
    let mut child = command.spawn()?;
    if let Err(error) = deadline.check() {
        kill_and_reap(&mut child);
        return Err(error);
    }
    let status = wait_command(&mut child, deadline)?;
    if !status.success() {
        return Err(unavailable());
    }
    read_show_output(&mut stdout, deadline)
}

fn property<'a>(output: &'a str, name: &str) -> Option<&'a str> {
    let mut values = output.lines().filter_map(|line| {
        let (key, value) = line.split_once('=')?;
        (key == name).then_some(value)
    });
    let value = values.next()?;
    values.next().is_none().then_some(value)
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum ServiceState {
    Active,
    Activating,
    Invalid,
}

fn service_state(output: &str, path: &str, pid: u32) -> ServiceState {
    let exact = property(output, "ControlGroup") == Some(path)
        && property(output, "MainPID").and_then(|value| value.parse::<u32>().ok()) == Some(pid)
        && property(output, "ExitType") == Some("main")
        && property(output, "RemainAfterExit") == Some("no")
        && property(output, "Restart") == Some("no")
        && property(output, "KillMode") == Some("control-group")
        && matches!(property(output, "KillSignal"), Some("9" | "SIGKILL"))
        && property(output, "Transient") == Some("yes");
    if !exact {
        return ServiceState::Invalid;
    }
    match property(output, "ActiveState") {
        Some("active") => ServiceState::Active,
        Some("activating") => ServiceState::Activating,
        _ => ServiceState::Invalid,
    }
}

fn verify_service<Now, Show, Wait>(
    path: &str,
    pid: u32,
    deadline: &mut Deadline<Now>,
    mut show: Show,
    mut wait: Wait,
) -> io::Result<()>
where
    Now: FnMut() -> Instant,
    Show: FnMut(&mut Deadline<Now>) -> io::Result<String>,
    Wait: FnMut(Duration),
{
    loop {
        deadline.check()?;
        let output = show(deadline)?;
        deadline.check()?;
        match service_state(&output, path, pid) {
            ServiceState::Active => {
                deadline.check()?;
                return Ok(());
            }
            ServiceState::Activating => {
                let remaining = deadline.remaining()?;
                wait(POLL_INTERVAL.min(remaining));
                deadline.check()?;
            }
            ServiceState::Invalid => return Err(unavailable()),
        }
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum StopState {
    Running,
    Stopping,
    Stopped,
    Invalid,
}

fn stop_state(output: &str, unit: &str) -> StopState {
    if property(output, "Id") != Some(unit) {
        return StopState::Invalid;
    }
    let load = property(output, "LoadState");
    let active = property(output, "ActiveState");
    let pid = property(output, "MainPID").and_then(|value| value.parse::<u32>().ok());
    let group = property(output, "ControlGroup");
    if load == Some("not-found")
        && active == Some("inactive")
        && pid == Some(0)
        && group == Some("")
    {
        return StopState::Stopped;
    }
    let exact = load == Some("loaded")
        && property(output, "ExitType") == Some("main")
        && property(output, "RemainAfterExit") == Some("no")
        && property(output, "Restart") == Some("no")
        && property(output, "KillMode") == Some("control-group")
        && matches!(property(output, "KillSignal"), Some("9" | "SIGKILL"))
        && property(output, "Transient") == Some("yes");
    if !exact {
        return StopState::Invalid;
    }
    if active == Some("inactive") && pid == Some(0) && group == Some("") {
        return StopState::Stopped;
    }
    let owned_group =
        group.is_some_and(|path| path.starts_with('/') && path.ends_with(&format!("/{unit}")));
    if !owned_group {
        return StopState::Invalid;
    }
    match active {
        Some("active" | "activating") if pid.is_some_and(|value| value > 0) => StopState::Running,
        Some("deactivating") => StopState::Stopping,
        _ => StopState::Invalid,
    }
}

fn issue_stop<Now>(unit: &str, deadline: &mut Deadline<Now>) -> io::Result<()>
where
    Now: FnMut() -> Instant,
{
    deadline.check()?;
    let mut command = systemctl_command()?;
    command
        .args(["--user", "--no-block", "stop", unit])
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null());
    deadline.check()?;
    let mut child = command.spawn()?;
    if let Err(error) = deadline.check() {
        kill_and_reap(&mut child);
        return Err(error);
    }
    if !wait_command(&mut child, deadline)?.success() {
        return Err(unavailable());
    }
    deadline.check()
}

fn stop_user_service_with<Now, Show, Stop, Wait>(
    unit: &str,
    deadline: &mut Deadline<Now>,
    mut show: Show,
    mut stop: Stop,
    mut wait: Wait,
) -> io::Result<()>
where
    Now: FnMut() -> Instant,
    Show: FnMut(&mut Deadline<Now>) -> io::Result<String>,
    Stop: FnMut(&mut Deadline<Now>) -> io::Result<()>,
    Wait: FnMut(Duration),
{
    deadline.check()?;
    let initial = stop_state(&show(deadline)?, unit);
    deadline.check()?;
    match initial {
        StopState::Stopped => return Ok(()),
        StopState::Running | StopState::Stopping => stop(deadline)?,
        StopState::Invalid => return Err(unavailable()),
    }
    loop {
        deadline.check()?;
        match stop_state(&show(deadline)?, unit) {
            StopState::Stopped => {
                deadline.check()?;
                return Ok(());
            }
            StopState::Running | StopState::Stopping => {
                let remaining = deadline.remaining()?;
                wait(POLL_INTERVAL.min(remaining));
            }
            StopState::Invalid => return Err(unavailable()),
        }
    }
}

/// Stop only a validated Hormuz transient service through the canonical user
/// bus. This control operation does not open private state or Secret Service.
pub(crate) fn stop_user_service(token: &str) -> io::Result<()> {
    let unit = stop_unit_name(token)?;
    let mut deadline = Deadline::new(Instant::now() + STOP_BUDGET, Instant::now);
    stop_user_service_with(
        &unit,
        &mut deadline,
        |deadline| show_unit(&unit, deadline),
        |deadline| issue_stop(&unit, deadline),
        thread::sleep,
    )
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::cell::Cell;
    use std::io::Cursor;

    const GOOD: &str = "ControlGroup=/user.slice/user-1000.slice/user@1000.service/app.slice/hormuz-relay-test.service\nMainPID=123\nExitType=main\nRemainAfterExit=no\nRestart=no\nKillMode=control-group\nKillSignal=9\nTransient=yes\nActiveState=active\n";
    const PATH: &str =
        "/user.slice/user-1000.slice/user@1000.service/app.slice/hormuz-relay-test.service";
    const UNIT: &str = "hormuz-relay-test.service";
    const GOOD_STOP: &str = "Id=hormuz-relay-test.service\nLoadState=loaded\nControlGroup=/user.slice/user-1000.slice/user@1000.service/app.slice/hormuz-relay-test.service\nMainPID=123\nExitType=main\nRemainAfterExit=no\nRestart=no\nKillMode=control-group\nKillSignal=9\nTransient=yes\nActiveState=active\n";
    const COLLECTED: &str = "Id=hormuz-relay-test.service\nLoadState=not-found\nControlGroup=\nMainPID=0\nActiveState=inactive\n";

    #[test]
    fn stop_accepts_only_an_exact_bounded_unit_token() {
        assert_eq!(
            stop_unit_name("test_42-A").unwrap(),
            "hormuz-relay-test_42-A.service"
        );
        for token in ["", "other.service", "../other", "a/b", "a*", "a b", "é"] {
            assert_eq!(
                stop_unit_name(token).unwrap_err().kind(),
                io::ErrorKind::InvalidInput
            );
        }
        assert_eq!(
            stop_unit_name(&"a".repeat(MAX_UNIT_TOKEN_BYTES + 1))
                .unwrap_err()
                .kind(),
            io::ErrorKind::InvalidInput
        );
    }

    #[test]
    fn stop_requires_exact_transient_tree_kill_identity() {
        assert_eq!(stop_state(GOOD_STOP, UNIT), StopState::Running);
        assert_eq!(stop_state(COLLECTED, UNIT), StopState::Stopped);
        let inactive = GOOD_STOP
            .replace(&format!("ControlGroup={PATH}"), "ControlGroup=")
            .replace("MainPID=123", "MainPID=0")
            .replace("ActiveState=active", "ActiveState=inactive");
        assert_eq!(stop_state(&inactive, UNIT), StopState::Stopped);
        for (old, new) in [
            ("Id=hormuz-relay-test.service", "Id=other.service"),
            ("LoadState=loaded", "LoadState=masked"),
            ("MainPID=123", "MainPID=0"),
            ("ExitType=main", "ExitType=cgroup"),
            ("RemainAfterExit=no", "RemainAfterExit=yes"),
            ("Restart=no", "Restart=always"),
            ("KillMode=control-group", "KillMode=process"),
            ("KillSignal=9", "KillSignal=15"),
            ("Transient=yes", "Transient=no"),
            ("ActiveState=active", "ActiveState=failed"),
        ] {
            assert_eq!(
                stop_state(&GOOD_STOP.replace(old, new), UNIT),
                StopState::Invalid
            );
        }
        assert_eq!(
            stop_state(&format!("{GOOD_STOP}KillMode=control-group\n"), UNIT),
            StopState::Invalid
        );
        assert_eq!(
            stop_state(&GOOD_STOP.replace(PATH, "/other.service"), UNIT),
            StopState::Invalid
        );
    }

    #[test]
    fn stop_rechecks_the_exact_unit_until_collected() {
        let mut outputs = [
            GOOD_STOP.to_owned(),
            GOOD_STOP.to_owned(),
            COLLECTED.to_owned(),
        ]
        .into_iter();
        let now = Instant::now();
        let mut deadline = Deadline::new(now + STOP_BUDGET, || now);
        let mut stops = 0;
        let mut waits = 0;
        stop_user_service_with(
            UNIT,
            &mut deadline,
            |_| Ok(outputs.next().unwrap()),
            |_| {
                stops += 1;
                Ok(())
            },
            |_| waits += 1,
        )
        .unwrap();
        assert_eq!(stops, 1);
        assert_eq!(waits, 1);
    }

    #[test]
    fn stop_rejects_a_mismatch_without_signaling_and_times_out_after_a_signal() {
        let now = Instant::now();
        let mut deadline = Deadline::new(now + STOP_BUDGET, || now);
        let mut stops = 0;
        let error = stop_user_service_with(
            UNIT,
            &mut deadline,
            |_| Ok(GOOD_STOP.replace("KillMode=control-group", "KillMode=process")),
            |_| {
                stops += 1;
                Ok(())
            },
            |_| {},
        )
        .unwrap_err();
        assert_eq!(error.kind(), io::ErrorKind::PermissionDenied);
        assert_eq!(stops, 0);

        let now = Cell::new(Instant::now());
        let expires_at = now.get() + Duration::from_millis(30);
        let mut deadline = Deadline::new(expires_at, || now.get());
        let mut stops = 0;
        let error = stop_user_service_with(
            UNIT,
            &mut deadline,
            |_| Ok(GOOD_STOP.to_owned()),
            |_| {
                stops += 1;
                Ok(())
            },
            |_| now.set(expires_at),
        )
        .unwrap_err();
        assert_eq!(error.kind(), io::ErrorKind::TimedOut);
        assert_eq!(stops, 1);
    }

    #[test]
    fn parses_only_a_unified_service_membership() {
        assert_eq!(unified_path(&format!("0::{PATH}\n")), Some(PATH));
        assert_eq!(unified_path("0::/\n"), Some("/"));
        assert_eq!(unified_path(&format!("0::{PATH}\n0::{PATH}\n")), None);
        assert_eq!(service_unit(PATH), Some("hormuz-relay-test.service"));
        assert_eq!(service_unit("/user.slice/test.scope"), None);
        assert_eq!(service_unit("/user.slice/other.service"), None);
    }

    #[test]
    fn requires_the_main_pid_and_tree_kill_properties() {
        assert_eq!(service_state(GOOD, PATH, 123), ServiceState::Active);
        assert_eq!(service_state(GOOD, PATH, 124), ServiceState::Invalid);
        for (old, new) in [
            ("ExitType=main", "ExitType=cgroup"),
            ("RemainAfterExit=no", "RemainAfterExit=yes"),
            ("Restart=no", "Restart=yes"),
            ("Restart=no", "Restart=always"),
            ("Restart=no", "Restart=on-failure"),
            ("KillMode=control-group", "KillMode=process"),
            ("KillSignal=9", "KillSignal=15"),
            ("Transient=yes", "Transient=no"),
            ("ActiveState=active", "ActiveState=inactive"),
        ] {
            assert_eq!(
                service_state(&GOOD.replace(old, new), PATH, 123),
                ServiceState::Invalid
            );
        }
        assert_eq!(
            service_state(&format!("{GOOD}KillMode=control-group\n"), PATH, 123),
            ServiceState::Invalid
        );
        assert_eq!(
            service_state(&GOOD.replace("RemainAfterExit=no\n", ""), PATH, 123),
            ServiceState::Invalid
        );
        assert_eq!(
            service_state(&GOOD.replace("Restart=no\n", ""), PATH, 123),
            ServiceState::Invalid
        );
        assert_eq!(
            service_state(&format!("{GOOD}Restart=no\n"), PATH, 123),
            ServiceState::Invalid
        );
        assert_eq!(
            service_state(GOOD, "/another.service", 123),
            ServiceState::Invalid
        );
    }

    #[test]
    fn retries_an_exact_activating_service_until_active() {
        let activating = GOOD.replace("ActiveState=active", "ActiveState=activating");
        let mut outputs = [activating, GOOD.to_owned()].into_iter();
        let mut reads = 0;
        let mut retries = 0;
        let now = Instant::now();
        let mut deadline = Deadline::new(now + SERVICE_BUDGET, || now);
        assert!(verify_service(
            PATH,
            123,
            &mut deadline,
            |_| {
                reads += 1;
                Ok(outputs.next().unwrap())
            },
            |_| {
                retries += 1;
            }
        )
        .is_ok());
        assert_eq!(reads, 2);
        assert_eq!(retries, 1);
    }

    #[test]
    fn activating_service_times_out_without_sleeping() {
        let activating = GOOD.replace("ActiveState=active", "ActiveState=activating");
        let mut reads = 0;
        let mut retries = 0;
        let now = Cell::new(Instant::now());
        let expires_at = now.get() + Duration::from_millis(30);
        let mut deadline = Deadline::new(expires_at, || now.get());
        let error = verify_service(
            PATH,
            123,
            &mut deadline,
            |_| {
                reads += 1;
                Ok(activating.clone())
            },
            |duration| {
                retries += 1;
                now.set(now.get() + duration);
            },
        )
        .unwrap_err();
        assert_eq!(error.kind(), io::ErrorKind::TimedOut);
        assert_eq!(reads, 3);
        assert_eq!(retries, 3);
    }

    #[test]
    fn invalid_activating_service_is_rejected_without_retry() {
        let invalid = GOOD
            .replace("ActiveState=active", "ActiveState=activating")
            .replace("KillMode=control-group", "KillMode=process");
        let mut reads = 0;
        let mut retries = 0;
        let now = Instant::now();
        let mut deadline = Deadline::new(now + SERVICE_BUDGET, || now);
        let error = verify_service(
            PATH,
            123,
            &mut deadline,
            |_| {
                reads += 1;
                Ok(invalid.clone())
            },
            |_| {
                retries += 1;
            },
        )
        .unwrap_err();
        assert_eq!(error.kind(), io::ErrorKind::PermissionDenied);
        assert_eq!(reads, 1);
        assert_eq!(retries, 0);
    }

    #[test]
    fn active_service_is_rejected_if_property_validation_reaches_the_deadline() {
        let now = Instant::now();
        let expires_at = now + SERVICE_BUDGET;
        let mut samples = [now, now, expires_at].into_iter();
        let mut deadline = Deadline::new(expires_at, || samples.next().unwrap_or(expires_at));
        let mut reads = 0;
        let mut retries = 0;
        let error = verify_service(
            PATH,
            123,
            &mut deadline,
            |_| {
                reads += 1;
                Ok(GOOD.to_owned())
            },
            |_| retries += 1,
        )
        .unwrap_err();
        assert_eq!(error.kind(), io::ErrorKind::TimedOut);
        assert_eq!(reads, 1);
        assert_eq!(retries, 0);
    }

    #[test]
    fn parsed_show_output_is_rejected_if_it_reaches_the_deadline() {
        let now = Instant::now();
        let expires_at = now + SERVICE_BUDGET;
        let mut samples = [now, expires_at].into_iter();
        let mut deadline = Deadline::new(expires_at, || samples.next().unwrap_or(expires_at));
        let mut output = Cursor::new(GOOD.as_bytes());
        let error = read_show_output(&mut output, &mut deadline).unwrap_err();
        assert_eq!(error.kind(), io::ErrorKind::TimedOut);
    }

    #[test]
    fn wrapper_declares_the_literal_path_and_lifetime_contract() {
        let wrapper = include_str!("../run-in-user-service.sh");
        assert!(wrapper.contains("/usr/bin/systemd-run --help"));
        assert!(wrapper.contains("--expand-environment=no"));
        assert!(wrapper.contains("--setenv=PATH"));
        assert!(!wrapper.contains("--setenv=PATH="));
        assert!(wrapper.contains("--property=RemainAfterExit=no"));
        assert!(wrapper.contains("--property=Restart=no"));
    }

    #[test]
    fn user_bus_path_does_not_follow_caller_environment() {
        assert_eq!(
            user_runtime_directory(1000),
            PathBuf::from("/run/user/1000")
        );
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
    const EXPECTED_PATH: &str = "HORMUZ_SERVICE_TEST_EXPECTED_PATH";
    const LITERAL: &str = "HORMUZ_SERVICE_TEST_LITERAL";

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
        require_user_service().unwrap();
        let membership = fs::read_to_string("/proc/self/cgroup").unwrap();
        let path = unified_path(&membership).unwrap();
        let unit = service_unit(path).unwrap();
        let expires_at = Instant::now() + SERVICE_BUDGET;
        let mut deadline = Deadline::new(expires_at, Instant::now);
        let properties = show_unit(unit, &mut deadline).unwrap();
        assert_eq!(property(&properties, "Restart"), Some("no"));
        assert_eq!(
            std::env::var("PATH").unwrap(),
            std::env::var(EXPECTED_PATH).unwrap(),
            "wrapper did not preserve the caller session PATH"
        );
        assert_eq!(
            std::env::var(LITERAL).unwrap(),
            "${HOME}",
            "systemd expanded an already-formed command argument"
        );
        fs::write(root.join("launcher-pid"), std::process::id().to_string()).unwrap();
        assert!(fixture("direct", root, mode)
            .spawn()
            .unwrap()
            .wait()
            .unwrap()
            .success());
        let deadline = Instant::now() + Duration::from_secs(20);
        while Instant::now() < deadline {
            if mode == "normal" && root.join("release").exists() {
                return;
            }
            thread::sleep(Duration::from_millis(10));
        }
        panic!("synthetic launcher was not stopped before its fixture deadline");
    }

    // This stage must exit without waiting for the grandchild: otherwise the
    // fixture would not prove service cleanup after a double fork. The test's
    // unique user service owns the orphan, and the host reaps it on exit.
    #[allow(clippy::zombie_processes)]
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
        listener.set_nonblocking(true).unwrap();
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
        // Drain probes so a full TCP backlog cannot mimic listener death.
        // Bound any orphan if a test or service-manager assertion regresses.
        let deadline = Instant::now() + Duration::from_secs(30);
        let mut accepted = 0;
        while Instant::now() < deadline {
            match listener.accept() {
                Ok((stream, _)) => {
                    drop(stream);
                    accepted += 1;
                    fs::write(root.join("accepted"), accepted.to_string()).unwrap();
                }
                Err(error) if error.kind() == io::ErrorKind::WouldBlock => {
                    thread::sleep(Duration::from_millis(5));
                }
                Err(error) => panic!("synthetic listener accept failed: {error}"),
            }
        }
    }

    struct UnitGuard {
        unit: String,
        wrapper: Child,
    }
    impl Drop for UnitGuard {
        fn drop(&mut self) {
            if let Ok(mut command) = systemctl_command() {
                let _ = command
                    .args(["--user", "stop", &self.unit])
                    .stdout(Stdio::null())
                    .stderr(Stdio::null())
                    .status();
            }
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
                let log =
                    fs::read_to_string(path.with_file_name("service.log")).unwrap_or_default();
                panic!("user service exited before fixture startup: {status}; log={log}");
            }
            thread::sleep(Duration::from_millis(20));
        }
        panic!("synthetic grandchild did not open its listener");
    }

    fn run_case(mode: &str) {
        let temporary = tempfile::tempdir().unwrap();
        let root = temporary.path();
        let nonce = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let token = format!("test-{}-{mode}-{nonce}", std::process::id());
        let unit = format!("hormuz-relay-{token}.service");
        let script = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("run-in-user-service.sh");
        let log = fs::File::create(root.join("service.log")).unwrap();
        let spoofed_bus = format!("unix:path={}", root.join("spoofed-bus").display());
        let session_path = format!("{}:/session/${{HOME}}/bin", root.join("bin").display());
        let mut command = Command::new(script);
        command
            .arg(&token)
            .arg("/usr/bin/env")
            .arg(format!("DBUS_SESSION_BUS_ADDRESS={spoofed_bus}"))
            .arg(format!("XDG_RUNTIME_DIR={}", root.display()))
            .arg(format!("{STAGE}=launcher"))
            .arg(format!("{ROOT}={}", root.display()))
            .arg(format!("{MODE}={mode}"))
            .arg(format!("{EXPECTED_PATH}={session_path}"))
            .arg(format!("{LITERAL}=${{HOME}}"))
            .arg(std::env::current_exe().unwrap())
            .args(["--exact", TEST_NAME, "--nocapture"])
            .env("DBUS_SESSION_BUS_ADDRESS", spoofed_bus.as_str())
            .env("XDG_RUNTIME_DIR", root)
            .env("PATH", session_path)
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::from(log));
        let mut guard = UnitGuard {
            unit: unit.clone(),
            wrapper: command.spawn().unwrap(),
        };
        let (address, _grandchild, cgroup) = wait_ready(&root.join("ready"), &mut guard);
        assert!(
            cgroup.ends_with(&format!("/{unit}")),
            "unexpected cgroup: {cgroup}"
        );
        for _ in 0..4 {
            assert!(TcpStream::connect_timeout(&address, Duration::from_millis(250)).is_ok());
        }
        let accepted_deadline = Instant::now() + Duration::from_secs(2);
        while Instant::now() < accepted_deadline {
            let count = fs::read_to_string(root.join("accepted"))
                .ok()
                .and_then(|value| value.parse::<u32>().ok())
                .unwrap_or(0);
            if count >= 4 {
                break;
            }
            thread::sleep(Duration::from_millis(10));
        }
        assert!(
            fs::read_to_string(root.join("accepted"))
                .ok()
                .and_then(|value| value.parse::<u32>().ok())
                .is_some_and(|count| count >= 4),
            "synthetic listener did not drain connection probes"
        );
        match mode {
            "normal" => fs::write(root.join("release"), b"").unwrap(),
            "cancel" => {
                stop_user_service(&token).unwrap();
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
            let empty = match fs::read_to_string(&events) {
                Ok(contents) => contents.lines().any(|line| line == "populated 0"),
                Err(error) if error.kind() == io::ErrorKind::NotFound => true,
                Err(error) => panic!("could not read service cgroup events: {error}"),
            };
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
            || canonical_user_bus().is_err()
        {
            eprintln!("host-only cgroup test skipped: a real Linux user manager is unavailable");
            return;
        }
        let required_options = Command::new("/usr/bin/systemd-run")
            .arg("--help")
            .output()
            .ok()
            .filter(|output| output.status.success())
            .and_then(|output| String::from_utf8(output.stdout).ok())
            .is_some_and(|help| {
                help.contains("--setenv=") && help.contains("--expand-environment=")
            });
        if !required_options {
            eprintln!(
                "host-only cgroup test skipped: systemd-run lacks literal argv or PATH forwarding"
            );
            return;
        }
        let manager = systemctl_command().and_then(|mut command| {
            command
                .args(["--user", "show-environment"])
                .stdout(Stdio::null())
                .stderr(Stdio::null())
                .status()
        });
        if !manager.is_ok_and(|status| status.success()) {
            eprintln!("host-only cgroup test skipped: no reachable systemd user manager");
            return;
        }
        for mode in ["normal", "cancel", "abrupt"] {
            run_case(mode);
        }
    }
}
