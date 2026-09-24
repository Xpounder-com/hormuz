"""Strict metadata-only evidence for explicit run links and associations."""

from __future__ import annotations

from collections.abc import Mapping

from .portfolio_wire import PortfolioError, validate


LINK_SCHEMA_ID = "hormuz.run-work-link-event"
ASSOCIATION_SCHEMA_ID = "hormuz.run-outcome-association-event"
ASSOCIATION_SOURCE_SCHEMA_IDS = frozenset({LINK_SCHEMA_ID, ASSOCIATION_SCHEMA_ID})


def validate_association_evidence(schema_id: str, value: Mapping[str, object]) -> None:
    if schema_id not in ASSOCIATION_SOURCE_SCHEMA_IDS or value.get("schema_id") != schema_id:
        raise ValueError("association_schema_invalid")
    try:
        validate(dict(value), schema_id)
    except PortfolioError:
        raise ValueError("association_evidence_invalid") from None
    if value.get("schema_version") != 1:
        raise ValueError("association_schema_invalid")
    if schema_id == LINK_SCHEMA_ID:
        state = value.get("state")
        reason = value.get("reason_code")
        predecessor = value.get("supersedes_link_event_id")
        if (
            (reason == "explicit_link" and (state != "active" or predecessor is not None))
            or (reason == "corrected" and (state != "active" or predecessor is None))
            or (reason == "tombstoned" and (state != "tombstoned" or predecessor is None))
        ):
            raise ValueError("association_evidence_invalid")
        return

    state = value.get("state")
    candidates = value.get("candidate_count")
    attempt = value.get("request_attempt_id")
    scope = value.get("work_scope")
    evidence_level = value.get("evidence_level")
    reason = value.get("reason_code")
    if value.get("rule") is None:
        raise ValueError("association_evidence_invalid")
    if state == "associated":
        valid = (
            candidates == 1
            and attempt is not None
            and scope is not None
            and evidence_level == "associated"
            and reason == "eligible"
        )
    elif state == "unmatched":
        valid = (
            candidates == 0
            and attempt is None
            and scope is None
            and evidence_level == "descriptive"
            and reason in {"missing_evidence", "unmatched"}
        )
    elif state == "ambiguous":
        valid = (
            isinstance(candidates, int)
            and not isinstance(candidates, bool)
            and candidates >= 2
            and attempt is None
            and scope is None
            and evidence_level == "descriptive"
            and reason == "ambiguous"
        )
    elif state == "excluded":
        valid = (
            isinstance(candidates, int)
            and not isinstance(candidates, bool)
            and candidates >= 0
            and attempt is None
            and scope is None
            and evidence_level == "descriptive"
            and reason in {"excluded", "superseded", "unsupported"}
        )
    else:
        valid = False
    if not valid:
        raise ValueError("association_evidence_invalid")


def association_source_identity(schema_id: str, value: Mapping[str, object]) -> str:
    validate_association_evidence(schema_id, value)
    field = "link_event_id" if schema_id == LINK_SCHEMA_ID else "association_event_id"
    identity = value.get(field)
    if not isinstance(identity, str):
        raise ValueError("association_evidence_invalid")
    return identity
