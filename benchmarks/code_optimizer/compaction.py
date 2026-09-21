"""Local synthetic reference fixtures for Hormuz structural compaction.

The CLI runs only this file's own checkout. The independent optimizer app may
copy the pure fixture functions but owns candidate execution and containment.
"""

from __future__ import annotations

import sys

if __name__ == "__main__" and not (sys.flags.isolated and sys.flags.no_site):
    raise SystemExit("benchmark_requires_isolated_python")

import argparse
import cProfile
import hashlib
import hmac
import importlib.util
import itertools
import json
import math
import os
import pstats
import stat
import time
from pathlib import Path
from types import CodeType, ModuleType

try:
    import resource as _resource
except ImportError:  # Windows has no resource module.
    _resource = None


_IMPORT_SEQUENCE = itertools.count()
_MAX_SOURCE_BYTES = 1_048_576


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
                f"src/δelta/part_{(index * 7) % 23:02d}.py" for index in range(80)
            ) + "\nunrelated footer\n", "path_list",
        ),
        "crlf_paths": ("src/a.py\r\nsrc/b.py\r\n", "path_list"),
        "malformed_envelope": ('{"format":"hormuz-path-list-v1","suffixes":[]}', "path_list"),
        "lines_mixed": (
            "OK\n" * 320 + "ERROR permission denied\n" + "OK\n" * 40,
            "line_runs",
        ),
        "search_mixed": (
            "src/a.py:1:alpha\nsrc/a.py:2:beta\nsrc/b.py:3:gamma\n", "search_lines",
        ),
        "search_framed": (
            "Output:\n" + "".join(
                f"src/request.py:{index:03d}:value: {index}\n"
                for index in range(1, 140)
            ) + "Notice: results complete",
            "search_lines",
        ),
        "json_table": (
            json.dumps(
                [{"record_id": index, "value": "保留原文", "active": index % 2 == 0,
                  "nothing": None} for index in range(80)],
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            "json_table",
        ),
    }


def timed_variant(name: str, value: str, ordinal: int, seed: bytes | None = None) -> str:
    """Keep each timed input structurally equivalent and content-distinct."""
    marker = (
        hmac.digest(seed, f"{name}:{ordinal}".encode("ascii"), "sha256")[:16].hex()
        if seed is not None else f"{ordinal:06d}"
    )
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


def _own_root() -> Path:
    return Path(__file__).resolve(strict=True).parents[2]


def _path_is_alias(path: Path) -> bool:
    is_junction = getattr(path, "is_junction", None)
    return path.is_symlink() or (callable(is_junction) and is_junction())


def _checked_local_root(root: Path) -> Path:
    """Accept only this benchmark file's own checkout, without path aliases."""
    supplied = root if root.is_absolute() else Path.cwd() / root
    if any(_path_is_alias(path) for path in (supplied, *supplied.parents)):
        raise RuntimeError("benchmark_foreign_root")
    try:
        resolved = supplied.resolve(strict=True)
    except OSError as error:
        raise RuntimeError("benchmark_foreign_root") from error
    if resolved != supplied or resolved != _own_root():
        raise RuntimeError("benchmark_foreign_root")
    return resolved


def _source_signature(info: os.stat_result) -> tuple[int, int, int, int, int, int]:
    return (
        info.st_dev, info.st_ino, info.st_mode, info.st_size,
        info.st_mtime_ns, info.st_ctime_ns,
    )


def _read_local_source(source: Path, root: Path, initial: os.stat_result) -> bytes:
    """Read one regular-file descriptor and reject observable path changes."""
    flags = os.O_RDONLY
    for optional in ("O_CLOEXEC", "O_NOFOLLOW", "O_NONBLOCK", "O_BINARY"):
        flags |= getattr(os, optional, 0)
    try:
        descriptor = os.open(source, flags)
    except OSError as error:
        raise RuntimeError("benchmark_source_changed") from error

    def verify_path(opened: os.stat_result) -> None:
        component = root
        for part in source.relative_to(root).parts:
            component = component / part
            if _path_is_alias(component):
                raise RuntimeError("benchmark_source_changed")
        try:
            canonical = source.resolve(strict=True)
        except (OSError, RuntimeError) as error:
            raise RuntimeError("benchmark_source_changed") from error
        if canonical != source:
            raise RuntimeError("benchmark_source_changed")
        on_path = os.stat(source, follow_symlinks=False)
        if not stat.S_ISREG(on_path.st_mode) or _source_signature(on_path) != _source_signature(opened):
            raise RuntimeError("benchmark_source_changed")

    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode) or _source_signature(opened) != _source_signature(initial):
            raise RuntimeError("benchmark_source_changed")
        verify_path(opened)
        chunks: list[bytes] = []
        size = 0
        while True:
            chunk = os.read(descriptor, min(65_536, _MAX_SOURCE_BYTES + 1 - size))
            if not chunk:
                break
            chunks.append(chunk)
            size += len(chunk)
            if size > _MAX_SOURCE_BYTES:
                raise RuntimeError("benchmark_source_too_large")
        if _source_signature(os.fstat(descriptor)) != _source_signature(opened):
            raise RuntimeError("benchmark_source_changed")
        verify_path(opened)
        return b"".join(chunks)
    except OSError as error:
        raise RuntimeError("benchmark_source_changed") from error
    finally:
        os.close(descriptor)


def _prepare_local_source(package_name: str, name: str) -> tuple[ModuleType, CodeType, str]:
    """Compile the checkout's exact source bytes, ignoring package exports and pyc."""
    root = _own_root()
    source = root / "hormuz" / f"{name}.py"
    component = root
    for part in source.relative_to(root).parts:
        component = component / part
        if _path_is_alias(component):
            raise RuntimeError("benchmark_source_alias")
    try:
        initial = os.stat(source, follow_symlinks=False)
    except OSError as error:
        raise RuntimeError("benchmark_source_not_regular_file") from error
    if not stat.S_ISREG(initial.st_mode):
        raise RuntimeError("benchmark_source_not_regular_file")
    try:
        expected = source.resolve(strict=True)
    except (OSError, RuntimeError) as error:
        raise RuntimeError("benchmark_source_changed") from error
    if expected != source:
        raise RuntimeError("benchmark_source_alias")
    if not expected.is_relative_to(root):
        raise RuntimeError("benchmark_source_outside_root")
    source_name = str(expected)
    spec = importlib.util.spec_from_file_location(f"{package_name}.{name}", expected)
    if spec is None or spec.loader is None:
        raise RuntimeError("benchmark_source_unloadable")
    module = importlib.util.module_from_spec(spec)
    if getattr(module, "__file__", None) != source_name:
        raise RuntimeError("benchmark_imported_wrong_source")
    code = compile(_read_local_source(source, root, initial), source_name, "exec", dont_inherit=True)
    return module, code, source_name


def _load_local_compaction() -> tuple[ModuleType, str]:
    """Load the adjacent implementation and retain its pre-execution source path."""
    trusted_exec = exec
    package_name = f"_hormuz_optimizer_local_{next(_IMPORT_SEQUENCE)}"
    package = ModuleType(package_name)
    package.__path__ = [str(_own_root() / "hormuz")]
    package.__package__ = package_name
    formats, formats_code, _ = _prepare_local_source(package_name, "compaction_formats")
    compaction, compaction_code, source = _prepare_local_source(package_name, "compaction")
    sys.modules[package_name] = package
    sys.modules[formats.__name__] = formats
    trusted_exec(formats_code, formats.__dict__)
    sys.modules[compaction.__name__] = compaction
    trusted_exec(compaction_code, compaction.__dict__)
    return compaction, source


def load_local_compaction() -> ModuleType:
    """Load only the implementation adjacent to this reference workload."""
    return _load_local_compaction()[0]


def _local_call(compaction: ModuleType, items: list[tuple[str, str]]) -> list[str]:
    return [compaction.compact_text(value, format_name) for value, format_name in items]


def _local_hotspots(profiler: cProfile.Profile, root: Path) -> list[dict[str, object]]:
    hot: list[dict[str, object]] = []
    for (filename, line, function), record in pstats.Stats(profiler).stats.items():
        path = Path(filename)
        if path.is_relative_to(root / "hormuz"):
            hot.append({
                "path": str(path.relative_to(root)), "line": line,
                "function": function, "cumulative_seconds": round(record[3], 6),
            })
    hot.sort(key=lambda item: float(item["cumulative_seconds"]), reverse=True)
    return hot[:12]


def _rss_to_bytes(value: int, platform: str) -> int | None:
    if platform == "darwin":
        return value
    if platform.startswith(("linux", "freebsd")):
        return value * 1024
    return None


def evaluate(root: Path, action: str, *, seed: bytes | None = None) -> dict[str, object]:
    """Exercise only the checkout containing this script with synthetic inputs."""
    root = _checked_local_root(root)
    if seed is not None and (not isinstance(seed, bytes) or len(seed) != 32):
        raise ValueError("benchmark_invalid_seed")
    if action not in {"validate", "heldout", "profile", "benchmark"}:
        raise ValueError("unknown_benchmark_action")
    compaction, source = _load_local_compaction()
    selected = heldout_cases() if action == "heldout" else cases()
    fixture_sha256 = hashlib.sha256(json.dumps(selected, sort_keys=True).encode("utf-8")).hexdigest()
    outputs: dict[str, dict[str, object]] = {}
    canonical = _local_call(compaction, list(selected.values()))
    for name, result in zip(selected, canonical, strict=True):
        encoded = result.encode("utf-8")
        outputs[name] = {"sha256": hashlib.sha256(encoded).hexdigest(), "bytes": len(encoded)}
    report: dict[str, object] = {
        "source": source, "fixture_sha256": fixture_sha256,
        "python_version": sys.version.split()[0], "outputs": outputs,
    }
    if action in {"validate", "heldout"}:
        return report
    if action == "profile":
        value, format_name = selected["paths_typical"]
        variants = [(timed_variant("paths_typical", value, index, seed), format_name)
                    for index in range(300)]
        profiler = cProfile.Profile()
        profiler.enable()
        _local_call(compaction, variants)
        profiler.disable()
        report["hotspots"] = _local_hotspots(profiler, root)
        return report
    measurements: dict[str, list[float]] = {}
    # Empty and malformed inputs remain correctness fixtures, not timed.
    for name, (value, format_name) in selected.items():
        if name in {"paths_empty", "paths_malformed_marker"}:
            continue
        warmup = [(timed_variant(name, value, index, seed), format_name)
                  for index in range(20)]
        _local_call(compaction, warmup)
        ordinal = 20
        loops = 40 if name.endswith("large") else 100
        samples: list[float] = []
        timed_outputs = hashlib.sha256()
        for _ in range(17):
            inputs = [(timed_variant(name, value, ordinal + index, seed), format_name)
                      for index in range(loops)]
            ordinal += loops
            started = time.perf_counter_ns()
            results = _local_call(compaction, inputs)
            elapsed = time.perf_counter_ns() - started
            sample = elapsed / loops
            if not math.isfinite(sample) or sample <= 0:
                raise RuntimeError("benchmark_invalid_sample")
            samples.append(sample)
            for result in results:
                encoded = result.encode("utf-8")
                timed_outputs.update(len(encoded).to_bytes(4, "big"))
                timed_outputs.update(encoded)
        measurements[name] = samples
        outputs[name]["timed_sha256"] = timed_outputs.hexdigest()
    report["samples_ns"] = measurements
    if _resource is None:
        report["peak_rss_bytes"] = None
    else:
        peak_rss = _resource.getrusage(_resource.RUSAGE_SELF).ru_maxrss
        report["peak_rss_bytes"] = _rss_to_bytes(peak_rss, sys.platform)
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--action", choices=("validate", "heldout", "profile", "benchmark"), required=True)
    parser.add_argument("--seed-stdin", action="store_true")
    args = parser.parse_args()
    try:
        root = _checked_local_root(args.root)
    except RuntimeError:
        parser.error("benchmark_foreign_root")
    seed = None
    if args.seed_stdin:
        raw = sys.stdin.buffer.readline(66)
        if (
            len(raw) != 65 or not raw.endswith(b"\n")
            or any(character not in b"0123456789abcdef" for character in raw[:-1])
        ):
            parser.error("benchmark_invalid_seed")
        seed = bytes.fromhex(raw[:-1].decode("ascii"))
    print(json.dumps(evaluate(root, args.action, seed=seed), sort_keys=True))


if __name__ == "__main__":
    main()
