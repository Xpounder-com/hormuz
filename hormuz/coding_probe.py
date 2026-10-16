"""One bounded operator-authorized local experiment; never a coding receipt."""
import json
import platform
import secrets
import threading
import time

from ._contract_schemas.local_coding import canonical_digest
from .client_versions import SUPPORTED_CODEX_VERSIONS
from .coding_qualification import request_profile_digest
from .contracts import ContractValidationError, validate_contract
from .work_learning import contains_provider_state

HEADER = "X-Hormuz-Qualification-Probe"


class ProbeError(ValueError):
    pass


class CodingProbe:
    def __init__(self, admission):
        self.admission = admission
        self.lock = threading.RLock()
        self.plan = None
        self.revoked = False
        self.count = 0
        self.work_id = None

    def status(self):
        with self.lock:
            plan = self.plan
            remaining = max(0, int((self.deadline - time.monotonic()) * 1000)) if plan else 0
            state = ("inactive" if plan is None else "revoked" if self.revoked else "expired" if not remaining
                     else "exhausted" if self.count >= plan["max_requests"] else "running" if self.count else "armed")
            return {"schema_id": "hormuz.coding-probe-status", "schema_version": 1, "state": state,
                "probe_id": self.probe_id if plan else None, "plan_sha256": plan["plan_sha256"] if plan else None,
                "case_id": plan["case_id"] if plan else None, "model": plan["model"] if plan else None,
                "destination_generation": self.generation if plan else None, "submitted_requests": self.count,
                "max_requests": plan["max_requests"] if plan else 0,
                "remaining_ms": 0 if self.revoked else remaining, "automatic_admission_effect": "none"}

    def control(self, identity, plan=None, revoke=False):
        with self.lock:
            if revoke:
                self.revoked = True
            elif plan is not None:
                try:
                    validate_contract(plan)
                    if plan["schema_id"] != "hormuz.coding-probe-plan":
                        raise ValueError()
                except (ValueError, KeyError, TypeError, RecursionError, ContractValidationError):
                    raise ProbeError("probe_invalid_plan") from None
                if self.status()["state"] in {"armed", "running", "exhausted"}:
                    raise ProbeError("probe_already_active")
                config = self.admission.config
                if config.route_admission.mode != "local_only":
                    raise ProbeError("probe_requires_local_only")
                if not config.ai_work.enabled:
                    raise ProbeError("probe_requires_work_binding")
                if config.ai_work.cache_enabled:
                    raise ProbeError("probe_requires_uncached_execution")
                route = config.model_routes.get(plan["model"])
                qualification = self.admission.qualifications.get(plan["model"])
                managed = config.managed_llama
                subject = plan["subject"]
                if route is None or qualification is None or managed is None:
                    raise ProbeError("probe_subject_mismatch")
                expected = {"client_version": subject["client_version"], "system": platform.system(),
                    "machine": platform.machine(), "runtime_profile": "managed-llama.v1",
                    "runtime_sha256": managed.runtime_sha256, "model_revision": route.model_revision,
                    "upstream_model": route.upstream_model, "protocol": route.protocol,
                    "capabilities": sorted(route.capabilities), "max_request_bytes": qualification.max_request_bytes,
                    "max_output_tokens": qualification.max_output_tokens,
                    "upstream_timeout_seconds": config.upstream_timeout_seconds,
                    "request_profile_sha256": subject["request_profile_sha256"]}
                destination = self.admission.destinations.resolve(route)
                if (subject["client_version"] not in SUPPORTED_CODEX_VERSIONS
                        or {**subject, "capabilities": sorted(subject["capabilities"])} != expected
                        or managed.route != route.alias or destination.locality != "local"
                        or "code_change" not in qualification.task_types):
                    raise ProbeError("probe_subject_mismatch")
                self.plan = json.loads(json.dumps(plan))
                self.probe_id = secrets.token_hex(16)
                self.actor_id = identity.actor_id
                self.generation = destination.generation
                self.deadline = time.monotonic() + plan["max_elapsed_seconds"]
                self.revoked, self.count, self.work_id = False, 0, None
            return self.status()

    def check(self, handler, identity, decision, value, *, client, claim=False):
        with self.lock:
            plan = self.plan
            identifiers = handler.headers.get_all(HEADER, [])
            claimed = getattr(handler, "_coding_probe_claimed", False)
            if (plan is None or self.revoked or time.monotonic() >= self.deadline
                    or not claimed and self.count >= plan["max_requests"]
                    or identifiers != [self.probe_id] or identity.actor_id != self.actor_id
                    or client != "codex" or handler.path != "/v1/responses"
                    or decision.requested_model != plan["model"] or decision.resolved_alias != plan["model"]
                    or self.admission.destinations.resolve(decision.route).generation != self.generation):
                raise ProbeError("probe_request_mismatch")
            agents = handler.headers.get_all("User-Agent", [])
            if (len(agents) != 1 or not 0 < len(agents[0]) <= 512
                    or any(not 32 <= ord(c) < 127 for c in agents[0])
                    or agents[0].partition(" ")[0] != "codex_exec/" + plan["subject"]["client_version"]
                    or request_profile_digest(value) != plan["subject"]["request_profile_sha256"]
                    or getattr(handler, "_work_conversation", None) is None):
                raise ProbeError("probe_request_mismatch")
            items = value.get("input")
            first = self.work_id is None or claimed and getattr(handler, "_coding_probe_first", False)
            if first:
                if contains_provider_state(value) or canonical_digest(items) != plan["first_input_sha256"]:
                    raise ProbeError("probe_request_mismatch")
            else:
                if (getattr(handler, "_work_id", None) != self.work_id or type(items) is not list
                        or len(items) <= self.first_length
                        or canonical_digest(items[:self.first_length]) != plan["first_input_sha256"]
                        or not claimed and (len(items) <= self.last_length
                            or canonical_digest(items[:self.last_length]) != self.last_input_sha256)
                        or not contains_provider_state(value)):
                    raise ProbeError("probe_request_mismatch")
                for item in items[self.first_length:]:
                    if (type(item) is not dict or not (item.get("type") in {"function_call", "function_call_output"}
                            or item.get("type", "message") == "message" and item.get("role") == "assistant")):
                        raise ProbeError("probe_request_mismatch")
            if claim and not claimed:
                if not getattr(handler, "_work_id", None):
                    raise ProbeError("probe_request_mismatch")
                if first:
                    self.work_id = handler._work_id
                    self.first_length = len(items) if isinstance(items, list) else 1
                self.last_length = len(items) if isinstance(items, list) else 1
                self.last_input_sha256 = canonical_digest(items)
                self.count += 1
                handler._coding_probe_claimed = True
                handler._coding_probe_first = first
            handler._coding_probe = True

    def remaining(self):
        with self.lock:
            return max(0, self.deadline - time.monotonic())
