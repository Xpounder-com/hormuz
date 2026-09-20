#!/usr/bin/env python3
"""Measure sequential synthetic-preview launch to a nontransparent panel window.

This is a warm-LaunchServices observation, not a cold-boot startup benchmark.
Only numeric timing and owned-window geometry are retained. The app must be a
fresh, signature-verified extraction of the pinned v1.2.0 release.
"""

import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import time


SOURCE = "d854a5a453fcbe20cb3f4c1e261e146f2da93855"
EXECUTABLE_SHA256 = "2ece94a031a039d0e27c7ce873787f83f704045e44b0e63cedb71841b9bb3ecc"


def rows():
    raw = subprocess.check_output(["/bin/ps", "-axo", "pid=,comm="], text=True)
    result = {}
    for line in raw.splitlines():
        pieces = line.split(None, 1)
        if len(pieces) == 2:
            result[int(pieces[0])] = pieces[1]
    return result


def geometry(probe, pid):
    raw = subprocess.check_output([str(probe), str(pid)], text=True)
    return json.loads(raw)


def panel_present(windows):
    # The pinned preview's edge panel is much taller than its small handle.
    return any(is_panel(window) for window in windows)


def is_panel(window):
    return (window["width"] >= 50 and window["height"] >= 150
            and window["alpha"] > 0)


def wait_for_exit(pid, binary):
    for _ in range(100):
        if rows().get(pid) != str(binary):
            return
        time.sleep(0.05)
    if rows().get(pid) == str(binary):
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            return
    for _ in range(100):
        if rows().get(pid) != str(binary):
            return
        time.sleep(0.05)
    raise RuntimeError("The dedicated preview did not exit after SIGKILL.")


def trial(app, binary, probe, timeout_seconds):
    initial = rows()
    if str(binary) in initial.values():
        raise RuntimeError("The measurement copy is already running.")
    pid = None
    started = time.monotonic()
    try:
        subprocess.run(
            ["/usr/bin/open", "-n", "-g", str(app), "--args", "--companion-preview",
             "connected", "--companion-scale", "1", "--companion-always"],
            check=True,
        )
        process_seen = None
        last_negative = None
        while time.monotonic() - started < timeout_seconds:
            if pid is None:
                added = [candidate for candidate, command in rows().items()
                         if candidate not in initial and command == str(binary)]
                if len(added) > 1:
                    raise RuntimeError("More than one measurement process appeared.")
                if added:
                    pid = added[0]
                    process_seen = time.monotonic() - started
            if pid is not None:
                if rows().get(pid) != str(binary):
                    raise RuntimeError("The measurement process exited before a panel appeared.")
                probe_started = time.monotonic() - started
                windows = geometry(probe, pid)
                observed_at = time.monotonic() - started
                if panel_present(windows):
                    return {
                        "process_seen_seconds": round(process_seen, 4),
                        "last_no_panel_seconds": round(last_negative, 4) if last_negative is not None else None,
                        "first_panel_seconds": round(observed_at, 4),
                        "owned_onscreen_windows": len(windows),
                        "panel_bounds": [
                            {"width": item["width"], "height": item["height"]}
                            for item in windows if is_panel(item)
                        ],
                    }
                last_negative = probe_started
            time.sleep(0.05)
        raise RuntimeError("No nontransparent on-screen panel appeared before the timeout.")
    finally:
        if pid is None:
            # LaunchServices may return before the new process is visible.
            for _ in range(50):
                added = [candidate for candidate, command in rows().items()
                         if candidate not in initial and command == str(binary)]
                if len(added) > 1:
                    raise RuntimeError("More than one measurement process appeared during cleanup.")
                if added:
                    pid = added[0]
                    break
                time.sleep(0.1)
        if pid is not None and rows().get(pid) == str(binary):
            try:
                os.kill(pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            else:
                wait_for_exit(pid, binary)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--recording-directory", type=Path, required=True)
    parser.add_argument("--window-probe", type=Path, required=True)
    parser.add_argument("--trials", type=int, default=10)
    parser.add_argument("--timeout", type=float, default=10.0)
    args = parser.parse_args()
    if not 1 <= args.trials <= 100 or not 1 <= args.timeout <= 60:
        parser.error("Trials must be 1–100 and timeout 1–60 seconds.")
    root = args.recording_directory.resolve()
    probe = args.window_probe.resolve()
    if not probe.is_file() or not os.access(probe, os.X_OK):
        parser.error("A compiled executable window probe is required.")
    destination = root / "macos-visible-startup.json"
    if destination.exists():
        parser.error("Refusing to overwrite an existing report.")
    app = root / "extracted/Hormuz.app"
    binary = app / "Contents/MacOS/Hormuz"
    if hashlib.sha256(binary.read_bytes()).hexdigest() != EXECUTABLE_SHA256:
        raise RuntimeError("The executable does not match the pinned release.")

    measurements = []
    for index in range(1, args.trials + 1):
        sample = trial(app, binary, probe, args.timeout)
        sample["trial"] = index
        measurements.append(sample)
        print(json.dumps(sample, sort_keys=True), flush=True)
        time.sleep(1)
    report = {
        "schema_id": "hormuz.native-client-startup-sample",
        "schema_version": 1,
        "source_commit": SOURCE,
        "version": "1.2.0",
        "executable_sha256": EXECUTABLE_SHA256,
        "recorded_at_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "scenario_requested": "visible connected synthetic preview",
        "method": "monotonic clock before LaunchServices open -n -g; poll owned process and numeric CoreGraphics on-screen panel geometry with alpha greater than zero",
        "conditions": "Sequential launches on an active developer workstation; OS caches were not cleared; this is not a cold-start measurement.",
        "trial_count": len(measurements),
        "trials": measurements,
        "limits": [
            "The first nontransparent on-screen window with pinned panel-like geometry is an observable proxy, not a rendered-pixel or interaction-ready guarantee.",
            "The first panel time is an upper bound at the probe's actual sampling cadence; the last negative probe, when present, is a lower bound.",
            "LaunchServices, process discovery and the numeric WindowServer probe contribute observer overhead.",
            "Cold boot, sign-in, real gateway and active-client startup remain unmeasured.",
        ],
    }
    with destination.open("x") as stream:
        json.dump(report, stream, indent=2)
        stream.write("\n")


if __name__ == "__main__":
    main()
