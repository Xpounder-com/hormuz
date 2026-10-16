"""User-supplied tasks and independent checks, executed only through the router."""
import hashlib
import errno
import http.client
import json
import math
import os
from pathlib import Path
import platform
import re
try:
    import resource
except ImportError:
    resource = None
import socket
import stat
import statistics
import subprocess
import threading
import time

from .compaction_formats import strict_json_loads
from .config import GatewayConfig
from ._config_input import load_configuration_input
from .execution_destination import route_fingerprint
from .coding_qualification import request_profile_digest
from .local_history_files import export_private, local_path, read_private, safe_path
from .work_learning import contains_provider_state, request_characteristics

MAX_BYTES = 262144
MAX_SCORECARD_BYTES = 2 * 1024 * 1024
TASKS = {"summarization", "translation", "structured_extraction"}


class BenchmarkError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def initialize_suite(path):
    path = local_path(Path(path))
    if path.exists() or path.is_symlink():
        raise BenchmarkError("benchmark_output_exists")
    cases = []
    for word, number in (("seven", 7), ("nine", 9), ("twelve", 12)):
        cases.append({"id": "summary-" + word, "task_type": "summarization", "partition": "held_out",
                      "request": {"input": f"Summarize the count as a digit only: {word} boxes.", "max_output_tokens": 128},
                      "checks": [{"metric": "correctness", "kind": "text_equals", "value": str(number)}]})
        cases.append({"id": "json-" + word, "task_type": "structured_extraction", "partition": "held_out",
                      "request": {"input": f"Extract the count into JSON with integer field count: {word} boxes.",
                                  "max_output_tokens": 128, "text": {"format": {"type": "json_schema", "name": "count",
                                      "strict": True, "schema": {"type": "object", "properties": {"count": {"type": "integer"}},
                                      "required": ["count"], "additionalProperties": False}}}},
                      "checks": [{"metric": "structured_accuracy", "kind": "json_equals", "value": {"count": number}}]})
    suite = {"schema_id": "hormuz.benchmark-suite", "schema_version": 1, "name": "my-work",
             "targets": {"objective": "quality", "min_pass_rate": 1, "min_cases": 3}, "cases": cases}
    export_private(path, json.dumps(suite, indent=2, allow_nan=False).encode())
    return suite


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     allow_nan=False).encode()).hexdigest()


def benchmark_profile_digest(value):
    # Quality and speed measured with a larger generation budget must not be
    # silently inherited by a request that can truncate sooner.
    return digest({"controls": request_profile_digest({"stream": False, **value, "store": False}),
                   "max_output_tokens": value.get("max_output_tokens")})


def machine_facts():
    memory = None
    try:
        memory = os.sysconf("SC_PHYS_PAGES") * os.sysconf("SC_PAGE_SIZE")
    except (OSError, ValueError, AttributeError):
        if platform.system() == "Darwin":
            try:
                probe = subprocess.run(["/usr/sbin/sysctl", "-n", "hw.memsize"], capture_output=True,
                                       text=True, timeout=2, env={"PATH": "/usr/bin:/bin"})
                if probe.returncode == 0 and probe.stdout.strip().isdigit():
                    memory = int(probe.stdout.strip())
            except (OSError, subprocess.SubprocessError):
                pass
    return {"system": platform.system(), "architecture": platform.machine(),
            "logical_cpu_count": os.cpu_count(), "physical_memory_bytes": memory if memory and memory > 0 else None}


def load_document(path):
    descriptor = os.open(local_path(Path(path)), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_BYTES:
            raise BenchmarkError("benchmark_file_invalid")
        raw = stream.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise BenchmarkError("benchmark_file_invalid")
    return strict_json_loads(raw.decode())


def read_suite(path):
    suite = load_document(path)
    validate_suite(suite)
    return suite


def validate_suite(suite):
    if (type(suite) is not dict or set(suite) != {"schema_id", "schema_version", "name", "targets", "cases"}
            or suite.get("schema_id") != "hormuz.benchmark-suite"
            or type(suite.get("schema_version")) is not int or suite["schema_version"] != 1
            or type(suite.get("name")) is not str or not 1 <= len(suite["name"]) <= 128
            or not valid_targets(suite.get("targets")) or type(suite.get("cases")) is not list
            or not 1 <= len(suite["cases"]) <= 32):
        raise BenchmarkError("benchmark_suite_invalid")
    seen = set()
    for case in suite["cases"]:
        if (type(case) is not dict or set(case) != {"id", "task_type", "partition", "request", "checks"}
                or not label(case.get("id")) or case["id"] in seen
                or type(case.get("task_type")) is not str or case["task_type"] not in TASKS
                or case.get("partition") not in ("development", "held_out")
                or not valid_request(case.get("request"))
                or type(case.get("checks")) is not list or not 1 <= len(case["checks"]) <= 16
                or any(not valid_check(check) for check in case["checks"])):
            raise BenchmarkError("benchmark_suite_invalid")
        if len({check["metric"] for check in case["checks"]}) != len(case["checks"]):
            raise BenchmarkError("benchmark_suite_invalid")
        seen.add(case["id"])
    return suite


def label(value):
    return type(value) is str and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", value) is not None


def number(value, minimum, maximum):
    return type(value) in (int, float) and minimum <= value <= maximum and math.isfinite(value)


def valid_targets(value):
    return (type(value) is dict and {"objective", "min_pass_rate", "min_cases"} <= set(value)
            and not set(value) - {"objective", "min_pass_rate", "min_cases", "max_p95_latency_ms", "max_mean_cost_microusd", "min_check_rates"}
            and value["objective"] in ("quality", "latency", "cost")
            and number(value["min_pass_rate"], 0, 1)
            and type(value["min_cases"]) is int and 1 <= value["min_cases"] <= 32
            and all(value.get(key) is None or number(value[key], 0, limit)
                    for key, limit in (("max_p95_latency_ms", 3600000), ("max_mean_cost_microusd", 10**12)))
            and type(value.get("min_check_rates", {})) is dict
            and all(label(key) and number(rate, 0, 1) for key, rate in value.get("min_check_rates", {}).items()))


def valid_request(value):
    fields = {"input", "instructions", "max_output_tokens", "tools", "tool_choice", "text", "temperature",
              "top_p", "stream", "parallel_tool_calls", "reasoning", "include"}
    return (type(value) is dict and not set(value) - fields and type(value.get("input")) in (str, list)
            and bool(value["input"]) and not contains_provider_state(value)
            and type(value.get("max_output_tokens")) is int and 1 <= value["max_output_tokens"] <= 4096
            and type(value.get("stream", False)) is bool
            and len(json.dumps(value, allow_nan=False).encode()) <= 65536)


def valid_check(check):
    if (type(check) is not dict or set(check) != {"metric", "kind", "value"}
            or not label(check["metric"])):
        return False
    kind, value = check["kind"], check["value"]
    if kind == "text_equals":
        return type(value) is str
    if kind == "text_contains_all":
        return type(value) is list and 1 <= len(value) <= 32 and all(type(item) is str and item for item in value)
    if kind == "json_equals":
        return True
    if kind == "tool_call":
        return (type(value) is dict and set(value) == {"name", "arguments"}
                and type(value["name"]) is str and type(value["arguments"]) is dict)
    return False


def check_answer(check, value):
    try:
        text = response_text(value).strip()
        if check["kind"] == "text_equals":
            return text == check["value"]
        if check["kind"] == "text_contains_all":
            return all(item in text for item in check["value"])
        if check["kind"] == "json_equals":
            return digest(strict_json_loads(text)) == digest(check["value"])
        calls = [item for item in value.get("output", [])
                 if type(item) is dict and item.get("type") == "function_call"]
        return (len(calls) == 1 and calls[0].get("name") == check["value"]["name"]
                and digest(strict_json_loads(calls[0].get("arguments", ""))) == digest(check["value"]["arguments"]))
    except (ValueError, TypeError, KeyError, RecursionError):
        return False


def read_scorecard(path):
    path = local_path(Path(path))
    safe_path(path.parent)
    raw = read_private(path, maximum=MAX_SCORECARD_BYTES)
    if raw is None:
        raise BenchmarkError("benchmark_scorecard_unavailable")
    report = strict_json_loads(raw.decode())
    if (type(report) is not dict or report.get("schema_id") != "hormuz.model-scorecard"
            or type(report.get("schema_version")) is not int or report["schema_version"] != 1
            or type(report.get("models")) is not list or not 1 <= len(report["models"]) <= 8):
        raise BenchmarkError("benchmark_scorecard_invalid")
    validate_scorecard(report)
    return report


def validate_scorecard(report):
    """Summaries are derived from bounded observations, never trusted as input."""
    try:
        if (type(report) is not dict or set(report) != {"schema_id", "schema_version", "suite", "actor_id", "organization_id", "machine",
                            "resources", "recorded_at", "expires_at", "targets", "models"}
                or report["schema_id"] != "hormuz.model-scorecard"
                or type(report["schema_version"]) is not int or report["schema_version"] != 1
                or type(report["models"]) is not list or not 1 <= len(report["models"]) <= 8
                or not valid_targets(report["targets"])
                or any(type(report[key]) is not str or not 1 <= len(report[key]) <= 256 for key in ("actor_id", "organization_id"))
                or type(report["recorded_at"]) is not int or report["recorded_at"] < 0
                or type(report["expires_at"]) is not int
                or not report["recorded_at"] < report["expires_at"] <= report["recorded_at"] + 7 * 86400
                or set(report["suite"]) != {"name", "sha256"} or not hash_label(report["suite"]["sha256"])
                or type(report["suite"]["name"]) is not str or not 1 <= len(report["suite"]["name"]) <= 128
                or set(report["machine"]) != {"system", "architecture", "logical_cpu_count", "physical_memory_bytes"}
                or any(type(report["machine"][key]) is not str or not 1 <= len(report["machine"][key]) <= 128 for key in ("system", "architecture"))
                or any(report["machine"][key] is not None and (type(report["machine"][key]) is not int or not 1 <= report["machine"][key] <= 10**16)
                       for key in ("logical_cpu_count", "physical_memory_bytes"))
                or set(report["resources"]) != {"runner_cpu_ms", "runner_peak_rss_bytes", "model_peak_rss_bytes",
                                               "gpu_peak_memory_bytes", "energy_joules", "scope"}
                or report["resources"]["scope"] != "runner_process_only_model_resources_unobserved"
                or any(item is not None and not number(item, 0, 10**16)
                       for key, item in report["resources"].items() if key != "scope")):
            raise ValueError()
        aliases, total = set(), 0
        for model in report["models"]:
            if (type(model) is not dict or set(model) != {"model_alias", "subject", "route_fingerprint", "observations", "metrics", "workloads"}
                    or type(model["model_alias"]) is not str or not 1 <= len(model["model_alias"]) <= 4096
                    or type(model["subject"]) is not dict or set(model["subject"]) != {"upstream_model", "model_revision", "destination", "protocol", "locality"}
                    or model["subject"]["protocol"] != "openai" or model["subject"]["locality"] != "local"
                    or any(type(model["subject"][key]) is not str or not 1 <= len(model["subject"][key]) <= 4096 for key in ("upstream_model", "destination"))
                    or model["subject"]["model_revision"] is not None and (type(model["subject"]["model_revision"]) is not str or not 1 <= len(model["subject"]["model_revision"]) <= 4096)
                    or model["model_alias"] in aliases or type(model["route_fingerprint"]) is not str
                    or re.fullmatch(r"destination-v1:[a-f0-9]{64}", model["route_fingerprint"]) is None
                    or type(model["observations"]) is not list or not 1 <= len(model["observations"]) <= 96):
                raise ValueError()
            aliases.add(model["model_alias"])
            samples = set()
            for row in model["observations"]:
                fields = {"case_id", "case_sha256", "task_type", "partition", "repeat", "request_profile_sha256", "input_size",
                          "request_bytes", "max_output_tokens", "completed", "passed", "checks", "error", "usage",
                          "latency_ms", "headers_ms", "ttft_ms", "first_byte_ms"}
                if (type(row) is not dict or set(row) != fields or not label(row["case_id"])
                        or type(row["task_type"]) is not str or row["task_type"] not in TASKS
                        or row["partition"] not in ("development", "held_out")
                        or type(row["repeat"]) is not int or not 0 <= row["repeat"] <= 2
                        or (row["case_id"], row["repeat"]) in samples
                        or not hash_label(row["request_profile_sha256"]) or not hash_label(row["case_sha256"])
                        or row["input_size"] not in ("small", "medium", "large")
                        or type(row["request_bytes"]) is not int or not 1 <= row["request_bytes"] <= 131072
                        or type(row["max_output_tokens"]) is not int or not 1 <= row["max_output_tokens"] <= 4096
                        or type(row["completed"]) is not bool or type(row["passed"]) is not bool
                        or (row["error"] is None) != row["completed"]
                        or row["error"] is not None and (type(row["error"]) is not str or re.fullmatch(r"[a-z0-9_]{1,64}", row["error"]) is None)
                        or type(row["checks"]) is not list or not 1 <= len(row["checks"]) <= 16
                        or any(type(check) is not dict or set(check) != {"metric", "passed"}
                               or not label(check["metric"]) or type(check["passed"]) is not bool for check in row["checks"])
                        or len({check["metric"] for check in row["checks"]}) != len(row["checks"])
                        or row["passed"] != (row["completed"] and all(check["passed"] for check in row["checks"]))
                        or not row["completed"] and any(check["passed"] for check in row["checks"])
                        or type(row["usage"]) is not dict or set(row["usage"]) != {"input_tokens", "output_tokens", "cache_read_tokens", "cost_microusd"}
                        or any(item is not None and (type(item) is not int or not 0 <= item <= 10**15) for item in row["usage"].values())
                        or (row["usage"]["cache_read_tokens"] is not None and row["usage"]["input_tokens"] is not None
                            and row["usage"]["cache_read_tokens"] > row["usage"]["input_tokens"])
                        or (row["latency_ms"] is None and (row["error"] is None or not row["error"].endswith("_not_run")))
                        or (row["latency_ms"] is not None and not number(row["latency_ms"], 0, 3600000))
                        or any(row[key] is not None and not number(row[key], 0, row["latency_ms"])
                               for key in ("headers_ms", "ttft_ms", "first_byte_ms"))):
                    raise ValueError()
                samples.add((row["case_id"], row["repeat"]))
            total += len(samples)
            if (digest(model["metrics"]) != digest(summarize(model["observations"]))
                    or digest(model["workloads"]) != digest(workload_scores(model["observations"], report["targets"]))):
                raise ValueError()
        if total > 128:
            raise ValueError()
    except (ValueError, TypeError, KeyError, RecursionError):
        raise BenchmarkError("benchmark_scorecard_invalid") from None


def hash_label(value):
    return type(value) is str and re.fullmatch(r"[a-f0-9]{64}", value) is not None


def validate_runtime(value):
    try:
        if (type(value) is not dict or set(value) != {"schema_id", "schema_version", "actor_id", "organization_id", "shadow_router_enabled", "routes"}
                or value["schema_id"] != "hormuz.benchmark-runtime" or type(value["schema_version"]) is not int or value["schema_version"] != 1
                or any(type(value[key]) is not str or not 1 <= len(value[key]) <= 256 for key in ("actor_id", "organization_id"))
                or type(value["shadow_router_enabled"]) is not bool or type(value["routes"]) is not list):
            raise ValueError()
        seen = set()
        prices = {"input_cost_per_million", "output_cost_per_million", "cache_read_cost_per_million", "cache_write_cost_per_million"}
        for route in value["routes"]:
            if (type(route) is not dict or set(route) != {"model", "generation", "route_fingerprint", "explicit_price_fields"}
                    or type(route["model"]) is not str or not 1 <= len(route["model"]) <= 4096 or route["model"] in seen
                    or not hash_label(route["generation"]) or type(route["route_fingerprint"]) is not str
                    or re.fullmatch(r"destination-v1:[a-f0-9]{64}", route["route_fingerprint"]) is None
                    or type(route["explicit_price_fields"]) is not list or len(route["explicit_price_fields"]) > 4
                    or any(type(item) is not str or item not in prices for item in route["explicit_price_fields"])
                    or len(set(route["explicit_price_fields"])) != len(route["explicit_price_fields"])):
                raise ValueError()
            seen.add(route["model"])
    except (ValueError, TypeError, KeyError, RecursionError):
        raise BenchmarkError("benchmark_execution_unverified") from None


def format_scorecard(report):
    def measured(value):
        return "unknown" if value is None else f"{value:.2f}"
    def cell(value):
        return str(value).replace("|", "\\|").replace("\n", " ").replace("\r", " ")
    lines = [f"# Model scorecard: {cell(report['suite']['name'])}", "",
             "| Model | Passed/tasks | Success | p50 ms | p95 ms | TTFT p50 ms | Output tok/s | Est. mean USD | Target-ready workloads |",
             "| --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
    for model in report["models"]:
        metrics = model["metrics"]
        cost = metrics["mean_cost_microusd"]
        ready = sum(workload["target_met"] for workload in model["workloads"])
        lines.append(f"| {cell(model['model_alias'])} | {metrics['passed']}/{metrics['attempts']} | "
                     f"{metrics['task_success_rate']:.1%} | {measured(metrics['latency_ms']['p50'])} | "
                     f"{measured(metrics['latency_ms']['p95'])} | {measured(metrics['ttft_ms']['p50'])} | "
                     f"{measured(metrics['output_tokens_per_second'])} | "
                     f"{'unknown' if cost is None else format(cost / 1000000, '.6f')} | {ready}/{len(model['workloads'])} |")
    lines.extend(["", "Latency and throughput include the full router request. TTFT requires an observed streamed text delta.",
                  "Cost uses configured prices and observed tokens; missing prices remain unknown. Model memory and GPU usage are unobserved.",
                  "", "| Model / workload | Distinct held-out cases | Quality | Target result |",
                  "| --- | --- | --- | --- |"])
    for model in report["models"]:
        for workload in model["workloads"]:
            rate = workload["metrics"]["task_success_rate"]
            result = "met" if workload["target_met"] else ", ".join(workload["target_failures"])
            lines.append(f"| {cell(model['model_alias'])} / {workload['task_type']} / {workload['request_profile_sha256'][:8]} | "
                         f"{workload['held_out_case_count']} | {'unknown' if rate is None else format(rate, '.1%')} | {cell(result)} |")
    return "\n".join(lines)


def configure_routing(args):
    source_path = local_path(Path(args.config)).resolve()
    source = load_configuration_input(source_path)
    config = GatewayConfig.parse_preflight(source_path, json.dumps(source, ensure_ascii=False, allow_nan=False, separators=(",", ":")))
    report = read_scorecard(args.scorecard)
    identity = config.identities_by_actor.get(report["actor_id"])
    if identity is None or identity.organization_id != report["organization_id"]:
        raise BenchmarkError("benchmark_scorecard_owner_mismatch")
    if not report["recorded_at"] <= time.time() < report["expires_at"]:
        raise BenchmarkError("benchmark_scorecard_expired")
    if not config.route_admission.local_routes:
        raise BenchmarkError("benchmark_qualified_routes_required")
    output = local_path(Path(args.output))
    if output.parent != config.source_path.parent or output == config.source_path:
        raise BenchmarkError("benchmark_configuration_must_be_new_and_beside_source")
    if output.exists() or output.is_symlink():
        raise BenchmarkError("benchmark_output_exists")
    admission = source.setdefault("route_admission", {})
    admission["scorecard"] = str(local_path(Path(args.scorecard)))
    if admission.get("mode", "configured") == "configured":
        admission["mode"] = "auto"
    serialized = json.dumps(source, indent=2, ensure_ascii=False, allow_nan=False)
    GatewayConfig.parse_preflight(output, serialized)
    if not args.dry_run:
        export_private(output, serialized.encode())
    return {"schema_id": "hormuz.benchmark-routing-configuration", "schema_version": 1,
            "scorecard_sha256": digest(report), "output": str(output), "dry_run": args.dry_run,
            "mode": admission["mode"], "activation": "requires_reconnect",
            "changes": {"route_admission.scorecard": admission["scorecard"]}}


class RouterClient:
    """One reusable loopback connection, finite reads, an absolute request deadline."""
    def __init__(self, config, actor=None):
        self.config = config
        identities = [identity for identity in config.identities_by_actor.values()
                      if actor is None or identity.actor_id == actor]
        if len(identities) != 1:
            raise BenchmarkError("benchmark_actor_required")
        self.identity = identities[0]
        self.token = next(token for token, owner in config.identities_by_token.items()
                          if owner.actor_id == self.identity.actor_id)
        if config.listen.host not in {"127.0.0.1", "::1", "localhost"}:
            raise BenchmarkError("benchmark_loopback_router_required")
        self.connection = http.client.HTTPConnection(
            "127.0.0.1" if config.listen.host == "localhost" else config.listen.host,
            config.listen.port, timeout=3)

    def close(self):
        self.connection.close()

    def bindings(self, routes, *, timeout):
        status, _, value, _ = self.request(None, method="GET", path="/v1/router/benchmark-state", timeout=timeout)
        if status != 200:
            raise BenchmarkError("benchmark_execution_unverified")
        validate_runtime(value)
        if value["actor_id"] != self.identity.actor_id or value["organization_id"] != self.identity.organization_id:
            raise BenchmarkError("benchmark_execution_mismatch")
        if value["shadow_router_enabled"]:
            raise BenchmarkError("benchmark_shadow_router_unsupported")
        result = {}
        for route in routes:
            matches = [item for item in value["routes"]
                       if type(item) is dict and item.get("model") == route.alias]
            if len(matches) != 1:
                raise BenchmarkError("benchmark_execution_mismatch")
            live = matches[0]
            if (live["route_fingerprint"] != route_fingerprint(route, upstreams=self.config.upstreams)
                    or live["explicit_price_fields"] != sorted(route.explicit_price_fields)):
                raise BenchmarkError("benchmark_execution_mismatch")
            result[route.alias] = live["generation"]
        return result

    def request(self, payload, *, timeout=12, method="POST", path="/v1/responses"):
        connection = self.connection
        started = time.monotonic()
        if connection.sock is None:
            connection.timeout = min(3, timeout)
            connection.connect()
        transport = connection.sock
        transport.settimeout(max(0.001, timeout - (time.monotonic() - started)))
        expired = threading.Event()
        def expire():
            expired.set()
            try:
                transport.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        timer = threading.Timer(max(0.001, timeout - (time.monotonic() - started)), expire)
        timer.daemon = True
        timer.start()
        try:
            connection.request(method, path, None if payload is None else json.dumps(payload), headers={
                "Authorization": "Bearer " + self.token, "Content-Type": "application/json",
                "User-Agent": "Hormuz codex-benchmark", "X-Hormuz-Routing-Mode": "local_only",
                "X-Hormuz-Benchmark": "1"})
            response = connection.getresponse()
            headers_ms = (time.monotonic() - started) * 1000
            if response.status != 200:
                connection.close()
                return (response.status, dict((k.lower(), v) for k, v in response.getheaders()), {},
                        {"latency_ms": (time.monotonic() - started) * 1000,
                         "headers_ms": headers_ms, "ttft_ms": None, "first_byte_ms": None})
            first_byte_ms, ttft_ms = None, None
            data, pending = bytearray(), bytearray()
            streaming = response.getheader("Content-Type", "").split(";", 1)[0].strip().lower() == "text/event-stream"
            if payload is not None and streaming != payload.get("stream", False):
                raise BenchmarkError("benchmark_response_mode_mismatch")
            terminal, event_lines = None, []
            while True:
                block = response.read1(min(65536, MAX_BYTES + 1 - len(data)))
                if not block:
                    break
                elapsed = (time.monotonic() - started) * 1000
                if first_byte_ms is None:
                    first_byte_ms = elapsed
                data.extend(block)
                if len(data) > MAX_BYTES:
                    raise BenchmarkError("benchmark_response_limit")
                if streaming:
                    pending.extend(block)
                    while b"\n" in pending:
                        line, _, rest = pending.partition(b"\n")
                        pending = bytearray(rest)
                        line = line.rstrip(b"\r")
                        if line.startswith(b"data:"):
                            event_lines.append(line[5:].lstrip())
                        elif not line and event_lines:
                            event_data = b"\n".join(event_lines)
                            event_lines = []
                            if event_data == b"[DONE]":
                                continue
                            event = strict_json_loads(event_data.decode())
                            if type(event) is not dict:
                                raise BenchmarkError("benchmark_stream_invalid")
                            if event.get("type") in ("error", "response.error", "response.failed", "response.incomplete"):
                                raise BenchmarkError("benchmark_stream_invalid")
                            if event.get("type") == "response.output_text.delta" and event.get("delta") and ttft_ms is None:
                                ttft_ms = elapsed
                            if event.get("type") == "response.completed":
                                if terminal is not None:
                                    raise BenchmarkError("benchmark_stream_invalid")
                                terminal = event.get("response")
            if expired.is_set():
                raise TimeoutError()
            return (response.status, dict((k.lower(), v) for k, v in response.getheaders()),
                    terminal if streaming else strict_json_loads(data.decode()),
                    {"latency_ms": (time.monotonic() - started) * 1000,
                     "headers_ms": headers_ms, "ttft_ms": ttft_ms, "first_byte_ms": first_byte_ms})
        except (OSError, http.client.HTTPException):
            if expired.is_set():
                raise TimeoutError() from None
            raise
        finally:
            timer.cancel()
            timer.join(timeout=1)


def response_text(value):
    return "".join(block.get("text", "") for item in value.get("output", [])
                   if type(item) is dict and item.get("type") == "message"
                   for block in item.get("content", []) if type(block) is dict
                   and block.get("type") == "output_text" and type(block.get("text")) is str)


def distribution(values):
    values = sorted(value for value in values if value is not None)
    if not values:
        return {"samples": 0, "mean": None, "p50": None, "p95": None, "p99": None}
    return {"samples": len(values), "mean": statistics.mean(values),
            **{name: values[max(0, math.ceil(fraction * len(values)) - 1)]
               for name, fraction in (("p50", 0.5), ("p95", 0.95), ("p99", 0.99))}}


def measured_usage(value, route):
    usage = value.get("usage")
    def count(key):
        item = usage.get(key) if type(usage) is dict else None
        return item if type(item) is int and 0 <= item <= 10**12 else None
    observed = {key: count(key) for key in ("input_tokens", "output_tokens")}
    details = usage.get("input_tokens_details") if type(usage) is dict else None
    cached = details.get("cached_tokens") if type(details) is dict else None
    invalid_cache = cached is not None and (type(cached) is not int or not 0 <= cached <= 10**12
        or observed["input_tokens"] is not None and cached > observed["input_tokens"])
    observed["cache_read_tokens"] = cached if not invalid_cache else None
    required = {"input_cost_per_million", "output_cost_per_million"}
    if cached:
        required.add("cache_read_cost_per_million")
    observed["cost_microusd"] = None
    if (value.get("model") == route.upstream_model and not invalid_cache
            and all(observed[key] is not None for key in ("input_tokens", "output_tokens"))
            and required <= route.explicit_price_fields):
        observed["cost_microusd"] = route.estimate_cost_microusd(
            input_tokens=observed["input_tokens"], output_tokens=observed["output_tokens"],
            cache_read_tokens=observed["cache_read_tokens"] or 0, cache_write_tokens=0)
    return observed


def forwarded_count(headers, name, maximum):
    value = headers.get(name, "")
    if type(value) is not str or not value.isascii() or not value.isdigit() or len(value) > 6:
        return None
    count = int(value)
    return count if 1 <= count <= maximum else None


def summarize(observations):
    count = len(observations)
    completed = sum(row["completed"] for row in observations)
    passed = sum(row["passed"] for row in observations)
    checks = {}
    for row in observations:
        for check in row["checks"]:
            checks.setdefault(check["metric"], []).append(check["passed"])
    totals = {}
    for key in ("input_tokens", "output_tokens", "cache_read_tokens", "cost_microusd"):
        values = [row["usage"][key] for row in observations]
        totals[key] = sum(values) if all(value is not None for value in values) else None
    elapsed = sum(row["latency_ms"] or 0 for row in observations)
    return {"attempts": count, "completed": completed, "passed": passed,
            "task_success_rate": passed / count if count else None, "error_rate": (count - completed) / count if count else None,
            "check_pass_rates": {key: sum(values) / len(values) for key, values in checks.items()},
            **totals, "mean_cost_microusd": None if not count or totals["cost_microusd"] is None else totals["cost_microusd"] / count,
            "cost_basis": "configured_rate_estimate_unreported_cache_as_uncached",
            "latency_ms": distribution(row["latency_ms"] for row in observations),
            "headers_ms": distribution(row["headers_ms"] for row in observations),
            "first_byte_ms": distribution(row["first_byte_ms"] for row in observations),
            "ttft_ms": distribution(row["ttft_ms"] for row in observations),
            "output_tokens_per_second": totals["output_tokens"] * 1000 / elapsed if totals["output_tokens"] is not None and elapsed > 0 else None,
            "throughput_basis": "output_tokens_per_end_to_end_second"}


def target_failures(metrics, cases, targets):
    failures = []
    if not metrics["attempts"] or metrics["completed"] != metrics["attempts"]:
        failures.append("execution_evidence_incomplete")
    if cases < targets["min_cases"]:
        failures.append("insufficient_held_out_cases")
    if metrics["task_success_rate"] is None or metrics["task_success_rate"] < targets["min_pass_rate"]:
        failures.append("pass_rate_below_target")
    for metric, minimum in sorted(targets.get("min_check_rates", {}).items()):
        measured = metrics["check_pass_rates"].get(metric)
        if measured is None or measured < minimum:
            failures.append("check_target_unmet:" + metric)
    latency = metrics["latency_ms"]["p95"]
    if targets.get("max_p95_latency_ms") is not None and (latency is None or latency > targets["max_p95_latency_ms"]):
        failures.append("latency_target_unmet")
    cost = metrics["mean_cost_microusd"]
    if targets.get("max_mean_cost_microusd") is not None and (cost is None or cost > targets["max_mean_cost_microusd"]):
        failures.append("cost_target_unmet")
    if targets["objective"] == "cost" and cost is None:
        failures.append("cost_unobserved")
    return failures


def workload_scores(observations, targets):
    groups = {}
    for row in observations:
        key = row["task_type"], row["request_profile_sha256"], row["input_size"]
        groups.setdefault(key, []).append(row)
    scores = []
    for (task, profile, size), rows in sorted(groups.items()):
        held_out = [row for row in rows if row["partition"] == "held_out"]
        case_count = len({row["case_sha256"] for row in held_out})
        metrics = summarize(held_out)
        failures = target_failures(metrics, case_count, targets)
        scores.append({"task_type": task, "request_profile_sha256": profile, "input_size": size,
                       "request_bytes_max": max(row["request_bytes"] for row in held_out) if held_out else 0,
                       "max_output_tokens": min(row["max_output_tokens"] for row in held_out) if held_out else 0,
                       "held_out_case_count": case_count, "metrics": metrics,
                       "development_metrics": summarize([row for row in rows if row["partition"] == "development"]),
                       "target_met": not failures, "target_failures": failures})
    return scores


def run_benchmark(args):
    cpu_started = time.process_time()
    suite = read_suite(args.suite)
    config = GatewayConfig.load(args.config)
    if config.shadow_router is not None:
        raise BenchmarkError("benchmark_shadow_router_unsupported")
    output = local_path(Path(args.output))
    safe_path(output.parent)
    if output.exists() or output.is_symlink():
        raise BenchmarkError("benchmark_output_exists")
    if not 1 <= len(args.models) <= 8 or len(set(args.models)) != len(args.models):
        raise BenchmarkError("benchmark_models_invalid")
    if not 1 <= args.repeats <= 3 or len(args.models) * len(suite["cases"]) * args.repeats > 128:
        raise BenchmarkError("benchmark_request_limit")
    if not number(args.timeout_seconds, 0.05, 120) or not number(args.deadline_seconds, 0.05, 1800):
        raise BenchmarkError("benchmark_deadline_invalid")
    routes = []
    for alias in args.models:
        route = config.model_routes.get(alias)
        if route is None or route.protocol != "openai" or config.upstreams[route.destination_id].locality != "local":
            raise BenchmarkError("benchmark_local_responses_route_required")
        routes.append(route)
    client = RouterClient(config, args.actor)
    models = []
    stopped, consecutive_failures = None, 0
    deadline = time.monotonic() + args.deadline_seconds
    try:
        bindings = client.bindings(routes, timeout=min(args.timeout_seconds, args.deadline_seconds))
        for route in routes:
            observations = []
            for case, repeat in ((case, repeat) for case in suite["cases"] for repeat in range(args.repeats)):
                payload = {"stream": False, **case["request"], "model": route.alias, "store": False}
                cap = config.resolved_policy(client.identity).max_output_tokens
                if cap is not None:
                    payload["max_output_tokens"] = min(payload["max_output_tokens"], cap)
                started = time.monotonic()
                value, error = {}, None
                request_bytes = len(json.dumps({**payload, "model": route.upstream_model}, separators=(",", ":")).encode())
                timing = {"latency_ms": 0, "headers_ms": None, "ttft_ms": None, "first_byte_ms": None}
                try:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        stopped = "deadline_not_run"
                    if stopped:
                        raise BenchmarkError(stopped)
                    status, headers, value, timing = client.request(payload, timeout=min(args.timeout_seconds, remaining))
                    observed_bytes = forwarded_count(headers, "x-hormuz-benchmark-request-bytes", 131072)
                    observed_limit = forwarded_count(headers, "x-hormuz-benchmark-max-output-tokens", 4096)
                    if observed_bytes is not None:
                        request_bytes = observed_bytes
                    if status != 200:
                        error = "http_" + str(status)
                        if status in (429, 529):
                            stopped = "capacity_rejection_not_run"
                    elif (headers.get("x-hormuz-destination-locality") != "local"
                            or headers.get("x-hormuz-destination") != route.destination_id
                            or headers.get("x-hormuz-destination-generation") != bindings[route.alias]
                            or headers.get("x-hormuz-requested-model") != route.alias
                            or headers.get("x-hormuz-routed-model") != route.upstream_model
                            or observed_bytes is None or observed_limit != payload["max_output_tokens"]):
                        error = "execution_mismatch"
                    elif (type(value) is not dict or value.get("status") != "completed"
                            or value.get("error") is not None):
                        error = "incomplete_response"
                    elif value.get("model") != route.upstream_model:
                        error = "execution_mismatch"
                except (OSError, http.client.HTTPException, ValueError, RecursionError) as failure:
                    client.close()
                    error = failure.code if isinstance(failure, BenchmarkError) else "timeout" if isinstance(failure, TimeoutError) else "request_failed"
                    if isinstance(failure, OSError) and failure.errno == errno.EADDRNOTAVAIL:
                        stopped = "socket_exhaustion_not_run"
                    timing["latency_ms"] = None if error.endswith("_not_run") else (time.monotonic() - started) * 1000
                    value = {}
                consecutive_failures = consecutive_failures + 1 if error else 0
                if consecutive_failures >= 3 and stopped is None:
                    stopped = "repeated_failure_not_run"
                completed = error is None
                checks = [{"metric": check["metric"], "passed": completed and check_answer(check, value)}
                          for check in case["checks"]]
                observations.append({"case_id": case["id"], "task_type": case["task_type"],
                    "case_sha256": digest({"task_type": case["task_type"], "request": case["request"]}),
                    "partition": case["partition"], "repeat": repeat,
                    "request_profile_sha256": benchmark_profile_digest(payload),
                    "input_size": request_characteristics(payload)["input_size"],
                    "request_bytes": request_bytes,
                    "max_output_tokens": payload["max_output_tokens"],
                    "completed": completed, "passed": completed and all(check["passed"] for check in checks),
                    "checks": checks, "error": error, "usage": measured_usage(value if type(value) is dict else {}, route), **timing})
            models.append({"model_alias": route.alias,
                "subject": {"upstream_model": route.upstream_model, "model_revision": route.model_revision,
                            "destination": route.destination_id, "protocol": route.protocol, "locality": "local"},
                "route_fingerprint": route_fingerprint(route, upstreams=config.upstreams),
                "observations": observations, "metrics": summarize(observations),
                "workloads": workload_scores(observations, suite["targets"])})
    finally:
        client.close()
    recorded = int(time.time())
    report = {"schema_id": "hormuz.model-scorecard", "schema_version": 1,
              "suite": {"name": suite["name"], "sha256": digest(suite)}, "actor_id": client.identity.actor_id,
              "organization_id": client.identity.organization_id,
              "machine": machine_facts(),
              "resources": {"runner_cpu_ms": (time.process_time() - cpu_started) * 1000,
                            "runner_peak_rss_bytes": None if resource is None else resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * (1 if platform.system() == "Darwin" else 1024),
                            "model_peak_rss_bytes": None, "gpu_peak_memory_bytes": None, "energy_joules": None,
                            "scope": "runner_process_only_model_resources_unobserved"},
              "recorded_at": recorded, "expires_at": recorded + 7 * 86400,
              "targets": suite["targets"], "models": models}
    serialized = json.dumps(report, sort_keys=True, allow_nan=False).encode()
    if len(serialized) > MAX_SCORECARD_BYTES:
        raise BenchmarkError("benchmark_report_limit")
    validate_scorecard(report)
    export_private(output, serialized)
    return report
