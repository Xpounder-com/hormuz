"""Pure optional-header parsing and operator authority, before any lookup."""

from __future__ import annotations

from dataclasses import dataclass
import re

from .attribution_config import WorkScopeRef
from .config import GatewayConfig, Identity


REQUEST_HEADER = "X-Hormuz-Work-Scope"
RESULT_HEADER = "X-Hormuz-Work-Scope-Result"
REASONS = frozenset({"bound", "missing_evidence", "ambiguous", "invalid_reference", "stale_version",
                     "unsupported", "unauthorized_scope", "dependency_unavailable"})


class AdmissionError(ValueError):
    def __init__(self, reason: str, status: int = 400):
        if reason not in REASONS or status not in {400, 403, 409, 503}:
            raise ValueError("admission_error_invalid")
        self.reason, self.status = reason, status
        self.result_status = "unavailable" if status == 503 else "rejected"
        super().__init__("attribution_" + reason)

    @property
    def result_header(self) -> str:
        return f"v1;status={self.result_status};reason={self.reason}"


@dataclass(frozen=True)
class Admission:
    work_scope: WorkScopeRef | None
    confidence: str
    reason: str

    @property
    def result_header(self) -> str:
        status = "attributed" if self.work_scope else "ambiguous" if self.confidence == "ambiguous" else "unattributed"
        return f"v1;status={status};reason={self.reason}"


def binding_for(config: GatewayConfig, identity: Identity, client: str):
    control = config.attribution_control
    if control is not None:
        for binding in control.bindings:
            if (binding.organization_id, binding.actor_id, binding.client) == (identity.organization_id, identity.actor_id, client):
                return binding
    return None


def select_admission(
    config: GatewayConfig, identity: Identity, client: str, headers: list[str] | tuple[str, ...], *,
    account_usage: bool,
) -> Admission | None:
    binding = binding_for(config, identity, client)
    if not headers and (binding is None or not account_usage):
        return None
    if len(headers) > 1:
        raise AdmissionError("ambiguous")
    if headers:
        raw = headers[0]
        if not isinstance(raw, str) or not raw.isascii() or len(raw) > 192:
            raise AdmissionError("invalid_reference")
        if re.match(r"v[0-9]+;", raw) and not raw.startswith("v1;"):
            raise AdmissionError("unsupported")
        match = re.fullmatch(r"v1;work_scope_id=([A-Za-z0-9][A-Za-z0-9._:-]{0,127});version=([1-9][0-9]{0,9})", raw)
        if match is None or int(match[2]) > 2147483647:
            raise AdmissionError("invalid_reference")
        if not account_usage:
            raise AdmissionError("unsupported")
        if config.attribution_control is None:
            raise AdmissionError("unsupported", 403)
        reference = WorkScopeRef(match[1], int(match[2]))
        if binding is None or reference not in binding.allowed_work_scopes:
            raise AdmissionError("unauthorized_scope", 403)
        return Admission(reference, "explicit_authorized", "bound")
    defaults = binding.default_work_scopes
    if len(defaults) == 1:
        return Admission(defaults[0], "server_side_default", "bound")
    reason = "ambiguous" if defaults else "missing_evidence"
    if binding.require_scope:
        raise AdmissionError(reason, 403)
    return Admission(None, "ambiguous" if defaults else "unattributed", reason)
