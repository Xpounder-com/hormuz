"""Authorization boundary for tenant-scoped metadata-only audit reads."""

from __future__ import annotations

from .config import Identity


class AuditAccessError(ValueError):
    """Stable, content-free denial for an audit-event read."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def authorize_audit_read(identity: Identity) -> None:
    """Require the independent audit-viewer capability."""

    if "audit_viewer" not in identity.capabilities:
        raise AuditAccessError("audit_viewer_capability_required")
