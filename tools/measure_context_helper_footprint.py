#!/usr/bin/env python3
"""Compare actual one-shot helper process trees on identical synthetic input.

This is macOS helper-only evidence, not a desktop footprint, provider quality,
power, customer machine, or shipping-backend acceptance claim.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import signal
import statistics
import subprocess
import threading
import time
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from hormuz.compaction_formats import canonical_json, restore_text
from hormuz.compaction_runtime import ENCODING_SHA256, TOKENIZER_VERSION, validate_tokenizer_resources

SNAPSHOT_TIMEOUT_SECONDS = 2
OBSERVER_JOIN_TIMEOUT_SECONDS = SNAPSHOT_TIMEOUT_SECONDS + 1


class SyntheticGateway(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path != "/health" or self.headers.get("Authorization"):
            self.send_error(400)
            return
        self.send_response(200)
        self.send_header("X-Hormuz-Context-Formats", "structural-v1")
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"{}")

    def log_message(self, *_args: object) -> None:
        pass


def process_table() -> dict[int, tuple[int, int]]:
    output = subprocess.check_output(["/bin/ps", "-axo", "pid=,ppid=,rss="], text=True,
                                     timeout=SNAPSHOT_TIMEOUT_SECONDS)
    return {int(pid): (int(parent), int(rss)) for pid, parent, rss in (line.split() for line in output.splitlines())}


def descendants(root: int, table: dict[int, tuple[int, int]]) -> set[int]:
    selected = {root}
    while True:
        children = {pid for pid, (parent, _) in table.items() if parent in selected}
        expanded = selected | children
        if expanded == selected:
            return selected - {root}  # exclude /usr/bin/time, not the helper
        selected = expanded


def measured_run(helper: Path, cache: Path, wire: bytes) -> tuple[dict[str, object], bytes]:
    command = ["/usr/bin/time", "-l", str(helper), "relay-bridge", "--client", "codex", "--path", "/v1/responses"]
    # Match the native relay's credential-free child boundary, not ambient
    # developer API keys, proxy settings or application configuration.
    environment = {"PATH": os.defpath, "HORMUZ_CONTEXT_TOKENIZER_CACHE": str(cache)}
    started = time.perf_counter()
    process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               env=environment, start_new_session=True)
    samples: list[dict[str, int | float]] = []
    observed: set[int] = set()
    observer_errors: list[BaseException] = []
    stop = threading.Event()

    def sample() -> None:
        try:
            while not stop.is_set():
                table = process_table()
                selected = descendants(process.pid, table)
                observed.update(selected)
                samples.append({"elapsed_seconds": round(time.perf_counter() - started, 6),
                                "helper_processes": len(selected), "rss_sum_bytes": sum(table[pid][1] * 1024 for pid in selected)})
                stop.wait(0.005)
        except BaseException as error:
            observer_errors.append(error)
            stop.set()

    observer = threading.Thread(target=sample, daemon=True)
    observer.start()
    try:
        output, diagnostic = process.communicate(wire, timeout=30)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)  # only this newly owned process group
        process.communicate()
        raise ValueError("Synthetic helper timed out") from None
    finally:
        stop.set()
        observer.join(timeout=OBSERVER_JOIN_TIMEOUT_SECONDS)
    if observer.is_alive():
        raise ValueError("Process observer did not stop")
    if observer_errors:
        raise ValueError("Process observer failed") from observer_errors[0]
    elapsed = time.perf_counter() - started
    if process.returncode != 0 or not output.startswith(b"\x01"):
        raise ValueError("Synthetic eligible helper request failed or did not optimize")
    if observed & process_table().keys():
        raise ValueError("A sampled owned helper process survived completion")
    timing = diagnostic.decode("utf-8")
    clocks = re.search(r"([\d.]+) real\s+([\d.]+) user\s+([\d.]+) sys", timing)
    memory = re.search(r"(\d+)\s+maximum resident set size", timing)
    if clocks is None or memory is None or not any(sample["helper_processes"] for sample in samples):
        raise ValueError("Incomplete macOS process/time measurement")
    return {
        "result": "passed", "observer_status": "passed", "wall_seconds_observed": round(elapsed, 6),
        "wall_seconds_time": float(clocks[1]), "user_cpu_seconds": float(clocks[2]),
        "system_cpu_seconds": float(clocks[3]), "time_max_rss_bytes": int(memory[1]),
        "sampled_tree_peak_rss_sum_bytes": max(sample["rss_sum_bytes"] for sample in samples),
        "max_helper_processes": max(sample["helper_processes"] for sample in samples),
        "sample_count": len(samples), "samples": samples,
        "output_bytes": len(output), "output_sha256": hashlib.sha256(output).hexdigest(),
        "owned_processes_survived": 0,
    }, output


def compressed_component(path: Path) -> int:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        information = zipfile.ZipInfo("helper", (1980, 1, 1, 0, 0, 0))
        information.compress_type = zipfile.ZIP_DEFLATED
        archive.writestr(information, path.read_bytes(), compresslevel=9)
    return len(output.getvalue())


def measure(rust: Path, python: Path, python_backend: Path, cache: Path, repetitions: int) -> dict[str, object]:
    validate_tokenizer_resources(cache)
    text = "Final output:\n" + "".join(f"src/generated/item_{index % 20}.py\n" for index in range(180))
    body = canonical_json({"model": "synthetic-model", "input": [
        {"type": "function_call", "call_id": "synthetic-call", "name": "exec_command", "arguments": canonical_json({"cmd": "rg --files src/generated"})},
        {"type": "function_call_output", "call_id": "synthetic-call", "output": text},
    ]}).encode()
    server = ThreadingHTTPServer(("127.0.0.1", 0), SyntheticGateway)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    origin = f"http://127.0.0.1:{server.server_port}".encode()
    wire = len(origin).to_bytes(2, "big") + origin + body
    results: dict[str, list[dict[str, object]]] = {"python": [], "rust": []}
    expected = None
    try:
        for repetition in range(repetitions):
            for name in (("python", "rust") if repetition % 2 == 0 else ("rust", "python")):
                record, output = measured_run(rust if name == "rust" else python, cache, wire)
                changed = json.loads(output[1:])
                if restore_text(changed["input"][1]["output"]) != text:
                    raise ValueError("Synthetic request reconstruction differs")
                if expected is not None and expected != output:
                    raise ValueError("Python/Rust helper output bytes differ")
                expected = output
                record["repetition"] = repetition + 1
                results[name].append(record)
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)
    summary = {}
    for name, records in results.items():
        summary[name] = {field + "_median": statistics.median(record[field] for record in records) for field in (
            "wall_seconds_time", "user_cpu_seconds", "system_cpu_seconds", "sampled_tree_peak_rss_sum_bytes", "time_max_rss_bytes")}
    files = {"rust": rust, "python_backend": python_backend, "python_launcher": python}
    artifacts = {name: {"bytes": path.stat().st_size, "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                       "deflated_component_zip_bytes": compressed_component(path)} for name, path in files.items()}
    return {
        "schema_id": "hormuz.context-helper-footprint", "schema_version": 1, "result": "passed",
        "scope": "actual one-shot macOS helper process trees; not resident UI or full desktop",
        "os": os.uname().sysname, "architecture": os.uname().machine, "repetitions_per_helper": repetitions,
        "reference_tokenizer_version": TOKENIZER_VERSION, "resource_sha256": ENCODING_SHA256,
        "input_bytes": len(body), "input_classification": "existing-contract synthetic framed path-list",
        "sampling_interval_target_seconds": 0.005, "provider_requests": 0,
        "shipping_default_changed": False, "artifacts": artifacts, "summary": summary, "runs": results,
        "limitations": [
            "Fresh process per run; warm filesystem, developer Mac, observer overhead, not a controlled clean-machine baseline.",
            "Original notarized PyInstaller backend versus local development Rust release build; signing/distribution modes differ, especially for startup wall time.",
            "Tree RSS is a sum, can double-count shared pages and may miss shorter transients; it is not macOS physical footprint.",
            "time -l max RSS is retained separately, not misrepresented as a sum of the PyInstaller process tree.",
            "Compressed component ZIPs are identical compression measurements, not customer app release archives.",
            "No installed-client, resident UI, signing, packaging-default, rollback or production benefit acceptance is inferred.",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rust-helper", type=Path, required=True)
    parser.add_argument("--python-helper", type=Path, required=True)
    parser.add_argument("--python-backend", type=Path, required=True)
    parser.add_argument("--tokenizer-cache", type=Path, required=True)
    parser.add_argument("--repetitions", type=int, default=5)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if os.uname().sysname != "Darwin" or not 3 <= args.repetitions <= 10:
        parser.error("Use macOS and 3 to 10 alternating repetitions")
    result = measure(args.rust_helper.resolve(strict=True), args.python_helper.resolve(strict=True),
                     args.python_backend.resolve(strict=True), args.tokenizer_cache.resolve(strict=True), args.repetitions)
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, indent=2)
        stream.write("\n")
    print(json.dumps({"result": "passed", "summary": result["summary"], "artifacts": result["artifacts"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
