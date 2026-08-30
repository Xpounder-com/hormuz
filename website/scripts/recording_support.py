"""Bounded recording and clean-source checks; no Hormuz runtime dependency."""

from __future__ import annotations

import os
from pathlib import Path
from queue import Empty, Queue
import signal
import subprocess
from threading import Thread
import time


def verified_revision(root: Path) -> str:
    """Bind both staged and unstaged runtime bytes to HEAD before publication."""
    subprocess.run(
        ["git", "diff", "--no-ext-diff", "--exit-code", "HEAD", "--", "hormuz"],
        cwd=root, check=True, stdout=subprocess.DEVNULL,
    )
    # Include ignored source files too. Normal interpreter caches are not source.
    untracked = subprocess.check_output(
        ["git", "ls-files", "--others", "-z", "--", "hormuz"], cwd=root,
    ).decode("utf-8", errors="surrogateescape").split("\0")
    if any(name and "__pycache__" not in Path(name).parts for name in untracked):
        raise RuntimeError("Untracked runtime files prevent recording provenance")
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=root, text=True,
    ).strip()


def collect_output(
    command: list[str], *, cwd: Path, env: dict[str, str] | None = None,
    timeout: float = 30,
) -> tuple[list[list], int, float]:
    """Preserve observed line timings while enforcing a wall-clock deadline."""
    if timeout <= 0:
        raise ValueError("Recording timeout must be positive")
    start = time.monotonic()
    deadline = start + timeout
    process = subprocess.Popen(
        command, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", start_new_session=os.name == "posix",
    )
    assert process.stdout is not None
    output: Queue = Queue()

    def read_output() -> None:
        try:
            for line in process.stdout:
                output.put([round(time.monotonic() - start, 4), "o", line])
        except Exception as error:
            output.put(error)
        finally:
            output.put(None)

    reader = Thread(target=read_output, daemon=True)
    reader.start()
    events = []
    try:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise subprocess.TimeoutExpired(command, timeout)
            try:
                event = output.get(timeout=remaining)
            except Empty as error:
                raise subprocess.TimeoutExpired(command, timeout) from error
            if event is None:
                break
            if isinstance(event, Exception):
                raise event
            events.append(event)
        exit_code = process.wait(timeout=max(0, deadline - time.monotonic()))
        return events, exit_code, round(time.monotonic() - start, 4)
    finally:
        # Also kill the process group when a descendant retained the output pipe.
        if process.poll() is None or reader.is_alive():
            try:
                if os.name == "posix":
                    os.killpg(process.pid, signal.SIGKILL)
                else:
                    process.kill()
            except ProcessLookupError:
                pass
            process.wait(timeout=5)
        reader.join(timeout=1)
        if not reader.is_alive():
            process.stdout.close()
