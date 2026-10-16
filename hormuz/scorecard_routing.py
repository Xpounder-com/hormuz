"""Deterministic preferences over the routes already admitted by policy."""
from pathlib import Path
import threading
import time

from .execution_destination import route_fingerprint
from .model_benchmark import benchmark_profile_digest, digest, machine_facts, read_scorecard
from .work_learning import request_characteristics


class ScorecardRouting:
    def __init__(self, config):
        self.config = config
        self.path = config.route_admission.scorecard
        self.machine = machine_facts() if self.path else None
        self._lock = threading.Lock()
        self._stamp, self._report = None, None

    def choose(self, identity, candidates, value, provider_body):
        def result(status, alias=None, report=None):
            return alias, {"X-Hormuz-Scorecard-Status": status,
                           **({"X-Hormuz-Scorecard-SHA256": digest(report)} if report else {})}
        try:
            with self._lock:
                info = Path(self.path).lstat()
                stamp = (info.st_ino, info.st_mtime_ns, info.st_size)
                if stamp != self._stamp:
                    self._report = read_scorecard(self.path)
                    self._stamp = stamp
                report = self._report
        except FileNotFoundError:
            return result("missing")
        except (OSError, ValueError, TypeError, KeyError, RecursionError):
            return result("invalid")
        now = time.time()
        if report["expires_at"] <= now or report["recorded_at"] > now:
            return result("expired")
        if report["actor_id"] != identity.actor_id or report["organization_id"] != identity.organization_id:
            return result("owner_mismatch")
        if report["machine"] != self.machine:
            return result("machine_changed")
        characteristics = request_characteristics(value)
        profile = benchmark_profile_digest(value)
        eligible = []
        for model in report["models"]:
            candidate = candidates.get(model["model_alias"])
            if candidate is None or self.config.upstreams[candidate.route.destination_id].locality != "local":
                continue
            if model["route_fingerprint"] != route_fingerprint(candidate.route, upstreams=self.config.upstreams):
                continue
            required_prices = {"input_cost_per_million", "output_cost_per_million"}
            if any(row["usage"]["cache_read_tokens"] for row in model["observations"]):
                required_prices.add("cache_read_cost_per_million")
            prices_known = required_prices <= candidate.route.explicit_price_fields
            if (not prices_known and (report["targets"]["objective"] == "cost"
                    or report["targets"].get("max_mean_cost_microusd") is not None)):
                continue
            for workload in model["workloads"]:
                if (not workload["target_met"] or workload["task_type"] != characteristics["task_hint"]
                        or workload["request_profile_sha256"] != profile or workload["input_size"] != characteristics["input_size"]
                        or len(provider_body(value, candidate.route)) > workload["request_bytes_max"]
                        or type(value.get("max_output_tokens")) is not int
                        or value["max_output_tokens"] > workload["max_output_tokens"]):
                    continue
                metrics = workload["metrics"]
                quality = -metrics["task_success_rate"]
                latency = metrics["latency_ms"]["p95"]
                cost = metrics["mean_cost_microusd"] if prices_known else None
                cost = cost if cost is not None else float("inf")
                objective = report["targets"]["objective"]
                rank = (quality, latency, cost) if objective == "quality" else (latency, quality, cost) if objective == "latency" else (cost, quality, latency)
                eligible.append((rank, model["model_alias"]))
        if not eligible:
            return result("no_target_match")
        return result("selected", min(eligible)[1], report)
