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
        "json_typical": (
            json.dumps(
                [{"item_id": index, "status": "active", "region": "local"}
                 for index in range(48)],
                separators=(",", ":"),
            ),
            "json_table",
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
        "lines_mixed": (
            "OK\n" * 80 + "ERROR permission denied\n" + "OK\n" * 40,
            "line_runs",
        ),
        "search_mixed": (
            "src/a.py:1:alpha\nsrc/a.py:2:beta\nsrc/b.py:3:gamma\n", "search_lines",
        ),
        "search_framed": (
            "Output:\n" + "".join(
                f"src/request.py:{index}:value: {index}\n"
                for index in range(1, 140)
            ) + "Notice: results complete",
            "search_lines",
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


def timed_variant(name: str, value: str, ordinal: int) -> str:
    """Keep each timed input structurally equivalent and content-distinct."""
    marker = f"{ordinal:06d}"
    if name in {"paths_small", "paths_typical", "paths_large"}:
        return value.replace("src/generated/", f"src/generated/{marker}/")
    if name == "paths_duplicate_unicode":
        return value.replace("src/résumé/", f"src/résumé/{marker}/")
    if name == "json_typical":
        return value.replace('"region":"local"', f'"region":"{marker}"')
    if name in {"lines_small", "lines_typical", "lines_large"}:
        return value.replace("INFO heartbeat", f"INFO heartbeat {marker}")
    if name in {"search_typical", "search_large"}:
        return value.replace("src/service/request.py:", f"src/service/{marker}/request.py:")
    raise ValueError("unsupported_timed_case")


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
    # Empty and malformed inputs remain correctness fixtures. They cannot be
    # varied without changing the behavior under test, so they are not timed.
    for name, (value, format_name) in selected.items():
        if name in {"paths_empty", "paths_malformed_marker"}:
            continue
        ordinal = 0
        for _ in range(20):
            compaction.compact_text(timed_variant(name, value, ordinal), format_name)
            ordinal += 1
        loops = 40 if name.endswith("large") else 100
        samples: list[float] = []
        timed_outputs = hashlib.sha256()
        for _ in range(17):
            inputs = [timed_variant(name, value, ordinal + index) for index in range(loops)]
            ordinal += loops
            started = time.perf_counter_ns()
            results = [compaction.compact_text(item, format_name) for item in inputs]
            samples.append((time.perf_counter_ns() - started) / loops)
            for result in results:
                encoded = result.encode("utf-8")
                timed_outputs.update(len(encoded).to_bytes(4, "big"))
                timed_outputs.update(encoded)
        measurements[name] = samples
        outputs[name]["timed_sha256"] = timed_outputs.hexdigest()
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
