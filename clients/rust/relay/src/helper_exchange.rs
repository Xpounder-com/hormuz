//! Bounded macOS optimizer stdio. Only the parent's owned pipe ends become
//! nonblocking; the helper keeps ordinary stdin/stdout. No content is written
//! to temporary files, and no I/O thread can outlive the exchange.
#![forbid(unsafe_code)]

use rustix::fs::{fcntl_getfl, fcntl_setfl, OFlags};
use std::io::{self, Read, Write};
use std::os::fd::AsFd;
use std::process::{Child, Command, Stdio};
use std::thread;
use std::time::{Duration, Instant};
use zeroize::Zeroizing;

const CHUNK_BYTES: usize = 8192;
const POLL_INTERVAL: Duration = Duration::from_millis(10);

pub(super) fn run(
    command: &mut Command,
    gateway: &[u8],
    original: &[u8],
    budget: Duration,
    max_body_bytes: usize,
) -> Option<Vec<u8>> {
    let length = u16::try_from(gateway.len()).ok()?.to_be_bytes();
    if original.len() > max_body_bytes {
        return None;
    }
    let max_output_bytes = max_body_bytes.checked_add(1)?;
    let deadline = Instant::now().checked_add(budget)?;
    let mut input = Zeroizing::new(Vec::new());
    input.extend_from_slice(&length);
    input.extend_from_slice(gateway);
    input.extend_from_slice(original);
    let mut child = OwnedTransform(
        command
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::null())
            .spawn()
            .ok()?,
    );
    let stdin = child.0.stdin.take()?;
    let stdout = child.0.stdout.take()?;
    exchange(child, stdin, stdout, &input, deadline, max_output_bytes).ok()
}

fn nonblocking(pipe: &impl AsFd) -> io::Result<()> {
    let flags = fcntl_getfl(pipe)?;
    fcntl_setfl(pipe, flags | OFlags::NONBLOCK)?;
    Ok(())
}

fn exchange(
    mut child: OwnedTransform,
    stdin: impl AsFd + Write,
    mut stdout: impl AsFd + Read,
    input: &[u8],
    deadline: Instant,
    max_output_bytes: usize,
) -> io::Result<Vec<u8>> {
    nonblocking(&stdin)?;
    nonblocking(&stdout)?;
    let mut stdin = Some(stdin);
    let mut written = 0;
    let mut output = Zeroizing::new(Vec::new());
    let mut buffer = Zeroizing::new([0_u8; CHUNK_BYTES]);
    let mut eof = false;
    let mut exited = false;

    loop {
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

struct OwnedTransform(Child);
impl Drop for OwnedTransform {
    fn drop(&mut self) {
        // Child caches an observed exit status, so kill/wait after try_wait
        // has reaped it cannot target a recycled PID. This owns the direct
        // child only; process-tree containment is a separate native adapter.
        let _ = self.0.kill();
        let _ = self.0.wait();
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use rustix::process::{waitpid, Pid, WaitOptions};

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
            waitpid(Some(pid), WaitOptions::NOHANG),
            Err(rustix::io::Errno::CHILD),
            "the direct helper must already have been reaped"
        );
    }

    #[test]
    fn preserves_request_frame_and_drains_partial_reads_and_writes() {
        let gateway = b"https://gateway.example.invalid";
        let body = vec![b'x'; 1024 * 1024];
        let result = run(
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
        let output = run(
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
        assert!(run(&mut command, b"origin", &[0; 8], BUDGET, 7).is_none());
        assert!(run(&mut command, &vec![0; 65536], b"body", BUDGET, 8).is_none());
        assert!(!marker.exists());
    }

    #[test]
    fn retained_pipe_ends_cannot_extend_deadline_after_helper_exit() {
        // The test owns both processes. The holder has the same inherited pipe
        // ends as a helper descendant, but is independently reaped even when
        // an assertion fails. No background orphan or PID-based kill is needed.
        let (input_read, input_write) = io::pipe().unwrap();
        let (output_read, output_write) = io::pipe().unwrap();
        let mut holder = OwnedTransform(
            Command::new("/bin/sleep")
                .arg("10")
                .stdin(input_read.try_clone().unwrap())
                .stdout(output_write.try_clone().unwrap())
                .spawn()
                .unwrap(),
        );
        let child = OwnedTransform(
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
        )
        .unwrap_err();
        assert_eq!(error.kind(), io::ErrorKind::TimedOut);
        assert!(started.elapsed() < TEST_LIMIT);
        assert_reaped(pid);
        assert!(holder.0.try_wait().unwrap().is_none());
    }

    #[test]
    fn timeout_kills_and_reaps_helper_that_never_reads_input() {
        let mut child = OwnedTransform(
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
        )
        .unwrap_err();
        assert_eq!(error.kind(), io::ErrorKind::TimedOut);
        assert!(started.elapsed() < TEST_LIMIT);
        assert_reaped(pid);
    }

    #[test]
    fn oversized_output_and_failed_exit_fail_without_waiting_for_deadline() {
        let started = Instant::now();
        assert!(run(
            Command::new("/usr/bin/head").args(["-c", "131072", "/dev/zero"]),
            b"origin",
            b"body",
            Duration::from_secs(5),
            1024,
        )
        .is_none());
        assert!(run(
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
