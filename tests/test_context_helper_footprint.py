"""Observer failures must invalidate helper-process evidence, without launching helpers."""
from __future__ import annotations

import subprocess
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from tools import measure_context_helper_footprint as footprint


class SyntheticProcess:
    pid = 100
    returncode = 0

    def __init__(self, ready: threading.Event) -> None:
        self.ready = ready

    def communicate(self, *_args: object, **_kwargs: object) -> tuple[bytes, bytes]:
        if not self.ready.wait(1):
            raise AssertionError("Synthetic observer did not reach the intended phase")
        return b"\x01{}", b"0.08 real 0.07 user 0.00 sys\n80000000 maximum resident set size\n"


class ContextHelperFootprintTests(unittest.TestCase):
    def test_process_snapshot_has_a_bounded_subprocess_timeout(self) -> None:
        with patch.object(footprint.subprocess, "check_output", return_value="101 100 80000\n") as snapshot:
            self.assertEqual(footprint.process_table(), {101: (100, 80000)})
        snapshot.assert_called_once_with(
            ["/bin/ps", "-axo", "pid=,ppid=,rss="], text=True,
            timeout=footprint.SNAPSHOT_TIMEOUT_SECONDS,
        )

    def test_snapshot_failure_after_a_successful_sample_cannot_pass(self) -> None:
        for failure in (OSError("synthetic snapshot failure"),
                        subprocess.TimeoutExpired("/bin/ps", footprint.SNAPSHOT_TIMEOUT_SECONDS)):
            with self.subTest(failure=type(failure).__name__):
                failed = threading.Event()
                calls = 0

                def snapshot() -> dict[int, tuple[int, int]]:
                    nonlocal calls
                    calls += 1
                    if calls == 1:
                        return {101: (100, 80000)}
                    failed.set()
                    raise failure

                with (
                    patch.object(footprint.subprocess, "Popen", return_value=SyntheticProcess(failed)),
                    patch.object(footprint, "process_table", side_effect=snapshot),
                    self.assertRaisesRegex(ValueError, "Process observer failed") as raised,
                ):
                    footprint.measured_run(Path("/synthetic-not-executed"), Path("/synthetic-cache"), b"synthetic")
                self.assertIs(raised.exception.__cause__, failure)
                self.assertEqual(calls, 2)

    def test_an_observer_that_does_not_join_cannot_pass(self) -> None:
        blocked = threading.Event()
        release = threading.Event()
        observer_threads: list[threading.Thread] = []
        original_thread = threading.Thread
        calls = 0

        def thread(*args: object, **kwargs: object) -> threading.Thread:
            observer = original_thread(*args, **kwargs)
            observer_threads.append(observer)
            return observer

        def snapshot() -> dict[int, tuple[int, int]]:
            nonlocal calls
            calls += 1
            if calls == 1:
                return {101: (100, 80000)}
            if calls == 2:
                blocked.set()
                release.wait(1)
            return {}

        try:
            with (
                patch.object(footprint.subprocess, "Popen", return_value=SyntheticProcess(blocked)),
                patch.object(footprint, "process_table", side_effect=snapshot),
                patch.object(footprint.threading, "Thread", side_effect=thread),
                patch.object(footprint, "OBSERVER_JOIN_TIMEOUT_SECONDS", 0.01),
                self.assertRaisesRegex(ValueError, "Process observer did not stop"),
            ):
                footprint.measured_run(Path("/synthetic-not-executed"), Path("/synthetic-cache"), b"synthetic")
        finally:
            release.set()
            for observer in observer_threads:
                observer.join(timeout=1)
                self.assertFalse(observer.is_alive())


if __name__ == "__main__":
    unittest.main()
