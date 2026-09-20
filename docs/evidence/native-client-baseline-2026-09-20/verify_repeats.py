#!/usr/bin/env python3
"""Recompute the published numeric Mac idle summaries from all raw samples."""

import json
from pathlib import Path
from statistics import median
from normalize_macos_idle import normalize


ROOT = Path(__file__).resolve().parent
SOURCE = "d854a5a453fcbe20cb3f4c1e261e146f2da93855"
EXECUTABLE_SHA256 = "2ece94a031a039d0e27c7ce873787f83f704045e44b0e63cedb71841b9bb3ecc"


def require(condition, message):
    if not condition:
        raise ValueError(message)


def verify(path, scenario):
    report = json.loads(path.read_text())
    raw = (ROOT / "raw-v2" / path.name).read_bytes()
    require(report == normalize(raw), "Normalized report differs from its immutable v2 source.")
    require(report["schema_id"] == "hormuz.native-client-idle-sample", "Unexpected schema.")
    require(report["schema_version"] == 3, "Unexpected schema version.")
    require(report["source_commit"] == SOURCE, "Unexpected source commit.")
    require(report["executable_sha256"] == EXECUTABLE_SHA256, "Unexpected executable.")
    require(report["scenario_requested"] == scenario, "Unexpected scenario.")
    require(report["warmup_seconds"] == 60, "Unexpected warm-up.")
    require(report["sample_interval_seconds"] == 5, "Unexpected sample interval.")
    require(not report["child_departures_observed"], "A process departed during the sample.")
    require(report["process_topology_stable"], "Process topology changed.")
    samples = report["samples"]
    require(60 <= len(samples) <= 62, "Unexpected sample count.")
    elapsed = [sample["elapsed_seconds"] for sample in samples]
    require(all(later > earlier for earlier, later in zip(elapsed, elapsed[1:])), "Sample times are not increasing.")
    require(299 <= elapsed[-1] - elapsed[0] <= 301, "Sample duration is incomplete.")
    require(abs(report["duration_seconds"] - (elapsed[-1] - elapsed[0])) <= 0.002, "Duration summary differs from raw samples.")
    require(report["max_process_count"] == max(sample["process_count"] for sample in samples), "Process summary differs from raw samples.")
    require(all(sample["process_count"] == 1 for sample in samples), "The v2 samples cannot prove stable helper identity.")
    require(report["rss_kib_min"] == min(sample["rss_kib_sum"] for sample in samples), "RSS minimum differs from raw samples.")
    require(report["rss_kib_max"] == max(sample["rss_kib_sum"] for sample in samples), "RSS maximum differs from raw samples.")
    require(report["rss_kib_first"] == samples[0]["rss_kib_sum"], "First RSS differs from raw samples.")
    require(report["rss_kib_last"] == samples[-1]["rss_kib_sum"], "Last RSS differs from raw samples.")
    require(report["physical_footprint_bytes_min"] == min(sample["physical_footprint_bytes_sum"] for sample in samples), "Footprint minimum differs from raw samples.")
    require(report["physical_footprint_bytes_max"] == max(sample["physical_footprint_bytes_sum"] for sample in samples), "Footprint maximum differs from raw samples.")
    require(report["package_idle_wakeups_delta"] == samples[-1]["package_idle_wakeups_sum"] - samples[0]["package_idle_wakeups_sum"], "Package wake-up delta differs from raw samples.")
    require(report["interrupt_wakeups_delta"] == samples[-1]["interrupt_wakeups_sum"] - samples[0]["interrupt_wakeups_sum"], "Interrupt wake-up delta differs from raw samples.")
    require(report["package_idle_wakeups_delta"] >= 0, "Package wake-up counter went backward.")
    require(report["interrupt_wakeups_delta"] >= 0, "Interrupt wake-up counter went backward.")
    ticks = [sample["rusage_cpu_time_ticks_sum"] for sample in samples]
    require(all(later >= earlier for earlier, later in zip(ticks, ticks[1:])), "CPU tick counter went backward.")
    require((report["rusage_cpu_timebase_numer"], report["rusage_cpu_timebase_denom"]) == (125, 3), "Unexpected Mach timebase.")
    expected_cpu = round(100 * (ticks[-1] - ticks[0]) * 125 / 3 / ((elapsed[-1] - elapsed[0]) * 1e9), 6)
    require(report["rusage_cpu_percent_of_one_core"] == expected_cpu, "CPU summary differs from converted ticks.")
    require(report["observer_cpu_seconds"] >= 0, "Observer CPU cannot be negative.")
    return {
        "samples": len(samples),
        "processes": report["max_process_count"],
        "rss_mib_range": [round(report[key] / 1024, 3) for key in ("rss_kib_min", "rss_kib_max")],
        "physical_footprint_mib_range": [round(report[key] / (1024 * 1024), 3) for key in ("physical_footprint_bytes_min", "physical_footprint_bytes_max")],
        "package_idle_wakeups": report["package_idle_wakeups_delta"],
        "interrupt_wakeups": report["interrupt_wakeups_delta"],
        "rusage_cpu_percent_of_one_core": report["rusage_cpu_percent_of_one_core"],
        "observer_cpu_seconds": report["observer_cpu_seconds"],
    }


def verify_startup():
    report = json.loads((ROOT / "macos-visible-startup.json").read_text())
    require(report["schema_id"] == "hormuz.native-client-startup-sample", "Unexpected startup schema.")
    require(report["schema_version"] == 1, "Unexpected startup schema version.")
    require(report["source_commit"] == SOURCE, "Unexpected startup source commit.")
    require(report["executable_sha256"] == EXECUTABLE_SHA256, "Unexpected startup executable.")
    require(report["scenario_requested"] == "visible connected synthetic preview", "Unexpected startup scenario.")
    trials = report["trials"]
    require(report["trial_count"] == len(trials) == 10, "Unexpected startup trial count.")
    for index, trial in enumerate(trials, 1):
        require(trial["trial"] == index, "Startup trial order is incomplete.")
        require(0 <= trial["process_seen_seconds"] <= trial["last_no_panel_seconds"] < trial["first_panel_seconds"] < 10,
                "Startup observation bounds are invalid.")
        require(trial["owned_onscreen_windows"] == 2, "Unexpected startup window count.")
        require(trial["panel_bounds"] and all(bounds["width"] >= 50 and bounds["height"] >= 150
                                              for bounds in trial["panel_bounds"]), "Unexpected panel geometry.")
    later = [trial["first_panel_seconds"] for trial in trials[1:]]
    return {
        "first_trial_seconds": trials[0]["first_panel_seconds"],
        "subsequent_nine_seconds_range": [min(later), max(later)],
        "subsequent_nine_seconds_median": median(later),
    }


def main():
    summary = {}
    for scenario in ("hidden", "visible", "folded"):
        summary[scenario] = [
            verify(ROOT / f"macos-{scenario}-idle-repeat-{repeat}.json", scenario)
            for repeat in (1, 2, 3)
        ]
    summary["startup"] = verify_startup()
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
