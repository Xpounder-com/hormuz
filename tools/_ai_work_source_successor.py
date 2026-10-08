"""Project approved additive AI-work source changes onto their frozen parent.

This does not re-pin a runtime plan or normalize arbitrary additions. Each
reversible edit is explicitly authorized by a separately digest-pinned document;
the projected bytes must still satisfy the original source SHA guard.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re

CONTRACT_PATH = "docs/ai-work-source-successor-v1.json"
CONTRACT_SHA256 = "18f8369c4e04a86e390c8cf15db07acd34f6907a028924793cac3381caf73928"
SOURCE_PATHS = frozenset({"MANIFEST.in", "docs/DURABLE_DATA.md", "docs/durable-data-v1.json",
                          "pyproject.toml", "tests/test_durable_data_inventory.py",
                          "tools/verify_durable_data_inventory.py"})
REQUIRED_FILES = (CONTRACT_PATH, "tools/_ai_work_source_successor.py", "hormuz/work_runtime.py",
                  "hormuz/work_billing.py")
_VERSION = re.compile(rb'(?m)^version[ \t]*=[ \t]*"[^"\r\n]+"[ \t]*$')


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("ai_work_source_authorization_invalid")
        result[key] = value
    return result


def contract(root: Path) -> dict:
    try:
        payload = (root / CONTRACT_PATH).read_bytes()
        if not 2 <= len(payload) <= 131_072:
            raise ValueError()
        value = json.loads(payload, object_pairs_hook=_unique,
                           parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        canonical = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
        if hashlib.sha256(canonical).hexdigest() != CONTRACT_SHA256:
            raise ValueError()
        if set(value) != {"schema_id", "schema_version", "predecessor", "source_edits", "addition_counts", "nonclaims"} \
                or value["schema_id"] != "hormuz.ai-work-source-successor" or value["schema_version"] != 1 \
                or value["predecessor"] != "docs/recommendation-runtime-plan-v1.json" \
                or not isinstance(value["source_edits"], dict) or not set(value["source_edits"]).issubset(SOURCE_PATHS) \
                or value["addition_counts"] != {"database_classes": 2, "sqlite_tables": 7, "postgresql_tables": 0, "operator_artifacts": 2}:
            raise ValueError()
        return value
    except (OSError, ValueError, TypeError, KeyError, UnicodeError, RecursionError):
        raise ValueError("ai_work_source_authorization_invalid") from None


def projected_sha256(root: Path, relative: str, expected: str) -> str:
    """Return a parent-source digest only for the exact approved additive edits."""
    authorization = contract(root)
    item = authorization["source_edits"].get(relative)
    if not isinstance(item, dict) or set(item) != {"predecessor_sha256", "edits"} \
            or item["predecessor_sha256"] != expected or not isinstance(item["edits"], list) \
            or not 1 <= len(item["edits"]) <= 20:
        raise ValueError("ai_work_source_authorization_invalid")
    payload = (root / relative).read_bytes()
    for edit in reversed(item["edits"]):
        if not isinstance(edit, dict) or set(edit) != {"predecessor", "addition"} \
                or not isinstance(edit["predecessor"], str) or not isinstance(edit["addition"], str) \
                or not edit["addition"] or edit["predecessor"] == edit["addition"]:
            raise ValueError("ai_work_source_authorization_invalid")
        addition = edit["addition"].encode()
        if payload.count(addition) != 1:
            raise ValueError("ai_work_source_addition_changed")
        payload = payload.replace(addition, edit["predecessor"].encode(), 1)
    if relative == "pyproject.toml":
        payload, count = _VERSION.subn(b'version = "<release-version>"', payload)
        if count != 1:
            raise ValueError("project_version_line_invalid")
    return hashlib.sha256(payload).hexdigest()


def inventory_counts(root: Path) -> tuple[int, int, int]:
    counts = contract(root)["addition_counts"]
    return 42 + counts["database_classes"], 90 + counts["sqlite_tables"], 92 + counts["postgresql_tables"]
