//! Bounded Unix optimizer stdio. Only the parent's owned pipe ends become
//! nonblocking; the helper keeps ordinary stdin/stdout. No content is written
//! to temporary files, and no I/O thread can outlive the exchange.
#![forbid(unsafe_code)]

#[cfg(any(target_os = "macos", target_os = "linux"))]
use hormuz_client_relay::OptimizerCancellation;
use rustix::fs::{fcntl_getfl, fcntl_setfl, OFlags};
use std::io::{self, Read, Write};
use std::os::fd::AsFd;
#[cfg(target_os = "macos")]
use std::os::unix::process::CommandExt;
use std::process::{Child, Command, Stdio};
use std::thread;
use std::time::{Duration, Instant};
use zeroize::Zeroizing;

const CHUNK_BYTES: usize = 8192;
const POLL_INTERVAL: Duration = Duration::from_millis(10);

#[cfg(any(target_os = "macos", target_os = "linux"))]
pub(super) fn run(
    command: &mut Command,
    gateway: &[u8],
    original: &[u8],
    budget: Duration,
    max_body_bytes: usize,
    cancellation: &OptimizerCancellation,
) -> Option<Vec<u8>> {
    run_controlled(command, gateway, original, budget, max_body_bytes, &|| {
        cancellation.is_cancelled()
    })
}

fn run_controlled<C: Fn() -> bool>(
    command: &mut Command,
    gateway: &[u8],
    original: &[u8],
    budget: Duration,
    max_body_bytes: usize,
    cancelled: &C,
) -> Option<Vec<u8>> {
    if cancelled() {
        return None;
    }
    let length = u16::try_from(gateway.len()).ok()?.to_be_bytes();
    if original.len() > max_body_bytes {
        return None;
    }
    let mut input = Zeroizing::new(Vec::new());
    input.extend_from_slice(&length);
    input.extend_from_slice(gateway);
    input.extend_from_slice(original);
    capture_controlled(
        command,
        &input,
        budget,
        max_body_bytes.checked_add(1)?,
        cancelled,
    )
}

/// The Mac app remains the Keychain broker. Its one-line credential response
/// uses the same bounded, memory-only pipe exchange as optimizer responses.
#[cfg(target_os = "macos")]
pub(super) fn capture(
    command: &mut Command,
    input: &[u8],
    budget: Duration,
    limit: usize,
) -> Option<Vec<u8>> {
    capture_controlled(command, input, budget, limit, &|| false)
}

fn capture_controlled<C: Fn() -> bool>(
    command: &mut Command,
    input: &[u8],
    budget: Duration,
    max_output_bytes: usize,
    cancelled: &C,
) -> Option<Vec<u8>> {
    let deadline = Instant::now().checked_add(budget)?;
    if cancelled() {
        return None;
    }
    #[cfg(target_os = "macos")]
    command.process_group(0);
    let process = command
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .spawn()
        .ok()?;
    #[cfg(target_os = "macos")]
    let group = rustix::process::Pid::from_raw(process.id() as i32);
    let mut child = OwnedTransform(
        process,
        #[cfg(target_os = "macos")]
        group,
    );
    let stdin = child.0.stdin.take()?;
    let stdout = child.0.stdout.take()?;
    exchange(
        child,
        stdin,
        stdout,
        input,
        deadline,
        max_output_bytes,
        cancelled,
    )
    .ok()
}

fn nonblocking(pipe: &impl AsFd) -> io::Result<()> {
    let flags = fcntl_getfl(pipe)?;
    fcntl_setfl(pipe, flags | OFlags::NONBLOCK)?;
    Ok(())
}

fn exchange<C: Fn() -> bool>(
    child: OwnedTransform,
    stdin: impl AsFd + Write,
    mut stdout: impl AsFd + Read,
    input: &[u8],
    deadline: Instant,
    max_output_bytes: usize,
    cancelled: &C,
) -> io::Result<Vec<u8>> {
    #[cfg(not(target_os = "macos"))]
    let mut child = child;
    nonblocking(&stdin)?;
    nonblocking(&stdout)?;
    let mut stdin = Some(stdin);
    let mut written = 0_usize;
    let mut output = Zeroizing::new(Vec::new());
    let mut buffer = Zeroizing::new([0_u8; CHUNK_BYTES]);
    let mut eof = false;
    let mut exited = false;

    loop {
        if cancelled() {
            return Err(io::ErrorKind::Interrupted.into());
        }
        if Instant::now() >= deadline {
            return Err(io::ErrorKind::TimedOut.into());
        }
        let mut progressed = false;
        if let Some(pipe) = &mut stdin {
            let end = written.saturating_add(CHUNK_BYTES).min(input.len());
            match pipe.write(&input[written..end]) {
                Ok(0) if written < input.len() => return Err(io::ErrorKind::WriteZero.into()),
                Ok(count) => {
                    written += count;
                    progressed = count > 0;
                    if written == input.len() {
                        // EOF is part of the request framing. Drop only this
                        // owned end; a descendant retaining its read end must
                        // not hold the caller in a writer-thread join.
                        stdin = None;
                    }
                }
                Err(error) if retryable(&error) => {}
                Err(error) => return Err(error),
            }
        }
        if !eof {
            // Drain alongside writes so a helper which fills stdout before
            // reading stdin cannot deadlock the exchange.
            match stdout.read(buffer.as_mut()) {
                Ok(0) => eof = true,
                Ok(count) => {
                    if count > max_output_bytes.saturating_sub(output.len()) {
                        return Err(io::ErrorKind::InvalidData.into());
                    }
                    output.extend_from_slice(&buffer[..count]);
                    progressed = true;
                }
                Err(error) if retryable(&error) => {}
                Err(error) => return Err(error),
            }
        }
        if !exited {
            #[cfg(target_os = "macos")]
            if let Some(status) = rustix::process::waitid(
                rustix::process::WaitId::Pid(
                    rustix::process::Pid::from_raw(child.0.id() as i32).unwrap(),
                ),
                rustix::process::WaitIdOptions::EXITED
                    | rustix::process::WaitIdOptions::NOHANG
                    | rustix::process::WaitIdOptions::NOWAIT,
            )? {
                // Keep the exited leader waitable until Drop has stopped its
                // group. Its PID cannot be recycled before that cleanup.
                if status.exit_status() != Some(0) {
                    return Err(io::Error::other("optimizer helper failed"));
                }
                exited = true;
            }
            #[cfg(not(target_os = "macos"))]
            if let Some(status) = child.0.try_wait()? {
                if !status.success() {
                    return Err(io::Error::other("optimizer helper failed"));
                }
                exited = true;
            }
        }
        if exited && eof && stdin.is_none() {
            return Ok(std::mem::take(&mut *output));
        }
        if !progressed {
            thread::sleep(POLL_INTERVAL.min(deadline.saturating_duration_since(Instant::now())));
        }
    }
}

fn retryable(error: &io::Error) -> bool {
    matches!(
        error.kind(),
        io::ErrorKind::WouldBlock | io::ErrorKind::Interrupted
    )
}

struct OwnedTransform(
    Child,
    #[cfg(target_os = "macos")] Option<rustix::process::Pid>,
);
#[cfg(test)]
impl OwnedTransform {
    fn new(child: Child) -> Self {
        Self(
            child,
            #[cfg(target_os = "macos")]
            None,
        )
    }
}
impl Drop for OwnedTransform {
    fn drop(&mut self) {
        #[cfg(target_os = "macos")]
        {
            use rustix::process::{kill_process_group, waitid, Signal, WaitId, WaitIdOptions};
            if let Some(pid) = self.1 {
                // Only signal a group whose leader is still our waitable child;
                // never enumerate or kill an arbitrary/recycled PID tree.
                if waitid(
                    WaitId::Pid(pid),
                    WaitIdOptions::EXITED | WaitIdOptions::NOHANG | WaitIdOptions::NOWAIT,
                )
                .is_ok()
                {
                    let _ = kill_process_group(pid, Signal::KILL);
                }
            }
        }
        // Child caches an observed exit status, so kill/wait after try_wait
        // has reaped it cannot target a recycled PID. This owns the direct
        // child. Mac helper cleanup also covers ordinary same-group descendants,
        // but not a deliberately detached process which calls setsid/setpgid.
        let _ = self.0.kill();
        let _ = self.0.wait();
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use rustix::process::{waitpid, Pid, WaitOptions};
    use std::sync::{
        atomic::{AtomicBool, Ordering},
        Arc,
    };

    const BUDGET: Duration = Duration::from_millis(150);
    const TEST_LIMIT: Duration = Duration::from_secs(2);

    fn shell(script: &str) -> Command {
        let mut command = Command::new("/bin/sh");
        command.arg("-c").arg(script);
        command
    }

    fn assert_reaped(pid: u32) {
        let pid = Pid::from_raw(pid.try_into().unwrap()).unwrap();
        assert_eq!(
            waitpid(Some(pid), WaitOptions::NOHANG).unwrap_err(),
            rustix::io::Errno::CHILD,
            "the direct helper must already have been reaped"
        );
    }

    fn run_uncancelled(
        command: &mut Command,
        gateway: &[u8],
        original: &[u8],
        budget: Duration,
        max_body_bytes: usize,
    ) -> Option<Vec<u8>> {
        run_controlled(command, gateway, original, budget, max_body_bytes, &|| {
            false
        })
    }

    #[cfg(target_os = "macos")]
    #[test]
    fn same_group_helper_descendants_stop_after_direct_exit() {
        let temporary = tempfile::tempdir().unwrap();
        let marker = temporary.path().join("descendant-survived");
        let mut command = shell("(sleep 0.25; : > \"$1\") >/dev/null 2>&1 & printf ready");
        command.arg("fixture").arg(&marker);
        let output =
            run_uncancelled(&mut command, b"origin", b"body", Duration::from_secs(2), 32).unwrap();
        assert_eq!(output, b"ready");
        // kill(pid, 0) also succeeds for an already dead, launchd-owned zombie.
        // Observe the descendant's actual ability to keep doing work instead.
        thread::sleep(Duration::from_millis(500));
        assert!(
            !marker.exists(),
            "ordinary helper descendant survived cleanup"
        );
    }

    #[test]
    fn preserves_request_frame_and_drains_partial_reads_and_writes() {
        let gateway = b"https://gateway.example.invalid";
        let body = vec![b'x'; 1024 * 1024];
        let result = run_uncancelled(
            &mut Command::new("/bin/cat"),
            gateway,
            &body,
            Duration::from_secs(5),
            body.len() + gateway.len() + 2,
        )
        .unwrap();
        assert_eq!(&result[..2], &(gateway.len() as u16).to_be_bytes());
        assert_eq!(&result[2..2 + gateway.len()], gateway);
        assert_eq!(&result[2 + gateway.len()..], body);
    }

    #[test]
    fn drains_output_while_helper_has_not_yet_read_input() {
        let output = run_uncancelled(
            &mut shell("head -c 131072 /dev/zero; cat >/dev/null"),
            b"https://gateway.example.invalid",
            &vec![b'x'; 1024 * 1024],
            Duration::from_secs(5),
            1024 * 1024,
        )
        .unwrap();
        assert_eq!(output, vec![0; 131072]);
    }

    #[test]
    fn refuses_oversized_input_and_origin_before_launch() {
        let temporary = tempfile::tempdir().unwrap();
        let marker = temporary.path().join("launched");
        let mut command = shell("touch \"$1\"");
        command.arg("fixture").arg(&marker);
        assert!(run_uncancelled(&mut command, b"origin", &[0; 8], BUDGET, 7).is_none());
        assert!(run_uncancelled(&mut command, &vec![0; 65536], b"body", BUDGET, 8).is_none());
        assert!(!marker.exists());
    }

    #[test]
    fn retained_pipe_ends_cannot_extend_deadline_after_helper_exit() {
        // The test owns both processes. The holder has the same inherited pipe
        // ends as a helper descendant, but is independently reaped even when
        // an assertion fails. No background orphan or PID-based kill is needed.
        let (input_read, input_write) = io::pipe().unwrap();
        let (output_read, output_write) = io::pipe().unwrap();
        let mut holder = OwnedTransform::new(
            Command::new("/bin/sleep")
                .arg("10")
                .stdin(input_read.try_clone().unwrap())
                .stdout(output_write.try_clone().unwrap())
                .spawn()
                .unwrap(),
        );
        let child = OwnedTransform::new(
            Command::new("/usr/bin/true")
                .stdin(input_read)
                .stdout(output_write)
                .spawn()
                .unwrap(),
        );
        let pid = child.0.id();
        let started = Instant::now();
        let error = exchange(
            child,
            input_write,
            output_read,
            &vec![b'x'; 1024 * 1024],
            started + BUDGET,
            1024 * 1024,
            &|| false,
        )
        .unwrap_err();
        assert_eq!(error.kind(), io::ErrorKind::TimedOut);
        assert!(started.elapsed() < TEST_LIMIT);
        assert_reaped(pid);
        assert!(holder.0.try_wait().unwrap().is_none());
    }

    #[test]
    fn timeout_kills_and_reaps_helper_that_never_reads_input() {
        let mut child = OwnedTransform::new(
            Command::new("/bin/sleep")
                .arg("10")
                .stdin(Stdio::piped())
                .stdout(Stdio::piped())
                .spawn()
                .unwrap(),
        );
        let pid = child.0.id();
        let stdin = child.0.stdin.take().unwrap();
        let stdout = child.0.stdout.take().unwrap();
        let started = Instant::now();
        let error = exchange(
            child,
            stdin,
            stdout,
            &vec![b'x'; 1024 * 1024],
            started + BUDGET,
            1024 * 1024,
            &|| false,
        )
        .unwrap_err();
        assert_eq!(error.kind(), io::ErrorKind::TimedOut);
        assert!(started.elapsed() < TEST_LIMIT);
        assert_reaped(pid);
    }

    #[test]
    fn cancellation_kills_and_reaps_helper_before_deadline() {
        let mut child = OwnedTransform::new(
            Command::new("/bin/sleep")
                .arg("10")
                .stdin(Stdio::piped())
                .stdout(Stdio::piped())
                .spawn()
                .unwrap(),
        );
        let pid = child.0.id();
        let stdin = child.0.stdin.take().unwrap();
        let stdout = child.0.stdout.take().unwrap();
        let cancelled = Arc::new(AtomicBool::new(false));
        let setter = cancelled.clone();
        let canceller = thread::spawn(move || {
            thread::sleep(Duration::from_millis(50));
            setter.store(true, Ordering::SeqCst);
        });
        let started = Instant::now();
        let error = exchange(
            child,
            stdin,
            stdout,
            &vec![b'x'; 1024 * 1024],
            started + Duration::from_secs(5),
            1024 * 1024,
            &|| cancelled.load(Ordering::SeqCst),
        )
        .unwrap_err();
        canceller.join().unwrap();
        assert_eq!(error.kind(), io::ErrorKind::Interrupted);
        assert!(started.elapsed() < TEST_LIMIT);
        assert_reaped(pid);
    }

    #[test]
    fn oversized_output_and_failed_exit_fail_without_waiting_for_deadline() {
        let started = Instant::now();
        assert!(run_uncancelled(
            Command::new("/usr/bin/head").args(["-c", "131072", "/dev/zero"]),
            b"origin",
            b"body",
            Duration::from_secs(5),
            1024,
        )
        .is_none());
        assert!(run_uncancelled(
            &mut shell("cat >/dev/null; printf '\\001changed'; exit 37"),
            b"origin",
            b"body",
            Duration::from_secs(5),
            1024,
        )
        .is_none());
        assert!(started.elapsed() < TEST_LIMIT);
    }
}
