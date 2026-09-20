"""Trusted, synthetic workloads for Hormuz's structural compaction runtime.

This file is executed by the controller from its own checkout. Candidate edits
cannot modify the workload definition used to judge their patch.
"""

from __future__ import annotations

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
import resource
import select
import subprocess
import sys
import time
from pathlib import Path
from types import ModuleType


_IMPORT_SEQUENCE = itertools.count()
_MAX_FRAME_BYTES = 4 * 1024 * 1024
_MAX_VALUE_BYTES = 64 * 1024
_MAX_ITEMS = 300
_ROUNDTRIP_TIMEOUT_SECONDS = 10


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


def load_compaction(root: Path) -> ModuleType:
    """Load the exact source file without executing a package export first."""
    source = root / "hormuz/compaction.py"
    if source.is_symlink() or not source.is_file():
        raise RuntimeError("benchmark_source_not_regular_file")
    expected = source.resolve(strict=True)
    if not expected.is_relative_to(root):
        raise RuntimeError("benchmark_source_outside_root")
    package_name = f"_hormuz_optimizer_target_{next(_IMPORT_SEQUENCE)}"
    package = ModuleType(package_name)
    package.__path__ = [str(source.parent)]
    package.__package__ = package_name
    sys.modules[package_name] = package
    spec = importlib.util.spec_from_file_location(f"{package_name}.compaction", expected)
    if spec is None or spec.loader is None:
        raise RuntimeError("benchmark_source_unloadable")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    loaded_file = getattr(module, "__file__", None)
    if not isinstance(loaded_file, str) or Path(loaded_file).resolve(strict=True) != expected:
        raise RuntimeError("benchmark_imported_wrong_source")
    return module


def _child_main(root: Path) -> int:
    """Run candidate code only in this disposable, sandbox-inheriting process."""
    root = root.resolve(strict=True)
    sys.path.insert(0, str(root))
    compaction = load_compaction(root)
    for raw in iter(lambda: sys.stdin.buffer.readline(_MAX_FRAME_BYTES + 1), b""):
        if len(raw) > _MAX_FRAME_BYTES or not raw.endswith(b"\n"):
            return 2
        try:
            request = json.loads(raw)
            if not isinstance(request, dict) or set(request) != {"id", "op", "items"}:
                return 2
            request_id, operation, items = request["id"], request["op"], request["items"]
            if (
                isinstance(request_id, bool) or not isinstance(request_id, int)
                or request_id < 0 or operation not in {"call", "profile"}
                or not isinstance(items, list) or not 1 <= len(items) <= _MAX_ITEMS
            ):
                return 2
            checked: list[tuple[str, str]] = []
            for item in items:
                if (
                    not isinstance(item, list) or len(item) != 2
                    or not isinstance(item[0], str) or not isinstance(item[1], str)
                    or len(item[0].encode("utf-8")) > _MAX_VALUE_BYTES
                    or item[1] not in {"path_list", "line_runs", "search_lines", "json_table"}
                ):
                    return 2
                checked.append((item[0], item[1]))
            profiler = cProfile.Profile() if operation == "profile" else None
            if profiler is not None:
                profiler.enable()
            results = [compaction.compact_text(value, format_name) for value, format_name in checked]
            if profiler is not None:
                profiler.disable()
            response: dict[str, object] = {"id": request_id, "outputs": results}
            if profiler is not None:
                hot: list[dict[str, object]] = []
                for (filename, line, function), record in pstats.Stats(profiler).stats.items():
                    path = Path(filename)
                    if path.is_relative_to(root / "hormuz"):
                        hot.append({
                            "path": str(path.relative_to(root)), "line": line,
                            "function": function, "cumulative_seconds": round(record[3], 6),
                        })
                hot.sort(key=lambda item: float(item["cumulative_seconds"]), reverse=True)
                response["hotspots"] = hot[:12]
            encoded = json.dumps(response, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n"
            if len(encoded) > _MAX_FRAME_BYTES:
                return 2
            sys.stdout.buffer.write(encoded)
            sys.stdout.buffer.flush()
        except (TypeError, ValueError, UnicodeError):
            return 2
    return 0


class CandidateProcess:
    """Keep candidate execution separate from trusted fixtures and measurements."""

    def __init__(self, root: Path) -> None:
        (root / "tmp").mkdir(exist_ok=True)
        environment = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": str(root), "TMPDIR": str(root / "tmp"),
            "PYTHONDONTWRITEBYTECODE": "1", "PYTHONNOUSERSITE": "1",
            "LANG": "C.UTF-8",
        }
        self.process = subprocess.Popen(
            [sys.executable, str(Path(__file__).resolve(strict=True)), "--root", str(root), "--child"],
            cwd=root, env=environment, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, bufsize=0,
        )
        assert self.process.stdin is not None and self.process.stdout is not None
        os.set_blocking(self.process.stdin.fileno(), False)
        os.set_blocking(self.process.stdout.fileno(), False)
        self.pending = bytearray()
        self.request_id = 0

    def __enter__(self) -> CandidateProcess:
        return self

    def __exit__(self, _type: object, _value: object, _traceback: object) -> None:
        assert self.process.stdin is not None and self.process.stdout is not None
        try:
            self.process.stdin.close()
            if self.process.poll() is None:
                try:
                    self.process.terminate()
                except ProcessLookupError:
                    pass
            try:
                self.process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=2)
        finally:
            self.process.stdout.close()

    @staticmethod
    def _ready(fd: int, *, writing: bool, deadline: float) -> None:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise RuntimeError("benchmark_child_timeout")
        readable, writable, _ = select.select(
            [] if writing else [fd], [fd] if writing else [], [], remaining,
        )
        if not (writable if writing else readable):
            raise RuntimeError("benchmark_child_timeout")

    def _write(self, frame: bytes, deadline: float) -> None:
        assert self.process.stdin is not None
        fd = self.process.stdin.fileno()
        view = memoryview(frame)
        while view:
            self._ready(fd, writing=True, deadline=deadline)
            try:
                sent = os.write(fd, view[:65536])
            except BlockingIOError:
                continue
            except BrokenPipeError as error:
                raise RuntimeError("benchmark_child_exited") from error
            view = view[sent:]

    def _read(self, deadline: float) -> bytes:
        assert self.process.stdout is not None
        fd = self.process.stdout.fileno()
        while True:
            newline = self.pending.find(b"\n")
            if newline >= 0:
                if newline + 1 > _MAX_FRAME_BYTES:
                    raise RuntimeError("benchmark_child_oversized_frame")
                frame = bytes(self.pending[:newline])
                del self.pending[:newline + 1]
                return frame
            if len(self.pending) > _MAX_FRAME_BYTES:
                raise RuntimeError("benchmark_child_oversized_frame")
            self._ready(fd, writing=False, deadline=deadline)
            try:
                chunk = os.read(fd, min(65536, _MAX_FRAME_BYTES + 1 - len(self.pending)))
            except BlockingIOError:
                continue
            if not chunk:
                raise RuntimeError("benchmark_child_exited")
            self.pending.extend(chunk)

    def call(self, items: list[tuple[str, str]], *, profile: bool = False) -> tuple[list[str], int, list[dict[str, object]]]:
        if not 1 <= len(items) <= _MAX_ITEMS:
            raise ValueError("benchmark_invalid_item_count")
        request_id = self.request_id
        self.request_id += 1
        payload = json.dumps(
            {"id": request_id, "op": "profile" if profile else "call", "items": items},
            ensure_ascii=False, separators=(",", ":"),
        ).encode("utf-8") + b"\n"
        if len(payload) > _MAX_FRAME_BYTES:
            raise ValueError("benchmark_oversized_request")
        deadline = time.monotonic() + _ROUNDTRIP_TIMEOUT_SECONDS
        started = time.perf_counter_ns()
        self._write(payload, deadline)
        frame = self._read(deadline)
        elapsed = time.perf_counter_ns() - started
        try:
            response = json.loads(frame)
        except (ValueError, UnicodeError) as error:
            raise RuntimeError("benchmark_child_invalid_json") from error
        if not isinstance(response, dict) or set(response) != ({"id", "outputs", "hotspots"} if profile else {"id", "outputs"}):
            raise RuntimeError("benchmark_child_invalid_response")
        outputs = response["outputs"]
        if (
            isinstance(response["id"], bool) or not isinstance(response["id"], int)
            or response["id"] != request_id or not isinstance(outputs, list)
            or len(outputs) != len(items)
            or any(not isinstance(value, str) or len(value.encode("utf-8")) > _MAX_VALUE_BYTES for value in outputs)
        ):
            raise RuntimeError("benchmark_child_invalid_outputs")
        hotspots: list[dict[str, object]] = []
        if profile:
            raw_hotspots = response["hotspots"]
            if not isinstance(raw_hotspots, list) or len(raw_hotspots) > 12:
                raise RuntimeError("benchmark_child_invalid_profile")
            for item in raw_hotspots:
                if not isinstance(item, dict) or set(item) != {"path", "line", "function", "cumulative_seconds"}:
                    raise RuntimeError("benchmark_child_invalid_profile")
                path, line = item["path"], item["line"]
                function, seconds = item["function"], item["cumulative_seconds"]
                if (
                    not isinstance(path, str) or not path.startswith("hormuz/")
                    or Path(path).is_absolute() or ".." in Path(path).parts
                    or isinstance(line, bool) or not isinstance(line, int) or line < 1
                    or not isinstance(function, str) or len(function) > 128
                    or isinstance(seconds, bool) or not isinstance(seconds, (int, float))
                    or not math.isfinite(seconds) or seconds < 0
                ):
                    raise RuntimeError("benchmark_child_invalid_profile")
                hotspots.append(item)
        return outputs, elapsed, hotspots


def evaluate(root: Path, action: str, *, seed: bytes | None = None) -> dict[str, object]:
    if seed is not None and (not isinstance(seed, bytes) or len(seed) != 32):
        raise ValueError("benchmark_invalid_seed")
    root = root.resolve(strict=True)
    source = root / "hormuz/compaction.py"
    if source.is_symlink() or not source.is_file():
        raise RuntimeError("benchmark_source_not_regular_file")
    imported = source.resolve(strict=True)
    if not imported.is_relative_to(root):
        raise RuntimeError("benchmark_source_outside_root")
    selected = heldout_cases() if action == "heldout" else cases()
    fixture_sha256 = hashlib.sha256(json.dumps(selected, sort_keys=True).encode("utf-8")).hexdigest()
    outputs: dict[str, dict[str, object]] = {}
    with CandidateProcess(root) as candidate:
        canonical, _, _ = candidate.call(list(selected.values()))
        for name, result in zip(selected, canonical, strict=True):
            encoded = result.encode("utf-8")
            outputs[name] = {"sha256": hashlib.sha256(encoded).hexdigest(), "bytes": len(encoded)}
        if action in {"validate", "heldout"}:
            return {"source": str(imported), "fixture_sha256": fixture_sha256,
                    "python_version": sys.version.split()[0], "outputs": outputs}
        if action == "profile":
            value, format_name = selected["paths_typical"]
            variants = [(timed_variant("paths_typical", value, index, seed), format_name)
                        for index in range(300)]
            _, _, hot = candidate.call(variants, profile=True)
            return {"source": str(imported), "fixture_sha256": fixture_sha256,
                    "python_version": sys.version.split()[0], "hotspots": hot, "outputs": outputs}
        if action != "benchmark":
            raise ValueError("unknown_benchmark_action")
        measurements: dict[str, list[float]] = {}
        # Empty and malformed inputs remain correctness fixtures, not timed.
        for name, (value, format_name) in selected.items():
            if name in {"paths_empty", "paths_malformed_marker"}:
                continue
            ordinal = 0
            warmup = [(timed_variant(name, value, index, seed), format_name) for index in range(20)]
            candidate.call(warmup)
            ordinal += 20
            loops = 40 if name.endswith("large") else 100
            samples: list[float] = []
            timed_outputs = hashlib.sha256()
            for _ in range(17):
                inputs = [(timed_variant(name, value, ordinal + index, seed), format_name)
                          for index in range(loops)]
                ordinal += loops
                results, elapsed, _ = candidate.call(inputs)
                samples.append(elapsed / loops)
                for result in results:
                    encoded = result.encode("utf-8")
                    timed_outputs.update(len(encoded).to_bytes(4, "big"))
                    timed_outputs.update(encoded)
            measurements[name] = samples
            outputs[name]["timed_sha256"] = timed_outputs.hexdigest()
    peak_rss = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
    return {
        "source": str(imported), "fixture_sha256": fixture_sha256,
        "python_version": sys.version.split()[0], "outputs": outputs,
        "samples_ns": measurements,
        "peak_rss_bytes": peak_rss * (1024 if sys.platform.startswith("linux") else 1),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--action", choices=("validate", "heldout", "profile", "benchmark"))
    parser.add_argument("--child", action="store_true")
    parser.add_argument("--seed-stdin", action="store_true")
    args = parser.parse_args()
    if args.child:
        if args.seed_stdin or args.action is not None:
            parser.error("child_mode_rejects_parent_options")
        raise SystemExit(_child_main(args.root))
    if args.action is None:
        parser.error("--action is required")
    seed = None
    if args.seed_stdin:
        raw = sys.stdin.buffer.readline(66)
        if (
            len(raw) != 65 or not raw.endswith(b"\n")
            or any(character not in b"0123456789abcdef" for character in raw[:-1])
        ):
            parser.error("benchmark_invalid_seed")
        seed = bytes.fromhex(raw[:-1].decode("ascii"))
    print(json.dumps(evaluate(args.root, args.action, seed=seed), sort_keys=True))


if __name__ == "__main__":
    main()
