"""Authorization rules for metadata-only usage reporting.

Usage reporting has intentionally narrow scopes.  The resolver is shared by
the HTTP boundary and both durable audit repositories so a report cannot be
read under one scope and audited as though it were authorized by another.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .config import Identity
from .usage_reporting import REPORT_DIMENSIONS


UsageReportScope = Literal["self", "team", "finance", "organization"]

_SELF = "usage_self_viewer"
_TEAM = "usage_team_viewer"
_FINANCE = "usage_finance_viewer"
_ORGANIZATION = "usage_organization_viewer"
_LEGACY_ORGANIZATION = "usage_viewer"
_FINANCE_DIMENSIONS = {
    "organization",
    "model",
    "requested_model",
    "actual_model",
    "policy",
    "status",
    "client",
    "provider",
}


class UsageReportAccessError(ValueError):
    """A stable, content-free authorization failure for a usage report."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class UsageReportAccess:
    """The report filters that are actually authorized for one credential."""

    scope: UsageReportScope
    actor_id: str | None
    team_id: str | None


def authorize_usage_report(
    identity: Identity,
    *,
    group_by: str,
    actor_id: str | None,
    team_id: str | None,
) -> UsageReportAccess:
    """Resolve a credential into one bounded usage-report query.

    Configuration rejects mixed usage-view scopes.  This function separately
    fails closed for synthetic or stale identities that bypass configuration.
    """

    scope = usage_report_scope(identity)
    if group_by not in REPORT_DIMENSIONS:
        raise UsageReportAccessError("invalid_usage_report_request")

    if scope == "organization":
        return UsageReportAccess(scope, actor_id, team_id)

    if scope == "self":
        if actor_id not in {None, identity.actor_id} or team_id not in {
            None,
            identity.team_id,
        }:
            raise UsageReportAccessError("usage_report_scope_forbidden")
        return UsageReportAccess("self", identity.actor_id, team_id)

    if scope == "team":
        if (
            group_by == "person"
            or actor_id is not None
            or team_id not in {None, identity.team_id}
        ):
            raise UsageReportAccessError("usage_report_scope_forbidden")
        return UsageReportAccess("team", None, identity.team_id)

    if (
        group_by not in _FINANCE_DIMENSIONS
        or actor_id is not None
        or team_id is not None
    ):
        raise UsageReportAccessError("usage_report_scope_forbidden")
    return UsageReportAccess("finance", None, None)


def usage_report_scope(identity: Identity) -> UsageReportScope:
    """Return the one configured report scope, or fail closed."""

    capabilities = set(identity.capabilities)
    scopes: set[UsageReportScope] = set()
    if capabilities & {_LEGACY_ORGANIZATION, _ORGANIZATION}:
        scopes.add("organization")
    if _SELF in capabilities:
        scopes.add("self")
    if _TEAM in capabilities:
        scopes.add("team")
    if _FINANCE in capabilities:
        scopes.add("finance")
    if not scopes:
        raise UsageReportAccessError("usage_viewer_capability_required")
    if len(scopes) != 1:
        raise UsageReportAccessError("usage_report_scope_ambiguous")
    return next(iter(scopes))
