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
    ".github/rulesets/candidate-tag-creation.json",
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
PERMISSION_KEYS = {
    "actions",
    "attestations",
    "checks",
    "contents",
    "deployments",
    "discussions",
    "id-token",
    "issues",
    "models",
    "packages",
    "pages",
    "pull-requests",
    "security-events",
    "statuses",
}
PERMISSION_LEVELS = {"none", "read", "write"}
AUTHORIZED_CONTENTS_WRITER = ("freeze-v1-candidate.yml", "freeze")
PermissionSpec = str | dict[str, str]


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
    version_tag_condition = {
        "ref_name": {"include": ["refs/tags/v*"], "exclude": []}
    }
    immutable_tag_condition = {
        "ref_name": {
            "include": ["refs/tags/v*", "refs/tags/candidate-v1.0.0-*"],
            "exclude": [],
        }
    }
    return {
        ".github/rulesets/candidate-tag-creation.json": {
            "name": "Steward workflow candidate tags",
            "target": "tag",
            "enforcement": "active",
            "bypass_actors": [
                {
                    "actor_id": 15368,
                    "actor_type": "Integration",
                    "bypass_mode": "always",
                }
            ],
            "conditions": {
                "ref_name": {
                    "include": ["refs/tags/candidate-v1.0.0-*"],
                    "exclude": [],
                }
            },
            "rules": [{"type": "creation"}],
        },
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
            "conditions": version_tag_condition,
            "rules": [{"type": "creation"}],
        },
        ".github/rulesets/version-tag-immutability.json": {
            "name": "Immutable version tags",
            "target": "tag",
            "enforcement": "active",
            "bypass_actors": [],
            "conditions": immutable_tag_condition,
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


def _yaml_indent(line: str, *, label: str) -> int:
    prefix = line[: len(line) - len(line.lstrip(" \t"))]
    if "\t" in prefix:
        raise RepositoryGovernanceError(
            f"workflow permissions use unsupported tab indentation: {label}"
        )
    return len(prefix)


def _plain_yaml_value(value: str, *, label: str) -> str:
    value = value.strip()
    if value.startswith("#"):
        return ""
    if " #" in value:
        value = value.split(" #", 1)[0].rstrip()
    if "#" in value:
        raise RepositoryGovernanceError(
            f"workflow permissions use unsupported YAML syntax: {label}"
        )
    return value


def _permission_key_and_value(line: str, *, indent: int) -> tuple[str, str] | None:
    match = re.fullmatch(
        rf" {{{indent}}}(?:([a-z][a-z0-9-]*)|'([a-z][a-z0-9-]*)'|\"([a-z][a-z0-9-]*)\"):\s*(.*)",
        line,
    )
    if match is None:
        return None
    key = next(value for value in match.groups()[:3] if value is not None)
    return key, match.group(4)


def _parse_permissions(
    lines: list[str],
    declaration_index: int,
    *,
    indent: int,
    end_index: int,
    label: str,
) -> PermissionSpec:
    declaration = _permission_key_and_value(
        lines[declaration_index], indent=indent
    )
    if declaration is None or declaration[0] != "permissions":
        raise RepositoryGovernanceError(
            f"workflow permissions declaration is invalid: {label}"
        )
    inline = _plain_yaml_value(declaration[1], label=label)
    if inline:
        if inline in {"{}", "read-all", "write-all"}:
            return inline
        raise RepositoryGovernanceError(
            f"workflow permissions use unsupported YAML syntax: {label}"
        )

    permissions: dict[str, str] = {}
    entry_indent: int | None = None
    for index in range(declaration_index + 1, end_index):
        line = lines[index]
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        current_indent = _yaml_indent(line, label=label)
        if current_indent <= indent:
            break
        if entry_indent is None:
            entry_indent = current_indent
        if current_indent != entry_indent:
            raise RepositoryGovernanceError(
                f"workflow permissions use unsupported YAML structure: {label}"
            )
        entry = _permission_key_and_value(line, indent=entry_indent)
        if entry is None:
            raise RepositoryGovernanceError(
                f"workflow permissions use unsupported YAML syntax: {label}"
            )
        key, raw_value = entry
        value = _plain_yaml_value(raw_value, label=label)
        if (
            key not in PERMISSION_KEYS
            or key in permissions
            or value not in PERMISSION_LEVELS
        ):
            raise RepositoryGovernanceError(
                f"workflow permissions entry is invalid: {label}"
            )
        permissions[key] = value
    if not permissions:
        raise RepositoryGovernanceError(
            f"workflow permissions mapping is empty or invalid: {label}"
        )
    return permissions


def _contents_permission(permissions: PermissionSpec) -> str:
    if permissions == "write-all":
        return "write"
    if permissions == "read-all":
        return "read"
    if permissions == "{}":
        return "none"
    assert isinstance(permissions, dict)
    return permissions.get("contents", "none")


def _workflow_permissions(
    text: str, *, workflow_name: str
) -> tuple[PermissionSpec, dict[str, PermissionSpec | None]]:
    lines = text.splitlines()
    top_level_permissions: list[int] = []
    jobs_declarations: list[int] = []
    for index, line in enumerate(lines):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        indent = _yaml_indent(line, label=workflow_name)
        entry = _permission_key_and_value(line, indent=indent)
        if indent == 0 and entry is None:
            raise RepositoryGovernanceError(
                f"workflow uses unsupported top-level mapping syntax: {workflow_name}"
            )
        if indent == 0 and entry is not None and entry[0] == "permissions":
            top_level_permissions.append(index)
        if indent == 0 and entry is not None and entry[0] == "jobs":
            if _plain_yaml_value(entry[1], label=workflow_name):
                raise RepositoryGovernanceError(
                    f"workflow jobs must use a block mapping: {workflow_name}"
                )
            jobs_declarations.append(index)

    if len(top_level_permissions) != 1:
        raise RepositoryGovernanceError(
            f"workflow lacks one explicit permissions mapping: {workflow_name}"
        )
    if len(jobs_declarations) != 1:
        raise RepositoryGovernanceError(
            f"workflow lacks one explicit jobs mapping: {workflow_name}"
        )

    jobs_index = jobs_declarations[0]
    workflow_permissions = _parse_permissions(
        lines,
        top_level_permissions[0],
        indent=0,
        end_index=len(lines),
        label=f"{workflow_name}:workflow",
    )
    job_starts: list[tuple[str, int]] = []
    job_indent: int | None = None
    for index in range(jobs_index + 1, len(lines)):
        line = lines[index]
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        indent = _yaml_indent(line, label=workflow_name)
        if indent == 0:
            break
        if job_indent is None:
            job_indent = indent
        if indent < job_indent:
            raise RepositoryGovernanceError(
                f"workflow jobs use inconsistent indentation: {workflow_name}"
            )
        if indent != job_indent:
            continue
        job = _permission_key_and_value(line, indent=job_indent)
        if job is None or _plain_yaml_value(job[1], label=workflow_name):
            raise RepositoryGovernanceError(
                f"workflow job must use a plain block mapping: {workflow_name}"
            )
        job_starts.append((job[0], index))
    if not job_starts or len({name for name, _index in job_starts}) != len(job_starts):
        raise RepositoryGovernanceError(
            f"workflow job mapping is empty or duplicated: {workflow_name}"
        )

    jobs: dict[str, PermissionSpec | None] = {}
    for position, (job_name, start_index) in enumerate(job_starts):
        end_index = (
            job_starts[position + 1][1]
            if position + 1 < len(job_starts)
            else len(lines)
        )
        permission_declarations: list[int] = []
        field_indent: int | None = None
        for index in range(start_index + 1, end_index):
            line = lines[index]
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            indent = _yaml_indent(line, label=f"{workflow_name}:{job_name}")
            if indent == 0:
                end_index = index
                break
            assert job_indent is not None
            if indent <= job_indent:
                raise RepositoryGovernanceError(
                    f"workflow job uses inconsistent indentation: {workflow_name}:{job_name}"
                )
            if field_indent is None:
                field_indent = indent
            if indent < field_indent:
                raise RepositoryGovernanceError(
                    f"workflow job uses inconsistent indentation: {workflow_name}:{job_name}"
                )
            if indent != field_indent:
                continue
            if line[field_indent:].startswith("<<:"):
                raise RepositoryGovernanceError(
                    f"workflow job uses unsupported YAML merge: {workflow_name}:{job_name}"
                )
            entry = _permission_key_and_value(line, indent=indent)
            if entry is None:
                raise RepositoryGovernanceError(
                    f"workflow job uses unsupported mapping syntax: {workflow_name}:{job_name}"
                )
            if entry[0] == "permissions":
                permission_declarations.append(index)
        if field_indent is None:
            raise RepositoryGovernanceError(
                f"workflow job mapping is empty: {workflow_name}:{job_name}"
            )
        if len(permission_declarations) > 1:
            raise RepositoryGovernanceError(
                f"workflow job repeats permissions: {workflow_name}:{job_name}"
            )
        jobs[job_name] = (
            _parse_permissions(
                lines,
                permission_declarations[0],
                indent=field_indent,
                end_index=end_index,
                label=f"{workflow_name}:{job_name}",
            )
            if permission_declarations
            else None
        )
    return workflow_permissions, jobs


def _validate_workflows(
    root: Path, allowed_owners: set[str]
) -> tuple[int, int]:
    workflow_paths = sorted((root / ".github/workflows").glob("*.y*ml"))
    if not workflow_paths:
        raise RepositoryGovernanceError("no GitHub Actions workflows found")
    action_use_count = 0
    contents_writers: list[tuple[str, str]] = []
    for path in workflow_paths:
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            raise RepositoryGovernanceError(f"cannot read workflow: {path.name}") from exc
        if "pull_request_target" in text:
            raise RepositoryGovernanceError(
                f"public-fork-unsafe pull_request_target trigger: {path.name}"
            )
        workflow_permissions, jobs = _workflow_permissions(
            text, workflow_name=path.name
        )
        for job_name, job_permissions in jobs.items():
            effective_permissions = (
                workflow_permissions if job_permissions is None else job_permissions
            )
            if _contents_permission(effective_permissions) == "write":
                contents_writers.append((path.name, job_name))
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
            if (
                not isinstance(workflow_permissions, dict)
                or workflow_permissions.get("contents") != "read"
            ):
                raise RepositoryGovernanceError(
                    f"pull-request workflow token is not read-only: {path.name}"
                )
        action_use_count += len(pinned)

    if contents_writers != [AUTHORIZED_CONTENTS_WRITER]:
        raise RepositoryGovernanceError(
            "only the steward-gated candidate freeze job may write contents"
        )

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
