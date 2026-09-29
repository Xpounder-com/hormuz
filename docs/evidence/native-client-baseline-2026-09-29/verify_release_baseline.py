#!/usr/bin/env python3
"""Verify the committed v1.3.0 Mac footprint evidence and proposed budgets."""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from statistics import median


ROOT = Path(__file__).resolve().parent
BUDGETS = ROOT.parent / "native-client-footprint-budgets-v1.json"
SOURCE = "cc5f5c1d38b4732823496240bbb4ace309a6240d"
VERSION = "1.3.0"
EXECUTABLE_SHA256 = "0726774b2c43695579633c920ad0e09bb8a6abb2399687a99d599807f314dd10"
SCENARIOS = ("hidden", "visible", "folded")
SAMPLE_KEYS = {
    "elapsed_seconds", "process_count", "rss_kib_sum", "cpu_seconds_sum",
    "physical_footprint_bytes_sum", "package_idle_wakeups_sum",
    "interrupt_wakeups_sum", "rusage_cpu_time_ticks_sum",
}
REPORT_KEYS = {
    "schema_id", "schema_version", "source_commit", "version",
    "executable_sha256", "recorded_at_utc", "scenario_requested", "preview",
    "conditions", "warmup_seconds", "sample_interval_seconds",
    "duration_seconds", "repeat", "method", "samples", "max_process_count",
    "rss_kib_min", "rss_kib_max", "rss_kib_first", "rss_kib_last",
    "cpu_percent_of_one_core", "rusage_cpu_percent_of_one_core",
    "rusage_cpu_timebase_numer", "rusage_cpu_timebase_denom",
    "observer_cpu_seconds", "physical_footprint_bytes_min",
    "physical_footprint_bytes_max", "package_idle_wakeups_delta",
    "interrupt_wakeups_delta", "process_topology_stable",
    "child_departures_observed", "limits",
}


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def reject_nonfinite(value: str) -> None:
    raise ValueError(f"Non-finite JSON number: {value}")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path: Path) -> dict:
    value = json.loads(path.read_text(), parse_constant=reject_nonfinite)
    require(isinstance(value, dict), f"{path.name} must contain a JSON object.")
    return value


def read_json_array(path: Path) -> list:
    value = json.loads(path.read_text(), parse_constant=reject_nonfinite)
    require(isinstance(value, list), f"{path.name} must contain a JSON array.")
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


def verify_static() -> dict:
    report = read_json(ROOT / "static-artifacts.json")
    require(report.get("schema_id") == "hormuz.native-client-static-baseline" and
            report.get("schema_version") == 2, "Unexpected static schema.")
    mac = report.get("mac")
    require(isinstance(mac, dict), "Mac static evidence is missing.")
    require((mac.get("source_commit"), mac.get("version"),
             mac.get("native_executable_sha256")) ==
            (SOURCE, VERSION, EXECUTABLE_SHA256), "Static artifact identity differs.")
    require(mac.get("archive_sha256") ==
            "77d463869f35c5bd4c9bae0548ce5e66ddc49fdb565f81d22ac36cd01d0b599d",
            "Archive hash differs.")
    require(mac.get("release_manifest_match") is True and
            mac.get("signature_valid") is True and
            mac.get("gatekeeper_accepted") is True and
            mac.get("notarization_ticket_valid") is True,
            "Release integrity checks are incomplete.")
    components = mac.get("component_logical_bytes")
    require(isinstance(components, dict) and
            all(integer(value, "component size", minimum=1) for value in components.values()) and
            sum(components.values()) == mac.get("bundle_logical_bytes"),
            "Component sizes do not sum to the bundle size.")
    require(mac.get("regular_file_count") == 16 and
            mac.get("native_executable_architectures") == ["arm64"] and
            mac.get("context_helper_backend_architectures") == ["arm64"] and
            mac.get("system_runtime_dependencies_only") is True,
            "Installed-content or runtime-dependency evidence differs.")
    return mac


def verify_idle(path: Path, scenario: str, repeat: int) -> dict:
    report = read_json(path)
    require(set(report) == REPORT_KEYS, f"{path.name} schema differs.")
    require(report["schema_id"] == "hormuz.native-client-idle-sample" and
            report["schema_version"] == 3, f"{path.name} has an unexpected schema.")
    require((report["source_commit"], report["version"], report["executable_sha256"]) ==
            (SOURCE, VERSION, EXECUTABLE_SHA256), f"{path.name} identity differs.")
    require(report["scenario_requested"] == scenario and
            integer(report["repeat"], "repeat", minimum=1) == repeat,
            f"{path.name} scenario or repeat differs.")
    require(integer(report["warmup_seconds"], "warm-up") == 60 and
            integer(report["sample_interval_seconds"], "sample interval") == 5,
            f"{path.name} used a different protocol.")
    require(report["process_topology_stable"] is True and
            report["child_departures_observed"] is False,
            f"{path.name} process topology changed.")

    samples = report["samples"]
    require(isinstance(samples, list) and len(samples) == 61,
            f"{path.name} must retain 61 samples.")
    require(all(isinstance(sample, dict) and set(sample) == SAMPLE_KEYS
                for sample in samples), f"{path.name} sample schema differs.")
    elapsed = [number(sample["elapsed_seconds"], "sample time") for sample in samples]
    gaps = [later - earlier for earlier, later in zip(elapsed, elapsed[1:])]
    require(all(3.5 <= gap <= 6.5 for gap in gaps[:-1]) and
            0 < gaps[-1] <= 6.5,
            f"{path.name} missed the five-second cadence.")
    duration = elapsed[-1] - elapsed[0]
    require(299 <= duration <= 301 and
            math.isclose(report["duration_seconds"], duration, abs_tol=0.002),
            f"{path.name} duration differs from its samples.")
    require(all(integer(sample["process_count"], "process count", minimum=1) == 1
                for sample in samples) and
            integer(report["max_process_count"], "maximum process count", minimum=1) == 1,
            f"{path.name} did not retain one stable process.")

    def values(key: str, *, minimum: float = 0) -> list[float]:
        return [number(sample[key], key, minimum=minimum) for sample in samples]

    def integer_values(key: str, *, minimum: int = 0) -> list[int]:
        return [integer(sample[key], key, minimum=minimum) for sample in samples]

    rss = integer_values("rss_kib_sum", minimum=1)
    physical = integer_values("physical_footprint_bytes_sum", minimum=1)
    coarse_cpu = values("cpu_seconds_sum")
    ticks = integer_values("rusage_cpu_time_ticks_sum")
    package_wakeups = integer_values("package_idle_wakeups_sum")
    interrupt_wakeups = integer_values("interrupt_wakeups_sum")
    for name, cumulative in (
        ("coarse CPU", coarse_cpu), ("Mach CPU", ticks),
        ("package-idle wake-ups", package_wakeups),
        ("interrupt wake-ups", interrupt_wakeups),
    ):
        require(all(later >= earlier for earlier, later in zip(cumulative, cumulative[1:])),
                f"{path.name} {name} counter went backward.")

    require((report["rss_kib_min"], report["rss_kib_max"],
             report["rss_kib_first"], report["rss_kib_last"]) ==
            (min(rss), max(rss), rss[0], rss[-1]),
            f"{path.name} RSS summary differs.")
    require((report["physical_footprint_bytes_min"],
             report["physical_footprint_bytes_max"]) ==
            (min(physical), max(physical)), f"{path.name} footprint summary differs.")
    require(integer(report["package_idle_wakeups_delta"], "package wake-up delta") ==
            package_wakeups[-1] - package_wakeups[0]
            and integer(report["interrupt_wakeups_delta"], "interrupt wake-up delta") ==
            interrupt_wakeups[-1] - interrupt_wakeups[0],
            f"{path.name} wake-up summary differs.")
    require((report["rusage_cpu_timebase_numer"],
             report["rusage_cpu_timebase_denom"]) == (125, 3),
            f"{path.name} Mach timebase differs.")
    expected_rusage_cpu = round(
        100 * (ticks[-1] - ticks[0]) * 125 / 3 / (duration * 1e9), 6
    )
    expected_coarse_cpu = round(100 * (coarse_cpu[-1] - coarse_cpu[0]) / duration, 4)
    require(report["rusage_cpu_percent_of_one_core"] == expected_rusage_cpu and
            report["cpu_percent_of_one_core"] == expected_coarse_cpu,
            f"{path.name} CPU summary differs.")
    number(report["observer_cpu_seconds"], "observer CPU")
    return {
        "samples": len(samples),
        "rss_kib_min": min(rss),
        "rss_kib_max": max(rss),
        "physical_footprint_bytes_min": min(physical),
        "physical_footprint_bytes_max": max(physical),
        "cpu_percent_one_core": expected_rusage_cpu,
        "package_idle_wakeups": report["package_idle_wakeups_delta"],
        "interrupt_wakeups": report["interrupt_wakeups_delta"],
    }


def verify_startup() -> dict:
    report = read_json(ROOT / "macos-visible-startup.json")
    require(report.get("schema_id") == "hormuz.native-client-startup-sample" and
            report.get("schema_version") == 1, "Unexpected startup schema.")
    require((report.get("source_commit"), report.get("version"),
             report.get("executable_sha256")) == (SOURCE, VERSION, EXECUTABLE_SHA256),
            "Startup artifact identity differs.")
    trials = report.get("trials")
    require(isinstance(trials, list) and report.get("trial_count") == len(trials) == 10,
            "Ten startup trials are required.")
    for index, trial in enumerate(trials, 1):
        require(trial.get("trial") == index, "Startup trial order differs.")
        process = number(trial.get("process_seen_seconds"), "process seen")
        negative = number(trial.get("last_no_panel_seconds"), "last negative probe")
        panel = number(trial.get("first_panel_seconds"), "first panel")
        require(process <= negative < panel < 10, "Startup observation bounds are invalid.")
        require(trial.get("owned_onscreen_windows") == 2 and trial.get("panel_bounds"),
                "Expected startup windows are missing.")
        require(all(number(bounds.get("width"), "panel width") >= 50 and
                    number(bounds.get("height"), "panel height") >= 150
                    for bounds in trial["panel_bounds"]),
                "Startup panel geometry differs.")
    observed = [trial["first_panel_seconds"] for trial in trials]
    return {
        "trial_count": len(trials),
        "first_trial_seconds": observed[0],
        "subsequent_seconds_min": min(observed[1:]),
        "subsequent_seconds_max": max(observed[1:]),
        "subsequent_seconds_median": median(observed[1:]),
        "all_trials_seconds_max": max(observed),
    }


def verify_interactions() -> dict:
    layout = read_json_array(ROOT / "macos-layout-audit.json")
    require(len(layout) == 8 and all(isinstance(item, dict) for item in layout),
            "Layout audit must contain eight checks.")
    scales = layout[:5]
    require([item.get("scale") for item in scales] == [1, 1.25, 1.5, 1.25, 1],
            "Layout audit scale sequence differs.")
    require(all(item.get("passed") is True and
                number(item.get("bodyWidth"), "body width", minimum=1) ==
                number(item.get("expectedBodyWidth"), "expected body width", minimum=1) and
                number(item.get("bodyHostWidth"), "body host width", minimum=1) ==
                number(item.get("bodyWidth"), "body width", minimum=1) and
                number(item.get("bodyHostHeight"), "body host height", minimum=1) ==
                number(item.get("bodyHeight"), "body height", minimum=1) and
                number(item.get("rightEdge"), "body right edge") ==
                number(item.get("screenRightEdge"), "screen right edge")
                for item in scales), "Live layout geometry failed.")
    require(layout[5] == {"folded": True, "passed": True} and
            layout[6] == {"hidden": True, "passed": True} and
            layout[7] == {"passed": True, "reopened": True},
            "Fold, hide, or reopen audit failed.")

    motion = read_json(ROOT / "macos-motion-audit.json")
    require(set(motion) == {
        "compactHeight", "compactLayoutPassed", "opacitySamples", "passed",
        "rapidReversalPassed", "smoothOpacityPassed",
    }, "Motion audit schema differs.")
    require(motion["passed"] is True and motion["compactLayoutPassed"] is True and
            motion["rapidReversalPassed"] is True and
            motion["smoothOpacityPassed"] is True and
            number(motion["compactHeight"], "compact height", minimum=1) == 318,
            "Motion audit failed.")
    samples = motion["opacitySamples"]
    require(isinstance(samples, list) and len(samples) == 20,
            "Motion audit must contain twenty opacity samples.")
    require(all(isinstance(sample, dict) and set(sample) ==
                {"alpha", "expanded", "height", "step"} and
                integer(sample["step"], "motion step") == index and
                0 <= number(sample["alpha"], "opacity") <= 1 and
                integer(sample["height"], "motion height", minimum=1) >= 1 and
                isinstance(sample["expanded"], bool)
                for index, sample in enumerate(samples)),
            "Motion sample schema or bounds differ.")
    require(any(0.01 < sample["alpha"] < 0.99 for sample in samples),
            "Motion audit has no intermediate opacity.")
    return {"layout_checks": len(layout), "motion_samples": len(samples)}


def verify_gpu() -> dict:
    report = read_json(ROOT / "macos-gpu-observation.json")
    require((report.get("schema_id"), report.get("schema_version")) ==
            ("hormuz.native-client-gpu-observation", 1),
            "Unexpected GPU observation schema.")
    require((report.get("source_commit"), report.get("version"),
             report.get("executable_sha256")) == (SOURCE, VERSION, EXECUTABLE_SHA256),
            "GPU observation artifact identity differs.")
    require(report.get("scenario") == "visible connected synthetic preview" and
            report.get("requested_seconds") == 10 and
            10 <= number(report.get("observed_seconds"), "GPU trace duration") <= 15,
            "GPU observation protocol differs.")
    for field in (
        "target_direct_metal_command_buffer_rows", "target_gpu_interval_rows",
        "target_current_allocated_size_rows", "target_displayed_surface_rows",
    ):
        require(integer(report.get(field), field) == 0,
                f"{field} differs from the sanitized trace summary.")
    require(integer(report.get("system_gpu_interval_rows"), "system GPU rows", minimum=1) > 0 and
            report.get("per_process_gpu_utilization_percent") is None and
            isinstance(report.get("utilization_state"), str) and
            report["utilization_state"].startswith("unavailable:"),
            "GPU availability boundary is missing.")
    return {
        "trace_seconds": report["observed_seconds"],
        "target_direct_metal_rows": 0,
        "per_process_utilization": None,
    }


def verify_conditions() -> None:
    report = read_json(ROOT / "macos-run-conditions.json")
    require(report.get("schema_id") == "hormuz.native-client-run-conditions" and
            report.get("schema_version") == 1, "Unexpected conditions schema.")
    host = report.get("host")
    require(isinstance(host, dict) and
            (host.get("hardware_model"), host.get("processor"),
             host.get("architecture"), host.get("logical_processors"),
             host.get("memory_bytes"), host.get("os"), host.get("os_build"),
             host.get("python"), host.get("low_power_mode")) ==
            ("Mac17,2", "Apple M5", "arm64", 10, 34359738368,
             "26.2", "25C56", "3.14.0", False),
            "Recorded Mac host conditions differ.")
    display = host.get("display")
    require(isinstance(display, dict) and
            (display.get("pixel_width"), display.get("pixel_height"),
             display.get("built_in"), display.get("main"), display.get("online")) ==
            (3024, 1964, True, True, True), "Recorded display conditions differ.")
    protocol = report.get("protocol")
    require(isinstance(protocol, dict) and
            (protocol.get("warmup_seconds"), protocol.get("duration_seconds"),
             protocol.get("sample_interval_seconds"), protocol.get("samples_per_run")) ==
            (60, 300, 5, 61), "Recorded idle protocol differs.")
    runs = report.get("runs")
    expected = {(scenario, repeat) for scenario in SCENARIOS for repeat in (1, 2, 3)}
    expected_order = [
        ("hidden", 1), ("visible", 1), ("folded", 1),
        ("visible", 2), ("folded", 2), ("hidden", 2),
        ("folded", 3), ("hidden", 3), ("visible", 3),
    ]
    require(isinstance(runs, list) and len(runs) == 9 and
            {(run.get("scenario"), run.get("repeat")) for run in runs} == expected,
            "Run conditions do not cover the nine idle recordings.")
    require([(run.get("scenario"), run.get("repeat")) for run in runs] == expected_order,
            "Run conditions do not preserve the balanced collection order.")
    valid_power = {"AC Power", "Battery Power"}
    require(all(run.get("power_before") in valid_power and
                run.get("power_before") == run.get("power_after") for run in runs),
            "A run crossed or omitted its recorded power-source boundary.")
    for run in runs:
        raw = read_json(
            ROOT / f"macos-{run['scenario']}-idle-repeat-{run['repeat']}.json"
        )
        require(run.get("recorded_at_utc") == raw.get("recorded_at_utc"),
                "A power condition is not bound to its raw idle report.")
    interaction = report.get("interaction_audits")
    require(isinstance(interaction, dict) and
            (interaction.get("source_commit"), interaction.get("version"),
             interaction.get("executable_sha256")) ==
            (SOURCE, VERSION, EXECUTABLE_SHA256) and
            interaction.get("signature_verified") is True and
            interaction.get("accessibility_automation_enabled") is False and
            interaction.get("layout_report_sha256") ==
            sha256(ROOT / "macos-layout-audit.json") and
            interaction.get("motion_report_sha256") ==
            sha256(ROOT / "macos-motion-audit.json"),
            "Interaction audit provenance differs.")


def verify_budgets(static: dict, scenarios: dict, startup: dict) -> None:
    report = read_json(BUDGETS)
    require(report.get("schema_id") == "hormuz.native-client-footprint-budgets" and
            report.get("schema_version") == 1 and report.get("status") == "proposed",
            "Unexpected footprint budget schema.")
    mac = report.get("macos")
    require(isinstance(mac, dict), "Mac budgets are missing.")
    sizes = (
        (static["archive_bytes"], "archive_bytes_max"),
        (static["bundle_logical_bytes"], "bundle_logical_bytes_max"),
        (static["bundle_allocated_bytes"], "bundle_allocated_bytes_max"),
    )
    require(all(observed <= number(mac.get(key), key) for observed, key in sizes),
            "The release artifact exceeds a size budget.")
    runs = [run for scenario in scenarios.values() for run in scenario]
    idle_checks = (
        ("rss_kib_max", "idle_rss_kib_max"),
        ("physical_footprint_bytes_max", "idle_physical_footprint_bytes_max"),
        ("cpu_percent_one_core", "idle_cpu_percent_one_core_max"),
        ("package_idle_wakeups", "package_idle_wakeups_per_five_minutes_max"),
        ("interrupt_wakeups", "interrupt_wakeups_per_five_minutes_max"),
    )
    require(all(max(run[observed] for run in runs) <= number(mac.get(budget), budget)
                for observed, budget in idle_checks),
            "An idle observation exceeds its proposed budget.")
    require(startup["all_trials_seconds_max"] <=
            number(mac.get("warm_start_panel_upper_bound_seconds_max"), "startup budget"),
            "A startup observation exceeds its proposed budget.")


def main() -> None:
    static = verify_static()
    verify_conditions()
    scenarios = {
        scenario: [
            verify_idle(ROOT / f"macos-{scenario}-idle-repeat-{repeat}.json",
                        scenario, repeat)
            for repeat in (1, 2, 3)
        ]
        for scenario in SCENARIOS
    }
    startup = verify_startup()
    interactions = verify_interactions()
    gpu = verify_gpu()
    verify_budgets(static, scenarios, startup)
    print(json.dumps({
        "result": "passed",
        "source_commit": SOURCE,
        "version": VERSION,
        "scenario_count": 3,
        "run_count": 9,
        "raw_sample_count": 549,
        "scenarios": scenarios,
        "startup": startup,
        "interactions": interactions,
        "gpu": gpu,
        "budgets": "passed",
    }, indent=2, sort_keys=True, allow_nan=False))


if __name__ == "__main__":
    main()
