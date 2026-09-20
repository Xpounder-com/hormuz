#!/usr/bin/env python3
"""Correct the archived schema-v2 Mac CPU units without changing observations.

The original collector mislabeled Darwin ri_user_time + ri_system_time as
nanoseconds. Those fields are Mach absolute-time ticks. This converter retains
the untouched v2 report as its provenance and emits a separate v3 report.
"""

import argparse
import copy
import hashlib
import json
from pathlib import Path


# mach_timebase_info() on the measured Mac17,2 / Apple M5 host returned 125/3.
TIMEBASE_NUMER = 125
TIMEBASE_DENOM = 3
SOURCE = "d854a5a453fcbe20cb3f4c1e261e146f2da93855"
EXECUTABLE_SHA256 = "2ece94a031a039d0e27c7ce873787f83f704045e44b0e63cedb71841b9bb3ecc"


def require(condition, message):
    if not condition:
        raise ValueError(message)


def normalize(raw_bytes):
    original = json.loads(raw_bytes)
    require(original["schema_id"] == "hormuz.native-client-idle-sample", "Unexpected schema.")
    require(original["schema_version"] == 2, "Only original v2 reports can be normalized.")
    require(original["source_commit"] == SOURCE, "Unexpected source commit.")
    require(original["executable_sha256"] == EXECUTABLE_SHA256, "Unexpected executable.")
    require(original["recorded_at_utc"].startswith("2026-09-20T"), "Unexpected collection date.")
    require(not original["child_departures_observed"], "A process departed during the sample.")
    samples = original["samples"]
    require(len(samples) >= 2, "At least two samples are required.")
    # The v2 data contains counts but not process identities. A count of one at
    # every sample proves that only the continuously checked root PID was used.
    require(all(sample["process_count"] == 1 for sample in samples),
            "v2 cannot prove stable member identity when helpers were present.")
    elapsed = samples[-1]["elapsed_seconds"] - samples[0]["elapsed_seconds"]
    require(elapsed > 0, "The sample interval is empty.")
    require(abs(original["duration_seconds"] - elapsed) <= 0.002,
            "Duration summary differs from samples.")
    original_ticks = [sample["rusage_cpu_time_ns_sum"] for sample in samples]
    require(all(later >= earlier for earlier, later in zip(original_ticks, original_ticks[1:])),
            "CPU counter went backward.")
    require(original["rusage_cpu_percent_of_one_core"] == round(
        100 * (original_ticks[-1] - original_ticks[0]) / (elapsed * 1e9), 4
    ), "The v2 CPU summary does not match its original erroneous formula.")

    corrected = copy.deepcopy(original)
    corrected["schema_version"] = 3
    corrected["source_raw_sha256"] = hashlib.sha256(raw_bytes).hexdigest()
    corrected["normalization"] = (
        "Schema v2 rusage_cpu_time_ns_sum was mislabeled: values were Mach ticks. "
        "Renamed that raw field and recalculated the derived CPU percentage "
        "with measured mach_timebase_info 125/3; all other numeric samples are unchanged."
    )
    corrected["rusage_cpu_timebase_numer"] = TIMEBASE_NUMER
    corrected["rusage_cpu_timebase_denom"] = TIMEBASE_DENOM
    corrected["process_topology_stable"] = True
    for sample in corrected["samples"]:
        sample["rusage_cpu_time_ticks_sum"] = sample.pop("rusage_cpu_time_ns_sum")
    tick_delta = original_ticks[-1] - original_ticks[0]
    corrected["rusage_cpu_percent_of_one_core"] = round(
        100 * tick_delta * TIMEBASE_NUMER / TIMEBASE_DENOM / (elapsed * 1e9), 6
    )
    corrected["method"] = (
        original["method"] + "; schema-v2 CPU ticks normalized to nanoseconds "
        "with the measured Mach timebase"
    )
    corrected["limits"].append(
        "The original schema-v2 CPU percentage underreported Mach ticks as nanoseconds; "
        "this v3 report retains the raw SHA-256 and corrected calculation."
    )
    return corrected


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("raw", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Refusing to overwrite an existing normalized report.")
    corrected = normalize(args.raw.read_bytes())
    with args.output.open("x") as stream:
        json.dump(corrected, stream, indent=2)
        stream.write("\n")


if __name__ == "__main__":
    main()
