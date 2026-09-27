"""Bounded, content-free local measurements for the personal optimizer."""

from __future__ import annotations

import hashlib
import json
import math
import os
import stat
import tempfile
import threading
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping

from .compaction_formats import strict_json_loads
from .credential_store import CredentialStoreError, validate_profile
from .personal_profiles import PERSONAL_RELEASE_VERSION


METRICS_SCHEMA_VERSION = 1
MAX_METRICS_BYTES = 128 * 1024
MAX_RESPONSE_INSPECTION_BYTES = 1024 * 1024
_METRIC_NAMES = (
    "request_before_bytes",
    "request_after_bytes",
    "eligible_before_bytes",
    "eligible_after_bytes",
    "cl100k_before_tokens",
    "cl100k_after_tokens",
    "o200k_before_tokens",
    "o200k_after_tokens",
    "eligible_cl100k_before_tokens",
    "eligible_cl100k_after_tokens",
    "eligible_o200k_before_tokens",
    "eligible_o200k_after_tokens",
    "optimizer_overhead_us",
    "provider_input_tokens",
    "provider_output_tokens",
    "provider_cache_read_tokens",
    "provider_cache_write_tokens",
    "provider_reasoning_tokens",
    "provider_total_tokens",
    "provider_actual_cost_microusd",
    "time_to_first_byte_ms",
    "total_latency_ms",
    "response_bytes",
    "auxiliary_calls",
    "fallback_calls",
)
_COUNTER_NAMES = (
    "sessions_started",
    "sessions_completed",
    "requests_total",
    "eligible_requests",
    "optimized_requests",
    "passthrough_requests",
    "provider_attempts",
    "provider_responses",
    "provider_failures",
    "cancelled_requests",
    "interventions_detected",
    "interventions_applied",
)
_REASONS = frozenset(
    {
        "compacted",
        "disabled",
        "no_savings",
        "no_eligible_result",
        "unsupported_history",
        "unsupported_shape",
        "unsupported_client",
        "resources_unavailable",
        "gateway_incompatible",
        "settings_invalid",
        "limit_exceeded",
        "counter_unavailable",
        "net_regression",
        "provider_failure",
        "cancelled",
    }
)
_USAGE_FIELDS = (
    "input_tokens",
    "output_tokens",
    "cache_read_tokens",
    "cache_write_tokens",
    "reasoning_tokens",
    "total_tokens",
    "actual_cost_microusd",
)


class PersonalMetricsError(RuntimeError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class OptimizationMeasurement:
    eligible: bool
    applied: bool
    reason: str
    before_bytes: int | None
    after_bytes: int | None
    before_tokens: Mapping[str, int] | None
    after_tokens: Mapping[str, int] | None
    overhead_us: int | None
    auxiliary_calls: int = 0
    fallback_calls: int = 0


@dataclass(frozen=True)
class ProviderMeasurement:
    succeeded: bool
    cancelled: bool
    response_bytes: int | None
    time_to_first_byte_ms: float | None
    total_latency_ms: float | None
    usage: Mapping[str, int | None]


class ResponseUsageAccumulator:
    """Inspect a bounded in-memory copy while forwarding bytes immediately."""

    def __init__(self, protocol: str, content_type: str | None):
        self.protocol = protocol
        self.content_type = (content_type or "").lower()
        self._body = bytearray()
        self._overflow = False
        self.total_bytes = 0

    def feed(self, chunk: bytes) -> None:
        self.total_bytes += len(chunk)
        if self._overflow:
            return
        if len(self._body) + len(chunk) > MAX_RESPONSE_INSPECTION_BYTES:
            self._body.clear()
            self._overflow = True
            return
        self._body.extend(chunk)

    def usage(self) -> dict[str, int | None]:
        if self._overflow:
            return {name: None for name in _USAGE_FIELDS}
        return extract_provider_usage(bytes(self._body), self.protocol, self.content_type)


class PersonalMetricsStore:
    def __init__(self, state_directory: Path, profile: str):
        try:
            validate_profile(profile)
        except CredentialStoreError as error:
            raise PersonalMetricsError("invalid_profile") from error
        self.directory = Path(os.path.abspath(state_directory.expanduser())) / "personal-metrics"
        self.path = self.directory / (profile + ".json")
        digest = hashlib.sha256(profile.encode("utf-8")).hexdigest()
        self.lock_path = self.directory / (".metrics-" + digest + ".lock")
        self.profile = profile
        self._thread_lock = threading.Lock()

    def record_session(self, *, completed: bool = False) -> None:
        with self._exclusive():
            value = self._load_or_empty(create=True)
            value["counters"]["sessions_completed" if completed else "sessions_started"] += 1
            _record_active_day(value)
            self._write(value)

    def record_optimization(self, measurement: OptimizationMeasurement) -> None:
        if measurement.reason not in _REASONS:
            raise PersonalMetricsError("invalid_measurement")
        with self._exclusive():
            value = self._load_or_empty(create=True)
            counters = value["counters"]
            counters["requests_total"] += 1
            counters["eligible_requests"] += int(measurement.eligible)
            counters["optimized_requests"] += int(measurement.applied)
            counters["passthrough_requests"] += int(not measurement.applied)
            reasons = value["reasons"]
            reasons[measurement.reason] = reasons.get(measurement.reason, 0) + 1
            _observe(value, "request_before_bytes", measurement.before_bytes)
            _observe(value, "request_after_bytes", measurement.after_bytes)
            if measurement.eligible:
                _observe(value, "eligible_before_bytes", measurement.before_bytes)
                _observe(value, "eligible_after_bytes", measurement.after_bytes)
            _observe(value, "optimizer_overhead_us", measurement.overhead_us)
            _observe(value, "auxiliary_calls", measurement.auxiliary_calls)
            _observe(value, "fallback_calls", measurement.fallback_calls)
            for encoding in ("cl100k", "o200k"):
                source = "cl100k_base" if encoding == "cl100k" else "o200k_base"
                before = None if measurement.before_tokens is None else measurement.before_tokens.get(source)
                after = None if measurement.after_tokens is None else measurement.after_tokens.get(source)
                _observe(value, f"{encoding}_before_tokens", before)
                _observe(value, f"{encoding}_after_tokens", after)
                if measurement.eligible:
                    _observe(value, f"eligible_{encoding}_before_tokens", before)
                    _observe(value, f"eligible_{encoding}_after_tokens", after)
            _record_active_day(value)
            self._write(value)

    def record_provider(self, measurement: ProviderMeasurement) -> None:
        with self._exclusive():
            value = self._load_or_empty(create=True)
            counters = value["counters"]
            counters["provider_attempts"] += 1
            counters["provider_responses"] += int(
                measurement.time_to_first_byte_ms is not None
            )
            counters["provider_failures"] += int(not measurement.succeeded)
            counters["cancelled_requests"] += int(measurement.cancelled)
            _observe(value, "response_bytes", measurement.response_bytes)
            _observe(value, "time_to_first_byte_ms", measurement.time_to_first_byte_ms)
            _observe(value, "total_latency_ms", measurement.total_latency_ms)
            for field in _USAGE_FIELDS:
                _observe(value, "provider_" + field, measurement.usage.get(field))
            if not measurement.succeeded:
                reason = "cancelled" if measurement.cancelled else "provider_failure"
                reasons = value["reasons"]
                reasons[reason] = reasons.get(reason, 0) + 1
            _record_active_day(value)
            self._write(value)

    def record_intervention(self, *, applied: bool) -> None:
        with self._exclusive():
            value = self._load_or_empty(create=True)
            value["counters"]["interventions_detected"] += 1
            value["counters"]["interventions_applied"] += int(applied)
            _record_active_day(value)
            self._write(value)

    def snapshot(self) -> dict[str, object]:
        with self._exclusive():
            return self._load_or_empty(create=False)

    def clear(self) -> bool:
        with self._exclusive():
            try:
                info = self.path.lstat()
                if (
                    not stat.S_ISREG(info.st_mode)
                    or info.st_uid != os.getuid()
                    or info.st_nlink != 1
                    or info.st_mode & 0o077
                ):
                    raise PersonalMetricsError("metrics_invalid")
                self.path.unlink()
                directory_fd = os.open(self.directory, os.O_RDONLY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
                return True
            except FileNotFoundError:
                return False
            except PersonalMetricsError:
                raise
            except OSError as error:
                raise PersonalMetricsError("metrics_clear_failed") from error

    @contextmanager
    def _exclusive(self):
        with self._thread_lock:
            _private_directory(self.directory, create=True)
            with _MetricsFileLock(self.lock_path):
                yield

    def benefit(self, *, enabled: bool) -> dict[str, object]:
        value = self.snapshot()
        counters = value["counters"]
        metrics = value["metrics"]
        overall = _reduction(
            metrics,
            "cl100k_before_tokens",
            "cl100k_after_tokens",
            expected_observations=counters["requests_total"],
            unit="tokens",
        )
        eligible = _reduction(
            metrics,
            "eligible_cl100k_before_tokens",
            "eligible_cl100k_after_tokens",
            expected_observations=counters["eligible_requests"],
            unit="tokens",
        )
        overall_bytes = _reduction(
            metrics,
            "request_before_bytes",
            "request_after_bytes",
            expected_observations=counters["requests_total"],
            unit="bytes",
        )
        eligible_bytes = _reduction(
            metrics,
            "eligible_before_bytes",
            "eligible_after_bytes",
            expected_observations=counters["eligible_requests"],
            unit="bytes",
        )
        cache = metrics["provider_cache_read_tokens"]
        actual_cost = metrics["provider_actual_cost_microusd"]
        exceptions = {
            reason: count
            for reason, count in value["reasons"].items()
            if reason != "compacted" and count
        }
        return {
            "schema_id": "hormuz.personal-benefit",
            "schema_version": 1,
            "release_version": PERSONAL_RELEASE_VERSION,
            "profile": self.profile,
            "optimization_enabled": enabled,
            "traffic": {
                "total_requests": counters["requests_total"],
                "eligible_requests": counters["eligible_requests"],
                "transformed_requests": counters["optimized_requests"],
            },
            "request_byte_reduction": {
                "all_captured_traffic": overall_bytes,
                "eligible_traffic": eligible_bytes,
                "basis": "exact_serialized_request_bytes",
            },
            "estimated_token_reduction": {
                "all_captured_traffic": overall,
                "eligible_traffic": eligible,
                "basis": "local_cl100k_estimate_not_provider_bill",
            },
            "provider_activity": {
                "attempts": counters["provider_attempts"],
                "responses": counters["provider_responses"],
                "failures": counters["provider_failures"],
                "cancelled": counters["cancelled_requests"],
            },
            "provider_reported": {
                "input_tokens": _metric_view(metrics["provider_input_tokens"]),
                "output_tokens": _metric_view(metrics["provider_output_tokens"]),
                "total_tokens": _metric_view(metrics["provider_total_tokens"]),
                "cache_read_tokens": _metric_view(cache),
                "cache_write_tokens": _metric_view(metrics["provider_cache_write_tokens"]),
                "reasoning_tokens": _metric_view(metrics["provider_reasoning_tokens"]),
            },
            "waiting": {
                "optimizer_overhead_us": _metric_view(metrics["optimizer_overhead_us"]),
                "time_to_first_byte_ms": _metric_view(metrics["time_to_first_byte_ms"]),
                "total_latency_ms": _metric_view(metrics["total_latency_ms"]),
            },
            "cost": {
                "actual_microusd": _metric_view(actual_cost),
                "basis": (
                    "provider_reported_actual_cost"
                    if actual_cost["observed"]
                    else "unavailable_no_monetary_savings_claim"
                ),
                "cache_adjusted_savings": (
                    None
                    if cache["observed"] == 0
                    else "not_attributed_without_request_level_provider_billing"
                ),
                "auxiliary_calls": _metric_view(metrics["auxiliary_calls"]),
                "fallback_calls": _metric_view(metrics["fallback_calls"]),
            },
            "exceptions": exceptions,
            "active_days_observed": len(value["active_days"]),
            "content_retained": False,
        }

    def _load_or_empty(self, *, create: bool) -> dict[str, object]:
        if create:
            _private_directory(self.directory, create=True)
        elif not self.directory.exists():
            return _empty(self.profile)
        else:
            _private_directory(self.directory, create=False)
        try:
            descriptor = os.open(
                self.path,
                os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
            )
        except FileNotFoundError:
            return _empty(self.profile)
        except OSError as error:
            raise PersonalMetricsError("metrics_unavailable") from error
        try:
            info = os.fstat(descriptor)
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != os.getuid()
                or info.st_nlink != 1
                or info.st_mode & 0o077
                or info.st_size > MAX_METRICS_BYTES
            ):
                raise PersonalMetricsError("metrics_invalid")
            chunks: list[bytes] = []
            remaining = MAX_METRICS_BYTES + 1
            while remaining:
                chunk = os.read(descriptor, remaining)
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            data = b"".join(chunks)
        finally:
            os.close(descriptor)
        if len(data) > MAX_METRICS_BYTES:
            raise PersonalMetricsError("metrics_invalid")
        try:
            value = strict_json_loads(data.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as error:
            raise PersonalMetricsError("metrics_invalid") from error
        return _validate(value, self.profile)

    def _write(self, value: dict[str, object]) -> None:
        _validate(value, self.profile)
        data = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
        if len(data) > MAX_METRICS_BYTES:
            raise PersonalMetricsError("metrics_limit_exceeded")
        temporary: str | None = None
        try:
            descriptor, temporary = tempfile.mkstemp(prefix=".personal-metrics-", dir=self.directory)
            with os.fdopen(descriptor, "wb") as stream:
                os.fchmod(stream.fileno(), 0o600)
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
            temporary = None
            directory_fd = os.open(self.directory, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except OSError as error:
            raise PersonalMetricsError("metrics_write_failed") from error
        finally:
            if temporary is not None:
                try:
                    os.unlink(temporary)
                except OSError:
                    pass


def extract_provider_usage(body: bytes, protocol: str, content_type: str) -> dict[str, int | None]:
    documents: list[object] = []
    try:
        if "text/event-stream" in content_type or body.lstrip().startswith(b"data:"):
            for line in body.splitlines():
                if not line.startswith(b"data:"):
                    continue
                payload = line[5:].strip()
                if payload and payload != b"[DONE]":
                    documents.append(strict_json_loads(payload.decode("utf-8")))
        elif body:
            documents.append(strict_json_loads(body.decode("utf-8")))
    except (UnicodeDecodeError, ValueError):
        return {name: None for name in _USAGE_FIELDS}
    result: dict[str, int | None] = {name: None for name in _USAGE_FIELDS}
    for document in documents:
        if not isinstance(document, dict):
            continue
        candidates = [document]
        response = document.get("response")
        message = document.get("message")
        if isinstance(response, dict):
            candidates.append(response)
        if isinstance(message, dict):
            candidates.append(message)
        for candidate in candidates:
            usage = candidate.get("usage")
            if isinstance(usage, dict):
                _merge_usage(result, usage, protocol)
            explicit_cost = candidate.get("actual_cost_microusd")
            if _count(explicit_cost) is not None:
                result["actual_cost_microusd"] = _count(explicit_cost)
    if result["total_tokens"] is None:
        input_tokens = result["input_tokens"]
        output_tokens = result["output_tokens"]
        if input_tokens is not None and output_tokens is not None:
            result["total_tokens"] = input_tokens + output_tokens
    return result


def _merge_usage(result: dict[str, int | None], usage: dict[str, object], protocol: str) -> None:
    input_tokens = _first_count(usage, "input_tokens", "prompt_tokens")
    output_tokens = _first_count(usage, "output_tokens", "completion_tokens")
    total_tokens = _first_count(usage, "total_tokens")
    cache_read = _first_count(usage, "cache_read_input_tokens", "cache_read_tokens")
    cache_write = _first_count(usage, "cache_creation_input_tokens", "cache_write_tokens")
    reasoning = _first_count(usage, "reasoning_tokens")
    input_details = usage.get("input_tokens_details") or usage.get("prompt_tokens_details")
    output_details = usage.get("output_tokens_details") or usage.get("completion_tokens_details")
    if isinstance(input_details, dict):
        cache_read = cache_read if cache_read is not None else _first_count(input_details, "cached_tokens")
        cache_write = cache_write if cache_write is not None else _first_count(input_details, "cache_write_tokens")
    if isinstance(output_details, dict):
        reasoning = reasoning if reasoning is not None else _first_count(output_details, "reasoning_tokens")
    for name, observed in (
        ("input_tokens", input_tokens),
        ("output_tokens", output_tokens),
        ("cache_read_tokens", cache_read),
        ("cache_write_tokens", cache_write),
        ("reasoning_tokens", reasoning),
        ("total_tokens", total_tokens),
    ):
        if observed is not None:
            result[name] = observed


def _empty(profile: str) -> dict[str, object]:
    return {
        "schema_version": METRICS_SCHEMA_VERSION,
        "release_version": PERSONAL_RELEASE_VERSION,
        "profile": profile,
        "counters": {name: 0 for name in _COUNTER_NAMES},
        "metrics": {name: _empty_metric() for name in _METRIC_NAMES},
        "reasons": {},
        "active_days": [],
    }


def _empty_metric() -> dict[str, int | float]:
    return {"observed": 0, "missing": 0, "total": 0, "maximum": 0}


def _observe(value: dict[str, object], name: str, observed: int | float | None) -> None:
    metric = value["metrics"][name]
    if observed is None:
        metric["missing"] += 1
        return
    if isinstance(observed, bool) or not isinstance(observed, (int, float)) or not math.isfinite(observed) or observed < 0:
        raise PersonalMetricsError("invalid_measurement")
    metric["observed"] += 1
    metric["total"] += observed
    metric["maximum"] = max(metric["maximum"], observed)


def _record_active_day(value: dict[str, object]) -> None:
    ordinal = datetime.now(timezone.utc).date().toordinal()
    days = value["active_days"]
    if ordinal not in days:
        days.append(ordinal)
        days.sort()
        del days[:-30]


def _validate(value: object, profile: str) -> dict[str, object]:
    if (
        not isinstance(value, dict)
        or set(value) != {"schema_version", "release_version", "profile", "counters", "metrics", "reasons", "active_days"}
        or value.get("schema_version") != METRICS_SCHEMA_VERSION
        or value.get("release_version") != PERSONAL_RELEASE_VERSION
        or value.get("profile") != profile
        or not isinstance(value.get("counters"), dict)
        or set(value["counters"]) != set(_COUNTER_NAMES)
        or not isinstance(value.get("metrics"), dict)
        or set(value["metrics"]) != set(_METRIC_NAMES)
        or not isinstance(value.get("reasons"), dict)
        or not set(value["reasons"]) <= _REASONS
        or not isinstance(value.get("active_days"), list)
        or len(value["active_days"]) > 30
    ):
        raise PersonalMetricsError("metrics_invalid")
    for count in value["counters"].values():
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            raise PersonalMetricsError("metrics_invalid")
    for metric in value["metrics"].values():
        if not isinstance(metric, dict) or set(metric) != {"observed", "missing", "total", "maximum"}:
            raise PersonalMetricsError("metrics_invalid")
        for number in metric.values():
            if isinstance(number, bool) or not isinstance(number, (int, float)) or not math.isfinite(number) or number < 0:
                raise PersonalMetricsError("metrics_invalid")
    if any(isinstance(count, bool) or not isinstance(count, int) or count < 0 for count in value["reasons"].values()):
        raise PersonalMetricsError("metrics_invalid")
    days = value["active_days"]
    if any(isinstance(day, bool) or not isinstance(day, int) or day < 1 for day in days) or days != sorted(set(days)):
        raise PersonalMetricsError("metrics_invalid")
    return value


def _reduction(
    metrics: dict[str, object],
    before_name: str,
    after_name: str,
    *,
    expected_observations: int,
    unit: str,
) -> dict[str, object]:
    before = metrics[before_name]
    after = metrics[after_name]
    known = min(before["observed"], after["observed"])
    complete = (
        before["observed"] == after["observed"] == expected_observations
        and before["missing"] == after["missing"] == 0
    )
    if expected_observations == 0:
        return {
            unit: 0,
            "percent": 0.0,
            "observed_requests": 0,
            "expected_requests": 0,
            "complete": True,
            "missing": False,
        }
    if known == 0 or not complete:
        return {
            unit: None,
            "percent": None,
            "observed_requests": known,
            "expected_requests": expected_observations,
            "complete": False,
            "missing": True,
        }
    saved = max(0, before["total"] - after["total"])
    percent = 0.0 if before["total"] == 0 else round(saved * 100 / before["total"], 3)
    return {
        unit: saved,
        "percent": percent,
        "observed_requests": known,
        "expected_requests": expected_observations,
        "complete": True,
        "missing": False,
    }


def _metric_view(metric: dict[str, int | float]) -> dict[str, int | float | None]:
    observed = metric["observed"]
    return {
        "total": metric["total"] if observed else None,
        "maximum": metric["maximum"] if observed else None,
        "observed": observed,
        "missing": metric["missing"],
    }


def _first_count(value: Mapping[str, object], *names: str) -> int | None:
    for name in names:
        observed = _count(value.get(name))
        if observed is not None:
            return observed
    return None


def _count(value: object) -> int | None:
    return value if type(value) is int and value >= 0 else None


def _private_directory(path: Path, *, create: bool) -> None:
    try:
        if create:
            path.mkdir(mode=0o700, parents=True, exist_ok=True)
        info = path.lstat()
    except OSError as error:
        raise PersonalMetricsError("metrics_directory_unavailable") from error
    if (
        not stat.S_ISDIR(info.st_mode)
        or path.is_symlink()
        or info.st_uid != os.getuid()
        or info.st_mode & 0o077
    ):
        raise PersonalMetricsError("metrics_directory_unsafe")


class _MetricsFileLock(AbstractContextManager["_MetricsFileLock"]):
    """Private cross-process serialization for read/modify/write counters."""

    def __init__(self, path: Path):
        self.path = path
        self._stream = None

    def __enter__(self) -> "_MetricsFileLock":
        flags = (
            os.O_RDWR
            | os.O_CREAT
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        try:
            descriptor = os.open(self.path, flags, 0o600)
        except OSError as error:
            raise PersonalMetricsError("metrics_lock_unavailable") from error
        try:
            info = os.fstat(descriptor)
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != os.getuid()
                or info.st_nlink != 1
                or info.st_mode & 0o077
            ):
                raise PersonalMetricsError("metrics_lock_unsafe")
            os.fchmod(descriptor, 0o600)
            if os.name == "nt" and info.st_size == 0:  # pragma: no cover - Windows CI
                os.write(descriptor, b"\x00")
                os.lseek(descriptor, 0, os.SEEK_SET)
            self._stream = os.fdopen(descriptor, "a+")
            descriptor = -1
            _lock_stream(self._stream)
            return self
        except Exception:
            if self._stream is not None:
                self._stream.close()
                self._stream = None
            elif descriptor >= 0:
                os.close(descriptor)
            raise

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        if self._stream is not None:
            _unlock_stream(self._stream)
            self._stream.close()
            self._stream = None
        return None


def _lock_stream(stream) -> None:
    if os.name == "nt":  # pragma: no cover - Windows CI
        import msvcrt

        stream.seek(0)
        msvcrt.locking(stream.fileno(), msvcrt.LK_LOCK, 1)
    else:
        import fcntl

        fcntl.flock(stream.fileno(), fcntl.LOCK_EX)


def _unlock_stream(stream) -> None:
    if os.name == "nt":  # pragma: no cover - Windows CI
        import msvcrt

        stream.seek(0)
        try:
            msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
    else:
        import fcntl

        fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


__all__ = [
    "MAX_RESPONSE_INSPECTION_BYTES",
    "OptimizationMeasurement",
    "PersonalMetricsError",
    "PersonalMetricsStore",
    "ProviderMeasurement",
    "ResponseUsageAccumulator",
    "extract_provider_usage",
]
