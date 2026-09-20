#!/usr/bin/env python3
"""Verify content-free hosted Windows preview evidence from one workflow run."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
from pathlib import Path


SCENARIOS = ("visible", "folded", "hidden")
MAX_SAMPLE_JITTER_SECONDS = 1.5
SHA256 = re.compile(r"^[0-9a-f]{64}$")
COMMIT = re.compile(r"^[0-9a-f]{40}$")
SAMPLE_KEYS = {
    "elapsed_seconds", "process_count", "working_set_bytes_sum",
    "private_bytes_sum", "process_cpu_seconds_sum", "root_handle_count",
}
RUN_KEYS = {
    "result", "scenario", "repetition", "warmup_seconds",
    "duration_seconds_requested", "sample_interval_milliseconds",
    "window_ready_upper_bound_seconds", "hidden_behavior", "samples",
    "sample_duration_seconds", "working_set_bytes_min", "working_set_bytes_max",
    "private_bytes_min", "private_bytes_max", "app_cpu_seconds_delta",
    "app_cpu_percent_one_core", "observer_cpu_seconds_sampling",
    "observer_wall_seconds_sampling", "observer_peak_working_set_bytes",
    "process_wakeups", "process_wakeups_state",
}
HOST_KEYS = {
    "os", "architecture", "logical_processors", "runner_image_os",
    "runner_image_version", "power_thermal_and_display",
}
REPORT_KEYS = {
    "schema_id", "schema_version", "result", "complete_three_run_protocol",
    "artifact_kind", "execution_mode", "source_commit", "proposed_head",
    "executable_sha256", "executable_bytes", "build_manifest_sha256",
    "target", "compiler", "host", "scenario", "run_count",
    "sample_count_per_run", "measurement_tools", "runs", "limitations",
    "manual_platform_acceptance",
}


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def reject_nonfinite(value: str) -> None:
    raise ValueError(f"Non-finite JSON number: {value}")


def read_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8-sig"), parse_constant=reject_nonfinite)
    require(isinstance(value, dict), "Evidence must be a JSON object.")
    return value


def number(value: object, name: str, *, minimum: float = 0) -> float:
    require(isinstance(value, (int, float)) and not isinstance(value, bool),
            f"{name} is not numeric.")
    result = float(value)
    require(math.isfinite(result) and result >= minimum, f"{name} is invalid.")
    return result


def integer(value: object, name: str, *, minimum: int = 0) -> int:
    require(isinstance(value, int) and not isinstance(value, bool) and value >= minimum,
            f"{name} is not a valid integer.")
    return value


def close(actual: object, expected: float, name: str) -> None:
    require(math.isclose(number(actual, name), expected, rel_tol=1e-9, abs_tol=1e-6),
            f"{name} disagrees with raw samples.")


def verify_run(run: dict, scenario: str, repetition: int) -> dict:
    require(set(run) == RUN_KEYS, "Run schema includes missing or nonnumeric content fields.")
    require(run.get("result") == "passed" and run.get("scenario") == scenario and
            run.get("repetition") == repetition, "Run identity or result differs.")
    require(run.get("warmup_seconds") == 60 and
            run.get("duration_seconds_requested") == 300 and
            run.get("sample_interval_milliseconds") == 5000,
            "Run used a different protocol.")
    ready = number(run.get("window_ready_upper_bound_seconds"), "window-ready proxy")
    require(run.get("process_wakeups") is None and
            run.get("process_wakeups_state") ==
            "unavailable: hosted runner has no verified per-process wake-up counter",
            "Unavailable wake-ups must be explicitly null.")
    samples = run.get("samples")
    require(isinstance(samples, list) and len(samples) == 61,
            "A five-minute run must retain 61 samples.")
    elapsed: list[float] = []
    working: list[int] = []
    private: list[int] = []
    cpu: list[float] = []
    for index, sample in enumerate(samples):
        require(isinstance(sample, dict), "Sample is not an object.")
        require(set(sample) == SAMPLE_KEYS,
                "Sample schema includes missing or nonnumeric content fields.")
        observed = number(sample.get("elapsed_seconds"), "sample elapsed")
        requested = index * 5.0
        require(requested <= observed <= requested + MAX_SAMPLE_JITTER_SECONDS,
                "A sample missed its requested five-second deadline.")
        if elapsed:
            require(abs(observed - elapsed[-1] - 5.0) <= MAX_SAMPLE_JITTER_SECONDS,
                    "The samples do not maintain a five-second cadence.")
        elapsed.append(observed)
        require(sample.get("process_count") == 1, "Empty preview process tree changed.")
        working.append(integer(sample.get("working_set_bytes_sum"), "working set", minimum=1))
        private.append(integer(sample.get("private_bytes_sum"), "private bytes", minimum=1))
        cpu.append(number(sample.get("process_cpu_seconds_sum"), "app CPU"))
        integer(sample.get("root_handle_count"), "root handle count", minimum=1)
    require(all(later > earlier for earlier, later in zip(elapsed, elapsed[1:])),
            "Sample timestamps are not increasing.")
    require(all(later >= earlier for earlier, later in zip(cpu, cpu[1:])),
            "App CPU counter regressed.")
    duration = elapsed[-1] - elapsed[0]
    require(duration >= 299, "Observed span is shorter than five minutes.")
    close(run.get("sample_duration_seconds"), duration, "sample duration")
    require(run.get("working_set_bytes_min") == min(working) and
            run.get("working_set_bytes_max") == max(working) and
            run.get("private_bytes_min") == min(private) and
            run.get("private_bytes_max") == max(private),
            "Memory summary disagrees with raw samples.")
    cpu_delta = cpu[-1] - cpu[0]
    close(run.get("app_cpu_seconds_delta"), cpu_delta, "app CPU delta")
    close(run.get("app_cpu_percent_one_core"), 100 * cpu_delta / duration,
          "app CPU percentage")
    number(run.get("observer_cpu_seconds_sampling"), "observer CPU")
    number(run.get("observer_wall_seconds_sampling"), "observer wall time", minimum=299)
    integer(run.get("observer_peak_working_set_bytes"), "observer peak working set", minimum=1)
    if scenario == "hidden":
        require(run.get("hidden_behavior") in
                ("tray_hidden", "taskbar_minimized_fallback"),
                "Hidden state behavior is missing.")
    return {
        "sample_count": len(samples),
        "sample_span_seconds": duration,
        "working_set_bytes_min": min(working),
        "working_set_bytes_max": max(working),
        "private_bytes_min": min(private),
        "private_bytes_max": max(private),
        "app_cpu_percent_one_core": 100 * cpu_delta / duration,
        "observer_cpu_seconds_sampling": run["observer_cpu_seconds_sampling"],
        "window_ready_upper_bound_seconds": ready,
    }


def verify(artifacts: Path, source_sha: str, proposed_head: str) -> dict:
    require(COMMIT.fullmatch(source_sha) is not None and
            COMMIT.fullmatch(proposed_head) is not None, "Expected full commit SHAs.")
    expected = {f"synthetic-windows-footprint-{name}-{source_sha}"
                for name in ("executable", *SCENARIOS)}
    actual = {entry.name for entry in artifacts.iterdir() if entry.is_dir()}
    require(actual == expected, "Expected exactly one build and three scenario artifacts.")
    build_dir = artifacts / f"synthetic-windows-footprint-executable-{source_sha}"
    build_path = build_dir / "windows-footprint-build.json"
    executable = build_dir / "hormuz-windows.exe"
    rebuild_path = build_dir / "windows-rebuild.json"
    build = read_json(build_path)
    require(build.get("schema_id") == "hormuz.windows.synthetic-footprint-build" and
            build.get("schema_version") == 1 and
            build.get("source_commit") == source_sha and
            build.get("proposed_head") == proposed_head and
            build.get("target") == "x86_64-pc-windows-msvc" and
            build.get("repeat_build") == "passed_same_runner_same_checkout",
            "Build provenance is incomplete or belongs to another revision.")
    artifact_hash = sha256(executable)
    require(SHA256.fullmatch(artifact_hash) is not None and
            build.get("executable_sha256") == artifact_hash and
            build.get("executable_bytes") == executable.stat().st_size,
            "The downloaded executable differs from build provenance.")
    require(build.get("rebuild_sha256") == sha256(rebuild_path),
            "The repeat-build proof changed.")
    rebuild = read_json(rebuild_path)
    require(rebuild.get("result") == "passed" and
            rebuild.get("source_commit") == source_sha and
            rebuild.get("first_sha256") == artifact_hash and
            rebuild.get("second_sha256") == artifact_hash,
            "The package-clean repeat build was not identical.")
    manifest_hash = sha256(build_path)

    scenarios: dict[str, dict] = {}
    evidence_hashes: dict[str, str] = {}
    for scenario in SCENARIOS:
        report_path = (artifacts / f"synthetic-windows-footprint-{scenario}-{source_sha}" /
                       f"windows-footprint-{scenario}.json")
        report = read_json(report_path)
        require(set(report) == REPORT_KEYS,
                "Report schema includes missing or nonnumeric content fields.")
        require(report.get("schema_id") == "hormuz.windows.synthetic-footprint-baseline" and
                report.get("schema_version") == 1 and
                report.get("result") == "passed" and
                report.get("complete_three_run_protocol") is True and
                report.get("source_commit") == source_sha and
                report.get("proposed_head") == proposed_head and
                report.get("scenario") == scenario and
                report.get("run_count") == 3 and
                report.get("sample_count_per_run") == 61 and
                report.get("executable_sha256") == artifact_hash and
                report.get("executable_bytes") == executable.stat().st_size and
                report.get("build_manifest_sha256") == manifest_hash and
                report.get("compiler") == build.get("compiler"),
                "Scenario evidence differs from the one pinned executable or protocol.")
        host = report.get("host")
        require(isinstance(host, dict) and set(host) == HOST_KEYS and
                isinstance(host.get("os"), str) and host["os"] and
                host.get("architecture") == "AMD64" and
                integer(host.get("logical_processors"), "host logical processors", minimum=1) and
                isinstance(host.get("runner_image_os"), str) and host["runner_image_os"] and
                isinstance(host.get("runner_image_version"), str) and host["runner_image_version"] and
                host.get("power_thermal_and_display") == "uncontrolled",
                "Hosted Windows conditions are missing.")
        runs = report.get("runs")
        require(isinstance(runs, list) and len(runs) == 3,
                "Scenario requires three independent runs.")
        scenarios[scenario] = {
            "run_count": 3,
            "total_samples": 183,
            "runs": [verify_run(run, scenario, index)
                     for index, run in enumerate(runs, 1)],
        }
        evidence_hashes[scenario] = sha256(report_path)
    return {
        "schema_id": "hormuz.windows.synthetic-footprint-verification",
        "schema_version": 1,
        "result": "passed",
        "source_commit": source_sha,
        "proposed_head": proposed_head,
        "executable_sha256": artifact_hash,
        "executable_bytes": executable.stat().st_size,
        "build_manifest_sha256": manifest_hash,
        "evidence_sha256": evidence_hashes,
        "scenario_count": 3,
        "run_count": 9,
        "raw_sample_count": 549,
        "scenarios": scenarios,
        "remaining_limits": [
            "Hosted runner power, display, thermal and background load are uncontrolled.",
            "Wake-ups and GPU were not measured; per-process wake-ups are null, not zero.",
            "Synthetic preview only; physical acceptance and connected workloads remain open.",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifacts", required=True, type=Path)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--proposed-head", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    summary = verify(args.artifacts, args.source_sha, args.proposed_head)
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(summary, stream, indent=2, allow_nan=False)
        stream.write("\n")
    print("windows_footprint_evidence=passed scenarios=3 runs=9 samples=549")


if __name__ == "__main__":
    main()
