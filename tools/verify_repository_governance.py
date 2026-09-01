#!/usr/bin/env python3
"""Fail-closed validation for Hormuz's GitHub repository governance contract."""

from __future__ import annotations

import argparse
import hashlib
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
CUSTODY_SECRET_NAMES = (
    "V1_RELEASE_ADMIN_TOKEN",
    "V1_RELEASE_PUBLISH_TOKEN",
)
CUSTODY_ENVIRONMENT_NAME = "v1-release-custody"
EXPECTED_WORKFLOW_SECRET_EXPRESSIONS = {
    "freeze-v1-candidate.yml": (
        "${{ secrets.V1_RELEASE_ADMIN_TOKEN }}",
        "${{ secrets.V1_RELEASE_PUBLISH_TOKEN != '' }}",
        (
            "${{ secrets.V1_RELEASE_ADMIN_TOKEN != "
            "secrets.V1_RELEASE_PUBLISH_TOKEN }}"
        ),
        "${{ secrets.V1_RELEASE_PUBLISH_TOKEN }}",
        "${{ secrets.V1_RELEASE_ADMIN_TOKEN }}",
        "${{ secrets.V1_RELEASE_PUBLISH_TOKEN }}",
        (
            "${{ secrets.V1_RELEASE_ADMIN_TOKEN != "
            "secrets.V1_RELEASE_PUBLISH_TOKEN }}"
        ),
        "${{ secrets.V1_RELEASE_ADMIN_TOKEN }}",
    ),
    "live-client-conformance.yml": (
        "${{ secrets.HORMUZ_LIVE_ANTHROPIC_PROVIDER_KEY }}",
        "${{ secrets.HORMUZ_LIVE_OPENAI_PROVIDER_KEY }}",
    ),
    "macos-distribution.yml": (
        "${{ secrets.APPLE_NOTARY_ISSUER_ID }}",
        "${{ secrets.APPLE_NOTARY_KEY_ID }}",
        "${{ secrets.APPLE_NOTARY_KEY_P8_BASE64 }}",
        "${{ secrets.MACOS_DEVELOPER_ID_P12_BASE64 }}",
        "${{ secrets.MACOS_DEVELOPER_ID_P12_PASSWORD }}",
    ),
    "release-oci.yml": (
        "${{ secrets.GITHUB_TOKEN }}",
        "${{ secrets.GITHUB_TOKEN }}",
        "${{ secrets.GITHUB_TOKEN }}",
        "${{ secrets.GITHUB_TOKEN }}",
    ),
}
EXPECTED_WORKFLOW_JOB_ENVIRONMENTS = {
    "freeze-v1-candidate.yml": {
        "preflight": CUSTODY_ENVIRONMENT_NAME,
        "publish": CUSTODY_ENVIRONMENT_NAME,
    },
    "live-client-conformance.yml": {
        "live-clients": "live-provider-conformance"
    },
    "macos-distribution.yml": {
        "sign-and-notarize": "macos-distribution"
    },
    "website.yml": {"deploy": "github-pages"},
}
MACOS_DISTRIBUTION_SOURCE_GUARD = (
    "HORMUZ_EXPECTED_REF: refs/heads/${{ github.event.repository.default_branch }}",
    'test "$GITHUB_REF" = "$HORMUZ_EXPECTED_REF"',
    'test "$(git rev-parse HEAD)" = "$GITHUB_SHA"',
    'test -z "$(git status --porcelain)"',
)
PAGES_PUBLISH_CONDITION = (
    "github.repository == 'Xpounder-com/hormuz' && "
    "github.event_name != 'pull_request' && github.ref == 'refs/heads/main'"
)
WORKFLOW_TOP_LEVEL_FIELDS = frozenset(
    {"name", "on", "permissions", "concurrency", "jobs"}
)
REQUIRED_WORKFLOW_TOP_LEVEL_FIELDS = frozenset(
    {"name", "on", "permissions", "jobs"}
)
CANONICAL_WORKFLOW_NAMES = frozenset(
    {
        "ci.yml",
        "freeze-v1-candidate.yml",
        "live-client-conformance.yml",
        "release-oci.yml",
        "upstream-canary.yml",
    }
)
CANDIDATE_CREDENTIAL_STEP_SHA256 = {
    "Verify credentials and live controls before the one permitted build": (
        "446ef1824e8593950491f4b35eb02a3535ec63ff714d61b47c7de5f16a870693"
    ),
    "Authenticate the publisher credential before the one permitted build": (
        "7a49bd36a93f1ad50e905363112bb5c86ac50c55a00c19be83608b9f0d8471e2"
    ),
    "Revalidate controls, publish the verified draft, and seal custody": (
        "d4c81896f26c49be54b7d9d20e5a7785cac062cc6673def7652db3cf43e35abd"
    ),
    "Verify the published immutable candidate and attestations": (
        "2580ad938db646bfbec7fe4585d2f75e286ba36f310179d68577bd1f6f05391d"
    ),
}
CANDIDATE_FREEZE_JOB_SHA256 = {
    "authorize": "17dcbe2c36d7cbd38e2c63df0924b54201aa7ef9fed255d21fd995c1856c8451",
    "preflight": "d888d54f6a88a34ee537a93b7c611f9be59ccbcbc4dad77ec1a132e9d623489c",
    "build": "bbdb6cc17297f7a013067f435f9323703f0093ef9013a4b53abfd4dc8d9dc834",
    "publish": "eefb21561e28cf8d0c1cd02170eb68ecea373e2beec1461b4fcaee22f307605b",
}
CANDIDATE_FREEZE_WORKFLOW_SHA256 = (
    "b3a606831e3f3b6e6182345a128ded2a4dd4db1d65bb97e711d75970246e18f6"
)
CANDIDATE_TOOL_SHA256 = (
    "03a67b662fffd621b10187bf1c41ecb520bbe641ef7cd797fa02e230a80fe88c"
)
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
            "name": "Owner-created candidate tags",
            "target": "tag",
            "enforcement": "active",
            "bypass_actors": [
                {
                    "actor_id": None,
                    "actor_type": "OrganizationAdmin",
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


def _workflow_named_step(text: str, *, name: str) -> str:
    marker = f"      - name: {name}\n"
    if text.count(marker) != 1:
        raise RepositoryGovernanceError(
            f"candidate freeze step identity changed: {name}"
        )
    start = text.index(marker)
    remainder_start = start + len(marker)
    next_step = re.search(
        r"^(?:      - |  [a-z][a-z0-9-]*:\s*$)",
        text[remainder_start:],
        flags=re.MULTILINE,
    )
    end = (
        remainder_start + next_step.start()
        if next_step is not None
        else -1
    )
    return text[start:] if end < 0 else text[start:end]


def _has_exact_line(text: str, expected: str) -> bool:
    return any(line.strip() == expected for line in text.splitlines())


def _workflow_step_fields(step: str, *, name: str) -> tuple[str, ...]:
    fields: list[str] = []
    for line in step.splitlines()[1:]:
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if _yaml_indent(line, label=name) != 8:
            continue
        field = _permission_key_and_value(line, indent=8)
        if field is None:
            raise RepositoryGovernanceError(
                "candidate freeze credential boundary changed"
            )
        fields.append(field[0])
    return tuple(fields)


def _workflow_step_environment(step: str, *, name: str) -> dict[str, str]:
    lines = step.splitlines()
    environment_declarations: list[int] = []
    for index, line in enumerate(lines[1:], start=1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        indent = _yaml_indent(line, label=name)
        if indent < 8:
            raise RepositoryGovernanceError(
                "candidate freeze credential boundary changed"
            )
        if indent != 8:
            continue
        field = _permission_key_and_value(line, indent=8)
        if field is None:
            raise RepositoryGovernanceError(
                "candidate freeze credential boundary changed"
            )
        if field[0] == "env":
            if _plain_yaml_value(field[1], label=name):
                raise RepositoryGovernanceError(
                    "candidate freeze credential boundary changed"
                )
            environment_declarations.append(index)
    if len(environment_declarations) != 1:
        raise RepositoryGovernanceError(
            "candidate freeze credential boundary changed"
        )

    environment: dict[str, str] = {}
    for line in lines[environment_declarations[0] + 1 :]:
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        indent = _yaml_indent(line, label=name)
        if indent <= 8:
            break
        if indent != 10:
            raise RepositoryGovernanceError(
                "candidate freeze credential boundary changed"
            )
        entry = re.fullmatch(r" {10}([A-Z][A-Z0-9_]*):\s*(.*)", line)
        if entry is None:
            raise RepositoryGovernanceError(
                "candidate freeze credential boundary changed"
            )
        key, value = entry.groups()
        value = value.strip()
        if not value or "#" in value or key in environment:
            raise RepositoryGovernanceError(
                "candidate freeze credential boundary changed"
            )
        environment[key] = value
    if not environment:
        raise RepositoryGovernanceError(
            "candidate freeze credential boundary changed"
        )
    return environment


def _workflow_step_run_lines(step: str, *, name: str) -> tuple[str, ...]:
    lines = step.splitlines()
    run_declarations: list[int] = []
    for index, line in enumerate(lines[1:], start=1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if _yaml_indent(line, label=name) != 8:
            continue
        field = _permission_key_and_value(line, indent=8)
        if field is None:
            raise RepositoryGovernanceError(
                "candidate freeze credential boundary changed"
            )
        if field[0] == "run":
            if _plain_yaml_value(field[1], label=name) != "|":
                raise RepositoryGovernanceError(
                    "candidate freeze credential boundary changed"
                )
            run_declarations.append(index)
    if len(run_declarations) != 1:
        raise RepositoryGovernanceError(
            "candidate freeze credential boundary changed"
        )

    run_lines: list[str] = []
    for line in lines[run_declarations[0] + 1 :]:
        if not line.strip():
            continue
        indent = _yaml_indent(line, label=name)
        if indent <= 8:
            break
        if indent < 10:
            raise RepositoryGovernanceError(
                "candidate freeze credential boundary changed"
            )
        run_lines.append(line.strip())
    if not run_lines:
        raise RepositoryGovernanceError(
            "candidate freeze credential boundary changed"
        )
    return tuple(run_lines)


def _validate_candidate_freeze_authorization(text: str) -> None:
    step_name = "Fail closed unless the trusted workflow and steward initiated this run"
    step = _workflow_named_step(text, name=step_name)
    expected_environment = {
        "AUTHORIZED_STEWARD": "${{ vars.V1_RELEASE_STEWARD }}",
        "ORIGINAL_ACTOR": "${{ github.actor }}",
        "TRIGGERING_ACTOR": "${{ github.triggering_actor }}",
        "WORKFLOW_REF": "${{ github.workflow_ref }}",
    }
    required_run_lines = (
        "set -euo pipefail",
        '[[ "$GITHUB_REPOSITORY" == "Xpounder-com/hormuz" ]] || {',
        '[[ "$GITHUB_EVENT_NAME" == "workflow_dispatch" ]] || {',
        '[[ "$WORKFLOW_REF" == "Xpounder-com/hormuz/.github/workflows/freeze-v1-candidate.yml@refs/heads/main" ]] || {',
        '[[ "$GITHUB_REF" == "refs/heads/main" && "$GITHUB_REF_TYPE" == "branch" ]] || {',
        '[[ "$GITHUB_REF_PROTECTED" == "true" ]] || {',
        '[[ "$GITHUB_SHA" =~ ^[0-9a-f]{40}$ ]] || {',
        '[[ "$GITHUB_RUN_ID" =~ ^[1-9][0-9]*$ && "$GITHUB_RUN_ATTEMPT" == "1" ]] || {',
        '[[ -n "$AUTHORIZED_STEWARD" ]] || {',
        '[[ "$ORIGINAL_ACTOR" == "$AUTHORIZED_STEWARD" ]] || {',
        '[[ "$TRIGGERING_ACTOR" == "$AUTHORIZED_STEWARD" ]] || {',
    )
    run_lines = _workflow_step_run_lines(step, name=step_name)
    if (
        _workflow_step_fields(step, name=step_name) != ("env", "run")
        or _workflow_step_environment(step, name=step_name)
        != expected_environment
        or any(line not in run_lines for line in required_run_lines)
    ):
        raise RepositoryGovernanceError(
            "candidate freeze authorization changed"
        )


def _validate_candidate_freeze_credentials(text: str) -> None:
    preflight_name = "Verify credentials and live controls before the one permitted build"
    readiness_name = (
        "Authenticate the publisher credential before the one permitted build"
    )
    publish_name = "Revalidate controls, publish the verified draft, and seal custody"
    verify_name = "Verify the published immutable candidate and attestations"
    publish = _workflow_named_step(text, name=publish_name)
    verify = _workflow_named_step(text, name=verify_name)
    preflight = _workflow_named_step(text, name=preflight_name)
    readiness = _workflow_named_step(text, name=readiness_name)
    preflight_environment = _workflow_step_environment(
        preflight, name=preflight_name
    )
    readiness_environment = _workflow_step_environment(
        readiness, name=readiness_name
    )
    publish_environment = _workflow_step_environment(
        publish, name=publish_name
    )
    verify_environment = _workflow_step_environment(
        verify, name=verify_name
    )
    credential_steps = (
        (preflight, preflight_name),
        (readiness, readiness_name),
        (publish, publish_name),
        (verify, verify_name),
    )
    actual_step_digests = {
        name: hashlib.sha256(step.encode("utf-8")).hexdigest()
        for step, name in credential_steps
    }
    if any(
        _workflow_step_fields(step, name=name) != ("env", "run")
        for step, name in credential_steps
    ) or actual_step_digests != CANDIDATE_CREDENTIAL_STEP_SHA256:
        raise RepositoryGovernanceError(
            "candidate freeze credential boundary changed"
        )
    expected_preflight_environment = {
        "GH_READ_TOKEN": "${{ github.token }}",
        "GH_ADMIN_TOKEN": "${{ secrets.V1_RELEASE_ADMIN_TOKEN }}",
        "PUBLISH_TOKEN_CONFIGURED": (
            "${{ secrets.V1_RELEASE_PUBLISH_TOKEN != '' }}"
        ),
        "RELEASE_TOKENS_SEPARATED": (
            "${{ secrets.V1_RELEASE_ADMIN_TOKEN != "
            "secrets.V1_RELEASE_PUBLISH_TOKEN }}"
        ),
        "AUTHORIZED_STEWARD": "${{ vars.V1_RELEASE_STEWARD }}",
        "WORKFLOW_REF": "${{ github.workflow_ref }}",
    }
    expected_publish_environment = {
        "GH_READ_TOKEN": "${{ github.token }}",
        "GH_ADMIN_TOKEN": "${{ secrets.V1_RELEASE_ADMIN_TOKEN }}",
        "GH_PUBLISH_TOKEN": "${{ secrets.V1_RELEASE_PUBLISH_TOKEN }}",
        "RELEASE_TOKENS_SEPARATED": (
            "${{ secrets.V1_RELEASE_ADMIN_TOKEN != "
            "secrets.V1_RELEASE_PUBLISH_TOKEN }}"
        ),
        "AUTHORIZED_STEWARD": "${{ vars.V1_RELEASE_STEWARD }}",
        "WORKFLOW_REF": "${{ github.workflow_ref }}",
    }
    expected_readiness_environment = {
        "GH_PUBLISH_TOKEN": "${{ secrets.V1_RELEASE_PUBLISH_TOKEN }}",
        "AUTHORIZED_STEWARD": "${{ vars.V1_RELEASE_STEWARD }}",
        "WORKFLOW_REF": "${{ github.workflow_ref }}",
    }
    expected_verify_environment = {
        "GH_TOKEN": "${{ github.token }}",
        "GH_ADMIN_TOKEN": "${{ secrets.V1_RELEASE_ADMIN_TOKEN }}",
    }
    if (
        preflight_environment != expected_preflight_environment
        or readiness_environment != expected_readiness_environment
        or publish_environment != expected_publish_environment
        or verify_environment != expected_verify_environment
    ):
        raise RepositoryGovernanceError(
            "candidate freeze credential boundary changed"
        )
    if (
        "PUBLISH_TOKEN_CONFIGURED" not in preflight
        or "${{ secrets.V1_RELEASE_PUBLISH_TOKEN }}" in preflight
        or readiness.count("${{ secrets.V1_RELEASE_PUBLISH_TOKEN }}") != 1
        or "GH_ADMIN_TOKEN" in readiness
        or "GH_READ_TOKEN" in readiness
        or "actions/checkout" in readiness
        or "python tools/" in readiness
        or "V1_RELEASE_PUBLISH_TOKEN" in verify
        or "GH_PUBLISH_TOKEN" in verify
    ):
        raise RepositoryGovernanceError(
            "candidate freeze credential boundary changed"
        )
    if (
        publish.count("${{ secrets.V1_RELEASE_PUBLISH_TOKEN }}") != 1
        or "tools/v1_candidate.py" in publish
        or "actions/checkout" in publish
    ):
        raise RepositoryGovernanceError(
            "candidate freeze credential boundary changed"
        )


def _workflow_control_lines(text: str, *, workflow_name: str) -> tuple[str, ...]:
    control_lines: list[str] = []
    block_scalar_indent: int | None = None
    for line in text.splitlines():
        if not line.strip():
            continue
        indent = _yaml_indent(line, label=workflow_name)
        if block_scalar_indent is not None:
            if indent > block_scalar_indent:
                continue
            block_scalar_indent = None
        if line.lstrip().startswith("#"):
            continue
        control_lines.append(line)
        if re.fullmatch(
            r"[ ]*(?:-[ ]+)?[a-z][a-z0-9-]*:[ ]*[|>][+-]?[ ]*(?:#.*)?",
            line,
        ):
            block_scalar_indent = indent
    return tuple(control_lines)


def _workflow_secret_expressions(text: str, *, workflow_name: str) -> tuple[str, ...]:
    for line in _workflow_control_lines(text, workflow_name=workflow_name):
        if re.search(
            r"\\(?:x[0-9a-fA-F]{2}|u[0-9a-fA-F]{4}|U[0-9a-fA-F]{8})",
            line,
        ) or re.search(r"\\[ \t]*$", line):
            raise RepositoryGovernanceError(
                f"workflow YAML character escapes are unsupported: {workflow_name}"
            )
    expressions: list[str] = []
    cursor = 0
    while True:
        start = text.find("${{", cursor)
        if start < 0:
            break
        end = text.find("}}", start + 3)
        if end < 0 or 0 <= text.find("${{", start + 3) < end:
            raise RepositoryGovernanceError(
                f"workflow expression syntax is unsupported: {workflow_name}"
            )
        expression = text[start : end + 2]
        if "\\" in expression:
            raise RepositoryGovernanceError(
                f"workflow expression syntax is unsupported: {workflow_name}"
            )
        if re.search(r"\bsecrets\b", expression, flags=re.IGNORECASE):
            expressions.append(expression)
        cursor = end + 2
    return tuple(expressions)


def _validate_workflow_secret_expressions(
    text: str, *, workflow_name: str
) -> None:
    expected = EXPECTED_WORKFLOW_SECRET_EXPRESSIONS.get(workflow_name, ())
    actual = _workflow_secret_expressions(text, workflow_name=workflow_name)
    if sorted(actual) != sorted(expected):
        raise RepositoryGovernanceError(
            f"workflow secret expression contract changed: {workflow_name}"
        )


def _workflow_job_fields(
    job: str, *, workflow_name: str, job_name: str
) -> dict[str, str]:
    lines = job.splitlines()
    if not lines:
        raise RepositoryGovernanceError(
            f"workflow job mapping is empty: {workflow_name}:{job_name}"
        )
    job_indent = _yaml_indent(
        lines[0], label=f"{workflow_name}:{job_name}"
    )
    field_indent: int | None = None
    fields: dict[str, str] = {}
    for line in lines[1:]:
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        indent = _yaml_indent(line, label=f"{workflow_name}:{job_name}")
        if indent <= job_indent:
            break
        if field_indent is None:
            field_indent = indent
        if indent != field_indent:
            continue
        field = _permission_key_and_value(line, indent=field_indent)
        if field is None:
            raise RepositoryGovernanceError(
                f"workflow job uses unsupported mapping syntax: {workflow_name}:{job_name}"
            )
        key, raw_value = field
        if key in fields:
            raise RepositoryGovernanceError(
                f"workflow job repeats field: {workflow_name}:{job_name}:{key}"
            )
        fields[key] = _plain_yaml_value(
            raw_value, label=f"{workflow_name}:{job_name}:{key}"
        )
    return fields


def _workflow_permissions(
    text: str, *, workflow_name: str
) -> tuple[
    PermissionSpec,
    dict[str, PermissionSpec | None],
    dict[str, str],
]:
    lines = text.splitlines()
    top_level_permissions: list[int] = []
    jobs_declarations: list[int] = []
    top_level_fields: set[str] = set()
    for index, line in enumerate(lines):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        indent = _yaml_indent(line, label=workflow_name)
        entry = _permission_key_and_value(line, indent=indent)
        if indent == 0 and entry is None:
            raise RepositoryGovernanceError(
                f"workflow uses unsupported top-level mapping syntax: {workflow_name}"
            )
        if indent == 0 and entry is not None:
            key = entry[0]
            if key in top_level_fields:
                raise RepositoryGovernanceError(
                    f"workflow repeats top-level field: {workflow_name}:{key}"
                )
            top_level_fields.add(key)
        if indent == 0 and entry is not None and entry[0] == "permissions":
            top_level_permissions.append(index)
        if indent == 0 and entry is not None and entry[0] == "jobs":
            if _plain_yaml_value(entry[1], label=workflow_name):
                raise RepositoryGovernanceError(
                    f"workflow jobs must use a block mapping: {workflow_name}"
                )
            jobs_declarations.append(index)

    if (
        not REQUIRED_WORKFLOW_TOP_LEVEL_FIELDS.issubset(top_level_fields)
        or not top_level_fields.issubset(WORKFLOW_TOP_LEVEL_FIELDS)
        or (
            workflow_name in CANONICAL_WORKFLOW_NAMES
            and top_level_fields != WORKFLOW_TOP_LEVEL_FIELDS
        )
    ):
        raise RepositoryGovernanceError(
            f"workflow top-level contract changed: {workflow_name}"
        )

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
    job_blocks: dict[str, str] = {}
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
        job_blocks[job_name] = "\n".join(lines[start_index:end_index]) + "\n"
    return workflow_permissions, jobs, job_blocks


def _validate_pages_workflow(
    workflow_permissions: PermissionSpec,
    jobs: dict[str, PermissionSpec | None],
    job_blocks: dict[str, str],
    job_fields: dict[str, dict[str, str]],
) -> None:
    """Keep the static-site publisher separate from PR builds and release custody."""
    expected_deploy_fields = {
        "name": "Publish project site",
        "if": PAGES_PUBLISH_CONDITION,
        "needs": "build",
        "runs-on": "ubuntu-latest",
        "timeout-minutes": "10",
        "permissions": "",
        "environment": "github-pages",
        "steps": "",
    }
    expected_deploy_steps = (
        "      - name: Deploy verified static artifact\n"
        "        id: deployment\n"
        "        uses: actions/deploy-pages@"
        "d6db90164ac5ed86f2b6aed7e0febac5b3c0c03e # v4\n"
    )
    expected_upload_step = (
        "      - name: Upload the Pages artifact\n"
        f"        if: {PAGES_PUBLISH_CONDITION}\n"
        "        uses: actions/upload-pages-artifact@"
        "7b1f4a764d45c48632c6b24a0339c27f5614fb0b # v4\n"
        "        with:\n"
        "          path: website/out\n"
    )
    if (
        workflow_permissions != {"contents": "read"}
        or set(jobs) != {"build", "deploy"}
        or jobs.get("build") not in (None, {"contents": "read"})
        or jobs.get("deploy") != {"pages": "write", "id-token": "write"}
        or job_fields.get("deploy") != expected_deploy_fields
        or job_blocks.get("deploy", "").partition("    steps:\n")[2]
        != expected_deploy_steps
        or not job_blocks.get("build", "").rstrip().endswith(
            expected_upload_step.rstrip()
        )
    ):
        raise RepositoryGovernanceError("Pages publication boundary changed")


def _validate_workflows(
    root: Path, allowed_owners: set[str]
) -> tuple[int, int]:
    workflow_paths = sorted((root / ".github/workflows").glob("*.y*ml"))
    if not workflow_paths:
        raise RepositoryGovernanceError("no GitHub Actions workflows found")
    action_use_count = 0
    contents_writers: list[tuple[str, str]] = []
    pages_writers: list[tuple[str, str]] = []
    pages_workflow_seen = False
    candidate_freeze_seen = False
    candidate_job_bytes_valid = False
    candidate_workflow_bytes_valid = False
    for path in workflow_paths:
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            raise RepositoryGovernanceError(f"cannot read workflow: {path.name}") from exc
        if "pull_request_target" in text:
            raise RepositoryGovernanceError(
                f"public-fork-unsafe pull_request_target trigger: {path.name}"
            )
        if path.name != "freeze-v1-candidate.yml" and any(
            secret_name in text for secret_name in CUSTODY_SECRET_NAMES
        ):
            raise RepositoryGovernanceError(
                "custody secret used outside candidate freeze workflow"
            )
        if (
            path.name != "freeze-v1-candidate.yml"
            and CUSTODY_ENVIRONMENT_NAME in text
        ):
            raise RepositoryGovernanceError(
                "custody environment used outside candidate freeze workflow"
            )
        workflow_permissions, jobs, job_blocks = _workflow_permissions(
            text, workflow_name=path.name
        )
        _validate_workflow_secret_expressions(text, workflow_name=path.name)
        job_fields = {
            job_name: _workflow_job_fields(
                job,
                workflow_name=path.name,
                job_name=job_name,
            )
            for job_name, job in job_blocks.items()
        }
        actual_environments = {
            job_name: fields["environment"]
            for job_name, fields in job_fields.items()
            if "environment" in fields
        }
        expected_environments = EXPECTED_WORKFLOW_JOB_ENVIRONMENTS.get(
            path.name, {}
        )
        if actual_environments != expected_environments:
            raise RepositoryGovernanceError(
                f"workflow environment contract changed: {path.name}"
            )
        if path.name == "macos-distribution.yml" and any(
            text.count(marker) != 1 for marker in MACOS_DISTRIBUTION_SOURCE_GUARD
        ):
            raise RepositoryGovernanceError(
                "macOS distribution source guard changed"
            )
        if path.name == "website.yml":
            pages_workflow_seen = True
            _validate_pages_workflow(
                workflow_permissions, jobs, job_blocks, job_fields
            )
        if path.name == "freeze-v1-candidate.yml":
            candidate_freeze_seen = True
            candidate_workflow_bytes_valid = (
                hashlib.sha256(text.encode("utf-8")).hexdigest()
                == CANDIDATE_FREEZE_WORKFLOW_SHA256
            )
            authorize_job = job_blocks.get("authorize")
            preflight_job = job_blocks.get("preflight")
            build_job = job_blocks.get("build")
            publish_job = job_blocks.get("publish")
            expected_authorize_job_fields = {
                "name": "Authorize the designated v1 release steward",
                "permissions": "{}",
                "runs-on": "ubuntu-24.04",
                "timeout-minutes": "2",
                "steps": "",
            }
            expected_preflight_job_fields = {
                "name": "Approve and verify candidate custody before build",
                "needs": "authorize",
                "if": "${{ github.repository == 'Xpounder-com/hormuz' }}",
                "environment": CUSTODY_ENVIRONMENT_NAME,
                "permissions": "",
                "runs-on": "ubuntu-24.04",
                "timeout-minutes": "8",
                "steps": "",
            }
            expected_build_job_fields = {
                "name": "Build the candidate once without publisher authority",
                "needs": "preflight",
                "if": "${{ github.repository == 'Xpounder-com/hormuz' }}",
                "permissions": "",
                "runs-on": "ubuntu-24.04",
                "timeout-minutes": "15",
                "steps": "",
            }
            expected_publish_job_fields = {
                "name": "Approve, independently verify, and publish candidate custody",
                "needs": "build",
                "if": "${{ github.repository == 'Xpounder-com/hormuz' }}",
                "environment": CUSTODY_ENVIRONMENT_NAME,
                "permissions": "",
                "runs-on": "ubuntu-24.04",
                "timeout-minutes": "15",
                "steps": "",
            }
            if (
                set(job_blocks) != {"authorize", "preflight", "build", "publish"}
                or authorize_job is None
                or preflight_job is None
                or build_job is None
                or publish_job is None
                or job_fields.get("authorize")
                != expected_authorize_job_fields
                or job_fields.get("preflight") != expected_preflight_job_fields
                or job_fields.get("build") != expected_build_job_fields
                or job_fields.get("publish") != expected_publish_job_fields
            ):
                raise RepositoryGovernanceError(
                    "candidate freeze job contract changed"
                )
            _validate_candidate_freeze_authorization(authorize_job)
            _validate_candidate_freeze_credentials(preflight_job + publish_job)
            if (
                workflow_permissions != "{}"
                or jobs.get("authorize") != "{}"
                or jobs.get("preflight")
                != {"actions": "read", "contents": "read"}
                or jobs.get("build")
                != {"actions": "read", "contents": "read"}
                or jobs.get("publish")
                != {
                    "actions": "read",
                    "attestations": "read",
                    "contents": "read",
                }
                or "${{ secrets." in build_job
                or CUSTODY_ENVIRONMENT_NAME in build_job
                or "id-token" in build_job
                or "contents: write" in build_job
                or "self-hosted" in build_job
                or "persist-credentials: false" not in build_job
                or "ref: ${{ github.sha }}" not in build_job
                or "actions/upload-artifact@" not in build_job
                or "retention-days: 1" not in build_job
                or "overwrite: false" not in build_job
                or "actions/checkout@" in preflight_job
                or "actions/checkout@" in publish_job
                or "tools/v1_candidate.py" in publish_job
                or "python -m build" in publish_job
                or re.search(r"(?:^|\s)(?:tar|unzip)\s", publish_job)
            ):
                raise RepositoryGovernanceError(
                    "candidate freeze isolation boundary changed"
                )
            actual_job_digests = {
                job_name: hashlib.sha256(job.encode("utf-8")).hexdigest()
                for job_name, job in job_blocks.items()
            }
            candidate_job_bytes_valid = (
                actual_job_digests == CANDIDATE_FREEZE_JOB_SHA256
            )
        for job_name, job_permissions in jobs.items():
            effective_permissions = (
                workflow_permissions if job_permissions is None else job_permissions
            )
            if _contents_permission(effective_permissions) == "write":
                contents_writers.append((path.name, job_name))
            if effective_permissions == "write-all" or (
                isinstance(effective_permissions, dict)
                and effective_permissions.get("pages") == "write"
            ):
                pages_writers.append((path.name, job_name))
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

    if not candidate_freeze_seen:
        raise RepositoryGovernanceError("candidate freeze workflow is required")
    if not pages_workflow_seen:
        raise RepositoryGovernanceError("Pages workflow is required")
    if contents_writers:
        raise RepositoryGovernanceError(
            "workflow-issued contents write is forbidden"
        )
    if any(writer != ("website.yml", "deploy") for writer in pages_writers):
        raise RepositoryGovernanceError(
            "Pages write authority is forbidden outside the static-site publisher"
        )
    if not candidate_job_bytes_valid:
        raise RepositoryGovernanceError(
            "candidate freeze job bytes changed"
        )
    if not candidate_workflow_bytes_valid:
        raise RepositoryGovernanceError(
            "candidate freeze workflow bytes changed"
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
    try:
        candidate_tool = (root / "tools/v1_candidate.py").read_bytes()
    except OSError as exc:
        raise RepositoryGovernanceError(
            "candidate custody tool is unavailable"
        ) from exc
    if hashlib.sha256(candidate_tool).hexdigest() != CANDIDATE_TOOL_SHA256:
        raise RepositoryGovernanceError("candidate custody tool bytes changed")
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
