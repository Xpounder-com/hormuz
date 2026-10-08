//! One on-demand terminal manager, not a second authentication controller.
//! The relay owns custody, client discovery and cgroup verification. This
//! shell owns the exact service token and a private app-lifetime socket.
use hormuz_client_platform::PrivateDirectory;
use std::{
    fs, io,
    os::unix::{
        fs::{FileTypeExt, MetadataExt, PermissionsExt},
        net::{UnixListener, UnixStream},
    },
    path::{Path, PathBuf},
    process::{Child, Command, Stdio},
    sync::{
        mpsc::{self, SyncSender},
        Arc, Mutex,
    },
    thread::{self, JoinHandle},
    time::{Duration, Instant},
};

type Notifier = Box<dyn Fn() -> bool + Send + Sync>;

trait Backend: Send + Sync + 'static {
    fn launch(&self, plan: &Plan) -> io::Result<Child>;
    fn stop(&self, token: &str) -> bool;
}
struct NativeBackend;
impl Backend for NativeBackend {
    fn launch(&self, plan: &Plan) -> io::Result<Child> {
        start_terminal(plan)
    }
    fn stop(&self, token: &str) -> bool {
        stop_unit(token)
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Phase {
    Starting,
    Running,
    StopFailed,
    Finished,
    Failed,
}

impl Phase {
    pub fn description(self) -> &'static str {
        match self {
            Self::Starting => "Opening the governed client in GNOME Terminal…",
            Self::Running => "Governed client terminal owns the verified relay service.",
            Self::StopFailed => "Could not confirm service stop. Ownership retained; retry Stop.",
            Self::Finished => "Governed client and owned service stopped.",
            Self::Failed => "Could not launch. GNOME Terminal, systemd 254+ user manager and a supported client are required.",
        }
    }
}

#[derive(Clone)]
struct Plan {
    token: String,
    root: PathBuf,
    profile: String,
    socket: PathBuf,
    relay: PathBuf,
}

impl Plan {
    fn new(root: PathBuf, profile: String, relay: PathBuf) -> io::Result<Self> {
        if !root.is_absolute()
            || !relay.is_absolute()
            || profile.len() != 36
            || !profile.bytes().enumerate().all(|(i, b)| {
                if [8, 13, 18, 23].contains(&i) {
                    b == b'-'
                } else {
                    b.is_ascii_hexdigit()
                }
            })
        {
            return Err(io::Error::from(io::ErrorKind::InvalidInput));
        }
        let mut bytes = [0u8; 16];
        getrandom::fill(&mut bytes).map_err(|_| io::Error::from(io::ErrorKind::Other))?;
        let token: String = bytes.iter().map(|b| format!("{b:02x}")).collect();
        let socket = root.join(format!("relay-owner-{token}.sock"));
        // Linux sockaddr_un has a 108-byte pathname buffer, including NUL.
        if socket.as_os_str().as_encoded_bytes().len() >= 108 {
            return Err(io::Error::from(io::ErrorKind::InvalidInput));
        }
        Ok(Self {
            token,
            root,
            profile,
            socket,
            relay,
        })
    }

    fn arguments(&self) -> Vec<std::ffi::OsString> {
        [
            "--wait",
            "--",
            "/usr/bin/systemd-run",
            "--user",
            "--wait",
            "--collect",
            "--quiet",
            "--same-dir",
            "--pty",
            "--pipe",
            "--expand-environment=no",
            "--setenv=PATH",
            &format!("--unit=hormuz-relay-{}.service", self.token),
            "--service-type=exec",
            "--property=ExitType=main",
            "--property=RemainAfterExit=no",
            "--property=Restart=no",
            "--property=KillMode=control-group",
            "--property=KillSignal=SIGKILL",
            "--",
        ]
        .into_iter()
        .map(std::ffi::OsString::from)
        .chain([
            self.relay.as_os_str().to_owned(),
            "--profile".into(),
            self.profile.clone().into(),
            "--state-directory".into(),
            self.root.as_os_str().to_owned(),
            "--owner-socket".into(),
            self.socket.as_os_str().to_owned(),
        ])
        .collect()
    }
}

enum Request {
    Stop(SyncSender<bool>),
}

pub struct Terminal {
    commands: Option<SyncSender<Request>>,
    worker: Option<JoinHandle<()>>,
    phase: Arc<Mutex<Phase>>,
}

impl Terminal {
    pub fn start(
        root: PathBuf,
        profile: String,
        relay: PathBuf,
        notify: Notifier,
    ) -> io::Result<Self> {
        Self::start_with(root, profile, relay, Arc::new(NativeBackend), notify)
    }

    fn start_with(
        root: PathBuf,
        profile: String,
        relay: PathBuf,
        backend: Arc<dyn Backend>,
        notify: Notifier,
    ) -> io::Result<Self> {
        let (commands, receiver) = mpsc::sync_channel(1);
        let phase = Arc::new(Mutex::new(Phase::Starting));
        let state = phase.clone();
        // The returned handle owns startup immediately. Quit can enqueue Stop
        // before the terminal/server/service has started; a delayed relay can
        // never pass its mandatory owner-socket preflight after socket close.
        let worker = thread::Builder::new()
            .name("hormuz-linux-terminal".into())
            .spawn(move || {
                let Ok(plan) = Plan::new(root, profile, relay) else {
                    *state.lock().unwrap() = Phase::Failed;
                    notify();
                    return;
                };
                manage(plan, receiver, state, backend, notify);
            })?;
        Ok(Self {
            commands: Some(commands),
            worker: Some(worker),
            phase,
        })
    }

    pub fn phase(&self) -> Phase {
        *self.phase.lock().unwrap()
    }

    /// Bounded and called off GTK's thread. Failure keeps the handle and exact
    /// unit token alive; it is not treated as completed app shutdown.
    pub fn stop(&mut self) -> io::Result<()> {
        if matches!(self.phase(), Phase::Finished | Phase::Failed) {
            return self.join();
        }
        let (answer, result) = mpsc::sync_channel(1);
        self.commands
            .as_ref()
            .ok_or(io::ErrorKind::BrokenPipe)?
            .try_send(Request::Stop(answer))
            .map_err(|_| io::Error::from(io::ErrorKind::WouldBlock))?;
        match result.recv_timeout(Duration::from_secs(8)) {
            Ok(true) => self.join(),
            _ => Err(io::Error::from(io::ErrorKind::TimedOut)),
        }
    }

    fn join(&mut self) -> io::Result<()> {
        self.commands.take();
        if let Some(worker) = self.worker.take() {
            worker
                .join()
                .map_err(|_| io::Error::from(io::ErrorKind::Other))?;
        }
        Ok(())
    }
}

impl Drop for Terminal {
    fn drop(&mut self) {
        // The channel disconnect closes the lease, attempts exact-unit stop,
        // kills/reaps the direct terminal client and drains this one worker.
        // Shell shutdown drops this object only on a blocking executor.
        self.commands.take();
        let _ = self.join();
    }
}

struct SocketOwner {
    path: PathBuf,
    identity: (u64, u64),
    listener: Option<UnixListener>,
    peer: Option<UnixStream>,
}
impl SocketOwner {
    fn open(plan: &Plan) -> io::Result<Self> {
        let _directory = PrivateDirectory::open(&plan.root).map_err(io::Error::other)?;
        let listener = UnixListener::bind(&plan.socket)?;
        let metadata = fs::symlink_metadata(&plan.socket)?;
        let mut owner = Self {
            path: plan.socket.clone(),
            identity: (metadata.dev(), metadata.ino()),
            listener: Some(listener),
            peer: None,
        };
        fs::set_permissions(&plan.socket, fs::Permissions::from_mode(0o600))?;
        owner.listener.as_ref().unwrap().set_nonblocking(true)?;
        owner.accept()?;
        Ok(owner)
    }

    fn accept(&mut self) -> io::Result<bool> {
        if self.peer.is_some() {
            return Ok(true);
        }
        let Some(listener) = &self.listener else {
            return Ok(false);
        };
        match listener.accept() {
            Ok((mut peer, _)) => {
                if !same_uid(&peer)? {
                    return Err(io::Error::from(io::ErrorKind::PermissionDenied));
                }
                // Linux relay waits for this bounded ACK before touching
                // custody or discovering a client. A queued connection is not
                // mistaken for completed ownership acceptance.
                use std::io::Write;
                peer.set_write_timeout(Some(Duration::from_secs(1)))?;
                peer.write_all(&[1])?;
                self.peer = Some(peer);
                self.listener.take();
                Ok(true)
            }
            Err(error) if error.kind() == io::ErrorKind::WouldBlock => Ok(false),
            Err(error) => Err(error),
        }
    }

    fn close(&mut self) {
        self.listener.take();
        self.peer.take();
    }
}
impl Drop for SocketOwner {
    fn drop(&mut self) {
        self.close();
        // Only unlink our randomly named socket; never remove an unrelated
        // substituted file. The private root retains its existing ownership.
        if fs::symlink_metadata(&self.path)
            .is_ok_and(|m| m.file_type().is_socket() && (m.dev(), m.ino()) == self.identity)
        {
            let _ = fs::remove_file(&self.path);
        }
    }
}

#[cfg(target_os = "linux")]
fn same_uid(peer: &UnixStream) -> io::Result<bool> {
    use std::os::fd::AsRawFd;
    // SAFETY: SO_PEERCRED writes a fixed-size ucred into this allocation.
    unsafe {
        let mut credentials = std::mem::MaybeUninit::<libc::ucred>::uninit();
        let mut size = std::mem::size_of::<libc::ucred>() as libc::socklen_t;
        if libc::getsockopt(
            peer.as_raw_fd(),
            libc::SOL_SOCKET,
            libc::SO_PEERCRED,
            credentials.as_mut_ptr().cast(),
            &mut size,
        ) != 0
        {
            return Err(io::Error::last_os_error());
        }
        Ok(size as usize == std::mem::size_of::<libc::ucred>()
            && credentials.assume_init().uid == libc::geteuid())
    }
}
#[cfg(not(target_os = "linux"))]
fn same_uid(_: &UnixStream) -> io::Result<bool> {
    Err(io::Error::from(io::ErrorKind::Unsupported))
}

fn trusted_executable(path: &Path) -> io::Result<PathBuf> {
    let path = path.canonicalize()?;
    let metadata = fs::metadata(&path)?;
    // SAFETY: geteuid has no pointer arguments or mutable state.
    let uid = unsafe { libc::geteuid() };
    if !metadata.is_file()
        || metadata.mode() & 0o022 != 0
        || metadata.mode() & 0o111 == 0
        || !(metadata.uid() == 0 || metadata.uid() == uid)
    {
        return Err(io::Error::from(io::ErrorKind::PermissionDenied));
    }
    Ok(path)
}

fn start_terminal(plan: &Plan) -> io::Result<Child> {
    trusted_executable(&plan.relay)?;
    trusted_executable(Path::new("/usr/bin/systemd-run"))?;
    let terminal = trusted_executable(Path::new("/usr/bin/gnome-terminal"))?;
    let mut command = Command::new(terminal);
    command
        .args(plan.arguments())
        .env_clear()
        .envs(std::env::vars_os().filter(|(name, _)| desktop_environment(name)))
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null());
    // Native bus identity, not an arbitrary injected session bus address. The
    // existing relay verifies ownership, socket mode and exact live unit.
    let uid = unsafe { libc::geteuid() };
    command
        .env("XDG_RUNTIME_DIR", format!("/run/user/{uid}"))
        .env(
            "DBUS_SESSION_BUS_ADDRESS",
            format!("unix:path=/run/user/{uid}/bus"),
        );
    command.spawn()
}

fn desktop_environment(name: &std::ffi::OsStr) -> bool {
    matches!(
        name.to_str(),
        Some(
            "PATH"
                | "HOME"
                | "USER"
                | "LOGNAME"
                | "LANG"
                | "LC_ALL"
                | "DISPLAY"
                | "WAYLAND_DISPLAY"
                | "XAUTHORITY"
                | "XDG_CURRENT_DESKTOP"
                | "XDG_SESSION_TYPE"
                | "TERM"
                | "COLORTERM"
        )
    )
}

#[cfg(target_os = "linux")]
fn stop_unit(token: &str) -> bool {
    hormuz_client_relay::stop_linux_user_service(token).is_ok()
}
#[cfg(not(target_os = "linux"))]
fn stop_unit(_: &str) -> bool {
    false
}

fn reap(child: &mut Child) {
    let _ = child.kill();
    let _ = child.wait();
}

fn manage(
    plan: Plan,
    receiver: mpsc::Receiver<Request>,
    phase: Arc<Mutex<Phase>>,
    backend: Arc<dyn Backend>,
    notify: Notifier,
) {
    let publish = |next| {
        *phase.lock().unwrap() = next;
        notify();
    };
    let Ok(mut owner) = SocketOwner::open(&plan) else {
        publish(Phase::Failed);
        return;
    };
    let Ok(mut child) = backend.launch(&plan) else {
        publish(Phase::Failed);
        return;
    };
    let mut connected = false;
    let mut child_exited = false;
    let deadline = Instant::now() + Duration::from_secs(20);
    loop {
        match receiver.recv_timeout(Duration::from_millis(50)) {
            Ok(Request::Stop(answer)) => {
                owner.close();
                // No accepted owner connection means no custody, discovery or
                // AI client could have started. Closing the socket also blocks
                // a late terminal/server spawn. Stop remains best-effort then.
                let stopped = backend.stop(&plan.token) || !connected;
                if stopped {
                    reap(&mut child);
                    publish(Phase::Finished);
                    let _ = answer.send(true);
                    return;
                }
                publish(Phase::StopFailed);
                let _ = answer.send(false);
            }
            Err(mpsc::RecvTimeoutError::Disconnected) => {
                owner.close();
                let _ = backend.stop(&plan.token);
                reap(&mut child);
                return;
            }
            Err(mpsc::RecvTimeoutError::Timeout) => {}
        }
        if !connected {
            match owner.accept() {
                Ok(true) => {
                    connected = true;
                    publish(Phase::Running);
                }
                Ok(false) => {}
                Err(_) => {
                    owner.close();
                    let _ = backend.stop(&plan.token);
                    reap(&mut child);
                    publish(Phase::Failed);
                    return;
                }
            }
            if !connected && Instant::now() >= deadline {
                owner.close();
                let _ = backend.stop(&plan.token);
                reap(&mut child);
                publish(Phase::Failed);
                return;
            }
        }
        if !child_exited && child.try_wait().is_ok_and(|status| status.is_some()) {
            child_exited = true;
            owner.close();
            if *phase.lock().unwrap() == Phase::StopFailed {
                continue;
            }
            if backend.stop(&plan.token) || !connected {
                publish(if connected {
                    Phase::Finished
                } else {
                    Phase::Failed
                });
                return;
            }
            publish(Phase::StopFailed);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn literal_service_plan_has_no_credential_and_keeps_the_existing_containment_policy() {
        let plan = Plan::new(
            PathBuf::from("/home/test/.hormuz-native"),
            "d23f09e0-783b-457d-854f-27e3660f84af".into(),
            PathBuf::from("/opt/hormuz/hormuz-client-relay"),
        )
        .unwrap();
        let arguments = plan.arguments();
        for option in [
            "--expand-environment=no",
            "--setenv=PATH",
            "--property=KillMode=control-group",
            "--property=KillSignal=SIGKILL",
            "--property=ExitType=main",
            "--property=Restart=no",
            "--property=RemainAfterExit=no",
            "--owner-socket",
        ] {
            assert!(arguments.iter().any(|value| value == option));
        }
        assert_eq!(arguments.last().unwrap(), plan.socket.as_os_str());
        assert!(plan.socket.starts_with(&plan.root));
        assert_eq!(plan.token.len(), 32);
        assert!(!arguments
            .iter()
            .any(|argument| argument.to_string_lossy().contains("hox_a_")));
        assert!(Plan::new("relative".into(), plan.profile.clone(), plan.relay.clone()).is_err());
        assert!(Plan::new(plan.root.clone(), "../other".into(), plan.relay.clone()).is_err());
    }
    #[test]
    fn terminal_environment_forwards_desktop_capabilities_not_credentials_or_code_injection() {
        for allowed in ["PATH", "DISPLAY", "WAYLAND_DISPLAY", "XAUTHORITY"] {
            assert!(desktop_environment(allowed.as_ref()));
        }
        for blocked in [
            "OPENAI_API_KEY",
            "ANTHROPIC_API_KEY",
            "HORMUZ_LOCAL_RELAY_TOKEN",
            "LD_PRELOAD",
            "PYTHONPATH",
            "DBUS_SESSION_BUS_ADDRESS",
            "XDG_RUNTIME_DIR",
        ] {
            assert!(!desktop_environment(blocked.as_ref()));
        }
    }

    #[cfg(target_os = "linux")]
    mod lifecycle {
        use super::*;
        use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};

        struct Fixture {
            launched: Arc<AtomicBool>,
            stop_ok: Arc<AtomicBool>,
            stops: Arc<AtomicUsize>,
            delay: bool,
        }
        impl Backend for Fixture {
            fn launch(&self, plan: &Plan) -> io::Result<Child> {
                self.launched.store(true, Ordering::SeqCst);
                if self.delay {
                    thread::sleep(Duration::from_millis(150));
                }
                Command::new("/usr/bin/python3").args(["-c", "import socket,sys,pathlib\ns=socket.socket(socket.AF_UNIX)\ns.connect(sys.argv[1])\nif s.recv(1)!=b'\\x01': sys.exit(0)\npathlib.Path(sys.argv[2]).touch()\ns.recv(1)\n"])
                    .arg(&plan.socket).arg(plan.root.join("client-started"))
                    .env_clear().stdin(Stdio::null()).stdout(Stdio::null()).stderr(Stdio::null()).spawn()
            }
            fn stop(&self, _: &str) -> bool {
                self.stops.fetch_add(1, Ordering::SeqCst);
                self.stop_ok.load(Ordering::SeqCst)
            }
        }
        fn root() -> tempfile::TempDir {
            let directory = tempfile::tempdir().unwrap();
            fs::set_permissions(directory.path(), fs::Permissions::from_mode(0o700)).unwrap();
            directory
        }
        fn start(directory: &Path, fixture: Fixture) -> Terminal {
            Terminal::start_with(
                directory.to_owned(),
                "d23f09e0-783b-457d-854f-27e3660f84af".into(),
                "/opt/hormuz/hormuz-client-relay".into(),
                Arc::new(fixture),
                Box::new(|| true),
            )
            .unwrap()
        }
        fn wait(condition: impl Fn() -> bool) {
            let deadline = Instant::now() + Duration::from_secs(3);
            while !condition() {
                assert!(Instant::now() < deadline);
                thread::sleep(Duration::from_millis(2));
            }
        }
        #[test]
        fn accepted_same_uid_lease_is_owned_until_explicit_stop() {
            let directory = root();
            let stops = Arc::new(AtomicUsize::new(0));
            let mut terminal = start(
                directory.path(),
                Fixture {
                    launched: Arc::new(AtomicBool::new(false)),
                    stop_ok: Arc::new(AtomicBool::new(true)),
                    stops: stops.clone(),
                    delay: false,
                },
            );
            wait(|| {
                terminal.phase() == Phase::Running
                    && directory.path().join("client-started").exists()
            });
            // Rendering, folding or hiding a panel has no terminal stop API.
            thread::sleep(Duration::from_millis(70));
            assert_eq!(terminal.phase(), Phase::Running);
            assert_eq!(stops.load(Ordering::SeqCst), 0);
            terminal.stop().unwrap();
            assert_eq!(terminal.phase(), Phase::Finished);
            assert!(terminal.worker.is_none());
            assert_eq!(stops.load(Ordering::SeqCst), 1);
            assert!(!fs::read_dir(directory.path()).unwrap().any(|entry| entry
                .unwrap()
                .path()
                .extension()
                .is_some_and(|value| value == "sock")));
        }
        #[test]
        fn quit_during_launch_does_not_acknowledge_a_delayed_client() {
            let directory = root();
            let launched = Arc::new(AtomicBool::new(false));
            let mut terminal = start(
                directory.path(),
                Fixture {
                    launched: launched.clone(),
                    stop_ok: Arc::new(AtomicBool::new(false)),
                    stops: Arc::new(AtomicUsize::new(0)),
                    delay: true,
                },
            );
            wait(|| launched.load(Ordering::SeqCst));
            terminal.stop().unwrap();
            assert_eq!(terminal.phase(), Phase::Finished);
            assert!(
                !directory.path().join("client-started").exists(),
                "no ACK means no custody/discovery/client start"
            );
            assert!(terminal.worker.is_none());
        }
        #[test]
        fn failed_stop_keeps_the_exact_unit_owned_without_automatic_retries() {
            let directory = root();
            let stop_ok = Arc::new(AtomicBool::new(false));
            let stops = Arc::new(AtomicUsize::new(0));
            let mut terminal = start(
                directory.path(),
                Fixture {
                    launched: Arc::new(AtomicBool::new(false)),
                    stop_ok: stop_ok.clone(),
                    stops: stops.clone(),
                    delay: false,
                },
            );
            wait(|| terminal.phase() == Phase::Running);
            assert!(terminal.stop().is_err());
            assert_eq!(terminal.phase(), Phase::StopFailed);
            assert!(terminal.worker.is_some());
            assert!(terminal.commands.is_some());
            thread::sleep(Duration::from_millis(160));
            assert_eq!(
                stops.load(Ordering::SeqCst),
                1,
                "a failed stop is not retried unchanged"
            );
            stop_ok.store(true, Ordering::SeqCst);
            terminal.stop().unwrap();
            assert!(terminal.worker.is_none());
            assert_eq!(stops.load(Ordering::SeqCst), 2);
        }
    }
}
