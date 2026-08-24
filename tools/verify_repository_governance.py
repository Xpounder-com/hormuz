#!/usr/bin/env python3
"""Fail-closed validation for Hormuz's GitHub repository governance contract."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


SCHEMA_VERSION = 1
MANIFEST_PATH = Path(".github/repository-governance-v1.json")
RULESET_PATHS = (
    ".github/rulesets/main.json",
    ".github/rulesets/version-tag-creation.json",
    ".github/rulesets/version-tag-immutability.json",
)
REQUIRED_CHECK_CONTEXTS = (
    "Build and install package",
    "Codex and Claude Code compatibility",
    "OCI reference runtime",
    "OCI reproducibility",
    "OCI supply-chain evidence",
    "PostgreSQL compatibility",
    "PostgreSQL recovery drills",
    "Python 3.11",
    "Python 3.12",
    "Python 3.13",
    "Python 3.14",
)
DISCUSSION_CATEGORIES = (
    "Announcements",
    "General",
    "Ideas",
    "Polls",
    "Q&A",
    "Show and tell",
)
PUBLIC_TRANSITION_CHECKS = (
    "disclosure_gate_closed",
    "owner_authorization_recorded",
    "social_preview_configured",
    "organization_profile_public",
    "repository_pinned",
    "anonymous_clone_verified",
    "anonymous_templates_verified",
    "anonymous_discussions_verified",
    "license_detection_verified",
    "public_ghcr_pull_verified",
)
FULL_ACTION_USE = re.compile(
    r"^[ \t]*uses:[ \t]*([^/\s]+)/([^@\s]+)@([0-9a-f]{40})(?:[ \t]+#.*)?$",
    flags=re.MULTILINE,
)
ANY_ACTION_USE = re.compile(
    r"^[ \t]*uses:[ \t]*([^\s#]+)(?:[ \t]+#.*)?$", flags=re.MULTILINE
)


class RepositoryGovernanceError(ValueError):
    """Raised when repository governance configuration is incomplete or unsafe."""


def _read_json(path: Path) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RepositoryGovernanceError(f"cannot read strict JSON: {path}") from exc
    if not isinstance(value, dict):
        raise RepositoryGovernanceError(f"{path} must contain one object")
    return value


def _require_fields(
    value: dict[str, object], expected: set[str], label: str
) -> None:
    if set(value) != expected:
        raise RepositoryGovernanceError(f"{label} fields changed")


def _require_string_list(value: object, label: str) -> list[str]:
    if (
        not isinstance(value, list)
        or any(not isinstance(item, str) or not item for item in value)
        or len(value) != len(set(value))
    ):
        raise RepositoryGovernanceError(f"{label} must be a unique string list")
    return value


def _validate_manifest(manifest: dict[str, object]) -> list[dict[str, object]]:
    _require_fields(
        manifest,
        {
            "schema_version",
            "repository",
            "metadata",
            "actions",
            "required_checks",
            "ruleset_files",
            "discussions",
            "phase_contracts",
            "public_transition_checks",
        },
        "manifest",
    )
    if manifest["schema_version"] != SCHEMA_VERSION:
        raise RepositoryGovernanceError("unsupported governance schema version")
    if manifest["repository"] != "Xpounder-com/hormuz":
        raise RepositoryGovernanceError("repository identity changed")

    metadata = manifest["metadata"]
    if not isinstance(metadata, dict):
        raise RepositoryGovernanceError("metadata must be an object")
    _require_fields(metadata, {"description", "topics", "features"}, "metadata")
    description = metadata["description"]
    if not isinstance(description, str) or not description:
        raise RepositoryGovernanceError("repository description is empty")
    if "context" in description.lower() or "enterprise-ready" in description.lower():
        raise RepositoryGovernanceError("repository description exceeds the alpha boundary")
    topics = _require_string_list(metadata["topics"], "metadata.topics")
    if topics != sorted(topics) or len(topics) > 20:
        raise RepositoryGovernanceError("repository topics must be sorted and bounded")
    features = metadata["features"]
    if features != {
        "discussions": True,
        "issues": True,
        "projects": False,
        "wiki": False,
    }:
        raise RepositoryGovernanceError("repository feature surface changed")

    actions = manifest["actions"]
    if not isinstance(actions, dict):
        raise RepositoryGovernanceError("actions must be an object")
    _require_fields(
        actions,
        {
            "sha_pinning_required",
            "default_workflow_permissions",
            "can_approve_pull_request_reviews",
            "pre_public_allowed_actions",
            "public_allowed_actions",
            "allowed_action_owners",
            "public_allowlist",
        },
        "actions",
    )
    if (
        actions["sha_pinning_required"] is not True
        or actions["default_workflow_permissions"] != "read"
        or actions["can_approve_pull_request_reviews"] is not False
        or actions["pre_public_allowed_actions"] != "all"
        or actions["public_allowed_actions"] != "selected"
    ):
        raise RepositoryGovernanceError("Actions permission boundary changed")
    owners = _require_string_list(
        actions["allowed_action_owners"], "actions.allowed_action_owners"
    )
    if owners != ["actions", "docker", "sigstore"]:
        raise RepositoryGovernanceError("Action owner allowlist changed")
    public_allowlist = actions["public_allowlist"]
    if public_allowlist != {
        "github_owned_allowed": True,
        "verified_allowed": False,
        "patterns_allowed": ["docker/*@*", "sigstore/cosign-installer@*"],
    }:
        raise RepositoryGovernanceError("public Action allowlist changed")

    checks = manifest["required_checks"]
    if not isinstance(checks, list) or any(not isinstance(item, dict) for item in checks):
        raise RepositoryGovernanceError("required_checks must be an object list")
    contexts: list[str] = []
    for item in checks:
        assert isinstance(item, dict)
        _require_fields(item, {"context", "integration_id"}, "required check")
        context = item["context"]
        if not isinstance(context, str) or item["integration_id"] != 15368:
            raise RepositoryGovernanceError("required check identity changed")
        contexts.append(context)
    if tuple(contexts) != REQUIRED_CHECK_CONTEXTS:
        raise RepositoryGovernanceError("required CI check set changed")

    if tuple(_require_string_list(manifest["ruleset_files"], "ruleset_files")) != RULESET_PATHS:
        raise RepositoryGovernanceError("ruleset file set changed")
    if tuple(_require_string_list(manifest["discussions"], "discussions")) != DISCUSSION_CATEGORIES:
        raise RepositoryGovernanceError("Discussion category set changed")
    if tuple(
        _require_string_list(
            manifest["public_transition_checks"], "public_transition_checks"
        )
    ) != PUBLIC_TRANSITION_CHECKS:
        raise RepositoryGovernanceError("public transition evidence set changed")
    if manifest["phase_contracts"] != {
        "pre_public": {
            "visibility": "private",
            "secret_scanning": "deferred_until_public",
            "selected_action_patterns": "deferred_until_public",
        },
        "public": {
            "visibility": "public",
            "secret_scanning": "enabled",
            "selected_action_patterns": "enforced",
        },
    }:
        raise RepositoryGovernanceError("repository phase contract changed")
    return checks


def _expected_rulesets(checks: list[dict[str, object]]) -> dict[str, object]:
    tag_condition = {
        "ref_name": {"include": ["refs/tags/v*"], "exclude": []}
    }
    return {
        ".github/rulesets/main.json": {
            "name": "Protect main",
            "target": "branch",
            "enforcement": "active",
            "bypass_actors": [],
            "conditions": {
                "ref_name": {"include": ["~DEFAULT_BRANCH"], "exclude": []}
            },
            "rules": [
                {"type": "deletion"},
                {"type": "non_fast_forward"},
                {
                    "type": "pull_request",
                    "parameters": {
                        "allowed_merge_methods": ["merge", "squash", "rebase"],
                        "dismiss_stale_reviews_on_push": False,
                        "require_code_owner_review": False,
                        "require_last_push_approval": False,
                        "required_approving_review_count": 0,
                        "required_review_thread_resolution": True,
                    },
                },
                {
                    "type": "required_status_checks",
                    "parameters": {
                        "do_not_enforce_on_create": False,
                        "required_status_checks": checks,
                        "strict_required_status_checks_policy": True,
                    },
                },
            ],
        },
        ".github/rulesets/version-tag-creation.json": {
            "name": "Owner-created version tags",
            "target": "tag",
            "enforcement": "active",
            "bypass_actors": [
                {
                    "actor_id": None,
                    "actor_type": "OrganizationAdmin",
                    "bypass_mode": "always",
                }
            ],
            "conditions": tag_condition,
            "rules": [{"type": "creation"}],
        },
        ".github/rulesets/version-tag-immutability.json": {
            "name": "Immutable version tags",
            "target": "tag",
            "enforcement": "active",
            "bypass_actors": [],
            "conditions": tag_condition,
            "rules": [
                {
                    "type": "update",
                    "parameters": {"update_allows_fetch_and_merge": False},
                },
                {"type": "deletion"},
                {"type": "non_fast_forward"},
            ],
        },
    }


def _validate_rulesets(root: Path, checks: list[dict[str, object]]) -> None:
    for relative, expected in _expected_rulesets(checks).items():
        actual = _read_json(root / relative)
        if actual != expected:
            raise RepositoryGovernanceError(f"ruleset contract changed: {relative}")


def _validate_workflows(
    root: Path, allowed_owners: set[str]
) -> tuple[int, int]:
    workflow_paths = sorted((root / ".github/workflows").glob("*.y*ml"))
    if not workflow_paths:
        raise RepositoryGovernanceError("no GitHub Actions workflows found")
    action_use_count = 0
    for path in workflow_paths:
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            raise RepositoryGovernanceError(f"cannot read workflow: {path.name}") from exc
        if "pull_request_target" in text:
            raise RepositoryGovernanceError(
                f"public-fork-unsafe pull_request_target trigger: {path.name}"
            )
        if re.search(r"^permissions:\s*$", text, flags=re.MULTILINE) is None:
            raise RepositoryGovernanceError(f"workflow lacks explicit permissions: {path.name}")
        uses = ANY_ACTION_USE.findall(text)
        pinned = FULL_ACTION_USE.findall(text)
        if len(uses) != len(pinned):
            raise RepositoryGovernanceError(
                f"workflow contains an unpinned external Action: {path.name}"
            )
        for owner, _name, _revision in pinned:
            if owner not in allowed_owners:
                raise RepositoryGovernanceError(
                    f"workflow uses an unapproved Action owner: {owner}"
                )
        if "pull_request:" in text:
            if "${{ secrets." in text:
                raise RepositoryGovernanceError(
                    f"pull-request workflow consumes repository secrets: {path.name}"
                )
            if not re.search(
                r"^permissions:\s*\n[ \t]+contents:[ \t]+read\s*$",
                text,
                flags=re.MULTILINE,
            ):
                raise RepositoryGovernanceError(
                    f"pull-request workflow token is not read-only: {path.name}"
                )
        action_use_count += len(pinned)

    ci = (root / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    if not re.search(
        r"^on:\s*\n  push:\s*\n    branches:\s*\n      - main\s*\n"
        r"  pull_request:\s*\n  workflow_dispatch:\s*$",
        ci,
        flags=re.MULTILINE,
    ):
        raise RepositoryGovernanceError(
            "CI must run once for pull requests and once after merge to main"
        )
    versions_match = re.search(r'python-version:\s*\[([^\]]+)\]', ci)
    versions = re.findall(r'"(3\.\d+)"', versions_match.group(1) if versions_match else "")
    if versions != ["3.11", "3.12", "3.13", "3.14"]:
        raise RepositoryGovernanceError("required Python check matrix changed")
    for context in REQUIRED_CHECK_CONTEXTS[:7]:
        if f"name: {context}" not in ci:
            raise RepositoryGovernanceError(f"required CI job is absent: {context}")

    release = (root / ".github/workflows/release-oci.yml").read_text(encoding="utf-8")
    if '- "v[0-9]*.[0-9]*.[0-9]*"' not in release:
        raise RepositoryGovernanceError("release workflow tag trigger changed")
    if '--ref-protected "$GITHUB_REF_PROTECTED"' not in release:
        raise RepositoryGovernanceError("release workflow no longer proves tag protection")
    return len(workflow_paths), action_use_count


def validate_repository_governance(root: Path) -> dict[str, object]:
    root = root.resolve()
    manifest = _read_json(root / MANIFEST_PATH)
    checks = _validate_manifest(manifest)
    _validate_rulesets(root, checks)
    actions = manifest["actions"]
    assert isinstance(actions, dict)
    owners = actions["allowed_action_owners"]
    assert isinstance(owners, list)
    workflow_count, action_use_count = _validate_workflows(root, set(owners))
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "passed",
        "repository": manifest["repository"],
        "ruleset_count": len(RULESET_PATHS),
        "required_check_count": len(REQUIRED_CHECK_CONTEXTS),
        "workflow_count": workflow_count,
        "pinned_action_use_count": action_use_count,
        "public_transition_check_count": len(PUBLIC_TRANSITION_CHECKS),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parent.parent,
        help="repository root",
    )
    args = parser.parse_args(argv)
    try:
        result = validate_repository_governance(args.root)
    except RepositoryGovernanceError as exc:
        print(f"repository_governance_failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
