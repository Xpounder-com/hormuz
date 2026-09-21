//! Bounded Windows optimizer exchange. The helper starts suspended and joins
//! a kill-on-close Job Object before it can inherit either pipe into a child.
//! Request bytes remain in memory and are never written to an artifact.

use crate::process_scope::OwnedClient;
use hormuz_client_relay::OptimizerCancellation;
use std::io::{self, Read, Write};
use std::process::{Command, ExitStatus, Stdio};
use std::sync::mpsc;
use std::thread::{self, JoinHandle};
use std::time::{Duration, Instant};
use zeroize::Zeroizing;

const POLL_INTERVAL: Duration = Duration::from_millis(10);
const CLEANUP_BUDGET: Duration = Duration::from_secs(2);

enum PipeResult {
    Written(io::Result<()>),
    Read(io::Result<Zeroizing<Vec<u8>>>),
}

pub(crate) fn run(
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
    let max_output_bytes = max_body_bytes.checked_add(1)?;
    let deadline = Instant::now().checked_add(budget)?;
    let capacity = 2_usize
        .checked_add(gateway.len())?
        .checked_add(original.len())?;
    let mut input = Zeroizing::new(Vec::with_capacity(capacity));
    input.extend_from_slice(&length);
    input.extend_from_slice(gateway);
    input.extend_from_slice(original);
    if cancelled() {
        return None;
    }

    let mut child = OwnedClient::spawn(
        command
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::null()),
    )
    .ok()?;
    let mut stdin = child.take_stdin()?;
    let stdout = child.take_stdout()?;
    let (sender, receiver) = mpsc::channel();
    let writer_sender = sender.clone();
    let writer = thread::Builder::new()
        .name("hormuz-optimizer-input".into())
        .spawn(move || {
            let _ = writer_sender.send(PipeResult::Written(stdin.write_all(&input)));
        })
        .ok()?;
    let reader = thread::Builder::new()
        .name("hormuz-optimizer-output".into())
        .spawn(move || {
            let mut output = Zeroizing::new(Vec::new());
            let result = stdout
                .take(
                    u64::try_from(max_output_bytes)
                        .unwrap_or(u64::MAX)
                        .saturating_add(1),
                )
                .read_to_end(&mut output)
                .and_then(|_| {
                    if output.len() > max_output_bytes {
                        Err(io::ErrorKind::InvalidData.into())
                    } else {
                        Ok(output)
                    }
                });
            let _ = sender.send(PipeResult::Read(result));
        });
    let reader = match reader {
        Ok(handle) => handle,
        Err(_) => {
            drop(child);
            finish_pipe_thread(writer);
            return None;
        }
    };

    let mut status: Option<ExitStatus> = None;
    let mut wrote = false;
    let mut output = None;
    let mut failed = false;
    while Instant::now() < deadline && !cancelled() {
        if status.is_none() {
            match child.try_wait_status() {
                Ok(Some(exit)) => status = Some(exit),
                Ok(None) => {}
                Err(_) => {
                    failed = true;
                    break;
                }
            }
        }
        if status.is_some() && wrote && output.is_some() {
            break;
        }
        if wrote && output.is_some() {
            // Both producers have sent their one result, so channel closure
            // is expected. Continue checking the direct helper's exit under
            // the same deadline instead of treating closure as an I/O error.
            thread::sleep(POLL_INTERVAL.min(deadline.saturating_duration_since(Instant::now())));
            continue;
        }
        match receiver
            .recv_timeout(POLL_INTERVAL.min(deadline.saturating_duration_since(Instant::now())))
        {
            Ok(PipeResult::Written(Ok(()))) => wrote = true,
            Ok(PipeResult::Read(Ok(bytes))) => output = Some(bytes),
            Ok(PipeResult::Written(Err(_)) | PipeResult::Read(Err(_))) => {
                failed = true;
                break;
            }
            Err(mpsc::RecvTimeoutError::Timeout) => {}
            Err(mpsc::RecvTimeoutError::Disconnected) => {
                failed = true;
                break;
            }
        }
    }

    let was_cancelled = cancelled();
    // The job closes before any join. Its kernel lifetime rule stops children
    // that inherited the pipe ends, including after the direct helper exited.
    drop(child);
    if was_cancelled || failed || status.is_none() || !wrote || output.is_none() {
        cancel_pipe_io(&writer);
        cancel_pipe_io(&reader);
    }
    finish_pipe_threads(writer, reader);
    if !was_cancelled
        && !cancelled()
        && !failed
        && status.is_some_and(|exit| exit.success())
        && wrote
    {
        let mut output = output?;
        Some(std::mem::take(&mut *output))
    } else {
        None
    }
}

fn finish_pipe_threads(writer: JoinHandle<()>, reader: JoinHandle<()>) {
    let deadline = Instant::now() + CLEANUP_BUDGET;
    while !(writer.is_finished() && reader.is_finished()) {
        cancel_pipe_io(&writer);
        cancel_pipe_io(&reader);
        if Instant::now() >= deadline {
            // The Job handle is already closed. Exit the dedicated relay
            // without a crash dump rather than return with data-bearing
            // workers still running; the OS closes their remaining handles.
            std::process::exit(1);
        }
        thread::sleep(POLL_INTERVAL);
    }
    let _ = writer.join();
    let _ = reader.join();
}

fn finish_pipe_thread(worker: JoinHandle<()>) {
    let deadline = Instant::now() + CLEANUP_BUDGET;
    while !worker.is_finished() {
        cancel_pipe_io(&worker);
        if Instant::now() >= deadline {
            std::process::exit(1);
        }
        thread::sleep(POLL_INTERVAL);
    }
    let _ = worker.join();
}

#[allow(unsafe_code)]
fn cancel_pipe_io(handle: &JoinHandle<()>) {
    use std::os::windows::io::AsRawHandle;
    use windows_sys::Win32::System::IO::CancelSynchronousIo;

    if !handle.is_finished() {
        // Each worker performs only synchronous I/O on one owned anonymous
        // pipe. ERROR_NOT_FOUND simply means it finished between the checks.
        let _ = unsafe { CancelSynchronousIo(handle.as_raw_handle()) };
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;
    use std::path::{Path, PathBuf};
    use std::sync::{
        atomic::{AtomicBool, Ordering},
        Arc,
    };

    const WORKER: &str = "helper_exchange_windows::tests::worker";
    const LATE_MARKER_DELAY: Duration = Duration::from_secs(6);
    const CANCEL_MARKER_DELAY: Duration = Duration::from_secs(2);

    fn command(mode: &str, marker: &Path) -> Command {
        let mut command = Command::new(std::env::current_exe().unwrap());
        command
            .args(["--exact", WORKER])
            .env_clear()
            .env("HORMUZ_WINDOWS_EXCHANGE_MODE", mode)
            .env("HORMUZ_WINDOWS_EXCHANGE_MARKER", marker);
        command
    }

    fn exchange(mode: &str, body: &[u8], budget: Duration, maximum: usize) -> Option<Vec<u8>> {
        let temporary = tempfile::tempdir().unwrap();
        run_uncancelled(
            &mut command(mode, &temporary.path().join("late")),
            b"origin",
            body,
            budget,
            maximum,
        )
    }

    fn run_uncancelled(
        command: &mut Command,
        gateway: &[u8],
        original: &[u8],
        budget: Duration,
        maximum: usize,
    ) -> Option<Vec<u8>> {
        run_controlled(command, gateway, original, budget, maximum, &|| false)
    }

    #[test]
    fn bidirectional_exchange_and_output_before_input_complete() {
        let body = vec![b'x'; 1024 * 1024];
        let output = exchange("output_first", &body, Duration::from_secs(5), body.len()).unwrap();
        // The Rust test harness also writes its own status to stdout; locate
        // the worker's uninterrupted synthetic frame inside that output.
        let mut frame = vec![1];
        frame.extend(std::iter::repeat_n(b'y', 131_072));
        assert!(output
            .windows(frame.len())
            .any(|bytes| bytes == frame.as_slice()));
    }

    #[test]
    fn oversized_output_fails_closed_and_stops_helper() {
        let temporary = tempfile::tempdir().unwrap();
        let marker = temporary.path().join("running");
        assert!(run_uncancelled(
            &mut command("overflow", &marker),
            b"origin",
            b"body",
            Duration::from_secs(5),
            1024,
        )
        .is_none());
        assert!(marker.exists(), "fake overflow helper did not start");
    }

    #[test]
    fn blocked_reader_and_writer_finish_after_deadline_cleanup() {
        let body = vec![b'x'; 1024 * 1024];
        let started = Instant::now();
        assert!(exchange("never_reads", &body, Duration::from_millis(150), body.len()).is_none());
        assert!(started.elapsed() < Duration::from_secs(3));
        let started = Instant::now();
        assert!(exchange("never_writes", b"body", Duration::from_millis(150), 16).is_none());
        assert!(started.elapsed() < Duration::from_secs(3));
    }

    #[test]
    fn cancellation_closes_job_and_pipe_workers_before_deadline() {
        let temporary = tempfile::tempdir().unwrap();
        let marker = temporary.path().join("cancelled");
        let ready = marker.with_extension("ready");
        let cancelled = Arc::new(AtomicBool::new(false));
        let setter = cancelled.clone();
        let canceller = thread::spawn(move || {
            let deadline = Instant::now() + Duration::from_secs(2);
            while !ready.exists() && Instant::now() < deadline {
                thread::sleep(POLL_INTERVAL);
            }
            assert!(ready.exists(), "fake cancellable helper did not start");
            setter.store(true, Ordering::SeqCst);
        });
        let started = Instant::now();
        assert!(run_controlled(
            &mut command("cancel_wait", &marker),
            b"origin",
            &vec![b'x'; 1024 * 1024],
            Duration::from_secs(10),
            1024 * 1024,
            &|| cancelled.load(Ordering::SeqCst),
        )
        .is_none());
        canceller.join().unwrap();
        assert!(started.elapsed() < Duration::from_secs(3));
        thread::sleep(CANCEL_MARKER_DELAY + Duration::from_millis(500));
        assert!(!marker.exists(), "cancelled helper survived job cleanup");
    }

    #[test]
    fn inherited_pipe_ends_do_not_block_or_leave_late_descendants() {
        let temporary = tempfile::tempdir().unwrap();
        for mode in ["inherited_stdout", "inherited_stdin"] {
            let marker = temporary.path().join(mode);
            let body = vec![b'x'; 1024 * 1024];
            let started = Instant::now();
            assert!(run_uncancelled(
                &mut command(mode, &marker),
                b"origin",
                &body,
                Duration::from_secs(3),
                body.len(),
            )
            .is_none());
            // The three-second exchange plus two-second cleanup has a five-
            // second ceiling. A six-second inherited handle must not extend it.
            assert!(started.elapsed() < Duration::from_millis(5_500));
            assert!(
                marker.with_extension("ready").exists(),
                "fake descendant did not start"
            );
        }
        thread::sleep(LATE_MARKER_DELAY + Duration::from_millis(500));
        for mode in ["inherited_stdout", "inherited_stdin"] {
            assert!(
                !temporary.path().join(mode).exists(),
                "helper descendant survived job cleanup"
            );
        }
    }

    #[test]
    fn rejects_oversized_input_before_helper_launch() {
        let temporary = tempfile::tempdir().unwrap();
        let marker = temporary.path().join("launched");
        assert!(run_uncancelled(
            &mut command("mark", &marker),
            b"origin",
            b"too long",
            Duration::from_secs(1),
            3,
        )
        .is_none());
        assert!(!marker.exists());
    }

    // The descendant deliberately outlives its direct parent so the owning
    // Job Object, rather than this fake helper, must stop it.
    #[allow(clippy::zombie_processes)]
    #[test]
    fn worker() {
        let Ok(mode) = std::env::var("HORMUZ_WINDOWS_EXCHANGE_MODE") else {
            return;
        };
        let marker = PathBuf::from(std::env::var_os("HORMUZ_WINDOWS_EXCHANGE_MARKER").unwrap());
        if mode == "mark" {
            fs::write(marker, b"launched").unwrap();
            return;
        }
        if mode == "late_marker" {
            fs::write(marker.with_extension("ready"), b"running").unwrap();
            thread::sleep(LATE_MARKER_DELAY);
            fs::write(marker, b"survived").unwrap();
            return;
        }
        if mode == "cancel_wait" {
            fs::write(marker.with_extension("ready"), b"running").unwrap();
            thread::sleep(CANCEL_MARKER_DELAY);
            fs::write(marker, b"survived").unwrap();
            return;
        }
        if mode.starts_with("inherited_") {
            let mut descendant = command("late_marker", &marker);
            if mode == "inherited_stdout" {
                descendant.stdin(Stdio::null()).stdout(Stdio::inherit());
            } else {
                descendant.stdin(Stdio::inherit()).stdout(Stdio::null());
            }
            descendant.stderr(Stdio::null()).spawn().unwrap();
            let ready = marker.with_extension("ready");
            let deadline = Instant::now() + Duration::from_secs(2);
            while !ready.exists() && Instant::now() < deadline {
                thread::sleep(POLL_INTERVAL);
            }
            assert!(ready.exists(), "fake descendant did not start");
            return;
        }
        if mode == "never_reads" {
            thread::sleep(Duration::from_secs(10));
            return;
        }
        if mode == "never_writes" {
            let mut input = Vec::new();
            io::stdin().read_to_end(&mut input).unwrap();
            thread::sleep(Duration::from_secs(10));
            return;
        }
        if mode == "overflow" {
            fs::write(marker, b"running").unwrap();
            io::stdout().write_all(&[1; 2048]).unwrap();
            thread::sleep(Duration::from_secs(10));
            return;
        }
        if mode == "output_first" {
            io::stdout().write_all(&[1]).unwrap();
            io::stdout().write_all(&vec![b'y'; 131_072]).unwrap();
            let mut input = Vec::new();
            io::stdin().read_to_end(&mut input).unwrap();
            assert_eq!(input.len(), 2 + 6 + 1024 * 1024);
        }
    }
}
