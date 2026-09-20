"""Trusted, synthetic workloads for Hormuz's structural compaction runtime.

This file is executed by the controller from its own checkout. Candidate edits
cannot modify the workload definition used to judge their patch.
"""

from __future__ import annotations

import argparse
import cProfile
import hashlib
import json
import pstats
import resource
import sys
import time
from pathlib import Path


def cases() -> dict[str, tuple[str, str]]:
    return {
        "paths_empty": ("", "path_list"),
        "paths_small": (
            "\n".join(f"src/generated/item_{index:04d}.py" for index in range(16)) + "\n",
            "path_list",
        ),
        "paths_typical": (
            "\n".join(f"src/generated/item_{index:04d}.py" for index in range(120)) + "\n",
            "path_list",
        ),
        "paths_large": (
            "\n".join(f"src/generated/item_{index:04d}.py" for index in range(300)) + "\n",
            "path_list",
        ),
        "paths_duplicate_unicode": (
            "\n".join("src/résumé/file.py" for _ in range(24)) + "\n", "path_list",
        ),
        "paths_malformed_marker": (
            '{"format":"hormuz-path-list-v1","suffixes":[', "path_list",
        ),
        "lines_small": ("INFO heartbeat\n" * 16, "line_runs"),
        "lines_typical": ("INFO heartbeat\n" * 120, "line_runs"),
        "lines_large": ("INFO heartbeat\n" * 300, "line_runs"),
        "search_typical": (
            "\n".join(
                f"src/service/request.py:{index}:candidate_{index}"
                for index in range(1, 121)
            ) + "\n",
            "search_lines",
        ),
        "search_large": (
            "\n".join(f"src/service/request.py:{index}:candidate_{index}" for index in range(1, 301)) + "\n",
            "search_lines",
        ),
    }


def heldout_cases() -> dict[str, tuple[str, str]]:
    """Correctness-only inputs that are not used to select a fast candidate."""
    return {
        "framed_paths": (
            "unrelated header\n" + "\n".join(
                f"src/δelta/part_{index}.py" for index in range(1, 33)
            ) + "\nunrelated footer\n", "path_list",
        ),
        "crlf_paths": ("src/a.py\r\nsrc/b.py\r\n", "path_list"),
        "malformed_envelope": ('{"format":"hormuz-path-list-v1","suffixes":[]}', "path_list"),
        "search_mixed": (
            "src/a.py:1:alpha\nsrc/a.py:2:beta\nsrc/b.py:3:gamma\n", "search_lines",
        ),
        "json_table": (
            json.dumps(
                [{"record_id": index, "status": "synthetic", "region": "local"}
                 for index in range(24)],
                separators=(",", ":"),
            ),
            "json_table",
        ),
    }


def evaluate(root: Path, action: str) -> dict[str, object]:
    root = root.resolve(strict=True)
    sys.path.insert(0, str(root))
    from hormuz import compaction  # noqa: PLC0415

    imported = Path(compaction.__file__).resolve(strict=True)
    if not imported.is_relative_to(root):
        raise RuntimeError("benchmark_imported_wrong_source")
    selected = heldout_cases() if action == "heldout" else cases()
    fixture_sha256 = hashlib.sha256(json.dumps(selected, sort_keys=True).encode("utf-8")).hexdigest()
    outputs: dict[str, dict[str, object]] = {}
    for name, (value, format_name) in selected.items():
        result = compaction.compact_text(value, format_name)
        outputs[name] = {
            "sha256": hashlib.sha256(result.encode("utf-8")).hexdigest(),
            "bytes": len(result.encode("utf-8")),
        }
    if action in {"validate", "heldout"}:
        return {"source": str(imported), "fixture_sha256": fixture_sha256,
                "python_version": sys.version.split()[0], "outputs": outputs}

    if action == "profile":
        value, format_name = selected["paths_typical"]
        profiler = cProfile.Profile()
        profiler.enable()
        for _ in range(300):
            compaction.compact_text(value, format_name)
        profiler.disable()
        stats = pstats.Stats(profiler)
        hot: list[dict[str, object]] = []
        for (filename, line, function), record in stats.stats.items():
            path = Path(filename)
            if path.is_relative_to(root / "hormuz"):
                hot.append({
                    "path": str(path.relative_to(root)),
                    "line": line,
                    "function": function,
                    "cumulative_seconds": round(record[3], 6),
                })
        hot.sort(key=lambda item: float(item["cumulative_seconds"]), reverse=True)
        return {"source": str(imported), "fixture_sha256": fixture_sha256,
                "python_version": sys.version.split()[0], "hotspots": hot[:12], "outputs": outputs}

    if action != "benchmark":
        raise ValueError("unknown_benchmark_action")
    measurements: dict[str, list[float]] = {}
    for name, (value, format_name) in selected.items():
        for _ in range(20):
            compaction.compact_text(value, format_name)
        loops = 40 if name.endswith("large") else 100
        samples: list[float] = []
        for _ in range(17):
            started = time.perf_counter_ns()
            for _ in range(loops):
                compaction.compact_text(value, format_name)
            samples.append((time.perf_counter_ns() - started) / loops)
        measurements[name] = samples
    return {
        "source": str(imported),
        "fixture_sha256": fixture_sha256,
        "python_version": sys.version.split()[0],
        "outputs": outputs,
        "samples_ns": measurements,
        "peak_rss_bytes": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * (
            1024 if sys.platform.startswith("linux") else 1
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--action", choices=("validate", "heldout", "profile", "benchmark"), required=True)
    args = parser.parse_args()
    print(json.dumps(evaluate(args.root, args.action), sort_keys=True))


if __name__ == "__main__":
    main()
